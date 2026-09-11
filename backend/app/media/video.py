"""
Video engine — real FFmpeg rendering.

Everything here produces genuine H.264 MP4 files (image → motion clip → timed
transition → captions → audio mux). This layer is fully functional without a
GPU because it is deterministic composition, not model inference: it is the
renderer that GPU-generated clips are later assembled with.

Pipeline
--------
1. ``prep_for_video`` (Pillow) cover-fits each still at 1.5× target and bakes
   its caption, so FFmpeg never needs font configuration.
2. Each clip becomes a normalised segment (identical size / fps / pixel format)
   with motion applied via the ``zoompan`` filter.
3. Segments are joined with the ``concat`` filter (cuts) or ``xfade``
   (transitions) — both require identical segment parameters, which step 2
   guarantees.
4. Optional audio is muxed with ``-c:v copy`` (fast, lossless video).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..config import detect_ffmpeg
from . import images as image_engine

VIDEO_CODEC_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p"]


class RenderError(RuntimeError):
    """Raised when FFmpeg fails; carries the tail of stderr for diagnostics."""


def ffmpeg_bin() -> str:
    binary = detect_ffmpeg()
    if not binary:
        raise RenderError(
            "FFmpeg not available. Install ffmpeg or `pip install imageio-ffmpeg` "
            "(the bundled static build is used automatically)."
        )
    return binary


def run_ffmpeg(args: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    command = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", *args]
    proc = subprocess.run(
        command, capture_output=True, text=True, timeout=timeout
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-8:]
        raise RenderError("ffmpeg failed: " + " | ".join(tail))
    return proc


TRANSITIONS = {
    "cut": None,
    "none": None,
    "fade": "fade",
    "fade_black": "fadeblack",
    "fade_white": "fadewhite",
    "dissolve": "dissolve",
    "wipe_left": "wipeleft",
    "wipe_right": "wiperight",
    "wipe_up": "wipeup",
    "wipe_down": "wipedown",
    "slide_left": "slideleft",
    "slide_right": "slideright",
    "slide_up": "slideup",
    "slide_down": "slidedown",
    "circle": "circleopen",
    "circle_close": "circleclose",
    "radial": "radial",
    "pixelize": "pixelize",
    "smooth_left": "smoothleft",
    "smooth_right": "smoothright",
    "horz_open": "horzopen",
    "vert_open": "vertopen",
    "square_crop": "sqcrop",
}


def normalise_transition(name: str | None) -> str | None:
    return TRANSITIONS.get((name or "cut").lower().strip(), "fade")


MOTIONS = {
    "none": None,
    "static": None,
    "zoom_in": "zoom_in",
    "zoom_out": "zoom_out",
    "pan_left": "pan_left",
    "pan_right": "pan_right",
    "pan_up": "pan_up",
    "pan_down": "pan_down",
}


def _motion_filter(motion: str, frames: int, width: int, height: int, fps: int) -> str | None:
    """Return the zoompan filter implementing ``motion`` (or None for static)."""
    motion = (motion or "none").lower()
    if motion in ("none", "static"):
        return None
    frames = max(2, frames)
    base = {"s": f"{width}x{height}", "fps": str(fps), "d": "1"}
    centre_x = "x='iw/2-(iw/zoom/2)'"
    centre_y = "y='ih/2-(ih/zoom/2)'"
    if motion == "zoom_in":
        z = "z='min(1+0.0016*on,1.28)'"
        return f"zoompan={z}:{centre_x}:{centre_y}:s={base['s']}:fps={base['fps']}:d=1"
    if motion == "zoom_out":
        z = "z='max(1.28-0.0016*on,1.0)'"
        return f"zoompan={z}:{centre_x}:{centre_y}:s={base['s']}:fps={base['fps']}:d=1"
    if motion.startswith("pan"):
        z = "z='1.18'"
        if motion == "pan_right":
            x, y = f"x='(iw-iw/zoom)*on/{frames}'", centre_y
        elif motion == "pan_left":
            x, y = f"x='(iw-iw/zoom)*(1-on/{frames})'", centre_y
        elif motion == "pan_down":
            x, y = centre_x, f"y='(ih-ih/zoom)*on/{frames}'"
        else:  # pan_up
            x, y = centre_x, f"y='(ih-ih/zoom)*(1-on/{frames})'"
        return f"zoompan={z}:{x}:{y}:s={base['s']}:fps={base['fps']}:d=1"
    return None


def _render_text_overlay(
    text: str,
    target: Path,
    width: int,
    height: int,
    *,
    size: int = 42,
    position: str = "bottom-center",
    color: str = "#ffffff",
    background: str = "#0b1220",
) -> Path:
    """Render text onto a full-frame transparent PNG for FFmpeg overlay."""
    from PIL import Image, ImageDraw

    from .fonts import font_for

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    drawer = ImageDraw.Draw(canvas)
    font = font_for(size, bold=True)
    box = drawer.textbbox((0, 0), text, font=font)
    text_width, text_height = box[2] - box[0], box[3] - box[1]
    padding = max(16, size // 2)
    position = position.replace("title-", "")
    if "center" in position:
        x = (width - text_width) // 2
    elif position.endswith("left"):
        x = padding * 2
    else:
        x = width - text_width - padding * 2
    if "top" in position:
        y = padding * 2
    elif "center" in position:
        y = (height - text_height) // 2
    else:
        y = height - text_height - padding * 3
    fill = tuple(int(color.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    bg = tuple(int(background.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    drawer.rounded_rectangle(
        (x - padding, y - padding // 2, x + text_width + padding, y + text_height + padding // 2),
        radius=max(6, padding // 2),
        fill=(*bg, 190),
    )
    drawer.text((x, y), text, font=font, fill=(*fill, 255))
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target, "PNG")
    return target


def _filter_chain(motion_filter: str | None, fps: int) -> str:
    parts = ["scale=iw:ih:flags=lanczos"]
    if motion_filter:
        parts.append(motion_filter)
    parts.append(f"fps={fps}")
    parts.append("format=yuv420p")
    parts.append("setsar=1")
    return ",".join(parts)


# --------------------------------------------------------------------------
# segment builders
# --------------------------------------------------------------------------
def image_segment(
    source: Path,
    out: Path,
    *,
    duration: float,
    width: int,
    height: int,
    fps: int = 30,
    motion: str = "none",
    caption: str = "",
    caption_size: int = 34,
) -> Path:
    """Render a still image into a normalised video segment with motion."""
    duration = max(0.2, float(duration))
    frames = int(round(duration * fps))
    prepared = image_engine.prep_for_video(
        source, width, height, caption=caption, caption_size=caption_size
    )
    prepared_path = out.with_name(out.stem + "_src.png")
    prepared.save(prepared_path, "PNG")
    chain = _filter_chain(_motion_filter(motion, frames, width, height, fps), fps)
    run_ffmpeg(
        [
            # Explicit input framerate is essential: a looped still otherwise
            # defaults to 25 fps, and zoompan (which emits one frame per input
            # frame) would then produce a clip of duration*fps/25 seconds.
            "-loop", "1", "-framerate", str(fps), "-t", f"{duration:.3f}",
            "-i", str(prepared_path),
            "-vf", chain, "-r", str(fps), *VIDEO_CODEC_ARGS,
            "-t", f"{duration:.3f}", str(out),
        ]
    )
    prepared_path.unlink(missing_ok=True)
    return out


def video_segment(
    source: Path,
    out: Path,
    *,
    duration: float | None = None,
    start: float = 0.0,
    width: int,
    height: int,
    fps: int = 30,
    caption: str = "",
    caption_size: int = 34,
    speed: float = 1.0,
) -> Path:
    """Trim/scale/re-time an existing video into a normalised segment."""
    chain = [
        f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos",
        f"crop={width}:{height}",
    ]
    if speed and abs(speed - 1.0) > 1e-3:
        chain.append(f"setpts=PTS/{speed:.4f}")
    chain += [f"fps={fps}", "format=yuv420p", "setsar=1"]
    args = ["-ss", f"{max(0.0, start):.3f}"]
    if duration:
        args += ["-t", f"{max(0.2, float(duration)) / max(speed, 1e-3):.3f}"]
    args += ["-i", str(source)]
    overlay_path: Path | None = None
    if caption:
        overlay_path = out.with_name(out.stem + "_cap.png")
        image_engine.draw_text(
            image_engine.prep_for_video(source, width, height, caption=caption, caption_size=caption_size),
            text="",
        ).save(overlay_path)
        args += ["-i", str(overlay_path)]
        args += ["-filter_complex", f"[0:v]{','.join(chain)}[v0];[v0][1:v]overlay=0:0:format=auto[v]"]
        args += ["-map", "[v]"]
    else:
        args += ["-vf", ",".join(chain)]
    if duration:
        args += ["-t", f"{max(0.2, float(duration)):.3f}"]
    args += ["-an", "-r", str(fps), *VIDEO_CODEC_ARGS, str(out)]
    run_ffmpeg(args)
    if overlay_path:
        overlay_path.unlink(missing_ok=True)
    return out


# --------------------------------------------------------------------------
# combine
# --------------------------------------------------------------------------
def combine_segments(
    segments: list[Path],
    out: Path,
    *,
    transition: str = "cut",
    transition_duration: float = 0.6,
    durations: list[float] | None = None,
    fps: int = 30,
    width: int | None = None,
    height: int | None = None,
) -> Path:
    """Join normalised segments with either hard cuts or cross-transitions."""
    if not segments:
        raise RenderError("no segments to combine")
    if len(segments) == 1:
        shutil.copyfile(segments[0], out)
        return out

    xfade = normalise_transition(transition)
    if xfade is None or not durations:
        args: list[str] = []
        for segment in segments:
            args += ["-i", str(segment)]
        labels = "".join(f"[{i}:v]" for i in range(len(segments)))
        args += [
            "-filter_complex",
            f"{labels}concat=n={len(segments)}:v=1:a=0[v]",
            "-map", "[v]", "-r", str(fps), *VIDEO_CODEC_ARGS, str(out),
        ]
        run_ffmpeg(args)
        return out

    # Cross-fade chain. xfade's ``offset`` is the time (on the accumulated
    # timeline) where the next clip starts fading in, so it is always
    # "current accumulated duration - transition duration".
    td = max(0.1, float(transition_duration))
    offsets: list[float] = []
    accumulated = 0.0
    for index, duration in enumerate(durations):
        if index > 0:
            offsets.append(max(0.01, accumulated - td))
            accumulated -= td
        accumulated += duration
    chain: list[str] = []
    current = "0:v"
    for index in range(1, len(segments)):
        offset = offsets[index - 1] if index - 1 < len(offsets) else 0.1
        label = f"v{index}"
        chain.append(
            f"[{current}][{index}:v]xfade=transition={xfade}:duration={td:.3f}:offset={offset:.3f}[{label}]"
        )
        current = label
    args = []
    for segment in segments:
        args += ["-i", str(segment)]
    args += [
        "-filter_complex", ";".join(chain),
        "-map", f"[{current}]", "-r", str(fps), *VIDEO_CODEC_ARGS, str(out),
    ]
    run_ffmpeg(args)
    return out


def render_slideshow(
    clips: list[dict[str, Any]],
    out: Path,
    *,
    width: int = 1280,
    height: int = 720,
    fps: int = 30,
    transition: str = "fade",
    transition_duration: float = 0.6,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Render image clips into a single MP4.

    ``clips`` items: ``{"image": Path, "duration": float, "motion": str, "caption": str}``
    """
    work_dir = work_dir or out.parent
    work_dir.mkdir(parents=True, exist_ok=True)
    segments: list[Path] = []
    durations: list[float] = []
    for index, clip in enumerate(clips):
        duration = max(0.2, float(clip.get("duration") or 3.0))
        segment = work_dir / f"seg_{index:04d}.mp4"
        image_segment(
            Path(clip["image"]),
            segment,
            duration=duration,
            width=width,
            height=height,
            fps=fps,
            motion=clip.get("motion") or "none",
            caption=clip.get("caption") or "",
            caption_size=int(clip.get("caption_size") or 34),
        )
        segments.append(segment)
        durations.append(duration)
    combine_segments(
        segments,
        out,
        transition=transition,
        transition_duration=transition_duration,
        durations=durations,
        fps=fps,
        width=width,
        height=height,
    )
    for segment in segments:
        segment.unlink(missing_ok=True)
    return {
        "duration_s": round(sum(durations) - (len(durations) - 1) * max(0.0, transition_duration) if normalise_transition(transition) else sum(durations), 3),
        "clips": len(clips),
        "width": width,
        "height": height,
        "fps": fps,
        "transition": transition,
    }


def render_timeline(
    clips: list[dict[str, Any]],
    out: Path,
    *,
    width: int = 1280,
    height: int = 720,
    fps: int = 30,
    transition: str = "cut",
    transition_duration: float = 0.6,
    work_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Render a mixed image/video timeline (video editor renderer).

    ``clips`` items: ``{"path": Path, "kind": "image"|"video", "duration": float,
    "start": float, "motion": str, "caption": str, "speed": float}``
    """
    work_dir = work_dir or out.parent
    work_dir.mkdir(parents=True, exist_ok=True)
    segments: list[Path] = []
    durations: list[float] = []
    for index, clip in enumerate(clips):
        duration = max(0.2, float(clip.get("duration") or 3.0))
        segment = work_dir / f"tl_{index:04d}.mp4"
        kind = (clip.get("kind") or "image").lower()
        if kind == "video":
            video_segment(
                Path(clip["path"]),
                segment,
                duration=duration,
                start=float(clip.get("start") or 0.0),
                width=width,
                height=height,
                fps=fps,
                caption=clip.get("caption") or "",
                speed=float(clip.get("speed") or 1.0),
            )
        else:
            image_segment(
                Path(clip["path"]),
                segment,
                duration=duration,
                width=width,
                height=height,
                fps=fps,
                motion=clip.get("motion") or "none",
                caption=clip.get("caption") or "",
            )
        segments.append(segment)
        durations.append(duration)
    combine_segments(
        segments,
        out,
        transition=transition,
        transition_duration=transition_duration,
        durations=durations,
        fps=fps,
        width=width,
        height=height,
    )
    for segment in segments:
        segment.unlink(missing_ok=True)
    if normalise_transition(transition) and len(durations) > 1:
        total_duration = sum(durations) - (len(durations) - 1) * max(0.1, transition_duration)
    else:
        total_duration = sum(durations)
    return {
        "clips": len(clips),
        "duration_s": round(total_duration, 3),
        "width": width,
        "height": height,
        "fps": fps,
        "transition": transition,
    }


# --------------------------------------------------------------------------
# audio / post
# --------------------------------------------------------------------------
def media_duration(path: Path) -> float | None:
    ffmpeg = detect_ffmpeg()
    if not ffmpeg:
        return None
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        import re

        match = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", proc.stderr or "")
        if match:
            return round(
                int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3)), 3
            )
    except Exception:
        return None
    return None


def mux_audio(
    video: Path,
    audio: Path,
    out: Path,
    *,
    mode: str = "replace",
    audio_gain: float = 1.0,
    video_gain: float = 1.0,
    fade_out: float = 0.0,
) -> Path:
    """Attach narration/music to a rendered video (video stream is copied)."""
    args = ["-i", str(video), "-i", str(audio)]
    duration = media_duration(video)
    filters: list[str] = []
    if mode == "mix":
        filters.append(
            f"[0:a]volume={video_gain}[a0];[1:a]volume={audio_gain}[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a]"
            if _has_audio(video)
            else f"[1:a]volume={audio_gain}[a]"
        )
    else:
        # ``apad`` lives inside the graph: simple (-af) and complex (-filter_complex)
        # filtering cannot be combined on one stream.
        filters.append(f"[1:a]volume={audio_gain},apad[a]")
    if fade_out > 0 and duration:
        filters.append(f"[a]afade=t=out:st={max(0.0, duration - fade_out):.3f}:d={fade_out:.3f}[af]")
        label = "[af]"
    else:
        label = "[a]"
    args += ["-filter_complex", ";".join(filters), "-map", "0:v", "-map", label]
    args += ["-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    if duration:
        args += ["-t", f"{duration:.3f}"]
    else:
        args += ["-shortest"]
    args += [str(out)]
    run_ffmpeg(args)
    return out


def _has_audio(path: Path) -> bool:
    ffmpeg = detect_ffmpeg()
    if not ffmpeg:
        return False
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        return "Audio:" in (proc.stderr or "")
    except Exception:
        return False


def apply_video_ops(source: Path, ops: list[dict], out: Path, *, fps: int = 30) -> Path:
    """Single-pass video edit: trim, scale, crop, speed, fades, caption overlay."""
    args: list[str] = []
    filters: list[str] = []
    start = 0.0
    duration: float | None = None
    for op in ops or []:
        name = (op.get("op") or "").lower()
        if name == "trim":
            start = float(op.get("start") or 0.0)
            if op.get("end") is not None:
                duration = max(0.1, float(op["end"]) - start)
            elif op.get("duration") is not None:
                duration = max(0.1, float(op["duration"]))
        elif name == "speed":
            factor = max(0.1, min(8.0, float(op.get("factor") or 1.0)))
            filters.append(f"setpts=PTS/{factor:.4f}")
            if duration:
                duration = duration / factor
    if start:
        args += ["-ss", f"{start:.3f}"]
    if duration:
        args += ["-t", f"{duration:.3f}"]
    args += ["-i", str(source)]

    overlay_index = 1
    overlays: list[tuple[int, str]] = []
    for op in ops or []:
        name = (op.get("op") or "").lower()
        if name == "scale":
            width = int(op.get("width") or 1280)
            height = int(op.get("height") or 720)
            mode = (op.get("mode") or "cover").lower()
            if mode == "contain":
                filters.append(
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
                )
            else:
                filters.append(
                    f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
                    f"crop={width}:{height}"
                )
        elif name == "crop":
            filters.append(
                f"crop={int(op.get('width') or 0) or 'iw'}:{int(op.get('height') or 0) or 'ih'}:"
                f"{int(op.get('x') or 0)}:{int(op.get('y') or 0)}"
            )
        elif name == "fade":
            fade_in = float(op.get("in") or 0)
            fade_out = float(op.get("out") or 0)
            if fade_in:
                filters.append(f"fade=t=in:st=0:d={fade_in:.2f}")
            if fade_out and duration:
                filters.append(f"fade=t=out:st={max(0.0, duration - fade_out):.2f}:d={fade_out:.2f}")
            elif fade_out:
                filters.append(f"fade=t=out:st={max(0.0, (media_duration(source) or 1) - fade_out):.2f}:d={fade_out:.2f}")
        elif name in ("text", "caption"):
            text = str(op.get("text") or "")
            if not text:
                continue
            canvas_width = int(op.get("width") or 1280)
            canvas_height = int(op.get("height") or 720)
            caption_png = out.with_name(f"{out.stem}_cap{overlay_index}.png")
            _render_text_overlay(
                text,
                caption_png,
                canvas_width,
                canvas_height,
                size=int(op.get("size") or 42),
                position=str(op.get("position") or "bottom-center"),
                color=str(op.get("color") or "#ffffff"),
                background=str(op.get("background") or "#0b1220"),
            )
            args += ["-i", str(caption_png)]
            overlays.append((overlay_index, caption_png.name))
            overlay_index += 1
    filters += [f"fps={fps}", "format=yuv420p", "setsar=1"]

    if overlays:
        chain = ",".join(filters)
        graph = f"[0:v]{chain}[base]"
        current = "base"
        for index, _ in overlays:
            graph += f";[{current}][{index}:v]overlay=0:0:format=auto[v{index}]"
            current = f"v{index}"
        run_ffmpeg(args + ["-filter_complex", graph, "-map", f"[{current}]", *VIDEO_CODEC_ARGS, str(out)])
        for _, name in overlays:
            (out.parent / name).unlink(missing_ok=True)
    else:
        run_ffmpeg(args + ["-vf", ",".join(filters), *VIDEO_CODEC_ARGS, str(out)])
    return out


def extract_frame(source: Path, out: Path, *, at: float = 0.0, width: int = 1280) -> Path:
    run_ffmpeg(
        ["-ss", f"{max(0.0, at):.3f}", "-i", str(source), "-frames:v", "1", "-vf", f"scale={width}:-2", str(out)]
    )
    return out


def probe_streams(path: Path) -> dict[str, Any]:
    """Structural probe returning duration, video and audio stream info."""
    ffmpeg = detect_ffmpeg()
    if not ffmpeg:
        return {"duration_s": None, "has_video": False, "has_audio": False}
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=60
        )
        text = proc.stderr or ""
        return {
            "duration_s": media_duration(path),
            "has_video": "Video:" in text,
            "has_audio": "Audio:" in text,
            "raw": json.dumps({"lines": text.strip().splitlines()[-4:]}),
        }
    except Exception:
        return {"duration_s": None, "has_video": False, "has_audio": False}
