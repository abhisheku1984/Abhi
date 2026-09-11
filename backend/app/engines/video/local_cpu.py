"""Local CPU video adapter — real frame synthesis + real FFmpeg encoding.

Camera moves are produced by animating a crop/zoom window over a rendered
plate (parallax), which is genuine camera motion over real pixels. Nothing is
simulated in the UI: every job writes an actual encoded video file.
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
from PIL import Image, ImageFilter

from app.core.errors import StudioError
from app.core.logging import get_logger
from app.engines.base import (
    Artifact, BaseModelAdapter, Capabilities, Estimate, GenerateRequest, JobContext, Reference, ValidationResult,
)
from app.engines.image.render import analyze_prompt, render_scene
from app.media import audio_ops, ffmpeg, image_ops

log = get_engine = get_logger("engines.video.local_cpu")

MODES = [
    "text-to-video", "image-to-video", "reference-to-video", "video-to-video",
    "first-frame-to-video", "first-last-frame-video", "extend-video", "video-upscale",
    "video-restyle", "video-edit", "interpolate", "story-scene",
]

CAMERA_PATHS: dict[str, Callable[[float], tuple[float, float, float, float]]] = {
    # t in [0,1] -> (cx, cy, zoom, rotation_deg)
    "static": lambda t: (0.5, 0.5, 1.0, 0.0),
    "pan-left": lambda t: (0.38 + 0.24 * t, 0.5, 1.06, 0.0),
    "pan-right": lambda t: (0.62 - 0.24 * t, 0.5, 1.06, 0.0),
    "tilt-up": lambda t: (0.5, 0.60 - 0.20 * t, 1.06, 0.0),
    "tilt-down": lambda t: (0.5, 0.40 + 0.20 * t, 1.06, 0.0),
    "zoom-in": lambda t: (0.5, 0.5, 1.0 + 0.22 * t, 0.0),
    "zoom-out": lambda t: (0.5, 0.5, 1.26 - 0.22 * t, 0.0),
    "dolly": lambda t: (0.5, 0.5, 1.0 + 0.16 * t, 0.0),
    "tracking": lambda t: (0.30 + 0.40 * t, 0.52, 1.04 + 0.06 * t, 0.0),
    "orbit": lambda t: (0.5 + 0.18 * math.sin(t * math.pi), 0.5, 1.05 + 0.05 * math.cos(t * math.pi), 2.0 * math.sin(t * math.pi)),
    "crane": lambda t: (0.5, 0.66 - 0.26 * t, 1.08 - 0.06 * t, 0.0),
    "handheld": lambda t: (0.5 + 0.012 * math.sin(t * 22.0), 0.5 + 0.014 * math.cos(t * 19.0), 1.05 + 0.01 * math.sin(t * 13.0), 0.35 * math.sin(t * 9.0)),
    "drone": lambda t: (0.5, 0.62 - 0.16 * t, 1.0 + 0.24 * t, 0.0),
    "pov": lambda t: (0.5 + 0.06 * math.sin(t * 6.0), 0.5, 1.0 + 0.30 * t, 0.6 * math.sin(t * 5.0)),
    "macro": lambda t: (0.5, 0.5, 1.0 + 0.34 * t, 0.0),
    "wide-shot": lambda t: (0.5, 0.5, 1.30 - 0.12 * t, 0.0),
    "close-up": lambda t: (0.5, 0.5, 1.0 + 0.30 * t, 0.0),
}

LENS_FACTORS = {"18mm": 0.72, "24mm": 0.85, "35mm": 1.0, "50mm": 1.18, "85mm": 1.42, "135mm": 1.7}
MOTION_FACTORS = {"slow": 0.55, "normal": 1.0, "fast": 1.6, "cinematic": 0.85, "dynamic": 1.35,
                  "realistic": 1.0, "smooth": 0.75, "handheld": 1.2}


def _plate_size(width: int, height: int) -> tuple[int, int]:
    """Render at higher resolution so camera moves never reveal empty edges."""
    return int(width * 1.55), int(height * 1.55)


def _frame_from_plate(plate: Image.Image, t: float, *, camera: str, lens: str, motion: str,
                      out_size: tuple[int, int], frame_index: int = 0) -> Image.Image:
    path = CAMERA_PATHS.get(camera, CAMERA_PATHS["static"])
    cx, cy, zoom, rot = path(t)
    lens_factor = LENS_FACTORS.get(lens, 1.0)
    motion_factor = MOTION_FACTORS.get(motion, 1.0)
    zoom = 1 + (zoom - 1) * motion_factor * lens_factor
    W, H = out_size
    crop_w = max(8, int(plate.width / zoom))
    crop_h = max(8, int(plate.height / zoom))
    cx_px = int(np.clip(cx * plate.width, crop_w / 2, plate.width - crop_w / 2))
    cy_px = int(np.clip(cy * plate.height, crop_h / 2, plate.height - crop_h / 2))
    frame = plate.crop((cx_px - crop_w // 2, cy_px - crop_h // 2, cx_px + crop_w // 2, cy_px + crop_h // 2))
    frame = frame.resize((W, H), Image.LANCZOS)
    if abs(rot) > 0.05:
        frame = frame.rotate(rot, resample=Image.BICUBIC, fillcolor=(0, 0, 0))
    # Breathing exposure keeps motion feeling alive rather than statically looped.
    flicker = 1.0 + 0.012 * math.sin(frame_index * 0.35) + 0.008 * math.sin(frame_index * 1.7)
    arr = np.asarray(frame, dtype=np.float32) * flicker
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


class LocalCPUVideoAdapter(BaseModelAdapter):
    id = "local-cpu-video"
    display_name = "Local CPU Video Engine (deterministic)"
    version = "1.0.0"
    family = "video"
    provider = "local"
    license = "Apache-2.0 (platform code)"
    size_mb = 0
    vram_mb = 0
    speed = "medium"
    is_local = True
    ready_out_of_the_box = True
    notes = ("Renders real frames and encodes with FFmpeg (libx264). Camera movement is a genuine "
             "animated crop over a rendered plate — not a neural video model.")
    capabilities = Capabilities(
        family="video",
        modes=MODES,
        max_resolution="1080p",
        supports_negative_prompt=True,
        supports_seed=True,
        supports_reference_images=True,
        supports_reference_video=True,
        supports_duration=True,
        supports_camera=True,
        supports_lens=True,
        supports_motion=True,
        supports_lighting=True,
        supports_character=True,
        supports_interpolation=True,
        supports_upscale=True,
        max_duration_sec=120,
        max_references=4,
    )

    def param_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "aspect": {"type": "string", "enum": list(image_ops.ASPECTS.keys()), "default": "16:9"},
                "resolution": {"type": "string", "enum": ["512", "768", "1024", "1080"], "default": "768"},
                "duration": {"type": "number", "minimum": 1, "maximum": 120, "default": 5},
                "fps": {"type": "integer", "enum": [12, 16, 24, 30], "default": 24},
                "camera": {"type": "string", "enum": sorted(CAMERA_PATHS.keys()), "default": "dolly"},
                "lens": {"type": "string", "enum": sorted(LENS_FACTORS.keys()), "default": "35mm"},
                "motion": {"type": "string", "enum": sorted(MOTION_FACTORS.keys()), "default": "cinematic"},
                "lighting": {"type": "string", "default": "cinematic"},
                "seed": {"type": "integer"},
                "interpolate": {"type": "boolean", "default": False},
            },
        }

    def validate(self, request: GenerateRequest) -> ValidationResult:
        result = ValidationResult()
        if request.mode in ("image-to-video", "first-frame-to-video") and not request.references:
            result.add_error("Attach a start image for this mode.")
        if request.mode == "first-last-frame-video" and len(request.references) < 2:
            result.add_error("Attach both the first and the last frame.")
        if request.mode in ("video-to-video", "video-restyle", "video-upscale", "video-edit", "extend-video",
                            "interpolate") and not any(r.kind == "video" for r in request.references):
            result.add_error("Attach a source video for this mode.")
        dur = float(request.params.get("duration", 5) or 5)
        if dur > 120:
            result.add_error("Maximum duration is 120 seconds for the CPU engine.")
        self._validate_prompt(request, result)
        return result

    def estimate(self, request: GenerateRequest) -> Estimate:
        dur = float(request.params.get("duration", 5))
        fps = int(request.params.get("fps", 24))
        w, h = image_ops.resolve_size(request.params.get("aspect", "16:9"), request.params.get("resolution", "768"))
        frames = int(dur * fps)
        seconds = 1.5 + frames * (0.03 + 0.05 * (w * h) / (768 * 432))
        if request.params.get("interpolate"):
            seconds += frames * 0.12
        return Estimate(seconds=round(seconds, 2), vram_mb=0, credits=round(dur * 0.6, 2), notes="CPU encode")

    # ------------------------------------------------------------------ #

    def generate(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        self._require_mode(request)
        mode = request.mode
        ctx.progress(5, "preparing")

        if mode in ("video-to-video", "video-restyle"):
            return self._restyle(request, ctx)
        if mode == "video-upscale":
            return self._upscale(request, ctx)
        if mode == "video-edit":
            return self._edit(request, ctx)
        if mode == "extend-video":
            return self._extend(request, ctx)
        if mode == "interpolate":
            return self._interpolate(request, ctx)
        if mode == "first-last-frame-video":
            return self._first_last(request, ctx)
        return self._synthesize(request, ctx)

    # -- synthesis ------------------------------------------------------ #

    def _synthesize(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        width, height = image_ops.resolve_size(request.params.get("aspect", "16:9"),
                                               request.params.get("resolution", "768"))
        fps = int(request.params.get("fps", 24))
        duration = float(request.params.get("duration", 5))
        frames_total = max(1, int(round(duration * fps)))
        camera = request.params.get("camera", "dolly")
        lens = request.params.get("lens", "35mm")
        motion = request.params.get("motion", "cinematic")
        seed = request.seed
        info = analyze_prompt(request.prompt)
        lighting = request.params.get("lighting") or info["lighting"]

        frames_dir = Path(ctx.path("frames"))
        frames_dir.mkdir(parents=True, exist_ok=True)

        start_image: Optional[Image.Image] = None
        if request.references:
            start_image = image_ops.resize(image_ops.to_rgb(image_ops.load(request.references[0].path)),
                                           width, height, keep_aspect=False)

        pw, ph = _plate_size(width, height)
        ctx.progress(12, "rendering plate")
        if start_image is not None:
            plate = image_ops.resize(start_image, pw, ph, keep_aspect=False)
            # Keep a little of the prompt's palette so image-to-video stays on-brief.
            accent = render_scene(request.prompt or "cinematic continuation", width=pw // 2, height=ph // 2,
                                  seed=seed, lighting=lighting, camera=request.params.get("camera", "wide"),
                                  style=request.params.get("style", "cinematic"))
            accent = image_ops.resize(accent, pw, ph, keep_aspect=False)
            strength = float(request.params.get("strength", 0.35))
            plate = Image.blend(plate, accent, float(np.clip(strength, 0, 1)))
        else:
            plate = render_scene(request.prompt or "cinematic scene", width=pw, height=ph, seed=seed,
                                 lighting=lighting, camera=request.params.get("camera", "wide"),
                                 style=request.params.get("style", "cinematic"),
                                 negative_prompt=request.negative_prompt, character=request.character)

        ctx.progress(25, "animating camera")
        for i in range(frames_total):
            if i % 5 == 0:
                ctx.check_cancelled()
                ctx.progress(25 + 55 * (i / frames_total), f"frame {i + 1}/{frames_total}")
            t = i / max(frames_total - 1, 1)
            frame = _frame_from_plate(plate, t, camera=camera, lens=lens, motion=motion,
                                      out_size=(width, height), frame_index=i)
            frame.save(frames_dir / f"frame_{i + 1:05d}.png")

        audio_path = self._maybe_audio(request, ctx, duration)
        out = ctx.path(f"video_{ctx.job_id}.mp4")
        ctx.progress(85, "encoding")
        ffmpeg.frames_to_video(frames_dir, out, fps=fps, pattern="frame_%05d.png", audio=audio_path)
        shutil.rmtree(frames_dir, ignore_errors=True)

        probe = ffmpeg.probe(out)
        return [Artifact(
            path=out, kind="video", mime="video/mp4", name=f"video_{ctx.job_id}",
            meta={
                "engine": self.id, "engine_name": self.display_name, "mode": request.mode,
                "deterministic": True, "diffusion": False, "seed": seed,
                "width": probe.get("width") or width, "height": probe.get("height") or height,
                "duration": probe.get("duration") or duration, "fps": fps,
                "camera": camera, "lens": lens, "motion": motion, "lighting": lighting,
                "frames": frames_total, "prompt": request.prompt, "params": request.params,
                "has_audio": bool(audio_path),
            },
        )]

    def _first_last(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        """Morph between two images with a camera move — real cross-dissolve motion."""
        width, height = image_ops.resolve_size(request.params.get("aspect", "16:9"),
                                               request.params.get("resolution", "768"))
        fps = int(request.params.get("fps", 24))
        duration = float(request.params.get("duration", 5))
        frames_total = max(2, int(round(duration * fps)))
        first = image_ops.resize(image_ops.to_rgb(image_ops.load(request.references[0].path)), width, height, keep_aspect=False)
        last = image_ops.resize(image_ops.to_rgb(image_ops.load(request.references[1].path)), width, height, keep_aspect=False)
        frames_dir = Path(ctx.path("frames"))
        frames_dir.mkdir(parents=True, exist_ok=True)
        camera = request.params.get("camera", "dolly")
        lens = request.params.get("lens", "35mm")
        motion = request.params.get("motion", "cinematic")
        pw, ph = _plate_size(width, height)

        for i in range(frames_total):
            if i % 5 == 0:
                ctx.check_cancelled()
            t = i / (frames_total - 1)
            # Ease the dissolve so the middle of the shot carries the transition.
            eased = 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, (t - 0.12) / 0.76)))
            plate = Image.blend(image_ops.resize(first, pw, ph, keep_aspect=False),
                                image_ops.resize(last, pw, ph, keep_aspect=False), eased)
            frame = _frame_from_plate(plate, t, camera=camera, lens=lens, motion=motion,
                                      out_size=(width, height), frame_index=i)
            frame.save(frames_dir / f"frame_{i + 1:05d}.png")
            ctx.progress(20 + 60 * (i / frames_total), f"frame {i + 1}/{frames_total}")

        out = ctx.path(f"video_{ctx.job_id}.mp4")
        ctx.progress(85, "encoding")
        ffmpeg.frames_to_video(frames_dir, out, fps=fps, pattern="frame_%05d.png")
        shutil.rmtree(frames_dir, ignore_errors=True)
        return [Artifact(path=out, kind="video", mime="video/mp4", name=f"video_{ctx.job_id}",
                         meta={"engine": self.id, "mode": request.mode, "deterministic": True, "diffusion": False,
                               "fps": fps, "duration": duration, "width": width, "height": height,
                               "prompt": request.prompt, "params": request.params})]

    # -- video-in / video-out ------------------------------------------- #

    def _restyle(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        src = next(r for r in request.references if r.kind == "video")
        info = ffmpeg.probe(src.path)
        duration = min(float(info.get("duration") or 5), 30)
        fps = int(request.params.get("fps", 24))
        scale_h = int(request.params.get("resolution", 512)) if str(request.params.get("resolution", "")).isdigit() else 512
        frames_dir = Path(ctx.path("frames"))
        out_dir = Path(ctx.path("out_frames"))
        frames_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        frames = ffmpeg.extract_frames(src.path, frames_dir, fps=min(fps, 12), max_frames=min(360, int(duration * 12)))
        if not frames:
            raise StudioError("Could not read frames from that video.",
                              suggested_action="Try a different clip or re-encode it as MP4 (H.264).")

        style_ref = next((r for r in request.references if r.kind == "image"), None)
        lighting = request.params.get("lighting") or analyze_prompt(request.prompt)["lighting"]
        strength = float(request.params.get("strength", 0.6))
        target = render_scene(request.prompt or "cinematic grade", width=256, height=256,
                              seed=request.seed, lighting=lighting)
        for i, fpath in enumerate(frames):
            if i % 3 == 0:
                ctx.check_cancelled()
            img = image_ops.to_rgb(image_ops.load(fpath))
            if img.height > scale_h:
                img = img.resize((int(img.width * scale_h / img.height), scale_h), Image.LANCZOS)
            if style_ref:
                img = image_ops.style_transfer(img, image_ops.to_rgb(image_ops.load(style_ref.path)), strength=strength)
            else:
                img = image_ops.style_transfer(img, target, strength=strength)
            img = image_ops.vignette(img, strength=0.22)
            img.save(out_dir / f"frame_{i + 1:05d}.png")
            ctx.progress(15 + 65 * (i / len(frames)), f"frame {i + 1}/{len(frames)}")

        out = ctx.path(f"video_{ctx.job_id}.mp4")
        ffmpeg.frames_to_video(out_dir, out, fps=min(fps, 12), pattern="frame_%05d.png")
        shutil.rmtree(frames_dir, ignore_errors=True)
        shutil.rmtree(out_dir, ignore_errors=True)
        return [Artifact(path=out, kind="video", mime="video/mp4", name=f"video_{ctx.job_id}",
                         meta={"engine": self.id, "mode": request.mode, "deterministic": True, "diffusion": False,
                               "source_duration": duration, "seed": request.seed, "prompt": request.prompt,
                               "params": request.params})]

    def _upscale(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        src = next(r for r in request.references if r.kind == "video")
        factor = int(request.params.get("upscale_factor", 2))
        info = ffmpeg.probe(src.path)
        duration = min(float(info.get("duration") or 5), 20)
        frames_dir = Path(ctx.path("frames"))
        out_dir = Path(ctx.path("out_frames"))
        frames_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        frames = ffmpeg.extract_frames(src.path, frames_dir, fps=12, max_frames=240)
        if not frames:
            raise StudioError("Could not read frames from that video.", suggested_action="Use an MP4/H.264 clip.")
        for i, fpath in enumerate(frames):
            if i % 2 == 0:
                ctx.check_cancelled()
            img = image_ops.upscale(image_ops.to_rgb(image_ops.load(fpath)), factor=factor)
            img.save(out_dir / f"frame_{i + 1:05d}.png")
            ctx.progress(15 + 70 * (i / len(frames)), f"upscaling {i + 1}/{len(frames)}")
        out = ctx.path(f"video_{ctx.job_id}.mp4")
        ffmpeg.frames_to_video(out_dir, out, fps=12, pattern="frame_%05d.png", crf=18)
        shutil.rmtree(frames_dir, ignore_errors=True)
        shutil.rmtree(out_dir, ignore_errors=True)
        return [Artifact(path=out, kind="video", mime="video/mp4", name=f"video_{ctx.job_id}",
                         meta={"engine": self.id, "mode": request.mode, "upscale_factor": factor,
                               "deterministic": True, "diffusion": False, "params": request.params})]

    def _edit(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        src = next(r for r in request.references if r.kind == "video")
        out = ctx.path(f"video_{ctx.job_id}.mp4")
        tmp = ctx.path(f"tmp_{ctx.job_id}.mp4")
        current = src.path
        start = float(request.params.get("start", 0))
        duration = request.params.get("duration")
        speed = float(request.params.get("speed", 1.0))
        crop_box = request.params.get("crop")
        fade_in = float(request.params.get("fade_in", 0))
        fade_out = float(request.params.get("fade_out", 0))

        if start or duration:
            ffmpeg.trim(current, tmp, start=start, duration=duration)
            current, tmp = tmp, ctx.path(f"tmp2_{ctx.job_id}.mp4")
            ctx.progress(30, "trimmed")
        if crop_box and len(crop_box) == 4:
            w, h, x, y = [int(v) for v in crop_box]
            ffmpeg.crop(current, tmp, width=w, height=h, x=x, y=y)
            current, tmp = tmp, ctx.path(f"tmp3_{ctx.job_id}.mp4")
            ctx.progress(45, "cropped")
        if abs(speed - 1.0) > 0.01:
            ffmpeg.set_speed(current, tmp, factor=speed)
            current, tmp = tmp, ctx.path(f"tmp4_{ctx.job_id}.mp4")
            ctx.progress(60, "retimed")
        if fade_in or fade_out:
            ffmpeg.fade_in_out(current, out, fade_in=fade_in, fade_out=fade_out)
        elif current != src.path:
            Path(current).rename(out)
        else:
            ffmpeg.trim(src.path, out, start=0)
        for leftover in {tmp, ctx.path(f"tmp2_{ctx.job_id}.mp4"), ctx.path(f"tmp3_{ctx.job_id}.mp4"),
                         ctx.path(f"tmp4_{ctx.job_id}.mp4")}:
            Path(leftover).unlink(missing_ok=True)
        ctx.progress(95, "done")
        return [Artifact(path=out, kind="video", mime="video/mp4", name=f"video_{ctx.job_id}",
                         meta={"engine": self.id, "mode": request.mode, "deterministic": True, "diffusion": False,
                               "params": request.params})]

    def _extend(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        src = next(r for r in request.references if r.kind == "video")
        extra = float(request.params.get("extend_seconds", 3))
        out = ctx.path(f"video_{ctx.job_id}.mp4")
        ctx.progress(40, "extending")
        ffmpeg.extend_video(src.path, out, extra_seconds=extra)
        probe = ffmpeg.probe(out)
        return [Artifact(path=out, kind="video", mime="video/mp4", name=f"video_{ctx.job_id}",
                         meta={"engine": self.id, "mode": request.mode, "deterministic": True, "diffusion": False,
                               "duration": probe.get("duration"), "extend_seconds": extra, "params": request.params})]

    def _interpolate(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        src = next(r for r in request.references if r.kind == "video")
        target_fps = int(request.params.get("target_fps", 48))
        out = ctx.path(f"video_{ctx.job_id}.mp4")
        ctx.progress(40, "interpolating")
        ffmpeg.interpolate(src.path, out, target_fps=target_fps)
        return [Artifact(path=out, kind="video", mime="video/mp4", name=f"video_{ctx.job_id}",
                         meta={"engine": self.id, "mode": request.mode, "deterministic": True, "diffusion": False,
                               "target_fps": target_fps, "params": request.params})]

    # -- audio helper ---------------------------------------------------- #

    def _maybe_audio(self, request: GenerateRequest, ctx: JobContext, duration: float) -> Optional[str]:
        """Attach real synthesised audio when the request asks for it."""
        want_music = bool(request.params.get("music")) or bool(request.params.get("background_music"))
        want_voice = bool(request.voice) and bool(request.params.get("narration") or request.params.get("voiceover"))
        if not (want_music or want_voice):
            return None
        tracks: list[str] = []
        if want_voice:
            text = str(request.params.get("narration") or request.params.get("voiceover") or request.prompt or "")
            audio = audio_ops.generate_voice_track(text, duration, seed=request.seed,
                                                   voice=str((request.voice or {}).get("name", "narrator")).lower(),
                                                   speed=float((request.voice or {}).get("speed", 1.0) or 1.0))
            p = audio_ops.write_wav(ctx.path(f"voice_{ctx.job_id}.wav"), audio_ops.fade(audio_ops.pad_to(audio, duration)))
            tracks.append(p)
        if want_music:
            genre = str(request.params.get("music_genre") or request.params.get("music") or "cinematic").lower()
            music = audio_ops.generate_music(genre=genre, seconds=duration + 1.0, seed=request.seed + 3)
            music = audio_ops.normalize(music, target=0.22)
            p = audio_ops.write_wav(ctx.path(f"music_{ctx.job_id}.wav"), audio_ops.pad_to(music, duration))
            tracks.append(p)
        if not tracks:
            return None
        if len(tracks) == 1:
            return tracks[0]
        mixed = audio_ops.mix_tracks([audio_ops.pad_to(t, duration) if isinstance(t, np.ndarray) else t for t in tracks])
        return audio_ops.write_wav(ctx.path(f"audio_{ctx.job_id}.wav"), mixed)


ADAPTERS = [LocalCPUVideoAdapter()]
