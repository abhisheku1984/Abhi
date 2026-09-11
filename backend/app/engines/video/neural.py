"""Neural video adapter (Tier B) — AnimateDiff / SVD / CogVideoX / Wan class.

Fully implemented for real inference once a checkpoint is configured; until
then it reports "Model not installed" (§42.3).
"""

from __future__ import annotations

import importlib
import os
import shutil
from pathlib import Path
from typing import Any

from app.core.errors import StudioError
from app.core.logging import get_logger
from app.engines.base import (
    Artifact, BaseModelAdapter, Capabilities, Estimate, GenerateRequest, JobContext, ValidationResult,
)
from app.media import ffmpeg, image_ops

log = get_logger("engines.video.neural")


def _available() -> bool:
    try:
        importlib.import_module("torch")
        importlib.import_module("diffusers")
        return True
    except Exception:
        return False


class DiffusersVideoAdapter(BaseModelAdapter):
    id = "diffusers-video"
    display_name = "Diffusion Video Model (AnimateDiff / SVD / CogVideoX)"
    version = "1.0.0"
    family = "video"
    provider = "local"
    license = "depends on selected checkpoint"
    size_mb = 9000
    vram_mb = 16384
    speed = "slow"
    is_local = True
    ready_out_of_the_box = False
    requires_extra = "requirements-gpu.txt"
    download_url = "https://huggingface.co/models?pipeline_tag=text-to-video"
    notes = "Set DIFFUSERS_VIDEO_MODEL to a local path or repo id. Weights are never auto-downloaded."
    capabilities = Capabilities(
        family="video",
        modes=["text-to-video", "image-to-video", "first-frame-to-video", "video-to-video"],
        max_resolution="1024p",
        supports_negative_prompt=True,
        supports_seed=True,
        supports_reference_images=True,
        supports_duration=True,
        supports_camera=True,
        supports_motion=True,
        max_duration_sec=16,
        max_references=2,
    )

    def __init__(self) -> None:
        self._pipe = None
        self._loaded: str = ""

    @property
    def model_path(self) -> str:
        return os.getenv("DIFFUSERS_VIDEO_MODEL", "")

    def status(self) -> dict[str, Any]:
        if not _available():
            return {"status": "not_installed", "reason": "pip install -r backend/requirements-gpu.txt"}
        if not self.model_path:
            return {"status": "not_installed", "reason": "Set DIFFUSERS_VIDEO_MODEL to a checkpoint path or repo id."}
        if not self.model_path.startswith(("http", "s3")) and not Path(self.model_path).exists():
            return {"status": "not_installed", "reason": f"Checkpoint not found at {self.model_path}"}
        return {"status": "installed", "reason": ""}

    def param_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "duration": {"type": "number", "minimum": 1, "maximum": 16, "default": 4},
                "fps": {"type": "integer", "enum": [8, 12, 16, 24], "default": 12},
                "steps": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
                "guidance": {"type": "number", "minimum": 0, "maximum": 25, "default": 7.5},
                "aspect": {"type": "string", "enum": list(image_ops.ASPECTS.keys()), "default": "16:9"},
                "resolution": {"type": "string", "enum": ["512", "768", "1024"], "default": "512"},
            },
        }

    def validate(self, request: GenerateRequest) -> ValidationResult:
        result = ValidationResult()
        self._validate_prompt(request, result)
        st = self.status()
        if st["status"] != "installed":
            result.add_error(st["reason"])
        return result

    def estimate(self, request: GenerateRequest) -> Estimate:
        frames = int(float(request.params.get("duration", 4)) * int(request.params.get("fps", 12)))
        steps = int(request.params.get("steps", 25))
        return Estimate(seconds=round(3.0 + frames * steps * 0.09, 2), vram_mb=self.vram_mb,
                        credits=round(frames * 0.25, 2))

    def generate(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        self.ensure_ready()
        import torch  # type: ignore
        from diffusers import DiffusionPipeline  # type: ignore

        w, h = image_ops.resolve_size(request.params.get("aspect", "16:9"), request.params.get("resolution", "512"))
        fps = int(request.params.get("fps", 12))
        duration = float(request.params.get("duration", 4))
        num_frames = max(8, int(round(duration * fps)))
        steps = int(request.params.get("steps", 25))
        guidance = float(request.params.get("guidance", 7.5))
        seed = request.seed

        if self._pipe is None or self._loaded != self.model_path:
            ctx.progress(10, "loading model")
            dtype = torch.float16 if torch.cuda.is_available() else torch.float32
            self._pipe = DiffusionPipeline.from_pretrained(self.model_path, torch_dtype=dtype)
            if torch.cuda.is_available():
                self._pipe = self._pipe.to("cuda")
            self._loaded = self.model_path

        gen = torch.Generator(device="cuda" if torch.cuda.is_available() else "cpu").manual_seed(seed)
        kwargs: dict[str, Any] = dict(
            prompt=request.prompt, negative_prompt=request.negative_prompt or None,
            num_frames=num_frames, num_inference_steps=steps, guidance_scale=guidance,
            width=w, height=h, generator=gen,
        )
        if request.mode in ("image-to-video", "first-frame-to-video") and request.references:
            kwargs["image"] = image_ops.load(request.references[0].path).convert("RGB").resize((w, h))

        ctx.progress(25, "diffusing video")
        output = self._pipe(**kwargs)
        frames_seq = getattr(output, "frames", None)
        if not frames_seq:
            raise StudioError("The video model returned no frames.",
                              suggested_action="Retry / Check GPU memory / Change model")
        frames = frames_seq[0] if isinstance(frames_seq[0], list) else list(frames_seq)

        frames_dir = Path(ctx.path("frames"))
        frames_dir.mkdir(parents=True, exist_ok=True)
        for i, frame in enumerate(frames):
            frame.save(frames_dir / f"frame_{i + 1:05d}.png")
            if i % 4 == 0:
                ctx.progress(30 + 50 * (i / len(frames)), f"frame {i + 1}/{len(frames)}")

        out = ctx.path(f"video_{ctx.job_id}.mp4")
        ctx.progress(88, "encoding")
        ffmpeg.frames_to_video(frames_dir, out, fps=fps, pattern="frame_%05d.png")
        shutil.rmtree(frames_dir, ignore_errors=True)
        info = ffmpeg.probe(out)
        return [Artifact(path=out, kind="video", mime="video/mp4", name=f"video_{ctx.job_id}",
                         meta={"engine": self.id, "engine_name": self.display_name, "mode": request.mode,
                               "diffusion": True, "deterministic": True, "seed": seed, "frames": len(frames),
                               "duration": info.get("duration"), "width": info.get("width"),
                               "height": info.get("height"), "prompt": request.prompt, "params": request.params})]

    def install(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "instructions": [
                "pip install -r backend/requirements-gpu.txt",
                "Set DIFFUSERS_VIDEO_MODEL=<local checkpoint path or HF repo id>",
                "Restart the platform, then run Model Manager → Test.",
            ],
            "auto_download": False,
        }


ADAPTERS = [DiffusersVideoAdapter()]
