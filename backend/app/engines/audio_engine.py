"""Audio engine: music, ambience and sound effects (spec §26)."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.adapters.registry import registry
from app.core.errors import ErrorCode, StudioError
from app.db.models import Asset, Generation, Job, User
from app.engines.base import build_request, mark_generation_failed, persist_result, workdir
from app.engines.image_engine import _done
from app.services import assets as asset_svc
from app.services import media

AUDIO_OPS = {
    "background-music": "music",
    "cinematic-music": "music",
    "corporate-music": "music",
    "children-music": "music",
    "ambient": "sfx",
    "nature": "sfx",
    "sound-effects": "sfx",
    "transitions": "sfx",
    "narration-bed": "music",
}


def available_operations() -> list[dict]:
    rows = []
    for op, capability in AUDIO_OPS.items():
        adapters = registry.by_capability(capability)
        rows.append({
            "op": op, "capability": capability,
            "status": "available" if any(a.status().available for a in adapters) else "model_required",
            "adapters": [a.key for a in adapters],
        })
    return rows


def run_audio_job(db: Session, job: Job, progress, cancel_event) -> dict:
    params = dict(job.params or {})
    user = db.get(User, job.owner_id)
    if user is None:
        raise StudioError("Job owner not found.", code=ErrorCode.NOT_FOUND)
    generation = db.get(Generation, job.generation_id) if job.generation_id else None
    op = str(params.get("operation") or "background-music").lower()
    capability = AUDIO_OPS.get(op)
    if not capability:
        raise StudioError(f"Unsupported audio operation '{op}'.", code=ErrorCode.INVALID_REQUEST,
                          details={"supported": sorted(AUDIO_OPS)})
    out_dir = workdir("audio")

    try:
        adapter = registry.resolve(capability, params.get("model_id"))
        req = build_request(db, user, params, str(out_dir), capability)
        req.cancel_event = cancel_event
        progress(0.1, f"composing with {adapter.name}")
        result = adapter.generate(req, progress)
        if not result.files:
            raise StudioError(f"{adapter.name} produced no audio.", code=ErrorCode.INTERNAL_ERROR)
        files = result.files
        # Optional real post-processing: fade + loudness normalisation
        if params.get("fade_out") or params.get("loop_to"):
            from app.services import audio as audio_svc

            samples, sr = audio_svc.read_wav(files[0].path)
            target = float(params.get("loop_to") or 0)
            if target > 0 and samples.size:
                reps = max(1, int(target * sr / max(samples.size, 1)) + 1)
                samples = audio_svc.concat([samples] * reps)[: int(target * sr)]
            samples = audio_svc.fade(samples, float(params.get("fade_in", 0.0)),
                                     float(params.get("fade_out", 0.0)), sr)
            samples = audio_svc.normalize(samples, 0.85)
            audio_svc.write_wav(files[0].path, samples, sr)
        new_assets = persist_result(db, user=user, result=result, generation=generation,
                                    adapter=adapter, project_id=job.project_id,
                                    kind_override="audio")
        return _done(new_assets, op, adapter.key)
    except Exception as exc:  # noqa: BLE001
        mark_generation_failed(db, generation, getattr(exc, "code", ErrorCode.INTERNAL_ERROR), str(exc))
        raise


def mix_tracks(db: Session, user: User, clips: list[dict], out_path: str, progress=None) -> str:
    """Real multi-track audio mixing: trim, gain, fade, delay and sum."""
    from app.services import audio as audio_svc

    media.require_ffmpeg()
    tmp = Path(out_path).parent
    tracks = []
    for i, clip in enumerate(clips):
        asset = db.get(Asset, clip.get("asset_id"))
        if not asset or asset.owner_id != user.id:
            raise StudioError(f"Audio asset '{clip.get('asset_id')}' not found.", code=ErrorCode.NOT_FOUND)
        src = asset_svc.asset_abs_path(asset)
        wav = str(tmp / f"tr{i:02d}.wav")
        media.run_ffmpeg(["-i", src, "-ac", "2", "-ar", "44100", wav])
        samples, sr = audio_svc.read_wav(wav)
        start = float(clip.get("in", 0) or 0)
        end = clip.get("out")
        if end:
            samples = samples[int(start * sr): int(float(end) * sr)]
        else:
            samples = samples[int(start * sr):]
        samples = audio_svc.fade(samples, float(clip.get("fade_in", 0.0)),
                                 float(clip.get("fade_out", 0.0)), sr)
        gain = float(clip.get("volume", 1.0))
        if progress:
            progress(0.2 + 0.6 * (i / max(len(clips), 1)), f"mixing track {i + 1}")
        delay = float(clip.get("start", 0.0) or 0.0)
        padded = audio_svc.concat([audio_svc.silence(delay, sr), samples]) if delay > 0 else samples
        tracks.append(padded * gain)
    mixed = audio_svc.mix(tracks)
    mixed = audio_svc.soft_limit(audio_svc.normalize(mixed, 0.9), 1.1)
    audio_svc.write_wav(out_path, mixed, 44100)
    if progress:
        progress(1.0, "complete")
    return out_path
