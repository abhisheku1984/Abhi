"""Local voice adapters (§15).

Two genuinely different engines:

* `EspeakTtsAdapter` — wraps a real `espeak-ng` binary when the user has it
  installed (Windows/macOS/Linux supported, produces intelligible speech).
* `ProsodyTtsAdapter` — synthesises a real, prosody-shaped vocal track with
  the script's timing. It is explicitly labelled `intelligible: false`, so the
  platform never claims to have produced speech it did not.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from app.core.errors import StudioError
from app.core.logging import get_logger
from app.engines.base import (
    Artifact, BaseModelAdapter, Capabilities, Estimate, GenerateRequest, JobContext, ValidationResult,
)
from app.media import audio_ops, ffmpeg

log = get_logger("engines.voice.local")

LANGUAGE_MAP = {
    "en": "en", "hi": "hi", "te": "te", "ta": "ta", "kn": "kn", "ml": "ml", "mr": "mr",
    "bn": "bn", "gu": "gu", "pa": "pa", "or": "or", "as": "as", "ur": "ur",
}


def _find_espeak() -> str:
    for name in ("espeak-ng", "espeak", r"C:\Program Files\eSpeak NG\espeak-ng.exe",
                 r"C:\Program Files (x86)\eSpeak NG\espeak-ng.exe"):
        found = shutil.which(name)
        if found:
            return found
        if Path(name).exists():
            return name
    return ""


class EspeakTtsAdapter(BaseModelAdapter):
    id = "espeak-tts"
    display_name = "eSpeak NG TTS (local, intelligible)"
    version = "1.0.0"
    family = "voice"
    provider = "local"
    license = "GPLv3+ (eSpeak NG)"
    size_mb = 12
    vram_mb = 0
    speed = "fast"
    is_local = True
    ready_out_of_the_box = False
    notes = "Install eSpeak NG (https://github.com/espeak-ng/espeak-ng/releases) for real speech synthesis."
    capabilities = Capabilities(
        family="voice",
        modes=["text-to-speech", "speech-to-speech"],
        supports_seed=False,
        supports_voice=True,
        supports_character=True,
        notes="Supports English and 12 Indian languages via eSpeak NG voices.",
    )

    def status(self) -> dict[str, Any]:
        exe = _find_espeak()
        if not exe:
            return {
                "status": "not_installed",
                "reason": "eSpeak NG was not found. Install it and ensure `espeak-ng` is on PATH.",
            }
        return {"status": "installed", "reason": "", "binary": exe}

    def param_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "language": {"type": "string", "default": "en"},
                "accent": {"type": "string", "default": ""},
                "speed": {"type": "number", "minimum": 0.5, "maximum": 2.0, "default": 1.0},
                "pitch": {"type": "number", "minimum": 0.5, "maximum": 2.0, "default": 1.0},
                "voice_name": {"type": "string", "default": ""},
            },
        }

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
        words = max(1, len(text.split()))
        return Estimate(seconds=round(min(60, 1.0 + words * 0.06), 2), credits=round(words * 0.02, 2))

    def generate(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        self.ensure_ready()
        exe = _find_espeak()
        text = str(request.params.get("text") or request.prompt or "")
        if not text.strip():
            raise StudioError("Nothing to speak.", suggested_action="Enter narration text.")
        lang = LANGUAGE_MAP.get(str(request.params.get("language", "en")).lower()[:2], "en")
        speed = float(request.params.get("speed", 1.0) or 1.0)
        pitch = float(request.params.get("pitch", 1.0) or 1.0)
        wpm = int(165 * max(0.4, min(2.0, speed)))
        pitch_val = int(50 * max(0.25, min(2.0, pitch)))
        wav = ctx.path(f"voice_{ctx.job_id}.wav")

        ctx.progress(30, "synthesising speech")
        cmd = [exe, "-v", lang, "-s", str(wpm), "-p", str(pitch_val), "-w", wav, text]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0 or not Path(wav).exists():
            log.error("espeak_failed", code=proc.returncode, stderr=proc.stderr[:800])
            raise StudioError("Speech synthesis failed.", detail=proc.stderr[:800],
                              suggested_action="Check that eSpeak NG is installed and supports this language.")
        out = ctx.path(f"voice_{ctx.job_id}.mp3")
        audio_ops.encode(wav, out)
        info = ffmpeg.probe(out)
        return [Artifact(path=out, kind="audio", mime="audio/mpeg", name=f"voice_{ctx.job_id}",
                         meta={"engine": self.id, "engine_name": self.display_name, "mode": request.mode,
                               "intelligible": True, "language": lang, "duration": info.get("duration"),
                               "text": text, "params": request.params})]


class ProsodyTtsAdapter(BaseModelAdapter):
    id = "prosody-voice"
    display_name = "Local Prosody Voice (placeholder audio — not speech)"
    version = "1.0.0"
    family = "voice"
    provider = "local"
    license = "Apache-2.0 (platform code)"
    size_mb = 0
    vram_mb = 0
    speed = "fast"
    is_local = True
    ready_out_of_the_box = True
    notes = (
        "Produces a REAL audio track whose rhythm follows your script, so narration, lip-sync, "
        "timeline and export all work offline. It is NOT intelligible speech — install eSpeak NG, "
        "Piper or a provider for real speech (§42: no fake output)."
    )
    capabilities = Capabilities(
        family="voice",
        modes=["text-to-speech", "speech-to-speech", "narration"],
        supports_seed=True,
        supports_voice=True,
        supports_character=True,
        notes="Timing-accurate placeholder audio; flagged intelligible=false everywhere it is used.",
    )

    def param_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "language": {"type": "string", "default": "en"},
                "voice_name": {"type": "string", "enum": ["narrator", "female", "child", "deep"], "default": "narrator"},
                "speed": {"type": "number", "minimum": 0.5, "maximum": 2.0, "default": 1.0},
                "pitch": {"type": "number", "minimum": 0.5, "maximum": 2.0, "default": 1.0},
                "duration": {"type": "number", "minimum": 1, "maximum": 600, "default": 0},
            },
        }

    def validate(self, request: GenerateRequest) -> ValidationResult:
        result = ValidationResult()
        if not (request.params.get("text") or request.prompt):
            result.add_error("Enter the text you want spoken.")
        return result

    def estimate(self, request: GenerateRequest) -> Estimate:
        text = str(request.params.get("text") or request.prompt or "")
        words = max(1, len(text.split()))
        return Estimate(seconds=round(min(30, 0.5 + words * 0.02), 2), credits=round(words * 0.005, 2))

    def generate(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        text = str(request.params.get("text") or request.prompt or "")
        if not text.strip():
            raise StudioError("Nothing to speak.", suggested_action="Enter narration text.")
        words = max(1, len(text.split()))
        duration = float(request.params.get("duration") or 0) or max(1.2, words / 2.6)
        duration = min(duration, 600)
        voice = str(request.params.get("voice_name") or (request.voice or {}).get("name") or "narrator").lower()
        speed = float(request.params.get("speed", (request.voice or {}).get("speed") or 1.0))
        pitch = float(request.params.get("pitch", (request.voice or {}).get("pitch") or 1.0))

        ctx.progress(35, "synthesising prosody track")
        samples = audio_ops.generate_voice_track(text, duration, voice=voice, seed=request.seed,
                                                 pitch=pitch, speed=1.0)
        wav = audio_ops.write_wav(ctx.path(f"voice_{ctx.job_id}.wav"), samples)
        out = ctx.path(f"voice_{ctx.job_id}.mp3")
        audio_ops.encode(wav, out)
        info = ffmpeg.probe(out)
        return [Artifact(path=out, kind="audio", mime="audio/mpeg", name=f"voice_{ctx.job_id}",
                         meta={
                             "engine": self.id, "engine_name": self.display_name, "mode": request.mode,
                             "intelligible": False, "placeholder": True, "deterministic": True,
                             "language": request.params.get("language", "en"),
                             "duration": info.get("duration"), "text": text, "voice": voice,
                             "params": request.params,
                             "notice": "Not intelligible speech — install a TTS engine for real narration.",
                         })]


ADAPTERS = [EspeakTtsAdapter(), ProsodyTtsAdapter()]
