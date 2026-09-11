"""HTTP TTS provider (Tier C) — OpenAI-compatible or ElevenLabs-style endpoints."""

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

log = get_logger("engines.voice.provider")


class HttpTtsAdapter(BaseModelAdapter):
    id = "http-tts-provider"
    display_name = "HTTP TTS Provider (OpenAI-compatible / ElevenLabs)"
    version = "1.0.0"
    family = "voice"
    provider = "http"
    license = "per provider terms"
    speed = "fast"
    is_local = False
    ready_out_of_the_box = False
    notes = "Configure TTS_API_BASE_URL + TTS_API_KEY (+ TTS_API_MODEL) to enable real speech."
    capabilities = Capabilities(
        family="voice",
        modes=["text-to-speech", "narration"],
        supports_voice=True,
        supports_character=True,
        notes="Multilingual narration; language support depends on the provider.",
    )

    @property
    def base_url(self) -> str:
        return os.getenv("TTS_API_BASE_URL", "").rstrip("/")

    @property
    def api_key(self) -> str:
        return os.getenv("TTS_API_KEY", "")

    @property
    def model(self) -> str:
        return os.getenv("TTS_API_MODEL", "")

    def status(self) -> dict[str, Any]:
        if not self.base_url or not self.api_key:
            return {"status": "not_installed", "reason": "Set TTS_API_BASE_URL and TTS_API_KEY in backend/.env."}
        return {"status": "installed", "reason": ""}

    def validate(self, request: GenerateRequest) -> ValidationResult:
        result = ValidationResult()
        if not (request.params.get("text") or request.prompt):
            result.add_error("Enter the text you want spoken.")
        st = self.status()
        if st["status"] != "installed":
            result.add_error(st["reason"])
        return result

    def estimate(self, request: GenerateRequest) -> Estimate:
        text = str(request.params.get("text") or request.prompt or "")
        return Estimate(seconds=round(min(40, 1.5 + len(text.split()) * 0.05), 2), credits=round(len(text) * 0.0004, 2))

    def generate(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        if not self.base_url or not self.api_key:
            raise ProviderNotConfiguredError(self.display_name, "TTS_API_BASE_URL / TTS_API_KEY")
        text = str(request.params.get("text") or request.prompt or "")
        voice_id = str(request.params.get("provider_voice_id") or (request.voice or {}).get("provider_voice_id")
                       or (request.voice or {}).get("name") or "alloy")
        payload: dict[str, Any] = {
            "model": self.model or "tts-1",
            "input": text,
            "voice": voice_id,
            "speed": float(request.params.get("speed", 1.0) or 1.0),
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        ctx.progress(30, "calling TTS provider")
        with httpx.Client(timeout=300) as client:
            resp = client.post(f"{self.base_url}/audio/speech", json=payload, headers=headers)
        if resp.status_code >= 400:
            log.error("tts_provider_error", status=resp.status_code, body=resp.text[:600])
            raise StudioError("Speech generation failed.", detail=resp.text[:600],
                              suggested_action="Retry / Check provider credentials / Change model")
        ext = "mp3"
        ctype = resp.headers.get("content-type", "")
        if "wav" in ctype:
            ext = "wav"
        out = ctx.path(f"voice_{ctx.job_id}.{ext}")
        Path(out).write_bytes(resp.content)
        info = ffmpeg.probe(out)
        return [Artifact(path=out, kind="audio", mime=resp.headers.get("content-type", "audio/mpeg"),
                         name=f"voice_{ctx.job_id}",
                         meta={"engine": self.id, "engine_name": self.display_name, "mode": request.mode,
                               "intelligible": True, "provider_voice": voice_id,
                               "duration": info.get("duration"), "text": text, "params": request.params})]


ADAPTERS = [HttpTtsAdapter()]
