"""Provider-agnostic HTTP video adapter (Tier C).

Supports both synchronous responses and the common async pattern
(POST → poll GET /{job_id} → download URL). Credentials stay server-side.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from app.core.errors import ProviderNotConfiguredError, StudioError
from app.core.logging import get_logger
from app.engines.base import (
    Artifact, BaseModelAdapter, Capabilities, Estimate, GenerateRequest, JobContext, ValidationResult,
)
from app.media import ffmpeg

log = get_logger("engines.video.provider")


class HttpVideoAdapter(BaseModelAdapter):
    id = "http-video-provider"
    display_name = "HTTP Video Provider (Replicate / fal / OpenAI-compatible)"
    version = "1.0.0"
    family = "video"
    provider = "http"
    license = "per provider terms"
    size_mb = 0
    vram_mb = 0
    speed = "slow"
    is_local = False
    ready_out_of_the_box = False
    notes = "Configure VIDEO_API_BASE_URL + VIDEO_API_KEY (+ VIDEO_API_MODEL)."
    capabilities = Capabilities(
        family="video",
        modes=["text-to-video", "image-to-video", "first-frame-to-video"],
        max_resolution="1080p",
        supports_negative_prompt=True,
        supports_seed=True,
        supports_reference_images=True,
        supports_duration=True,
        supports_camera=True,
        max_duration_sec=60,
        max_references=1,
    )

    @property
    def base_url(self) -> str:
        return os.getenv("VIDEO_API_BASE_URL", "").rstrip("/")

    @property
    def api_key(self) -> str:
        return os.getenv("VIDEO_API_KEY", "")

    @property
    def model(self) -> str:
        return os.getenv("VIDEO_API_MODEL", "")

    def status(self) -> dict[str, Any]:
        if not self.base_url or not self.api_key:
            return {"status": "not_installed",
                    "reason": "Set VIDEO_API_BASE_URL and VIDEO_API_KEY in backend/.env."}
        return {"status": "installed", "reason": ""}

    def validate(self, request: GenerateRequest) -> ValidationResult:
        result = ValidationResult()
        self._validate_prompt(request, result)
        st = self.status()
        if st["status"] != "installed":
            result.add_error(st["reason"])
        return result

    def estimate(self, request: GenerateRequest) -> Estimate:
        return Estimate(seconds=90.0, vram_mb=0, credits=12.0, notes="External provider")

    def generate(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        if not self.base_url or not self.api_key:
            raise ProviderNotConfiguredError(self.display_name, "VIDEO_API_BASE_URL / VIDEO_API_KEY")

        payload: dict[str, Any] = {
            "model": self.model or "video-generation",
            "prompt": request.prompt,
            "duration": float(request.params.get("duration", 5)),
            "aspect_ratio": request.params.get("aspect", "16:9"),
        }
        if request.negative_prompt:
            payload["negative_prompt"] = request.negative_prompt
        if request.references:
            payload["image"] = Path(request.references[0].path).name
            payload["image_url"] = request.params.get("image_url")

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=120) as client:
            resp = client.post(f"{self.base_url}/videos/generations", json=payload, headers=headers)
        if resp.status_code >= 400:
            log.error("video_provider_error", status=resp.status_code, body=resp.text[:600])
            raise StudioError("Generation failed.",
                              detail=f"provider {resp.status_code}: {resp.text[:600]}",
                              suggested_action="Retry / Check provider credentials / Change model")
        data = resp.json()

        url: Optional[str] = data.get("url") or (data.get("data") or [{}])[0].get("url") if isinstance(data, dict) else None
        job_id = data.get("id") or data.get("job_id")

        if not url and job_id:
            url = self._poll(job_id, headers, ctx)
        if not url:
            raise StudioError("The provider did not return a video.",
                              suggested_action="Retry, or check the provider's response format.")
        out = ctx.path(f"video_{ctx.job_id}.mp4")
        with httpx.Client(timeout=600, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            Path(out).write_bytes(r.content)
        info = ffmpeg.probe(out)
        return [Artifact(path=out, kind="video", mime="video/mp4", name=f"video_{ctx.job_id}",
                         meta={"engine": self.id, "engine_name": self.display_name, "mode": request.mode,
                               "deterministic": False, "diffusion": None, "provider_model": payload["model"],
                               "duration": info.get("duration"), "width": info.get("width"),
                               "height": info.get("height"), "prompt": request.prompt, "params": request.params})]

    def _poll(self, job_id: str, headers: dict[str, str], ctx: JobContext, *, timeout: int = 900) -> Optional[str]:
        deadline = time.time() + timeout
        with httpx.Client(timeout=60) as client:
            while time.time() < deadline:
                ctx.check_cancelled()
                r = client.get(f"{self.base_url}/videos/{job_id}", headers=headers)
                if r.status_code >= 400:
                    raise StudioError("Could not query the provider job.",
                                      detail=r.text[:400], suggested_action="Retry / Change model")
                data = r.json()
                status = str(data.get("status", "")).lower()
                if status in ("succeeded", "completed", "ready"):
                    return data.get("url") or data.get("output", {}).get("url")
                if status in ("failed", "error", "cancelled"):
                    raise StudioError("Generation failed.",
                                      detail=str(data.get("error") or "provider reported failure"),
                                      suggested_action="Retry / Change model")
                ctx.progress(40 + min(40, (timeout - (deadline - time.time())) / timeout * 40), f"provider: {status}")
                time.sleep(4)
        raise StudioError("The provider took too long to finish.",
                          suggested_action="Retry, or pick a faster model.")


ADAPTERS = [HttpVideoAdapter()]
