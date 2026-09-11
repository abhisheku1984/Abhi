"""Provider-agnostic HTTP image adapter (Tier C).

Works with any OpenAI-compatible image endpoint or a configured third-party
provider. Credentials come from env only — never from the browser (§34).
"""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any

import httpx

from app.core.errors import ProviderNotConfiguredError, StudioError
from app.core.logging import get_logger
from app.engines.base import (
    Artifact, BaseModelAdapter, Capabilities, Estimate, GenerateRequest, JobContext, ValidationResult,
)
from app.media import image_ops

log = get_logger("engines.image.provider")


class HttpImageAdapter(BaseModelAdapter):
    id = "http-image-provider"
    display_name = "HTTP Image Provider (OpenAI-compatible)"
    version = "1.0.0"
    family = "image"
    provider = "http"
    license = "per provider terms"
    size_mb = 0
    vram_mb = 0
    speed = "medium"
    is_local = False
    ready_out_of_the_box = False
    notes = "Configure IMAGE_API_BASE_URL + IMAGE_API_KEY (+ IMAGE_API_MODEL) to enable."
    capabilities = Capabilities(
        family="image",
        modes=["text-to-image", "image-to-image", "image-edit"],
        max_resolution="2048p",
        supports_negative_prompt=True,
        supports_seed=True,
        supports_reference_images=True,
        supports_style=True,
        max_references=4,
    )

    @property
    def base_url(self) -> str:
        return os.getenv("IMAGE_API_BASE_URL", "").rstrip("/")

    @property
    def api_key(self) -> str:
        return os.getenv("IMAGE_API_KEY", "")

    @property
    def model(self) -> str:
        return os.getenv("IMAGE_API_MODEL", "")

    def status(self) -> dict[str, Any]:
        if not self.base_url or not self.api_key:
            return {
                "status": "not_installed",
                "reason": "Set IMAGE_API_BASE_URL and IMAGE_API_KEY in backend/.env to enable this provider.",
            }
        return {"status": "installed", "reason": ""}

    def validate(self, request: GenerateRequest) -> ValidationResult:
        result = ValidationResult()
        self._validate_prompt(request, result)
        st = self.status()
        if st["status"] != "installed":
            result.add_error(st["reason"])
        return result

    def estimate(self, request: GenerateRequest) -> Estimate:
        return Estimate(seconds=12.0, vram_mb=0, credits=2.0, notes="External provider call")

    def generate(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        if not self.base_url or not self.api_key:
            raise ProviderNotConfiguredError(self.display_name, "IMAGE_API_BASE_URL / IMAGE_API_KEY")
        w, h = image_ops.resolve_size(request.params.get("aspect", "1:1"), request.params.get("resolution", "1024"))
        payload: dict[str, Any] = {
            "model": self.model or "image-generation",
            "prompt": request.prompt,
            "n": max(1, min(8, int(request.params.get("count", 1)))),
            "size": f"{w}x{h}",
        }
        if request.negative_prompt:
            payload["negative_prompt"] = request.negative_prompt

        ctx.progress(15, "calling provider")
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=300) as client:
            resp = client.post(f"{self.base_url}/images/generations", json=payload, headers=headers)
        if resp.status_code >= 400:
            log.error("provider_error", status=resp.status_code, body=resp.text[:800])
            raise StudioError(
                "Generation failed.",
                detail=f"provider responded {resp.status_code}: {resp.text[:800]}",
                suggested_action="Retry / Check provider credentials / Change model",
            )
        data = resp.json()
        items = data.get("data") or []
        artifacts: list[Artifact] = []
        for i, item in enumerate(items):
            name = f"image_{ctx.job_id}_{i}.png" if len(items) > 1 else f"image_{ctx.job_id}.png"
            out = ctx.path(name)
            if item.get("b64_json"):
                Path(out).write_bytes(base64.b64decode(item["b64_json"]))
            elif item.get("url"):
                with httpx.Client(timeout=300, follow_redirects=True) as client:
                    r = client.get(item["url"])
                    r.raise_for_status()
                    Path(out).write_bytes(r.content)
            else:
                continue
            info = image_ops.image_info(out)
            artifacts.append(Artifact(
                path=out, kind="image", mime="image/png", name=Path(name).stem,
                meta={
                    "engine": self.id, "engine_name": self.display_name, "mode": request.mode,
                    "deterministic": False, "diffusion": None, "provider_model": payload["model"],
                    "width": info.get("width"), "height": info.get("height"),
                    "prompt": request.prompt, "params": request.params,
                },
            ))
            ctx.progress(20 + (75 * (i + 1) / max(len(items), 1)), f"image {i + 1}/{len(items)}")
        if not artifacts:
            raise StudioError("The provider returned no images.",
                              suggested_action="Retry, or check the provider's response format.")
        return artifacts


ADAPTERS = [HttpImageAdapter()]
