"""HTTP avatar provider (Tier C) for neural avatar services."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from app.core.errors import ProviderNotConfiguredError, StudioError
from app.core.logging import get_logger
from app.engines.base import (
    Artifact, BaseModelAdapter, Capabilities, Estimate, GenerateRequest, JobContext, ValidationResult,
)
from app.media import ffmpeg

log = get_logger("engines.avatar.provider")


class HttpAvatarAdapter(BaseModelAdapter):
    id = "http-avatar-provider"
    display_name = "HTTP Avatar / Talking-Head Provider"
    version = "1.0.0"
    family = "avatar"
    provider = "http"
    license = "per provider terms"
    speed = "slow"
    is_local = False
    ready_out_of_the_box = False
    notes = "Configure AVATAR_API_BASE_URL + AVATAR_API_KEY to enable neural avatar video."
    capabilities = Capabilities(
        family="avatar",
        modes=["talking-avatar", "avatar-video", "avatar-from-script", "avatar-lip-sync"],
        supports_duration=True,
        supports_voice=True,
        supports_lipsync=True,
        max_duration_sec=300,
    )

    @property
    def base_url(self) -> str:
        return os.getenv("AVATAR_API_BASE_URL", "").rstrip("/")

    @property
    def api_key(self) -> str:
        return os.getenv("AVATAR_API_KEY", "")

    def status(self) -> dict[str, Any]:
        if not self.base_url or not self.api_key:
            return {"status": "not_installed", "reason": "Set AVATAR_API_BASE_URL and AVATAR_API_KEY in backend/.env."}
        return {"status": "installed", "reason": ""}

    def validate(self, request: GenerateRequest) -> ValidationResult:
        result = ValidationResult()
        self._validate_prompt(request, result)
        st = self.status()
        if st["status"] != "installed":
            result.add_error(st["reason"])
        return result

    def estimate(self, request: GenerateRequest) -> Estimate:
        return Estimate(seconds=120.0, credits=18.0)

    def generate(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        if not self.base_url or not self.api_key:
            raise ProviderNotConfiguredError(self.display_name, "AVATAR_API_BASE_URL / AVATAR_API_KEY")
        payload = {
            "script": request.params.get("script") or request.prompt,
            "avatar_id": request.params.get("avatar_id") or request.params.get("avatar_type"),
            "voice": (request.voice or {}).get("provider_voice_id") or (request.voice or {}).get("name"),
            "language": request.params.get("language", "en"),
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        ctx.progress(25, "calling avatar provider")
        with httpx.Client(timeout=600) as client:
            resp = client.post(f"{self.base_url}/avatars/generations", json=payload, headers=headers)
        if resp.status_code >= 400:
            log.error("avatar_provider_error", status=resp.status_code, body=resp.text[:500])
            raise StudioError("Avatar generation failed.", detail=resp.text[:500],
                              suggested_action="Retry / Check provider credentials / Change model")
        data = resp.json()
        url = data.get("url") or data.get("video_url")
        if not url:
            raise StudioError("The provider returned no avatar video.", suggested_action="Retry / Change model")
        out = ctx.path(f"avatar_video_{ctx.job_id}.mp4")
        with httpx.Client(timeout=600, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            Path(out).write_bytes(r.content)
        info = ffmpeg.probe(out)
        return [Artifact(path=out, kind="video", mime="video/mp4", name=f"avatar_video_{ctx.job_id}",
                         meta={"engine": self.id, "mode": request.mode, "deterministic": False,
                               "avatar": True, "duration": info.get("duration"), "params": request.params})]


ADAPTERS = [HttpAvatarAdapter()]
