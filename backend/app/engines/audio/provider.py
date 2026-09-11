"""HTTP audio provider (Tier C) — MusicGen / Stable Audio / ElevenLabs-style."""

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

log = get_logger("engines.audio.provider")


class HttpAudioAdapter(BaseModelAdapter):
    id = "http-audio-provider"
    display_name = "HTTP Audio / Music Provider"
    version = "1.0.0"
    family = "audio"
    provider = "http"
    license = "per provider terms"
    speed = "medium"
    is_local = False
    ready_out_of_the_box = False
    notes = "Configure AUDIO_API_BASE_URL + AUDIO_API_KEY to enable neural music/SFX generation."
    capabilities = Capabilities(
        family="audio",
        modes=["background-music", "sound-effects", "ambient", "voice-narration"],
        supports_duration=True,
        supports_voice=True,
        max_duration_sec=300,
    )

    @property
    def base_url(self) -> str:
        return os.getenv("AUDIO_API_BASE_URL", "").rstrip("/")

    @property
    def api_key(self) -> str:
        return os.getenv("AUDIO_API_KEY", "")

    def status(self) -> dict[str, Any]:
        if not self.base_url or not self.api_key:
            return {"status": "not_installed", "reason": "Set AUDIO_API_BASE_URL and AUDIO_API_KEY in backend/.env."}
        return {"status": "installed", "reason": ""}

    def validate(self, request: GenerateRequest) -> ValidationResult:
        result = ValidationResult()
        self._validate_prompt(request, result)
        st = self.status()
        if st["status"] != "installed":
            result.add_error(st["reason"])
        return result

    def estimate(self, request: GenerateRequest) -> Estimate:
        return Estimate(seconds=45.0, credits=4.0)

    def generate(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        if not self.base_url or not self.api_key:
            raise ProviderNotConfiguredError(self.display_name, "AUDIO_API_BASE_URL / AUDIO_API_KEY")
        payload = {
            "prompt": request.prompt,
            "duration": float(request.params.get("duration", 20)),
            "mode": request.mode,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        ctx.progress(30, "calling audio provider")
        with httpx.Client(timeout=600) as client:
            resp = client.post(f"{self.base_url}/audio/generations", json=payload, headers=headers)
        if resp.status_code >= 400:
            log.error("audio_provider_error", status=resp.status_code, body=resp.text[:500])
            raise StudioError("Audio generation failed.", detail=resp.text[:500],
                              suggested_action="Retry / Check provider credentials / Change model")
        data = resp.json()
        url = data.get("url") or (data.get("data") or [{}])[0].get("url")
        if not url:
            raise StudioError("The provider returned no audio.", suggested_action="Retry / Change model")
        out = ctx.path(f"audio_{ctx.job_id}.mp3")
        with httpx.Client(timeout=300, follow_redirects=True) as client:
            r = client.get(url)
            r.raise_for_status()
            Path(out).write_bytes(r.content)
        info = ffmpeg.probe(out)
        return [Artifact(path=out, kind="audio", mime="audio/mpeg", name=f"audio_{ctx.job_id}",
                         meta={"engine": self.id, "mode": request.mode, "deterministic": False,
                               "duration": info.get("duration"), "prompt": request.prompt, "params": request.params})]


ADAPTERS = [HttpAudioAdapter()]
