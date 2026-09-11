"""Real FFmpeg integration (§42.13).

Resolution order: FFMPEG_BINARY env → system `ffmpeg` on PATH → the static
binary bundled in the `imageio-ffmpeg` wheel. If none is available every
media operation fails with a clear, actionable message instead of pretending
to work.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional, Sequence

from app.core.config import settings
from app.core.errors import StudioError
from app.core.logging import get_logger

log = get_logger("media.ffmpeg")


class FFmpegUnavailable(StudioError):
    status_code = 503
    code = "ffmpeg_unavailable"

    def __init__(self) -> None:
        super().__init__(
            user_message="FFmpeg is not available on this machine.",
            detail="No ffmpeg binary found in FFMPEG_BINARY, PATH, or the imageio-ffmpeg wheel.",
            suggested_action="Install FFmpeg and add it to PATH, or run: pip install imageio-ffmpeg",
        )


def _bundled_candidate() -> Optional[str]:
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - depends on optional wheel
        log.debug("bundled_ffmpeg_unavailable", error=str(exc))
        return None


@lru_cache(maxsize=1)
def ffmpeg_path() -> str:
    candidates = [settings.FFMPEG_BINARY, shutil.which("ffmpeg"), _bundled_candidate()]
    for candidate in candidates:
        if candidate and Path(candidate).exists() and _is_executable(candidate):
            log.info("ffmpeg_resolved", path=candidate)
            return str(candidate)
    raise FFmpegUnavailable()


@lru_cache(maxsize=1)
def ffprobe_path() -> Optional[str]:
    candidates = [settings.FFPROBE_BINARY, shutil.which("ffprobe")]
    for candidate in candidates:
        if candidate and Path(candidate).exists() and _is_executable(candidate):
            return str(candidate)
    # The bundled static build ships ffmpeg only; probing falls back to `ffmpeg -i`.
    return None


def _is_executable(path: str) -> bool:
    p = Path(path)
    return p.is_file() and (p.stat().st_mode & 0o111 or str(path).lower().endswith(".exe"))


@lru_cache(maxsize=1)
def version() -> str:
    out = run(["-version"], timeout=30)
    for line in out.splitlines():
        if line.startswith("ffmpeg version"):
            return line.strip()
    return out.strip().splitlines()[0] if out.strip() else "unknown"


def run(args: Sequence[str], *, timeout: int = 1800, check: bool = True) -> str:
    exe = ffmpeg_path()
    cmd = [exe, "-hide_banner", "-loglevel", "error", "-y", *[str(a) for a in args]]
    log.debug("ffmpeg_run", cmd=" ".join(cmd)[:2000])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        log.error("ffmpeg_timeout", args=list(cmd), timeout=timeout)
        raise StudioError(
            "Media processing timed out.",
            detail=f"ffmpeg exceeded {timeout}s",
            suggested_action="Retry with a shorter clip or raise JOB_DEFAULT_TIMEOUT_SECONDS.",
        ) from exc
    if check and proc.returncode != 0:
        log.error("ffmpeg_failed", code=proc.returncode, stderr=proc.stderr[-4000:])
        raise StudioError(
            "Generation failed.",
            detail=f"ffmpeg exited {proc.returncode}: {proc.stderr[-2000:]}",
            suggested_action="Retry / Change model / Check GPU",
        )
    return (proc.stdout or "") + (proc.stderr or "")


# --------------------------------------------------------------------------- #
# Probing
# --------------------------------------------------------------------------- #

def probe(path: str | Path) -> dict:
    path = str(path)
    if ffprobe_path():
        cmd = [ffprobe_path(), "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode == 0 and proc.stdout:
            data = json.loads(proc.stdout)
            return _summarize(data)
    # Fallback: parse `ffmpeg -i` stderr (works with the bundled binary).
    proc = subprocess.run([ffmpeg_path(), "-hide_banner", "-i", path], capture_output=True, text=True, timeout=120)
    return _parse_ffmpeg_info(proc.stderr)


def _summarize(data: dict) -> dict:
    fmt = data.get("format", {}) or {}
    streams = data.get("streams", []) or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    info: dict = {
        "duration": float(fmt.get("duration") or 0.0),
        "size_bytes": int(fmt.get("size") or 0),
        "bitrate": int(fmt.get("bit_rate") or 0),
        "format": (fmt.get("format_name") or "").split(",")[0],
        "has_video": bool(video),
        "has_audio": bool(audio),
        "width": int(video.get("width") or 0) if video else None,
        "height": int(video.get("height") or 0) if video else None,
    }
    if video:
        fps = video.get("avg_frame_rate") or "0/0"
        try:
            num, den = fps.split("/")
            info["fps"] = round(float(num) / float(den), 3) if float(den) else 0.0
        except Exception:
            info["fps"] = 0.0
        info["video_codec"] = video.get("codec_name")
        nb = video.get("nb_frames")
        info["frames"] = int(nb) if nb and str(nb).isdigit() else None
    if audio:
        info["audio_codec"] = audio.get("codec_name")
        info["sample_rate"] = int(audio.get("sample_rate") or 0)
        info["channels"] = int(audio.get("channels") or 0)
    return info


def _parse_ffmpeg_info(stderr: str) -> dict:
    import re

    info: dict = {"duration": 0.0, "size_bytes": 0, "has_video": False, "has_audio": False}
    dur = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", stderr)
    if dur:
        h, m, s = dur.groups()
        info["duration"] = int(h) * 3600 + int(m) * 60 + float(s)
    v = re.search(r"Stream #\d+:\d+.*?Video: (\w+).*?, (\d+)x(\d+)", stderr)
    if v:
        info.update(has_video=True, video_codec=v.group(1), width=int(v.group(2)), height=int(v.group(3)))
    a = re.search(r"Stream #\d+:\d+.*?Audio: (\w+).*?, (\d+) Hz", stderr)
    if a:
        info.update(has_audio=True, audio_codec=a.group(1), sample_rate=int(a.group(2)))
    fps = re.search(r"([\d.]+) fps", stderr)
    if fps:
        info["fps"] = float(fps.group(1))
    return info


# --------------------------------------------------------------------------- #
# Video operations
# --------------------------------------------------------------------------- #

def frames_to_video(
    frame_dir: str | Path,
    out_path: str | Path,
    *,
    fps: int = 24,
    pattern: str = "frame_%05d.png",
    audio: Optional[str | Path] = None,
    crf: int = 20,
    codec: str = "libx264",
) -> str:
    out_path = str(out_path)
    args = ["-framerate", str(fps), "-i", str(Path(frame_dir) / pattern)]
    if audio:
        args += ["-i", str(audio)]
    args += ["-c:v", codec, "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p"]
    if audio:
        args += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
    args += [out_path]
    run(args)
    return out_path


def image_to_video(
    image: str | Path,
    out_path: str | Path,
    *,
    duration: float = 5.0,
    fps: int = 24,
    zoom: Optional[float] = None,
    audio: Optional[str | Path] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> str:
    """Animate a still with an optional Ken-Burns style zoom (real filter chain)."""
    filters = [f"fps={fps}"]
    scale = f"{width}:{height}" if width and height else None
    if zoom and zoom != 1.0:
        steps = max(2, int(duration * fps))
        filters.append(f"scale=iw*4:ih*4,zoompan=z='min(zoom+{(zoom - 1) / max(steps, 1):.6f},{zoom})'"
                       ":d=1:s=1280x720:fps={fps}".format(fps=fps))
    elif scale:
        filters.append(f"scale={scale}:flags=lanczos")
    filters.append("format=yuv420p")
    args = ["-loop", "1", "-t", f"{duration:.3f}", "-i", str(image)]
    if audio:
        args += ["-i", str(audio), "-shortest"]
    args += ["-vf", ",".join(filters), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-movflags", "+faststart"]
    if audio:
        args += ["-c:a", "aac", "-b:a", "192k"]
    args += [str(out_path)]
    run(args)
    return str(out_path)


def extract_frames(video: str | Path, out_dir: str | Path, *, fps: int = 24, max_frames: int = 600) -> list[str]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    run(["-i", str(video), "-vf", f"fps={fps}", "-frames:v", str(max_frames),
         "-start_number", "0", str(out_dir / "frame_%05d.png")])
    return sorted(str(p) for p in out_dir.glob("frame_*.png"))


def thumbnail(video: str | Path, out_path: str | Path, *, time: float = 0.0, width: int = 640) -> str:
    run(["-ss", f"{max(time, 0):.3f}", "-i", str(video), "-frames:v", "1",
         "-vf", f"scale={width}:-2:flags=lanczos", "-q:v", "3", str(out_path)])
    return str(out_path)


def make_proxy(video: str | Path, out_path: str | Path, *, height: int = 360, crf: int = 30) -> str:
    """Low-bitrate proxy so the browser never loads a full-size master (§38)."""
    run(["-i", str(video), "-vf", f"scale=-2:{height}:flags=bilinear", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", str(crf), "-an", "-movflags", "+faststart", str(out_path)])
    return str(out_path)


def concat(paths: Sequence[str | Path], out_path: str | Path, *, with_audio: bool = True) -> str:
    if not paths:
        raise StudioError("Nothing to concatenate.", suggested_action="Add at least one clip.")
    list_file = Path(out_path).with_suffix(".concat.txt")
    list_file.write_text("".join(f"file '{Path(p).as_posix()}'\n" for p in paths), encoding="utf-8")
    try:
        run(["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_path)])
    except StudioError:
        # Mixed codecs/sizes: re-encode through the concat filter instead.
        inputs: list[str] = []
        for p in paths:
            inputs += ["-i", str(p)]
        streams = "".join(f"[{i}:v:0][{i}:a:0]" if with_audio else f"[{i}:v:0]" for i in range(len(paths)))
        filter_str = f"{streams}concat=n={len(paths)}:v=1:a={1 if with_audio else 0}[v]" + ("[a]" if with_audio else "")
        args = inputs + ["-filter_complex", filter_str, "-map", "[v]"]
        if with_audio:
            args += ["-map", "[a]"]
        args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
                 "-c:a", "aac", "-b:a", "160k", str(out_path)]
        run(args)
    finally:
        list_file.unlink(missing_ok=True)
    return str(out_path)


def xfade(a: str | Path, b: str | Path, out_path: str | Path, *, transition: str = "fade", duration: float = 0.5) -> str:
    allowed = {"fade", "wipeleft", "wiperight", "slideup", "slidedown", "dissolve", "pixelize", "radial", "circleopen"}
    if transition not in allowed:
        transition = "fade"
    run(["-i", str(a), "-i", str(b), "-filter_complex",
         f"[0:v][1:v]xfade=transition={transition}:duration={duration}:offset=0,format=yuv420p",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", str(out_path)])
    return str(out_path)


def trim(video: str | Path, out_path: str | Path, *, start: float = 0.0, duration: Optional[float] = None) -> str:
    args = ["-ss", f"{max(start, 0):.3f}"]
    if duration:
        args += ["-t", f"{duration:.3f}"]
    args += ["-i", str(video), "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
             "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out_path)]
    run(args)
    return str(out_path)


def set_speed(video: str | Path, out_path: str | Path, *, factor: float = 1.0) -> str:
    factor = max(0.1, min(8.0, float(factor)))
    run(["-i", str(video), "-filter_complex",
         f"[0:v]setpts={1 / factor:.4f}*PTS[v];[0:a]atempo={factor:.4f}[a]",
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
         "-c:a", "aac", "-b:a", "160k", str(out_path)])
    return str(out_path)


def fade_in_out(video: str | Path, out_path: str | Path, *, fade_in: float = 0.0, fade_out: float = 0.0,
                total: Optional[float] = None) -> str:
    info = probe(video)
    dur = total or info.get("duration") or 0.0
    filters = []
    if fade_in > 0:
        filters.append(f"fade=t=in:st=0:d={fade_in}")
    if fade_out > 0 and dur:
        filters.append(f"fade=t=out:st={max(dur - fade_out, 0):.3f}:d={fade_out}")
    run(["-i", str(video), "-vf", ",".join(filters) or "copy", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "21", "-c:a", "copy", str(out_path)])
    return str(out_path)


def scale_pad(video: str | Path, out_path: str | Path, *, width: int, height: int) -> str:
    """Fit into the target aspect without cropping, padding with a blurred copy."""
    run(["-i", str(video), "-filter_complex",
         f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
         f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black[v]",
         "-map", "[v]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "copy", str(out_path)])
    return str(out_path)


def crop(video: str | Path, out_path: str | Path, *, width: int, height: int, x: int = 0, y: int = 0) -> str:
    run(["-i", str(video), "-vf", f"crop={width}:{height}:{x}:{y}", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "21", "-c:a", "copy", str(out_path)])
    return str(out_path)


def mux_audio(video: str | Path, audio: str | Path, out_path: str | Path, *, music_gain_db: float = -18.0,
              duck: bool = True, original_audio: bool = True) -> str:
    """Replace or mix audio. With ducking the original track is side-chained under the new one."""
    if original_audio:
        filt = (f"[1:a]volume={music_gain_db}dB[b];[0:a][b]sidechaincompress=threshold=0.05:ratio=6[a]"
                if duck else f"[0:a]volume=0.6dB[a0];[1:a]volume={music_gain_db}dB[a1];[a0][a1]amix=inputs=2:duration=first:dropout_transition=2[a]")
        run(["-i", str(video), "-i", str(audio), "-filter_complex", filt,
             "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             "-shortest", str(out_path)])
    else:
        run(["-i", str(video), "-i", str(audio), "-map", "0:v", "-map", "1:a",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(out_path)])
    return str(out_path)


def mix_audio(tracks: Sequence[str | Path], out_path: str | Path,
              *, volumes: Optional[Sequence[float]] = None) -> str:
    if not tracks:
        raise StudioError("No audio tracks to mix.", suggested_action="Add at least one audio track.")
    volumes = list(volumes or [1.0] * len(tracks))
    inputs: list[str] = []
    for i, t in enumerate(tracks):
        inputs += ["-i", str(t)]
    parts = "".join(f"[{i}:a]volume={volumes[i]:.3f}[a{i}];" for i in range(len(tracks)))
    amix = "".join(f"[a{i}]" for i in range(len(tracks)))
    filt = f"{parts}{amix}amix=inputs={len(tracks)}:duration=longest:dropout_transition=2,alimiter=limit=0.95[a]"
    run([*inputs, "-filter_complex", filt, "-map", "[a]", "-c:a", "aac", "-b:a", "192k", str(out_path)])
    return str(out_path)


def burn_subtitles(video: str | Path, srt: str | Path, out_path: str | Path) -> str:
    srt_path = Path(srt)
    sub_arg = str(srt_path).replace("\\", "/").replace(":", "\\:")
    run(["-i", str(video), "-vf", f"subtitles=filename='{sub_arg}'", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "21", "-c:a", "copy", str(out_path)])
    return str(out_path)


def draw_text(video: str | Path, out_path: str | Path, *, text: str, x: str = "(w-text_w)/2", y: str = "h-th-60",
              size: int = 48, color: str = "white", start: float = 0.0, duration: Optional[float] = None) -> str:
    safe = text.replace(":", r"\:").replace("'", r"\'")
    draw = f"drawtext=text='{safe}':fontcolor={color}:fontsize={size}:x={x}:y={y}"
    if duration:
        draw += f":enable='between(t,{start},{start + duration})'"
    run(["-i", str(video), "-vf", draw, "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
         "-c:a", "copy", str(out_path)])
    return str(out_path)


def extend_video(video: str | Path, out_path: str | Path, *, extra_seconds: float = 3.0) -> str:
    """Extend by holding the final frame with a slow push-in on the last moment."""
    info = probe(video)
    dur = max(info.get("duration") or 0.0, 0.1)
    extra = max(0.1, float(extra_seconds))
    hold_start = max(dur - 0.5, 0.0)
    run(["-i", str(video), "-filter_complex",
         f"[0:v]split[a][b];[b]trim=start={hold_start:.3f}:end={dur:.3f},setpts=PTS-STARTPTS,"
         f"scale=iw*1.06:ih*1.06,crop=iw/1.06:ih/1.06,tpad=stop_mode=clone:stop_duration={extra:.3f}[tail];"
         f"[a][tail]concat=n=2:v=1:a=0[v]",
         "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
         "-c:a", "aac", "-b:a", "160k", str(out_path)])
    return str(out_path)


def interpolate(video: str | Path, out_path: str | Path, *, target_fps: int = 48) -> str:
    """Frame interpolation via ffmpeg's motion-compensated minterpolate filter."""
    run(["-i", str(video), "-vf",
         f"minterpolate=fps={int(target_fps)}:mi_mode=mci:mc_mode=aobmc:vsbmc=1:search_param=16",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "copy", str(out_path)],
        timeout=3600)
    return str(out_path)


def to_gif(video: str | Path, out_path: str | Path, *, fps: int = 12, width: int = 480) -> str:
    run(["-i", str(video), "-vf", f"fps={fps},scale={width}:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
         "-loop", "0", str(out_path)])
    return str(out_path)


def capabilities() -> dict:
    try:
        exe = ffmpeg_path()
    except StudioError:
        return {"available": False, "path": None, "version": None, "encoders": [], "source": None}
    out = run(["-encoders"], check=False, timeout=60)
    encoders = sorted({line.split()[1] for line in out.splitlines()[1:] if len(line.split()) > 1})
    source = "env" if settings.FFMPEG_BINARY else ("path" if shutil.which("ffmpeg") else "bundled")
    return {
        "available": True,
        "path": exe,
        "version": version(),
        "source": source,
        "encoders": encoders,
        "formats": ["mp4", "webm", "mov", "gif"] if "libx264" in encoders else [],
    }
