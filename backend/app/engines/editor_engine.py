"""Timeline renderer and social export (spec §27, §28).

Real FFmpeg work:
  * visual clips are normalised (scale/pad to canvas, fps, trim, speed, filters)
  * clips are concatenated in timeline order with optional transitions
  * audio clips are trimmed, faded and mixed with delay offsets
  * text/subtitle clips are burned in with time-bounded drawtext
  * export presets reframe to 9:16 / 16:9 / 1:1 / 4:5 with platform settings

No mocks: if a clip references a missing asset the render fails with the exact
reason.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ErrorCode, StudioError
from app.db.models import Asset, User
from app.services import assets as asset_svc
from app.services import media
from app.services import audio as audio_svc

EXPORT_PRESETS: dict[str, dict[str, Any]] = {
    "youtube": {"width": 1920, "height": 1080, "fps": 30, "aspect": "16:9", "max_mb": 2048},
    "youtube_shorts": {"width": 1080, "height": 1920, "fps": 30, "aspect": "9:16", "max_mb": 512},
    "instagram_feed": {"width": 1080, "height": 1080, "fps": 30, "aspect": "1:1", "max_mb": 512},
    "instagram_reels": {"width": 1080, "height": 1920, "fps": 30, "aspect": "9:16", "max_mb": 512},
    "facebook": {"width": 1280, "height": 720, "fps": 30, "aspect": "16:9", "max_mb": 1024},
    "linkedin": {"width": 1280, "height": 720, "fps": 30, "aspect": "16:9", "max_mb": 1024},
    "tiktok": {"width": 1080, "height": 1920, "fps": 30, "aspect": "9:16", "max_mb": 512},
    "x": {"width": 1280, "height": 720, "fps": 30, "aspect": "16:9", "max_mb": 512},
    "original": {"width": 0, "height": 0, "fps": 0, "aspect": "", "max_mb": 0},
}

FIT_MODES = ("pad", "crop", "stretch")


def _asset_path(db: Session, user: User, asset_id: str) -> tuple[str, Asset]:
    asset = db.get(Asset, asset_id)
    if not asset or asset.owner_id != user.id:
        raise StudioError(f"Asset '{asset_id}' not found.", code=ErrorCode.NOT_FOUND, status_code=404)
    return asset_svc.asset_abs_path(asset), asset


def _even(v: int) -> int:
    return int(v) - (int(v) % 2)


def _scale_filter(width: int, height: int, fit: str = "pad", color: str = "black") -> str:
    if fit == "stretch":
        return f"scale={width}:{height},setsar=1"
    if fit == "crop":
        return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={color},setsar=1"
    )


def _render_visual_clip(db: Session, user: User, clip: dict, canvas: dict, out_path: str,
                        workdir: Path) -> str:
    """Produce a normalised MP4 segment for one visual clip."""
    width, height, fps = canvas["width"], canvas["height"], canvas["fps"]
    path, asset = _asset_path(db, user, clip["asset_id"])
    duration = float(clip.get("out", 0) or 0) - float(clip.get("in", 0) or 0)
    if asset.kind == "image":
        duration = float(clip.get("duration") or clip.get("out") or 3.0)
        zoom = str(clip.get("ken_burns") or clip.get("camera") or "")
        if zoom in ("zoom in", "zoom_in", "zoom-in"):
            vf = (f"scale=8000:-1,zoompan=z='min(zoom+0.0015,1.5)':d={int(duration*fps)}:"
                  f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}")
        elif zoom in ("zoom out", "zoom_out", "zoom-out"):
            vf = (f"scale=8000:-1,zoompan=z='if(lte(zoom,1.0),1.5,max(1.001,zoom-0.0015))':"
                  f"d={int(duration*fps)}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps={fps}")
        elif zoom in ("pan left", "pan_left"):
            vf = f"{_scale_filter(width*2, height, 'crop')},crop={width}:{height}:'t*{int(width/(duration or 1))}':0,fps={fps}"
        else:
            vf = f"{_scale_filter(width, height, clip.get('fit','pad'))},fps={fps}"
        media.run_ffmpeg(["-loop", "1", "-t", f"{duration}", "-i", path, "-vf", vf,
                          "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                          "-pix_fmt", "yuv420p", "-r", str(fps), out_path])
        return out_path

    # video (or audio used as a visual placeholder -> black frame)
    start = float(clip.get("in", 0) or 0)
    vf = [_scale_filter(width, height, clip.get("fit", "pad")), f"fps={fps}"]
    if float(clip.get("speed", 1) or 1) != 1.0:
        vf.insert(0, f"setpts={1.0 / float(clip['speed']):.6f}*PTS")
    if clip.get("filter"):
        vf.append(f"eq=contrast={float(clip.get('contrast',1.0)):.3f}:saturation={float(clip.get('saturation',1.0)):.3f}:brightness={float(clip.get('brightness',0.0)):.3f}")
    if clip.get("preset_filter") and clip["preset_filter"] != "none":
        preset = str(clip["preset_filter"])
        if preset == "grayscale":
            vf.append("hue=s=0")
        elif preset == "sepia":
            vf.append("colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131")
        elif preset == "vivid":
            vf.append("eq=saturation=1.4:contrast=1.08")
        elif preset == "noir":
            vf.append("hue=s=0,eq=contrast=1.35")
        elif preset == "cool":
            vf.append("colorbalance=rs=-.15:bs=.15")
        elif preset == "warm":
            vf.append("colorbalance=rs=.15:bs=-.15")
    if float(clip.get("fade_in", 0) or 0) > 0:
        vf.append(f"fade=t=in:st=0:d={float(clip['fade_in'])}")
    if float(clip.get("fade_out", 0) or 0) > 0:
        vf.append(f"fade=t=out:st={max(0.0, duration - float(clip['fade_out']))}:d={float(clip['fade_out'])}")

    args = ["-ss", str(start), "-i", path]
    if duration > 0:
        args += ["-t", str(duration)]
    args += ["-vf", ",".join(vf), "-an",
             "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-r", str(fps), out_path]
    media.run_ffmpeg(args)
    return out_path


def _render_audio_clip(db: Session, user: User, clip: dict, out_wav: str) -> str:
    path, asset = _asset_path(db, user, clip["asset_id"])
    start = float(clip.get("in", 0) or 0)
    duration = float(clip.get("duration") or (float(clip.get("out", 0) or 0) - start) or 0)
    tmp = str(Path(out_wav).with_suffix(".raw.wav"))
    args = ["-ss", str(start)]
    if duration > 0:
        args += ["-t", str(duration)]
    args += ["-i", path, "-ac", "2", "-ar", "44100", tmp]
    media.run_ffmpeg(args)
    filters = []
    if float(clip.get("volume", 1.0)) != 1.0:
        filters.append(f"volume={float(clip['volume']):.3f}")
    if float(clip.get("fade_in", 0) or 0) > 0:
        filters.append(f"afade=t=in:st=0:d={float(clip['fade_in'])}")
    if float(clip.get("fade_out", 0) or 0) > 0:
        filters.append(f"afade=t=out:st={max(0.0, duration - float(clip['fade_out']))}:d={float(clip['fade_out'])}")
    if float(clip.get("speed", 1) or 1) != 1.0:
        filters.append(f"atempo={min(4.0, max(0.5, float(clip['speed']))):.4f}")
    if filters:
        media.run_ffmpeg(["-i", tmp, "-af", ",".join(filters), out_wav])
        Path(tmp).unlink(missing_ok=True)
    else:
        shutil.move(tmp, out_wav)
    return out_wav


def render_timeline(db: Session, user: User, timeline: dict, out_path: str,
                    progress=None) -> dict:
    """Render a full timeline to an MP4 file. Returns metadata about the render."""
    media.require_ffmpeg()
    canvas = {
        "width": _even(int(timeline.get("width") or 1920)),
        "height": _even(int(timeline.get("height") or 1080)),
        "fps": int(timeline.get("fps") or 30),
    }
    workdir = Path(out_path).parent / f"tl-{Path(out_path).stem}"
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        tracks = timeline.get("tracks") or []
        visual_tracks = [t for t in tracks if t.get("type") in ("video", "image")]
        audio_tracks = [t for t in tracks if t.get("type") in ("audio", "voice", "music", "sfx")]
        text_clips = [c for t in tracks if t.get("type") == "text" for c in (t.get("clips") or [])]
        subtitle_clips = [c for t in tracks if t.get("type") == "subtitle" for c in (t.get("clips") or [])]

        # --- visual clips, ordered by start time ----------------------------
        clips: list[dict] = []
        for track in visual_tracks:
            for c in track.get("clips") or []:
                clips.append({**c, "_type": track.get("type")})
        clips.sort(key=lambda c: float(c.get("start", 0) or 0))
        if not clips:
            raise StudioError("Timeline has no visual clips to render.", code=ErrorCode.INVALID_REQUEST)

        segments: list[str] = []
        total_clips = len(clips)
        for i, clip in enumerate(clips):
            if progress:
                progress(0.05 + 0.6 * (i / max(total_clips, 1)), f"rendering clip {i + 1}/{total_clips}")
            seg = str(workdir / f"seg{i:04d}.mp4")
            _render_visual_clip(db, user, clip, canvas, seg, workdir)
            segments.append(seg)

        if progress:
            progress(0.68, "concatenating timeline")
        silent = str(workdir / "visual.mp4")
        transitions = [str(c.get("transition") or "cut") for c in clips]
        use_xfade = any(t not in ("cut", "", "none") for t in transitions[1:])
        if len(segments) == 1:
            shutil.copyfile(segments[0], silent)
        elif use_xfade:
            media.concat_videos(segments, silent, reencode=True,
                                transition=next((t for t in transitions if t not in ("cut", "", "none")), "fade"))
        else:
            media.concat_videos(segments, silent, reencode=False)

        # --- text / subtitle burn-in ---------------------------------------
        draw_filters = []
        for c in text_clips + subtitle_clips:
            text = str(c.get("text", "")).replace(":", "\\:").replace("'", "\\'")
            if not text:
                continue
            start = float(c.get("start", 0) or 0)
            end = start + float(c.get("duration", 3) or 3)
            size = int(c.get("font_size", 42))
            color = str(c.get("color", "white"))
            pos = str(c.get("position", "bottom"))
            y = {"top": "40", "center": "(h-text_h)/2", "bottom": "h-th-50"}.get(pos, "h-th-50")
            draw_filters.append(
                f"drawtext=text='{text}':fontcolor={color}:fontsize={size}:x=(w-text_w)/2:y={y}:"
                f"box=1:boxcolor=black@0.35:boxborderw=8:"
                f"enable='between(t,{start:.3f},{end:.3f})'"
            )
        if progress:
            progress(0.75, "burning in text")
        with_text = str(workdir / "visual_text.mp4")
        if draw_filters:
            media.run_ffmpeg(["-i", silent, "-vf", ",".join(draw_filters),
                              "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
                              "-pix_fmt", "yuv420p", "-an", with_text])
        else:
            with_text = silent

        # --- audio ----------------------------------------------------------
        audio_files: list[tuple[str, float]] = []
        for track in audio_tracks:
            for c in track.get("clips") or []:
                wav = str(workdir / f"aud{len(audio_files):04d}.wav")
                _render_audio_clip(db, user, c, wav)
                audio_files.append((wav, float(c.get("start", 0) or 0)))
        if progress:
            progress(0.85, "mixing audio")
        final_audio = None
        if audio_files:
            final_audio = str(workdir / "mix.wav")
            inputs: list[str] = []
            parts = []
            for idx, (wav, offset) in enumerate(audio_files):
                inputs += ["-i", wav]
                parts.append(f"[{idx}:a]adelay={int(offset*1000)}|{int(offset*1000)}[a{idx}]")
            amix = "".join(f"[a{i}]" for i in range(len(audio_files)))
            parts.append(f"{amix}amix=inputs={len(audio_files)}:duration=longest:dropout_transition=0:normalize=0[aout]")
            media.run_ffmpeg([*inputs, "-filter_complex", ";".join(parts), "-map", "[aout]",
                              "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", final_audio])

        if progress:
            progress(0.92, "final render")
        if final_audio:
            media.mux_audio(with_text, final_audio, out_path, audio_volume=1.0)
        else:
            media.run_ffmpeg(["-i", with_text, "-c", "copy", "-movflags", "+faststart", out_path])

        info = media.probe(out_path)
        if progress:
            progress(1.0, "complete")
        return {
            "path": out_path,
            "duration": info.duration,
            "width": info.width,
            "height": info.height,
            "fps": info.fps,
            "clips": len(clips),
            "audio_tracks": len(audio_files),
            "size_bytes": Path(out_path).stat().st_size if Path(out_path).exists() else 0,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def export_asset(db: Session, user: User, asset_id: str, preset: str, out_path: str,
                 fit: str = "pad", quality: str = "high", progress=None) -> dict:
    """Reframe/transcode an existing video for a social platform."""
    media.require_ffmpeg()
    cfg = EXPORT_PRESETS.get((preset or "original").lower(), EXPORT_PRESETS["original"])
    src, asset = _asset_path(db, user, asset_id)
    if asset.kind == "image":
        # Images export as a still at the target size.
        im = media.load_image(src)
        target = (cfg["width"], cfg["height"]) if cfg["width"] else (im.width, im.height)
        im = media.crop_image(im, aspect=cfg["aspect"]) if cfg["aspect"] else im
        im = media.resize_image(im, target[0], target[1], "lanczos")
        media.save_image(im, out_path, quality=95)
        return {"path": out_path, "width": im.width, "height": im.height, "preset": preset, "kind": "image"}

    info = media.probe(src)
    target_w = _even(cfg["width"] or info.width or 1920)
    target_h = _even(cfg["height"] or info.height or 1080)
    target_fps = int(cfg["fps"] or info.fps or 30)
    crf = {"draft": 26, "high": 20, "max": 16}.get(quality, 20)
    vf = [_scale_filter(target_w, target_h, fit if fit in FIT_MODES else "pad"), f"fps={target_fps}"]
    if progress:
        progress(0.3, f"exporting {preset}")
    media.run_ffmpeg([
        "-i", src, "-vf", ",".join(vf),
        "-c:v", "libx264", "-crf", str(crf), "-preset", "medium",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart", out_path,
    ], timeout=3600)
    out_info = media.probe(out_path)
    if progress:
        progress(1.0, "complete")
    return {
        "path": out_path, "preset": preset, "width": out_info.width, "height": out_info.height,
        "fps": out_info.fps, "duration": out_info.duration,
        "size_bytes": Path(out_path).stat().st_size if Path(out_path).exists() else 0,
        "fits": True if not cfg["max_mb"] else (Path(out_path).stat().st_size <= cfg["max_mb"] * 1024 * 1024),
        "limit_mb": cfg["max_mb"],
    }


def edit_operations() -> list[dict]:
    return [
        {"op": "trim", "status": "available", "note": "Real FFmpeg stream copy / re-encode."},
        {"op": "split", "status": "available", "note": "Two trims around a cut point."},
        {"op": "cut", "status": "available", "note": "Removes a range and re-concatenates."},
        {"op": "merge", "status": "available", "note": "Concatenates clips."},
        {"op": "speed", "status": "available", "note": "setpts + atempo."},
        {"op": "volume", "status": "available", "note": "volume filter."},
        {"op": "fade", "status": "available", "note": "Audio and video fades."},
        {"op": "transition", "status": "available", "note": "xfade between clips."},
        {"op": "crop", "status": "available", "note": "crop filter."},
        {"op": "resize", "status": "available", "note": "scale filter."},
        {"op": "text", "status": "available", "note": "drawtext with time bounds."},
        {"op": "captions", "status": "available", "note": "Burn-in or .srt sidecar."},
        {"op": "filter", "status": "available", "note": "eq / colorchannelmixer presets."},
        {"op": "color_adjust", "status": "available", "note": "Brightness / contrast / saturation."},
    ]


def build_srt(clips: list[dict]) -> str:
    def ts(v: float) -> str:
        h = int(v // 3600)
        m = int((v % 3600) // 60)
        s = int(v % 60)
        ms = int((v - int(v)) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, c in enumerate(clips, start=1):
        start = float(c.get("start", 0) or 0)
        end = start + float(c.get("duration", 2) or 2)
        lines += [str(i), f"{ts(start)} --> {ts(end)}", str(c.get("text", "")), ""]
    return "\n".join(lines)
