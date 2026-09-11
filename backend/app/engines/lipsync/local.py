"""Local lip-sync adapter.

Two real paths:

1. **Rig path** — when an avatar profile/seed is supplied the frames are
   re-rendered from the rig with mouth aperture taken from the audio envelope
   (highest quality, fully deterministic).
2. **Warp path** — for arbitrary video the mouth region is geometrically
   deformed frame-by-frame from the audio RMS envelope. This is a genuine
   audio-driven animation, clearly labelled as non-neural.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

from app.core.errors import StudioError
from app.core.logging import get_logger
from app.engines.avatar.local import mouth_curve
from app.engines.base import (
    Artifact, BaseModelAdapter, Capabilities, Estimate, GenerateRequest, JobContext, ValidationResult,
)
from app.engines.image.render import render_avatar_frame
from app.media import ffmpeg, image_ops

log = get_logger("engines.lipsync.local")


class VisemeLipSyncAdapter(BaseModelAdapter):
    id = "local-viseme-lipsync"
    display_name = "Local Audio-Driven Lip Sync"
    version = "1.0.0"
    family = "lipsync"
    provider = "local"
    license = "Apache-2.0 (platform code)"
    speed = "medium"
    is_local = True
    ready_out_of_the_box = True
    notes = ("Drives mouth motion from the real audio envelope. Not a neural lip-sync model — "
             "install Wav2Lip/MuseTalk for neural lip sync.")
    capabilities = Capabilities(
        family="lipsync",
        modes=["lip-sync", "avatar-lip-sync"],
        supports_reference_images=True,
        supports_duration=True,
        supports_lipsync=True,
        supports_seed=True,
        max_duration_sec=180,
    )

    def param_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "mouth_box": {"type": "array", "items": {"type": "number"},
                              "description": "Normalised [x0,y0,x1,y1] mouth region for the warp path",
                              "default": [0.36, 0.60, 0.64, 0.74]},
                "intensity": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.55},
                "fps": {"type": "integer", "enum": [12, 16, 24, 30], "default": 24},
            },
        }

    def validate(self, request: GenerateRequest) -> ValidationResult:
        result = ValidationResult()
        if not any(r.kind == "video" for r in request.references) and not request.params.get("avatar_profile"):
            result.add_error("Attach the video you want lip-synced.")
        if not any(r.kind == "audio" for r in request.references):
            result.add_error("Attach the speech audio to sync to.")
        return result

    def estimate(self, request: GenerateRequest) -> Estimate:
        return Estimate(seconds=25.0, credits=3.0)

    def generate(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        audio_ref = next((r for r in request.references if r.kind == "audio"), None)
        if audio_ref is None:
            raise StudioError("Attach speech audio.", suggested_action="Upload the narration track.")
        profile = request.params.get("avatar_profile") or request.character or {}
        video_ref = next((r for r in request.references if r.kind == "video"), None)

        if profile.get("rig") is not False and (profile.get("skin") or profile.get("avatar_id") or profile.get("seed")):
            return self._rig_path(request, ctx, audio_ref.path, profile)
        if video_ref is None:
            raise StudioError("Attach a video to lip-sync, or supply an avatar profile to re-render.",
                              suggested_action="Upload a talking-head clip.")
        return self._warp_path(request, ctx, video_ref.path, audio_ref.path)

    # ------------------------------------------------------------------ #

    def _rig_path(self, request: GenerateRequest, ctx: JobContext, audio_path: str, profile: dict) -> list[Artifact]:
        import math

        info = ffmpeg.probe(audio_path)
        duration = float(info.get("duration") or 5)
        fps = int(request.params.get("fps", 24))
        frames_total = max(1, int(round(duration * fps)))
        curve = mouth_curve(audio_path, frames_total, fps, seed=request.seed)
        frames_dir = Path(ctx.path("frames"))
        frames_dir.mkdir(parents=True, exist_ok=True)

        for i in range(frames_total):
            if i % 6 == 0:
                ctx.check_cancelled()
                ctx.progress(25 + 55 * (i / frames_total), f"frame {i + 1}/{frames_total}")
            t = i / float(fps)
            frame = render_avatar_frame(
                width=int(profile.get("width", 720)), height=int(profile.get("height", 1280)),
                seed=int(profile.get("seed", request.seed)),
                skin=tuple(profile.get("skin", (226, 178, 148))),
                hair=tuple(profile.get("hair", (42, 32, 28))),
                clothing=tuple(profile.get("clothing", (38, 52, 96))),
                background=tuple(profile.get("background", (24, 26, 40))),
                expression=profile.get("expression", "neutral"),
                mouth_open=float(curve[i]),
                blink=1.0 if i % max(int(fps * 3.2), 1) == 0 else 0.0,
                head_tilt=0.5 * math.sin(t * 0.9),
                gesture=profile.get("gesture", "talking"),
                style=profile.get("style", "photorealistic"),
                lighting=profile.get("lighting", "studio"),
            )
            frame.save(frames_dir / f"frame_{i + 1:05d}.png")

        out = ctx.path(f"lipsync_{ctx.job_id}.mp4")
        ctx.progress(88, "encoding")
        ffmpeg.frames_to_video(frames_dir, out, fps=fps, pattern="frame_%05d.png", audio=audio_path)
        shutil.rmtree(frames_dir, ignore_errors=True)
        info = ffmpeg.probe(out)
        return [Artifact(path=out, kind="video", mime="video/mp4", name=f"lipsync_{ctx.job_id}",
                         meta={"engine": self.id, "mode": request.mode, "lip_sync": True, "method": "rig",
                               "deterministic": True, "duration": info.get("duration"), "fps": fps,
                               "params": request.params})]

    def _warp_path(self, request: GenerateRequest, ctx: JobContext, video_path: str, audio_path: str) -> list[Artifact]:
        info = ffmpeg.probe(video_path)
        duration = min(float(info.get("duration") or 5), 60)
        fps = int(request.params.get("fps", 24))
        frames_dir = Path(ctx.path("frames"))
        out_dir = Path(ctx.path("out_frames"))
        frames_dir.mkdir(parents=True, exist_ok=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        frames = ffmpeg.extract_frames(video_path, frames_dir, fps=min(fps, 24), max_frames=min(1440, int(duration * 24)))
        if not frames:
            raise StudioError("Could not read frames from that video.",
                              suggested_action="Use an MP4 (H.264) clip.")
        effective_fps = min(fps, 24)
        curve = mouth_curve(audio_path, len(frames), effective_fps, seed=request.seed)
        intensity = float(request.params.get("intensity", 0.55))
        box = request.params.get("mouth_box") or [0.36, 0.60, 0.64, 0.74]

        for i, fpath in enumerate(frames):
            if i % 4 == 0:
                ctx.check_cancelled()
                ctx.progress(20 + 60 * (i / len(frames)), f"frame {i + 1}/{len(frames)}")
            img = image_ops.to_rgb(image_ops.load(fpath))
            W, H = img.size
            x0, y0, x1, y1 = [float(v) for v in box]
            bx0, by0, bx1, by1 = int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)
            if bx1 <= bx0 or by1 <= by0:
                img.save(out_dir / f"frame_{i + 1:05d}.png")
                continue
            mouth = img.crop((bx0, by0, bx1, by1))
            openness = float(np.clip(curve[i], 0, 1)) * intensity
            new_h = max(2, int(mouth.height * (1.0 + 0.55 * openness)))
            scaled = mouth.resize((mouth.width, new_h), Image.BICUBIC)
            dy = (new_h - mouth.height) // 2
            warped = scaled.crop((0, dy, mouth.width, dy + mouth.height)) if new_h > mouth.height else scaled
            warped = warped.resize((mouth.width, mouth.height), Image.BICUBIC)
            mask = Image.new("L", (mouth.width, mouth.height), 0)
            mask_px = mask.load()
            for yy in range(mouth.height):
                for xx in range(mouth.width):
                    fx = abs(xx / max(mouth.width - 1, 1) - 0.5) * 2
                    fy = abs(yy / max(mouth.height - 1, 1) - 0.5) * 2
                    mask_px[xx, yy] = int(255 * max(0.0, 1 - max(fx, fy) ** 1.6))
            mask = mask.filter(ImageFilter.GaussianBlur(max(1.0, mouth.width / 22)))
            img.paste(warped, (bx0, by0), mask)
            img.save(out_dir / f"frame_{i + 1:05d}.png")

        out = ctx.path(f"lipsync_{ctx.job_id}.mp4")
        ctx.progress(88, "encoding")
        ffmpeg.frames_to_video(out_dir, out, fps=effective_fps, pattern="frame_%05d.png")
        final = ctx.path(f"lipsync_av_{ctx.job_id}.mp4")
        ffmpeg.mux_audio(out, audio_path, final, original_audio=False)
        shutil.rmtree(frames_dir, ignore_errors=True)
        shutil.rmtree(out_dir, ignore_errors=True)
        probe = ffmpeg.probe(final)
        return [Artifact(path=final, kind="video", mime="video/mp4", name=f"lipsync_{ctx.job_id}",
                         meta={"engine": self.id, "mode": request.mode, "lip_sync": True, "method": "warp",
                               "deterministic": True, "duration": probe.get("duration"),
                               "intensity": intensity, "frames": len(frames), "params": request.params})]


ADAPTERS = [VisemeLipSyncAdapter()]
