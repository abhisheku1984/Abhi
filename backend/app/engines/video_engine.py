"""Video engine: generative modes, shot rendering and story assembly (spec §13-§18)."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.adapters.base import GeneratedFile, GenerationResult
from app.adapters.registry import registry
from app.core.errors import ErrorCode, StudioError
from app.db.models import Asset, Generation, Job, Shot, User
from app.engines.base import build_request, mark_generation_failed, persist_result, workdir
from app.engines.image_engine import _done
from app.services import assets as asset_svc
from app.services import media
from app.services import safety

VIDEO_OPS = {
    "text-to-video": ("text_to_video", "text_to_video"),
    "image-to-video": ("image_to_video", "image_to_video"),
    "reference-to-video": ("image_to_video", "image_to_video"),
    "first-frame-to-video": ("image_to_video", "first_frame_to_video"),
    "first-last-frame-to-video": ("image_to_video", "first_last_frame_to_video"),
    "video-to-video": ("video_to_video", "video_to_video"),
    "video-extension": ("video_to_video", "video_extension"),
    "video-restyle": ("video_to_video", "video_restyle"),
    "video-upscale": ("video_to_video", "video_upscale"),
    "slideshow": ("image_to_video", "slideshow"),
    "interpolate": ("video_interpolation", "interpolate"),
}


def available_operations() -> list[dict]:
    rows = []
    for op, (capability, _mode) in VIDEO_OPS.items():
        adapters = registry.by_capability(capability)
        rows.append({
            "op": op, "capability": capability,
            "status": "available" if any(a.status().available for a in adapters) else "model_required",
            "adapters": [a.key for a in adapters],
        })
    return rows


def run_video_job(db: Session, job: Job, progress, cancel_event) -> dict:
    params = dict(job.params or {})
    user = db.get(User, job.owner_id)
    if user is None:
        raise StudioError("Job owner not found.", code=ErrorCode.NOT_FOUND)
    generation = db.get(Generation, job.generation_id) if job.generation_id else None
    op = str(params.get("operation") or "text-to-video").lower()
    if op not in VIDEO_OPS:
        raise StudioError(f"Unsupported video operation '{op}'.", code=ErrorCode.INVALID_REQUEST,
                          details={"supported": sorted(VIDEO_OPS)})
    capability, mode = VIDEO_OPS[op]
    out_dir = workdir("video")

    try:
        safety.enforce(params.get("prompt", ""), allow_impersonation=bool(params.get("identity_rights")))
        adapter = registry.resolve(capability, params.get("model_id"))
        req = build_request(db, user, {**params, "mode": mode}, str(out_dir), capability)
        req.cancel_event = cancel_event
        adapter.validate(req)
        progress(0.08, f"generating with {adapter.name}")
        result = adapter.generate(req, progress)
        if not result.files:
            raise StudioError(f"{adapter.name} produced no output.", code=ErrorCode.INTERNAL_ERROR)
        new_assets = persist_result(db, user=user, result=result, generation=generation,
                                    adapter=adapter, project_id=job.project_id,
                                    parent_asset_id=params.get("asset_id"))
        return _done(new_assets, op, adapter.key)
    except Exception as exc:  # noqa: BLE001
        mark_generation_failed(db, generation, getattr(exc, "code", ErrorCode.INTERNAL_ERROR), str(exc))
        raise


def render_shot_video(db: Session, user: User, shot: Shot, image_asset: Asset,
                      out_dir: Path, progress=None, model_id: str | None = None) -> str:
    """Turn a shot's key image into a moving shot using the camera plan."""
    capability = "image_to_video"
    adapter = registry.resolve(capability, model_id)
    params = {
        "prompt": shot.video_prompt or shot.description or shot.image_prompt or "",
        "mode": "image_to_video",
        "camera": shot.camera or "static",
        "lens": shot.lens or "35mm",
        "motion": shot.motion or "normal",
        "lighting": shot.lighting or "cinematic",
        "duration": shot.duration_seconds or 4.0,
        "fps": 24,
        "width": image_asset.width or 1024,
        "height": image_asset.height or 1024,
        "references": [{"asset_id": image_asset.id, "role": "init"}],
    }
    req = build_request(db, user, params, str(out_dir), capability)
    adapter.validate(req)
    result = adapter.generate(req, progress or (lambda p, m: None))
    if not result.files:
        raise StudioError(f"{adapter.name} produced no video for shot {shot.shot_number}.",
                          code=ErrorCode.INTERNAL_ERROR)
    return result.files[0].path


def concatenate_shots(paths: list[str], out_path: str, transitions: list[str] | None = None) -> str:
    media.require_ffmpeg()
    if not paths:
        raise StudioError("No shots to assemble.", code=ErrorCode.INVALID_REQUEST)
    if len(paths) == 1:
        media.run_ffmpeg(["-i", paths[0], "-c", "copy", "-movflags", "+faststart", out_path])
        return out_path
    use_xfade = bool(transitions) and any(t and t != "cut" for t in transitions[1:])
    if use_xfade:
        media.concat_videos(paths, out_path, reencode=True,
                            transition=next((t for t in (transitions or []) if t and t != "cut"), "fade"))
    else:
        media.concat_videos(paths, out_path, reencode=True)
    return out_path


def assemble_video(video_paths: list[str], audio_paths: list[str] | None, out_path: str,
                   transitions: list[str] | None = None, music_volume: float = 0.35,
                   voice_volume: float = 1.0, music_path: str | None = None,
                   progress=None) -> str:
    """Concatenate shot videos, then mix narration + music into the final file."""
    media.require_ffmpeg()
    tmp = Path(out_path).parent
    merged = str(tmp / "merged_silent.mp4")
    concatenate_shots(video_paths, merged, transitions)
    if progress:
        progress(0.7, "mixing audio")

    tracks: list[tuple[str, float]] = []
    for p in audio_paths or []:
        if p and Path(p).exists():
            tracks.append((p, voice_volume))
    if music_path and Path(music_path).exists():
        tracks.append((music_path, music_volume))

    if not tracks:
        media.run_ffmpeg(["-i", merged, "-c", "copy", "-movflags", "+faststart", out_path])
        return out_path

    final_wav = str(tmp / "final_audio.wav")
    _mix_audio(tracks, final_wav)
    media.mux_audio(merged, final_wav, out_path)
    return out_path


def _mix_audio(tracks: list[tuple[str, float]], out_wav: str) -> str:
    """Align narration tracks sequentially, duck the music bed under them."""
    media.require_ffmpeg()
    tmp = Path(out_wav).parent
    normalized: list[str] = []
    narration_parts: list[str] = []
    for i, (path, gain) in enumerate(tracks):
        dst = str(tmp / f"mix_{i:02d}.wav")
        media.run_ffmpeg(["-i", path, "-af", f"volume={gain:.3f},aresample=44100",
                          "-ac", "2", "-ar", "44100", dst])
        normalized.append(dst)
    # First track is narration (sequential), remaining tracks are beds (parallel).
    if len(normalized) == 1:
        media.run_ffmpeg(["-i", normalized[0], "-c:a", "pcm_s16le", out_wav])
        return out_wav
    narration = normalized[0]
    beds = normalized[1:]
    inputs: list[str] = ["-i", narration]
    for b in beds:
        inputs += ["-i", b]
    parts = ["[0:a]anull[n]"]
    labels = ["[n]"]
    for j in range(len(beds)):
        parts.append(f"[{j + 1}:a]aloop=loop=-1:size=2e9[b{j}]")
        labels.append(f"[b{j}]")
    parts.append("".join(labels) + f"amix=inputs={len(labels)}:duration=first:dropout_transition=0:normalize=0[aout]")
    media.run_ffmpeg([*inputs, "-filter_complex", ";".join(parts), "-map", "[aout]",
                      "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", out_wav])
    return out_wav


def concat_narration(paths: list[str], out_wav: str, gaps: list[float] | None = None) -> str:
    """Join narration clips in order, inserting optional silences between them."""
    media.require_ffmpeg()
    tmp = Path(out_wav).parent
    parts: list[str] = []
    for i, p in enumerate(paths or []):
        if i and gaps and i - 1 < len(gaps) and gaps[i - 1] > 0:
            gap = str(tmp / f"gap{i:03d}.wav")
            from app.services import audio as audio_svc

            audio_svc.write_wav(gap, audio_svc.silence(float(gaps[i - 1])), 44100)
            parts.append(gap)
        parts.append(p)
    if not parts:
        raise StudioError("No narration clips to concatenate.", code=ErrorCode.INVALID_REQUEST)
    if len(parts) == 1:
        media.run_ffmpeg(["-i", parts[0], "-c:a", "pcm_s16le", out_wav])
        return out_wav
    list_file = tmp / "narration_list.txt"
    list_file.write_text("\n".join(f"file '{p}'" for p in parts), encoding="utf-8")
    media.run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(list_file),
                      "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2", out_wav])
    return out_wav
