"""Video adapters.

FFmpegMotionAdapter runs for real on CPU and covers:
  text_to_video (procedural keyframes -> motion), image_to_video (camera moves),
  first_frame_to_video, first_last_frame_to_video, video_to_video (restyle),
  video_upscale, video_extension, slideshow.

The diffusion video models (SVD / AnimateDiff / CogVideoX) are fully declared
and gated; they execute the moment weights + VRAM are available.
"""
from __future__ import annotations

import math
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from app.adapters.base import (
    GeneratedFile,
    GenerationRequest,
    GenerationResult,
    ProgressCallback,
    VideoModelAdapter,
    timed,
)
from app.core.errors import ErrorCode, StudioError
from app.services import media

CAMERA_PRESETS = {
    "static": {"zoom": 1.0, "pan": (0.0, 0.0)},
    "pan left": {"zoom": 1.08, "pan": (-0.18, 0.0)},
    "pan right": {"zoom": 1.08, "pan": (0.18, 0.0)},
    "tilt up": {"zoom": 1.08, "pan": (0.0, -0.16)},
    "tilt down": {"zoom": 1.08, "pan": (0.0, 0.16)},
    "zoom in": {"zoom": 1.35, "pan": (0.0, 0.0)},
    "zoom out": {"zoom": 0.72, "pan": (0.0, 0.0)},
    "dolly": {"zoom": 1.22, "pan": (0.0, 0.04)},
    "tracking": {"zoom": 1.12, "pan": (0.24, 0.0)},
    "orbit": {"zoom": 1.15, "pan": (0.20, -0.08)},
    "crane": {"zoom": 1.18, "pan": (0.0, -0.22)},
    "handheld": {"zoom": 1.06, "pan": (0.0, 0.0), "shake": 0.010},
    "drone": {"zoom": 1.25, "pan": (0.10, -0.10)},
    "pov": {"zoom": 1.05, "pan": (0.0, 0.0), "shake": 0.018},
    "macro": {"zoom": 1.6, "pan": (0.0, 0.0)},
}

MOTION_PRESETS = {
    "slow": {"speed": 0.6, "shake": 0.4},
    "normal": {"speed": 1.0, "shake": 1.0},
    "fast": {"speed": 1.7, "shake": 1.4},
    "cinematic": {"speed": 0.85, "shake": 0.5},
    "dynamic": {"speed": 1.45, "shake": 1.5},
    "realistic": {"speed": 1.0, "shake": 1.2},
    "smooth": {"speed": 0.9, "shake": 0.0},
    "handheld": {"speed": 1.05, "shake": 2.2},
}


def _ease(t: float, kind: str = "linear") -> float:
    if kind == "ease_in":
        return t * t
    if kind == "ease_out":
        return 1 - (1 - t) ** 2
    if kind == "ease_in_out":
        return 3 * t * t - 2 * t ** 3
    return t


def _tmp_dir(req: GenerationRequest, name: str) -> Path:
    d = Path(req.output_dir) / name
    d.mkdir(parents=True, exist_ok=True)
    return d


class FFmpegMotionAdapter(VideoModelAdapter):
    """Real frame synthesis + FFmpeg encoding. No model weights required."""

    key = "ffmpeg_motion"
    name = "FFmpeg Motion Engine (CPU)"
    capability = "image_to_video"
    capabilities = (
        "text_to_video", "image_to_video", "first_frame_to_video",
        "first_last_frame_to_video", "video_to_video", "video_upscale",
        "video_extension", "video_restyle", "slideshow",
    )
    version = "1.1"
    license = "MIT (this engine) / FFmpeg LGPL-GPL"
    source_url = "built-in"
    description = (
        "Renders real motion by synthesising every frame (camera move, parallax, "
        "dissolve, shake) and encoding with FFmpeg/H.264. Runs on CPU. Quality is "
        "limited by the quality of the input frames - with a diffusion image model "
        "installed the same pipeline produces model-quality video."
    )
    quality = "draft"
    ram_gb = 1.0
    requires_binaries = ()   # resolved via imageio-ffmpeg fallback

    def status(self, refresh: bool = False):
        st = super().status(refresh=refresh)
        if st.code == ErrorCode.DEPENDENCY_MISSING and not media.ffmpeg_available():
            st.message = "FFmpeg binary not found."
            st.remediation = "pip install imageio-ffmpeg"
        return st

    def validate(self, req: GenerationRequest) -> None:
        super().validate(req)
        if req.duration <= 0 or req.duration > 600:
            raise StudioError("Duration must be between 0 and 600 seconds.", code=ErrorCode.INVALID_REQUEST)
        if req.fps < 1 or req.fps > 60:
            raise StudioError("FPS must be between 1 and 60.", code=ErrorCode.INVALID_REQUEST)

    def estimate(self, req: GenerationRequest):
        est = super().estimate(req)
        frames = int(req.duration * req.fps)
        per_frame = (req.width * req.height) / 2_500_000.0
        est.eta_seconds = max(2.0, frames * (0.02 + per_frame))
        est.notes = f"{frames} frames rendered on CPU"
        return est

    # ---- frame synthesis ---------------------------------------------------
    def _camera_frame(self, base: Image.Image, t: float, camera: str, motion: str,
                      seed: int) -> Image.Image:
        w, h = base.size
        cfg = CAMERA_PRESETS.get(camera, CAMERA_PRESETS["static"])
        mot = MOTION_PRESETS.get(motion, MOTION_PRESETS["normal"])
        e = _ease(t, "ease_in_out")
        zoom = 1.0 + (cfg["zoom"] - 1.0) * e
        pan_x = cfg["pan"][0] * e * w
        pan_y = cfg["pan"][1] * e * h
        shake = cfg.get("shake", 0.0) * mot["shake"]
        if shake:
            rng = np.random.default_rng((seed + int(t * 1000)) & 0xFFFFFFFF)
            pan_x += rng.normal(0, shake * w)
            pan_y += rng.normal(0, shake * h)

        cw, ch = int(w / zoom), int(h / zoom)
        cw -= cw % 2
        ch -= ch % 2
        cx = int(w / 2 + pan_x - cw / 2)
        cy = int(h / 2 + pan_y - ch / 2)
        cx = max(0, min(w - cw, cx))
        cy = max(0, min(h - ch, cy))
        frame = base.crop((cx, cy, cx + cw, cy + ch)).resize((w, h), Image.Resampling.LANCZOS)
        return frame.convert("RGB")

    def _keyframes(self, req: GenerationRequest, progress: ProgressCallback) -> list[Image.Image]:
        """Render 1-4 procedural keyframes; intermediate frames are interpolated."""
        from app.services.procedural_render import render_scene, seed_from_text

        seed = req.seed if req.seed is not None else seed_from_text(req.prompt or "scene")
        count = max(1, min(4, int(req.param("keyframes", 2))))
        shots = []
        for i in range(count):
            progress(0.05 + 0.5 * (i / count), f"rendering keyframe {i + 1}/{count}")
            img = render_scene(
                req.prompt, req.width, req.height,
                seed=(seed + i * 7919) & 0xFFFFFFFF,
                style=req.param("style", ""), mood=req.param("mood", ""),
                lighting=req.param("lighting", ""), camera=req.param("camera", "static"),
                progress=None,
            )
            shots.append(img)
        return shots

    def _build_frames(self, req: GenerationRequest, progress: ProgressCallback) -> tuple[list[str], int]:
        mode = (req.param("mode") or "text_to_video").lower()
        w, h = req.width, req.height
        w -= w % 2
        h -= h % 2
        fps = int(req.fps)
        total = max(2, int(req.duration * fps))
        tmp = _tmp_dir(req, "frames")

        if mode in ("text_to_video",):
            keys = self._keyframes(req, progress)
            camera = req.param("camera", "static")
            motion = req.param("motion", "normal")
            seed = req.seed or 0
            paths = []
            for i in range(total):
                req.check_cancelled()
                p = float(i) / max(total - 1, 1)
                # blend between consecutive keyframes, then apply camera motion
                seg = p * (len(keys) - 1) if len(keys) > 1 else 0.0
                idx = min(len(keys) - 2, int(seg)) if len(keys) > 1 else 0
                frac = seg - idx if len(keys) > 1 else 0.0
                if len(keys) > 1:
                    base = Image.blend(keys[idx], keys[idx + 1], float(_ease(frac, "ease_in_out")))
                else:
                    base = keys[0]
                frame = self._camera_frame(base, p, camera, motion, seed)
                fp = tmp / f"f{i:06d}.png"
                frame.save(fp, optimize=False)
                paths.append(str(fp))
                if i % max(1, total // 20) == 0:
                    progress(0.55 + 0.35 * (i / total), f"frame {i + 1}/{total}")
            return paths, total

        # image-driven modes
        img_refs = [r for r in req.references if r.kind == "image"]
        if not img_refs:
            raise StudioError(
                f"Mode '{mode}' requires at least one image reference.",
                code=ErrorCode.INVALID_REQUEST,
                remediation="Attach a start image (and optionally an end image) to the request.",
            )
        start = Image.open(img_refs[0].path).convert("RGB")
        start = media.resize_image(start, w, h, "lanczos")
        end = None
        if len(img_refs) > 1 and mode in ("first_last_frame_to_video", "slideshow"):
            end = media.resize_image(Image.open(img_refs[1].path).convert("RGB"), w, h, "lanczos")

        camera = req.param("camera", "static" if end is None else "zoom in")
        motion = req.param("motion", "normal")
        seed = req.seed or 0
        paths = []
        gallery = [media.resize_image(Image.open(r.path).convert("RGB"), w, h, "lanczos")
                   for r in img_refs]
        per = float(req.param("seconds_per_image", 3.0))
        for i in range(total):
            req.check_cancelled()
            p = float(i) / max(total - 1, 1)
            if mode == "slideshow" and len(gallery) > 1:
                seg = p * len(gallery)
                idx = min(len(gallery) - 1, int(seg))
                frac = seg - idx
                base = gallery[idx]
                nxt = gallery[min(len(gallery) - 1, idx + 1)]
                if frac > 0.88 and idx + 1 <= len(gallery) - 1:
                    base = Image.blend(base, nxt, float((frac - 0.88) / 0.12))
                frame = self._camera_frame(base, (i / max(1, fps)) / max(per, 0.1) % 1.0, camera, motion, seed)
            elif end is not None:
                base = Image.blend(start, end, float(_ease(p, "ease_in_out")))
                frame = self._camera_frame(base, p, camera, motion, seed)
            else:
                frame = self._camera_frame(start, p, camera, motion, seed)
            fp = tmp / f"f{i:06d}.png"
            frame.save(fp, optimize=False)
            paths.append(str(fp))
            if i % max(1, total // 20) == 0:
                progress(0.2 + 0.7 * (i / total), f"frame {i + 1}/{total}")
        return paths, total

    @timed
    def generate(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        self.ensure_ready()
        mode = (req.param("mode") or "text_to_video").lower()

        if mode == "video_upscale":
            return self._upscale(req, progress)
        if mode in ("video_to_video", "video_restyle", "video_extension"):
            return self._restyle(req, progress, mode)

        paths, total = self._build_frames(req, progress)
        progress(0.92, "encoding video")
        out = str(Path(req.output_dir) / f"video_{req.seed or 0}_{int(req.duration)}s.mp4")
        media.encode_frames(paths, out, fps=req.fps, size=(req.width - req.width % 2, req.height - req.height % 2))
        progress(1.0, "complete")
        return GenerationResult(
            files=[GeneratedFile(out, "video", "video/mp4", {"frames": total, "mode": mode,
                                                             "renderer": "ffmpeg_motion"})],
            meta={"mode": mode, "frames": total, "fps": req.fps, "renderer": "ffmpeg_motion",
                  "quality": "draft", "note": "procedural/keyframe motion - not a diffusion video model"},
            seed=req.seed, model_id=self.key,
        )

    # ---- video-to-video ----------------------------------------------------
    def _restyle(self, req: GenerationRequest, progress: ProgressCallback, mode: str) -> GenerationResult:
        vids = [r for r in req.references if r.kind == "video"]
        if not vids:
            raise StudioError("video_to_video requires a source video reference.", code=ErrorCode.INVALID_REQUEST)
        src = vids[0].path
        out = str(Path(req.output_dir) / f"{mode}.mp4")
        info = media.probe(src)
        progress(0.3, "probing source")
        if mode == "video_extension":
            target = float(req.duration or (info.duration * 1.5))
            loops = max(1, int(math.ceil(target / max(info.duration, 0.1))))
            media.concat_videos([src] * loops, out, reencode=True)
            media.trim_video(out, out + ".trim.mp4", 0, target)
            shutil.move(out + ".trim.mp4", out)
        else:
            preset = str(req.param("filter", "cinematic"))
            media.apply_video_filters(
                src, out,
                speed=float(req.param("speed", 1.0)),
                volume=float(req.param("volume", 1.0)),
                brightness=float(req.param("brightness", 0.0)),
                contrast=float(req.param("contrast", 1.12)),
                saturation=float(req.param("saturation", 1.15)),
                fade_in=float(req.param("fade_in", 0.0)),
                fade_out=float(req.param("fade_out", 0.0)),
                text=req.param("text") or None,
                scale=(req.width - req.width % 2, req.height - req.height % 2) if req.param("resize") else None,
            )
        progress(1.0, "complete")
        return GenerationResult(files=[GeneratedFile(out, "video", "video/mp4", {"mode": mode})],
                                meta={"mode": mode, "renderer": "ffmpeg"}, model_id=self.key)

    def _upscale(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        vids = [r for r in req.references if r.kind == "video"]
        if not vids:
            raise StudioError("video_upscale requires a source video.", code=ErrorCode.INVALID_REQUEST)
        src = vids[0].path
        factor = float(req.param("scale", 2.0))
        info = media.probe(src)
        tmp = _tmp_dir(req, "upscale")
        frames = media.extract_frames(src, str(tmp), fps=float(info.fps or 24), max_frames=100000)
        out_frames = []
        for i, f in enumerate(frames):
            req.check_cancelled()
            with Image.open(f) as im:
                up = media.upscale_image(im.convert("RGB"), factor)
                p = tmp / f"up_{i:06d}.png"
                up.save(p)
                out_frames.append(str(p))
            if i % 10 == 0:
                progress(0.2 + 0.6 * (i / max(1, len(frames))), f"upscaling frame {i + 1}/{len(frames)}")
        out = str(Path(req.output_dir) / "video_upscaled.mp4")
        media.encode_frames(out_frames, out, fps=info.fps or 24.0)
        progress(1.0, "complete")
        return GenerationResult(files=[GeneratedFile(out, "video", "video/mp4", {"scale": factor})],
                                meta={"mode": "video_upscale", "scale": factor, "renderer": "lanczos"},
                                model_id=self.key)


class FFmpegInterpolateAdapter(VideoModelAdapter):
    """Real motion-compensated frame interpolation (FFmpeg minterpolate).

    Honest: this is classic motion interpolation, not generative in-betweening.
    GREEN on CPU for short clips, YELLOW for long ones (slow).
    """

    key = "ffmpeg_minterpolate"
    name = "FFmpeg Motion Interpolation"
    capability = "video_to_video"
    capabilities = ("video_interpolation", "video_to_video", "slow_motion")
    version = "1.0"
    license = "FFmpeg (LGPL/GPL)"
    description = "Converts low-FPS clips to smooth high-FPS using motion-compensated interpolation. CPU intensive."
    quality = "standard"
    ram_gb = 2.0

    @timed
    def generate(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        self.ensure_ready()
        vids = [r for r in req.references if r.kind == "video"]
        if not vids:
            raise StudioError("Interpolation requires a source video.", code=ErrorCode.INVALID_REQUEST)
        src = vids[0].path
        target_fps = int(req.param("target_fps", 60))
        mode = str(req.param("mi_mode", "mci"))
        out = str(Path(req.output_dir) / "interpolated.mp4")
        progress(0.2, f"interpolating to {target_fps} fps")
        media.run_ffmpeg([
            "-i", src,
            "-vf", f"minterpolate=fps={target_fps}:mi_mode={mode}:me_mode=bidir:mc_mode=aobmc:vsbmc=1",
            "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "copy", out,
        ], timeout=3600)
        progress(1.0, "complete")
        return GenerationResult(files=[GeneratedFile(out, "video", "video/mp4", {"target_fps": target_fps})],
                                meta={"renderer": "ffmpeg_minterpolate", "target_fps": target_fps},
                                model_id=self.key)


# ---------------------------------------------------------------------------
# Gated diffusion video models
# ---------------------------------------------------------------------------
class _DiffusionVideoBase(VideoModelAdapter):
    requires = ("torch", "diffusers", "transformers", "accelerate")
    local = True
    quality = "high"
    repo_id = ""

    @timed
    def generate(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        self.ensure_ready()
        import torch  # noqa: WPS433

        progress(0.05, "loading video pipeline")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        from diffusers import DiffusionPipeline  # type: ignore

        pipe = DiffusionPipeline.from_pretrained(self.repo_id, torch_dtype=torch.float16 if device == "cuda" else torch.float32)
        pipe = pipe.to(device)
        progress(0.3, "generating")

        kwargs = {"prompt": req.prompt, "num_frames": max(2, int(req.duration * req.fps)),
                  "num_inference_steps": int(req.param("steps", 25)),
                  "guidance_scale": req.guidance}
        if req.references:
            from PIL import Image

            init = Image.open(req.references[0].path).convert("RGB").resize((req.width, req.height))
            kwargs["image"] = init
        if req.seed is not None:
            kwargs["generator"] = torch.Generator(device=device).manual_seed(int(req.seed))

        result = pipe(**kwargs)
        frames = result.frames[0] if hasattr(result, "frames") else result.images
        out = str(Path(req.output_dir) / f"{self.key}.mp4")
        progress(0.8, "encoding")
        from app.services.procedural_render import _vignette  # not used; keep imports local

        media.encode_frames([
            (lambda i, im: (im.save(Path(req.output_dir) / f"_vtmp{i:05d}.png"), str(Path(req.output_dir) / f"_vtmp{i:05d}.png"))[1])(i, im)
            for i, im in enumerate(frames)
        ], out, fps=req.fps)
        for p in Path(req.output_dir).glob("_vtmp*.png"):
            p.unlink(missing_ok=True)
        progress(1.0, "complete")
        return GenerationResult(files=[GeneratedFile(out, "video", "video/mp4")],
                                meta={"renderer": "diffusers", "model": self.key}, model_id=self.key)


class StableVideoDiffusionAdapter(_DiffusionVideoBase):
    key = "svd_xt"
    name = "Stable Video Diffusion XT"
    capability = "image_to_video"
    capabilities = ("image_to_video",)
    version = "1.1"
    license = "SVD Community License"
    source_url = "https://huggingface.co/stabilityai/stable-video-diffusion-img2vid-xt"
    description = "25-frame image-to-video. Needs ~16 GB VRAM."
    repo_id = "stabilityai/stable-video-diffusion-img2vid-xt"
    size_gb = 10.2
    vram_gb = 16.0
    ram_gb = 24.0
    weights_path = str(Path("storage/models/svd-xt"))


class AnimateDiffAdapter(_DiffusionVideoBase):
    key = "animatediff"
    name = "AnimateDiff (SD1.5 motion)"
    capability = "text_to_video"
    capabilities = ("text_to_video", "image_to_video")
    version = "v3"
    license = "OpenRAIL"
    source_url = "https://huggingface.co/guoyww/animatediff-motion-adapter-v1-5-3"
    description = "Text-to-video on top of SD1.5. Lightest diffusion video option (~8 GB VRAM)."
    repo_id = "guoyww/animatediff-motion-adapter-v1-5-3"
    size_gb = 5.6
    vram_gb = 8.0
    ram_gb = 16.0
    weights_path = str(Path("storage/models/animatediff"))


class CogVideoXAdapter(_DiffusionVideoBase):
    key = "cogvideox"
    name = "CogVideoX-5B"
    capability = "text_to_video"
    capabilities = ("text_to_video", "image_to_video")
    version = "5b"
    license = "CogVideoX LICENSE"
    source_url = "https://huggingface.co/THUDM/CogVideoX-5b"
    description = "High quality 6s 720p text-to-video. Requires ~24 GB VRAM (or 12 GB with quantisation)."
    repo_id = "THUDM/CogVideoX-5b"
    size_gb = 19.0
    vram_gb = 24.0
    ram_gb = 32.0
    quality = "reference"
    requires_approval = True
    weights_path = str(Path("storage/models/cogvideox-5b"))


class RunwayProviderAdapter(VideoModelAdapter):
    key = "runway_gen3"
    name = "Runway Gen-3 (API)"
    capability = "text_to_video"
    capabilities = ("text_to_video", "image_to_video")
    provider = "runway"
    local = False
    quality = "reference"
    description = "Commercial hosted video model. Requires an API key and per-second billing."

    @timed
    def generate(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        self.ensure_ready()
        raise StudioError(
            "Runway provider adapter declared but no request implementation is bundled.",
            code=ErrorCode.EXTERNAL_PROVIDER_REQUIRED,
            remediation="Provide RUNWAY_API_KEY and implement the provider call in this adapter, or use a local model.",
        )
