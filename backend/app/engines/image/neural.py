"""Diffusion-backed image adapter (Tier B).

Real `diffusers` pipelines when the weights are installed; otherwise it reports
"Model not installed" — never a fake result (§42.3).
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any, Optional

from app.core.config import settings
from app.core.errors import ModelNotInstalledError, StudioError
from app.core.logging import get_logger
from app.engines.base import (
    Artifact, BaseModelAdapter, Capabilities, Estimate, GenerateRequest, JobContext, ValidationResult,
)
from app.engines.image.render import analyze_prompt
from app.media import image_ops

log = get_logger("engines.image.neural")


def _module(name: str):
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def _torch_available() -> bool:
    return _module("torch") is not None and _module("diffusers") is not None


class DiffusersImageAdapter(BaseModelAdapter):
    """Stable Diffusion / SDXL / Flux through HuggingFace diffusers."""

    id = "diffusers-image"
    display_name = "Diffusion Image Model (SD / SDXL / Flux)"
    version = "1.0.0"
    family = "image"
    provider = "local"
    license = "depends on selected checkpoint"
    size_mb = 4200
    vram_mb = 8192
    speed = "slow"
    is_local = True
    ready_out_of_the_box = False
    requires_extra = "requirements-gpu.txt"
    download_url = "https://huggingface.co/models?pipeline_tag=text-to-image"
    notes = "Needs torch + diffusers and a downloaded checkpoint (never downloaded automatically)."
    capabilities = Capabilities(
        family="image",
        modes=["text-to-image", "image-to-image", "inpaint", "upscale", "reference-to-image"],
        max_resolution="2048p",
        supports_negative_prompt=True,
        supports_seed=True,
        supports_reference_images=True,
        supports_mask=True,
        supports_style=True,
        max_references=4,
    )

    def __init__(self) -> None:
        self._pipeline = None
        self._loaded_model_id: Optional[str] = None

    # -- status --------------------------------------------------------- #

    def status(self) -> dict[str, Any]:
        if not _torch_available():
            return {
                "status": "not_installed",
                "reason": "Install GPU extras: pip install -r backend/requirements-gpu.txt",
            }
        if not self.model_path:
            return {
                "status": "not_installed",
                "reason": "No checkpoint configured. Set DIFFUSERS_IMAGE_MODEL to a local path or HF repo id.",
            }
        if self.model_path and not self.model_path.startswith(("http", "s3")) and not Path(self.model_path).exists():
            return {"status": "not_installed", "reason": f"Checkpoint not found at {self.model_path}"}
        return {"status": "installed", "reason": ""}

    @property
    def model_path(self) -> str:
        return os.getenv("DIFFUSERS_IMAGE_MODEL", "")

    def param_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "steps": {"type": "integer", "minimum": 1, "maximum": 150, "default": 30},
                "guidance": {"type": "number", "minimum": 0, "maximum": 25, "default": 7.5},
                "strength": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.7},
                "aspect": {"type": "string", "enum": list(image_ops.ASPECTS.keys()), "default": "1:1"},
                "resolution": {"type": "string", "enum": ["512", "768", "1024", "1536"], "default": "1024"},
                "count": {"type": "integer", "minimum": 1, "maximum": 8, "default": 1},
                "scheduler": {"type": "string", "default": "default"},
            },
        }

    def validate(self, request: GenerateRequest) -> ValidationResult:
        result = ValidationResult()
        self._validate_prompt(request, result)
        st = self.status()
        if st["status"] != "installed":
            result.add_error(st["reason"] or "Model not installed.")
        return result

    def estimate(self, request: GenerateRequest) -> Estimate:
        w, h = image_ops.resolve_size(request.params.get("aspect", "1:1"), request.params.get("resolution", "1024"))
        steps = int(request.params.get("steps", 30))
        n = max(1, int(request.params.get("count", 1)))
        per_image = 1.2 + steps * (0.16 if (w * h) <= 1024 * 1024 else 0.34)
        return Estimate(seconds=round(per_image * n, 2), vram_mb=self.vram_mb, credits=round(2.0 * n, 2))

    # -- pipeline ------------------------------------------------------- #

    def _load(self) -> Any:
        import torch  # type: ignore
        from diffusers import AutoPipelineForImage2Image, AutoPipelineForText2Image  # type: ignore

        if self._pipeline is not None and self._loaded_model_id == self.model_path:
            return self._pipeline
        dtype = torch.float16 if torch.cuda.is_available() else torch.float32
        try:
            pipe = AutoPipelineForText2Image.from_pretrained(self.model_path, torch_dtype=dtype,
                                                             safety_checker=None)
        except Exception:
            pipe = AutoPipelineForImage2Image.from_pretrained(self.model_path, torch_dtype=dtype,
                                                              safety_checker=None)
        if torch.cuda.is_available():
            pipe = pipe.to("cuda")
            try:
                pipe.enable_attention_slicing()
            except Exception:
                pass
        self._pipeline = pipe
        self._loaded_model_id = self.model_path
        return pipe

    def generate(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        self.ensure_ready()
        import torch  # type: ignore

        self._require_mode(request)
        pipe = self._load()
        w, h = image_ops.resolve_size(request.params.get("aspect", "1:1"), request.params.get("resolution", "1024"))
        seed = request.seed
        steps = int(request.params.get("steps", 30))
        guidance = float(request.params.get("guidance", 7.5))
        count = max(1, min(8, int(request.params.get("count", 1))))
        generator = torch.Generator(device="cuda" if torch.cuda.is_available() else "cpu").manual_seed(seed)

        kwargs: dict[str, Any] = dict(
            prompt=request.prompt, negative_prompt=request.negative_prompt or None,
            width=w, height=h, num_inference_steps=steps, guidance_scale=guidance,
            num_images_per_prompt=count, generator=generator,
        )
        if request.mode == "image-to-image" and request.references:
            init = image_ops.load(request.references[0].path).convert("RGB").resize((w, h))
            kwargs["image"] = init
            kwargs["strength"] = float(request.params.get("strength", 0.7))
        if request.mode == "inpaint" and len(request.references) >= 2:
            kwargs["image"] = image_ops.load(request.references[0].path).convert("RGB").resize((w, h))
            kwargs["mask_image"] = image_ops.load(request.references[1].path).convert("L").resize((w, h))

        ctx.progress(20, "diffusion running")
        result = pipe(**kwargs)
        artifacts: list[Artifact] = []
        for i, img in enumerate(getattr(result, "images", [])):
            name = f"image_{ctx.job_id}_{i}.png" if count > 1 else f"image_{ctx.job_id}.png"
            path = image_ops.save(img, ctx.path(name))
            artifacts.append(Artifact(
                path=path, kind="image", mime="image/png", name=Path(name).stem,
                meta={
                    "engine": self.id, "engine_name": self.display_name, "mode": request.mode,
                    "deterministic": True, "diffusion": True, "seed": seed, "steps": steps,
                    "guidance": guidance, "width": img.width, "height": img.height,
                    "prompt": request.prompt, "negative_prompt": request.negative_prompt,
                    "params": request.params, "model": self.model_path,
                },
            ))
            ctx.progress(20 + (75 * (i + 1) / max(count, 1)), f"image {i + 1}/{count}")
        return artifacts

    def install(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "instructions": [
                "pip install -r backend/requirements-gpu.txt",
                "Set DIFFUSERS_IMAGE_MODEL=<local checkpoint path or HF repo id> in backend/.env",
                "Restart the platform, then run Model Manager → Test.",
            ],
            "auto_download": False,
            "note": "Checkpoints are never downloaded without your explicit approval (§32).",
        }


ADAPTERS = [DiffusersImageAdapter()]
