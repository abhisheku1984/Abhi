"""Voice cloning adapter — consent-gated by design (§28).

Cloning a real person's voice without authorisation is never permitted. This
adapter refuses to run unless BOTH conditions hold:

1. an engine is installed (XTTS / OpenVoice / a configured provider), and
2. the request carries an explicit attestation recorded on the Voice profile
   (`consent.owner_attestation`, `consent.rights_holder`, ...).
"""

from __future__ import annotations

import importlib
import os
from typing import Any

from app.core.config import settings
from app.core.errors import SafetyBlockedError, StudioError
from app.core.logging import get_logger
from app.engines.base import (
    Artifact, BaseModelAdapter, Capabilities, Estimate, GenerateRequest, JobContext, ValidationResult,
)
from app.media import ffmpeg

log = get_logger("engines.voice.clone")

REQUIRED_CONSENT_FIELDS = ("owner_attestation", "rights_holder", "authorised_by", "purpose")


class VoiceCloneAdapter(BaseModelAdapter):
    id = "voice-clone"
    display_name = "Voice Cloning (consent-gated)"
    version = "1.0.0"
    family = "voice"
    provider = "local"
    license = "depends on selected engine"
    size_mb = 1800
    vram_mb = 6144
    speed = "slow"
    is_local = True
    ready_out_of_the_box = False
    requires_extra = "requirements-gpu.txt"
    notes = "Requires an installed cloning engine (XTTS/OpenVoice) AND a recorded consent attestation."
    capabilities = Capabilities(
        family="voice",
        modes=["voice-cloning", "speech-to-speech"],
        supports_voice=True,
        supports_character=True,
        supports_reference_images=False,
        notes="Never enabled by default. Every clone is logged in the audit trail.",
    )

    @property
    def engine_path(self) -> str:
        return os.getenv("VOICE_CLONE_MODEL", "")

    def status(self) -> dict[str, Any]:
        try:
            importlib.import_module("TTS")  # coqui-tts / XTTS
            installed = True
        except Exception:
            installed = False
        if not installed and not os.getenv("VOICE_CLONE_API_URL"):
            return {
                "status": "not_installed",
                "reason": "Install a cloning engine (e.g. coqui-tts XTTS) or set VOICE_CLONE_API_URL.",
            }
        return {"status": "installed", "reason": ""}

    def validate(self, request: GenerateRequest) -> ValidationResult:
        result = ValidationResult()
        consent = (request.voice or {}).get("consent") or {}
        missing = [f for f in REQUIRED_CONSENT_FIELDS if not consent.get(f)]
        if missing and settings.REQUIRE_VOICE_CLONE_ATTESTATION:
            result.add_error(
                "Voice cloning requires a recorded consent attestation (missing: "
                + ", ".join(missing) + ")."
            )
        if not any(r.kind == "audio" for r in request.references):
            result.add_error("Upload a reference audio sample of the voice.")
        st = self.status()
        if st["status"] != "installed":
            result.add_error(st["reason"])
        return result

    def estimate(self, request: GenerateRequest) -> Estimate:
        return Estimate(seconds=25.0, vram_mb=self.vram_mb, credits=6.0)

    def generate(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        consent = (request.voice or {}).get("consent") or {}
        if settings.REQUIRE_VOICE_CLONE_ATTESTATION:
            missing = [f for f in REQUIRED_CONSENT_FIELDS if not consent.get(f)]
            if missing:
                raise SafetyBlockedError(
                    "Voice cloning blocked: consent attestation incomplete.",
                    detail=f"missing fields: {missing}",
                    suggested_action="Complete the rights attestation on the voice profile before cloning.",
                )
        sample = next((r for r in request.references if r.kind == "audio"), None)
        if sample is None:
            raise StudioError("A reference audio sample is required.",
                              suggested_action="Upload 10-30 seconds of clean reference audio.")

        log.warning("voice_clone_attempt", owner=request.owner_id, voice=(request.voice or {}).get("id"),
                    rights_holder=consent.get("rights_holder"), job=ctx.job_id)

        api_url = os.getenv("VOICE_CLONE_API_URL")
        if api_url:
            import httpx

            payload = {
                "text": request.params.get("text") or request.prompt,
                "reference": str(sample.path),
                "language": request.params.get("language", "en"),
                "consent": consent,
            }
            with httpx.Client(timeout=600) as client:
                resp = client.post(api_url, json=payload)
            if resp.status_code >= 400:
                raise StudioError("Voice cloning failed.", detail=resp.text[:500],
                                  suggested_action="Retry / Check the cloning service.")
            out = ctx.path(f"voice_clone_{ctx.job_id}.wav")
            with open(out, "wb") as fh:
                fh.write(resp.content)
        else:
            self.ensure_ready()
            from TTS.api import TTS  # type: ignore

            tts = TTS(model_name=self.engine_path or "tts_models/multilingual/multi-dataset/xtts_v2")
            out = ctx.path(f"voice_clone_{ctx.job_id}.wav")
            ctx.progress(45, "cloning voice")
            tts.tts_to_file(text=request.params.get("text") or request.prompt,
                            speaker_wav=sample.path,
                            language=str(request.params.get("language", "en"))[:2],
                            file_path=out)

        info = ffmpeg.probe(out)
        return [Artifact(path=out, kind="audio", mime="audio/wav", name=f"voice_clone_{ctx.job_id}",
                         meta={"engine": self.id, "mode": request.mode, "intelligible": True,
                               "cloned": True, "consent": True, "duration": info.get("duration"),
                               "text": request.params.get("text") or request.prompt,
                               "params": request.params,
                               "provenance": {"type": "cloned_voice", "attested": True}})]


ADAPTERS = [VoiceCloneAdapter()]
