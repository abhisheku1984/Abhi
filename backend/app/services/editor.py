"""Timeline renderer (§18, §19).

Takes a declarative timeline (tracks → clips) and renders it with real FFmpeg:
trim, speed, crop, scale/pad, transitions, captions, text overlays, colour
adjustments, audio mixing and social export presets.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import StudioError
from app.core.logging import get_logger
from app.db.models import Asset, Job
from app.db.session import session_scope
from app.jobs import queue
from app.media import ffmpeg
from app.services import assets as asset_service
from app.storage.backends import get_storage

log = get_logger("services.editor")

EXPORT_PRESETS: dict[str, dict[str, Any]] = {
    "youtube": {"width": 1920, "height": 1080, "fps": 30, "container": "mp4", "crf": 20},
    "youtube-shorts": {"width": 1080, "height": 1920, "fps": 30, "container": "mp4", "crf": 20},
    "instagram-reels": {"width": 1080, "height": 1920, "fps": 30, "container": "mp4", "crf": 20},
    "instagram-post": {"width": 1080, "height": 1080, "fps": 30, "container": "mp4", "crf": 20},
    "instagram-story": {"width": 1080, "height": 1920, "fps": 30, "container": "mp4", "crf": 20},
    "tiktok": {"width": 1080, "height": 1920, "fps": 30, "container": "mp4", "crf": 20},
    "facebook": {"width": 1280, "height": 720, "fps": 30, "container": "mp4", "crf": 21},
    "linkedin": {"width": 1280, "height": 720, "fps": 30, "container": "mp4", "crf": 21},
    "square": {"width": 1080, "height": 1080, "fps": 30, "container": "mp4", "crf": 20},
    "portrait": {"width": 1080, "height": 1350, "fps": 30, "container": "mp4", "crf": 20},
    "webm": {"width": 1280, "height": 720, "fps": 30, "container": "webm", "crf": 32},
}

ASPECT_PRESETS = {
    "16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080),
    "4:5": (1080, 1350), "21:9": (2560, 1080),
}


def _local_path(db: Session, asset_id: str) -> str:
    storage = get_storage()
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise StudioError(f"Asset {asset_id} no longer exists.",
                          suggested_action="Re-add the clip to your timeline.")
    return storage.local_path(asset.storage_key)


def _prepare_clip(src: str, out: str, clip: dict[str, Any], width: int, height: int, workdir: Path) -> str:
    """Apply the clip's own edits, then normalise it to the target canvas."""
    current = src
    tmp_index = 0

    def next_tmp(suffix: str) -> str:
        nonlocal tmp_index
        tmp_index += 1
        return str(workdir / f"clip_{Path(out).stem}_{tmp_index}{suffix}")

    start = float(clip.get("start", 0) or 0)
    duration = clip.get("duration")
    if start or duration:
        tmp = next_tmp(".mp4")
        ffmpeg.trim(current, tmp, start=start, duration=float(duration) if duration else None)
        current = tmp

    speed = float(clip.get("speed", 1.0) or 1.0)
    if abs(speed - 1.0) > 0.01:
        tmp = next_tmp(".mp4")
        ffmpeg.set_speed(current, tmp, factor=speed)
        current = tmp

    crop_box = clip.get("crop")
    if crop_box and len(crop_box) == 4:
        tmp = next_tmp(".mp4")
        ffmpeg.crop(current, tmp, width=int(crop_box[0]), height=int(crop_box[1]),
                    x=int(crop_box[2]), y=int(crop_box[3]))
        current = tmp

    fade_in = float(clip.get("fade_in", 0) or 0)
    fade_out = float(clip.get("fade_out", 0) or 0)
    info = ffmpeg.probe(current)
    tmp = next_tmp(".mp4")
    ffmpeg.fade_in_out(current, tmp, fade_in=fade_in, fade_out=fade_out, total=info.get("duration"))
    current = tmp

    filters: list[str] = [f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos",
                          f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black"]
    grade = clip.get("color", {})
    if grade:
        eq = "eq=" + ":".join(f"{k}={v}" for k, v in grade.items() if k in
                              ("brightness", "contrast", "saturation", "gamma"))
        if eq != "eq=":
            filters.append(eq)
    if clip.get("filters"):
        filters.append(str(clip["filters"]))
    filters.append("format=yuv420p")

    ffmpeg.run(["-i", current, "-vf", ",".join(filters), "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "20", "-an", out])
    return out


def _write_srt(captions: list[dict[str, Any]], path: Path) -> Path:
    def ts(seconds: float) -> str:
        ms = int(round(seconds * 1000))
        h, ms = divmod(ms, 3600000)
        m, ms = divmod(ms, 60000)
        s, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, cap in enumerate(captions, start=1):
        start = float(cap.get("start", 0))
        end = float(cap.get("end", start + 2))
        text = str(cap.get("text", "")).replace("\n", " ")
        lines.append(f"{i}\n{ts(start)} --> {ts(end)}\n{text}\n")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def render_timeline(*, timeline: dict[str, Any], workdir: str | Path, out_path: str,
                    preset: str = "youtube", db: Optional[Session] = None) -> str:
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    preset_cfg = EXPORT_PRESETS.get(preset, EXPORT_PRESETS["youtube"])
    aspect = timeline.get("aspect")
    if aspect and aspect in ASPECT_PRESETS and preset == "custom":
        width, height = ASPECT_PRESETS[aspect]
    else:
        width, height = int(timeline.get("width") or preset_cfg["width"]), int(timeline.get("height") or preset_cfg["height"])
    fps = int(timeline.get("fps") or preset_cfg["fps"])

    video_clips = [c for c in timeline.get("clips", []) if c.get("track", "video") == "video"]
    image_clips = [c for c in timeline.get("clips", []) if c.get("track") == "image"]
    audio_clips = [c for c in timeline.get("clips", []) if c.get("track") in ("voice", "music", "sfx", "audio")]
    text_clips = [c for c in timeline.get("clips", []) if c.get("track") == "text"]

    if not video_clips and not image_clips:
        raise StudioError("Your timeline has no video or image clips.",
                          suggested_action="Add at least one clip before exporting.")

    prepared: list[str] = []
    for i, clip in enumerate(video_clips):
        src = clip.get("path") or (db and _local_path(db, clip["asset_id"]))
        if not src:
            raise StudioError(f"Clip {i + 1} has no source.", suggested_action="Re-link the missing clip.")
        out = str(workdir / f"prep_{i:03d}.mp4")
        prepared.append(_prepare_clip(src, out, clip, width, height, workdir))

    for i, clip in enumerate(image_clips):
        src = clip.get("path") or (db and _local_path(db, clip["asset_id"]))
        if not src:
            continue
        out = str(workdir / f"img_{i:03d}.mp4")
        duration = float(clip.get("duration", 4))
        tmp_img = str(workdir / f"img_{i:03d}_scaled.png")
        ffmpeg.run(["-i", src, "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                                      f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black", tmp_img])
        ffmpeg.image_to_video(tmp_img, out, duration=duration, fps=fps,
                              zoom=float(clip.get("zoom", 1.0) or 1.0))
        prepared.append(out)

    if not prepared:
        raise StudioError("Nothing to render.", suggested_action="Add a video or image clip.")

    # Concat with transitions between adjacent clips when requested.
    current = prepared[0]
    for i in range(1, len(prepared)):
        transition = (timeline.get("transitions") or {}).get(str(i), video_clips[i].get("transition", "cut") if i < len(video_clips) else "cut")
        if transition and transition != "cut":
            nxt = str(workdir / f"xfade_{i:03d}.mp4")
            ffmpeg.xfade(current, prepared[i], nxt, transition=transition,
                         duration=float(timeline.get("transition_duration", 0.5)))
            current = nxt
    # Simple concat for the remaining path (xfade already merged as we went).
    if len(prepared) > 1 and current is prepared[0]:
        video_only = str(workdir / "video_only.mp4")
        ffmpeg.concat(prepared, video_only, with_audio=False)
        current = video_only

    # Colour grade on the whole programme.
    grade = timeline.get("color", {})
    if grade:
        graded = str(workdir / "graded.mp4")
        eq = "eq=" + ":".join(f"{k}={v}" for k, v in grade.items()
                              if k in ("brightness", "contrast", "saturation", "gamma"))
        ffmpeg.run(["-i", current, "-vf", eq if eq != "eq=" else "null", "-c:v", "libx264",
                    "-preset", "veryfast", "-crf", "20", "-c:a", "copy", graded])
        current = graded

    # Captions / subtitles
    captions = timeline.get("captions") or []
    if captions:
        srt = _write_srt(captions, workdir / "captions.srt")
        burned = str(workdir / "captioned.mp4")
        ffmpeg.burn_subtitles(current, srt, burned)
        current = burned

    # Text overlays
    for i, clip in enumerate(text_clips):
        tmp = str(workdir / f"text_{i:03d}.mp4")
        ffmpeg.draw_text(current, tmp, text=str(clip.get("text", "")),
                         x=str(clip.get("x", "(w-text_w)/2")), y=str(clip.get("y", "h-th-80")),
                         size=int(clip.get("size", 48)), color=str(clip.get("color", "white")),
                         start=float(clip.get("start", 0)), duration=float(clip.get("duration", 3)))
        current = tmp

    # Audio
    audio_tracks: list[str] = []
    volumes: list[float] = []
    for clip in audio_clips:
        src = clip.get("path") or (db and _local_path(db, clip["asset_id"]))
        if not src:
            continue
        audio_tracks.append(src)
        volumes.append(float(clip.get("volume", 1.0) or 1.0))

    final = out_path
    if audio_tracks:
        mixed = str(workdir / "audio_mix.m4a")
        if len(audio_tracks) == 1:
            mixed = audio_tracks[0] if abs(volumes[0] - 1.0) < 0.01 else _apply_volume(audio_tracks[0], mixed, volumes[0])
        else:
            ffmpeg.mix_audio(audio_tracks, mixed, volumes=volumes)
        ffmpeg.mux_audio(current, mixed, final, original_audio=bool(timeline.get("keep_original_audio", False)),
                         music_gain_db=float(timeline.get("music_gain_db", -16)))
    else:
        shutil.copy2(current, final)

    log.info("timeline_rendered", out=final, preset=preset, clips=len(prepared))
    return final


def _apply_volume(src: str, out: str, volume: float) -> str:
    ffmpeg.run(["-i", src, "-af", f"volume={volume:.3f}", "-c:a", "aac", "-b:a", "192k", out])
    return out


def run_render_job(job_id: str) -> None:
    workdir = settings.data_root / "jobs" / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                return
            params = dict(job.params or {})
            owner_id = job.owner_id
            project_id = job.project_id
            preset = params.get("preset", "youtube")

        queue.progress(job_id, 10, "preparing timeline")
        out_name = f"render_{job_id}.{EXPORT_PRESETS.get(preset, {}).get('container', 'mp4')}"
        out_path = str(workdir / out_name)

        with session_scope() as db:
            render_timeline(timeline=params.get("timeline", {}), workdir=workdir, out_path=out_path,
                            preset=preset, db=db)

        queue.progress(job_id, 85, "finalising")
        with session_scope() as db:
            asset = asset_service.create_asset(
                db, owner_id=owner_id, path=out_path, kind="video",
                name=params.get("name") or f"Timeline export ({preset})",
                project_id=project_id,
                meta={"engine": "ffmpeg-timeline", "mode": "export", "preset": preset,
                      "deterministic": True, "timeline": params.get("timeline", {})},
            )
            asset_id = asset.id
        queue.complete(job_id, {"asset_ids": [asset_id], "preset": preset})
    except StudioError as exc:
        log.error("render_job_error", job_id=job_id, detail=exc.detail)
        queue.fail(job_id, {"code": exc.code, "message": exc.user_message,
                            "suggested_action": exc.suggested_action})
    except Exception as exc:  # noqa: BLE001
        log.exception("render_job_exception", job_id=job_id, error=str(exc))
        queue.fail(job_id, {"code": "render_failed", "message": "Generation failed.",
                            "suggested_action": "Retry / Change model / Check GPU", "detail": str(exc)})
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
