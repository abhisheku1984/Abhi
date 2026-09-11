"""Voice engine: text-to-speech, voice profiles and cloning orchestration (spec §25).

Synthesis quality depends on the active TTS adapter:
  * piper            - local neural TTS on CPU (needs a ~60 MB voice model)
  * espeak / pyttsx3 - instant offline system voices (robotic on Linux,
                       good on Windows via SAPI5)
  * elevenlabs/azure/google - hosted, requires a key you explicitly enable

Post-processing (speed, pitch, pauses, enhancement) is always real DSP and
works with whichever adapter produced the audio.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.adapters.registry import registry
from app.core.errors import ErrorCode, StudioError
from app.db.models import Generation, Job, User, VoiceProfile
from app.engines.base import build_request, mark_generation_failed, persist_result, workdir
from app.engines.image_engine import _done
from app.services import audio as audio_svc
from app.services import media
from app.services import safety


def available_operations() -> list[dict]:
    rows = []
    for capability, label in (("tts", "text-to-speech"), ("voice_clone", "voice-clone")):
        adapters = registry.by_capability(capability)
        rows.append({
            "op": label, "capability": capability,
            "status": "available" if any(a.status().available for a in adapters) else "model_required",
            "adapters": [a.key for a in adapters],
            "notes": [a.status().message for a in adapters if not a.status().available],
        })
    return rows


def run_voice_job(db: Session, job: Job, progress, cancel_event) -> dict:
    params = dict(job.params or {})
    user = db.get(User, job.owner_id)
    if user is None:
        raise StudioError("Job owner not found.", code=ErrorCode.NOT_FOUND)
    generation = db.get(Generation, job.generation_id) if job.generation_id else None
    op = str(params.get("operation") or "tts").lower()
    capability = "voice_clone" if op in ("voice-clone", "clone") else "tts"
    out_dir = workdir("voice")

    try:
        profile = None
        if params.get("voice_profile_id"):
            profile = db.get(VoiceProfile, params["voice_profile_id"])
            if not profile or profile.owner_id != user.id:
                raise StudioError("Voice profile not found.", code=ErrorCode.NOT_FOUND, status_code=404)

        if capability == "voice_clone":
            safety.require_voice_consent(
                profile.consent_attested_at if profile else None,
                bool(params.get("consent_attested")),
            )

        merged = dict(params)
        if profile:
            merged.setdefault("language", profile.language)
            merged.setdefault("voice_id", profile.meta.get("voice_id") if profile.meta else None)
            merged.setdefault("speed", profile.speed)
            merged.setdefault("pitch", profile.pitch)
            merged.setdefault("emotion", profile.emotion)

        adapter = registry.resolve(capability, params.get("model_id") or (profile.engine if profile else None))
        req = build_request(db, user, merged, str(out_dir), capability)
        req.cancel_event = cancel_event
        req.params.setdefault("allow_empty_prompt", False)
        if not (req.prompt or "").strip():
            raise StudioError("Text is required for speech synthesis.", code=ErrorCode.INVALID_REQUEST)
        progress(0.1, f"synthesising with {adapter.name}")
        result = adapter.generate(req, progress)
        if not result.files:
            raise StudioError(f"{adapter.name} produced no audio.", code=ErrorCode.INTERNAL_ERROR)

        # ---- real post-processing -----------------------------------------
        wav_path = result.files[0].path
        if not wav_path.lower().endswith(".wav"):
            converted = str(Path(out_dir) / "speech.wav")
            media.run_ffmpeg(["-i", wav_path, "-ac", "1", "-ar", "44100", converted])
            wav_path = converted
            result.files[0].path = converted
            result.files[0].mime = "audio/wav"

        samples, sr = audio_svc.read_wav(wav_path)
        speed = float(merged.get("speed", 1.0) or 1.0)
        pitch = float(merged.get("pitch", 0.0) or 0.0)
        if abs(speed - 1.0) > 1e-3:
            samples = audio_svc.change_speed(samples, speed)
        if abs(pitch) > 1e-3:
            samples = audio_svc.pitch_shift(samples, pitch * 12.0)
        pauses = merged.get("pauses") or []
        if pauses:
            parts = []
            cursor = 0.0
            for p in pauses:
                at = float(p.get("at", 0))
                length = float(p.get("duration", 0.3))
                if at > cursor:
                    parts.append(samples[int(cursor * sr): int(at * sr)])
                    parts.append(audio_svc.silence(length, sr))
                    cursor = at
            parts.append(samples[int(cursor * sr):])
            samples = audio_svc.concat(parts)
        if merged.get("enhance"):
            samples = audio_svc.soft_limit(audio_svc.normalize(samples, 0.92), 1.2)
        samples = audio_svc.normalize(samples, 0.9)
        audio_svc.write_wav(wav_path, samples, sr)
        result.meta.update({"duration": round(samples.size / sr, 2), "sample_rate": sr,
                            "speed": speed, "pitch": pitch})

        new_assets = persist_result(db, user=user, result=result, generation=generation,
                                    adapter=adapter, project_id=job.project_id, kind_override="audio")
        if profile and new_assets:
            profile.preview_asset_id = new_assets[0].id
            db.flush()
        return _done(new_assets, op, adapter.key, {"duration": result.meta.get("duration")})
    except Exception as exc:  # noqa: BLE001
        mark_generation_failed(db, generation, getattr(exc, "code", ErrorCode.INVALID_REQUEST), str(exc))
        raise
