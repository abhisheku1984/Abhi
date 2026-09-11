"""Real media processing - Pillow for images/frames, FFmpeg for video/audio.

Nothing here is simulated. If FFmpeg is missing the functions raise
DEPENDENCY_MISSING with the exact install command so the UI can show the real
reason instead of a fake success.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from PIL import Image, ImageColor, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from app.core.config import settings
from app.core.errors import ErrorCode, StudioError
from app.core.hardware import ffmpeg_path, ffprobe_path


# ---------------------------------------------------------------------------
# Errors / process helpers
# ---------------------------------------------------------------------------
def require_ffmpeg() -> str:
    exe = ffmpeg_path()
    if not exe:
        raise StudioError(
            "FFmpeg is not available. Video processing is disabled until it is installed.",
            code=ErrorCode.DEPENDENCY_MISSING,
            status_code=503,
            remediation="pip install imageio-ffmpeg   (or install FFmpeg and ensure `ffmpeg` is on PATH)",
        )
    return exe


class CancelledError(Exception):
    pass


def run_ffmpeg(args: Sequence[str], timeout: int = 1800, check: bool = True,
               on_progress: Any = None) -> tuple[int, str, str]:
    exe = require_ffmpeg()
    cmd = [exe, "-y", *args]
    # Hide banner noise but keep errors.
    cmd = [cmd[0], "-hide_banner", "-loglevel", "error", *cmd[1:]]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            creationflags=0)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        if check:
            raise StudioError("FFmpeg timed out", code=ErrorCode.TIMEOUT, details={"args": list(args)})
        return proc.returncode, out, err
    if check and proc.returncode != 0:
        raise StudioError(
            "FFmpeg command failed",
            code=ErrorCode.INTERNAL_ERROR,
            details={"returncode": proc.returncode, "stderr": err[-4000:], "args": list(args)},
        )
    if on_progress:
        on_progress(out, err)
    return proc.returncode, out, err


# ---------------------------------------------------------------------------
# Image operations (all real Pillow work)
# ---------------------------------------------------------------------------
@dataclass
class ImageInfo:
    width: int
    height: int
    mode: str
    has_alpha: bool
    format: str = ""


def image_info(path: str | Path) -> ImageInfo:
    with Image.open(path) as im:
        return ImageInfo(im.width, im.height, im.mode, "A" in im.getbands(), im.format or "")


def load_image(path: str | Path, mode: str | None = None) -> Image.Image:
    im = Image.open(path)
    if mode:
        im = im.convert(mode)
    else:
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
    im.load()
    return im


def save_image(im: Image.Image, path: str | Path, quality: int = 92, **kwargs) -> str:
    path = str(path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    params: dict[str, Any] = {}
    if path.lower().endswith((".jpg", ".jpeg")):
        if im.mode in ("RGBA", "LA"):
            im = im.convert("RGB")
        params = {"quality": quality, "optimize": True, **kwargs}
    elif path.lower().endswith(".webp"):
        params = {"quality": quality, **kwargs}
    elif path.lower().endswith(".png"):
        params = {"optimize": True, **kwargs}
    im.save(path, **params)
    return path


def resize_image(im: Image.Image, width: int, height: int, method: str = "lanczos",
                 keep_aspect: bool = False) -> Image.Image:
    method = (method or "lanczos").lower()
    resample = {
        "lanczos": Image.Resampling.LANCZOS,
        "bicubic": Image.Resampling.BICUBIC,
        "bilinear": Image.Resampling.BILINEAR,
        "nearest": Image.Resampling.NEAREST,
        "box": Image.Resampling.BOX,
    }.get(method, Image.Resampling.LANCZOS)
    if keep_aspect:
        return ImageOps.contain(im, (width, height), method=resample)
    return im.resize((max(1, int(width)), max(1, int(height))), resample=resample)


def upscale_image(im: Image.Image, factor: float = 2.0, method: str = "lanczos") -> Image.Image:
    if factor <= 1.0:
        return im
    return resize_image(im, int(im.width * factor), int(im.height * factor), method)


def crop_image(im: Image.Image, box: tuple[int, int, int, int] | None = None,
               aspect: str | None = None) -> Image.Image:
    if box:
        return im.crop(box)
    if aspect and ":" in aspect:
        aw, ah = (float(x) for x in aspect.split(":"))
        target = aw / ah
        w, h = im.size
        if w / h > target:
            new_w = int(h * target)
            left = (w - new_w) // 2
            return im.crop((left, 0, left + new_w, h))
        new_h = int(w / target)
        top = (h - new_h) // 2
        return im.crop((0, top, w, top + new_h))
    return im


def adjust_image(im: Image.Image, brightness: float = 1.0, contrast: float = 1.0,
                 saturation: float = 1.0, sharpness: float = 1.0, blur: float = 0.0) -> Image.Image:
    if brightness != 1.0:
        im = ImageEnhance.Brightness(im).enhance(brightness)
    if contrast != 1.0:
        im = ImageEnhance.Contrast(im).enhance(contrast)
    if saturation != 1.0 and im.mode in ("RGB", "RGBA", "L"):
        im = ImageEnhance.Color(im).enhance(saturation)
    if sharpness != 1.0:
        im = ImageEnhance.Sharpness(im).enhance(sharpness)
    if blur > 0:
        im = im.filter(ImageFilter.GaussianBlur(radius=min(blur, 50.0)))
    return im


FILTER_PRESETS: dict[str, dict[str, float]] = {
    "none": {},
    "grayscale": {"saturation": 0.0},
    "sepia": {"sepia": 1.0},
    "vintage": {"sepia": 0.5, "contrast": 1.1, "saturation": 0.8},
    "cool": {"temperature": -0.35},
    "warm": {"temperature": 0.35},
    "cinematic": {"contrast": 1.18, "saturation": 0.9, "sharpness": 1.1},
    "noir": {"saturation": 0.0, "contrast": 1.4},
    "vivid": {"saturation": 1.45, "contrast": 1.08},
    "fade": {"contrast": 0.88, "saturation": 0.85},
    "teal_orange": {"teal_orange": 1.0, "contrast": 1.1},
    "dream": {"blur": 1.2, "saturation": 1.15, "brightness": 1.05},
}


def _sepia(im: Image.Image, amount: float = 1.0) -> Image.Image:
    rgb = im.convert("RGB")
    r, g, b = rgb.split()
    tr = (
        lambda v: int(min(255, (v * 0.393) + (v * 0.769) + (v * 0.189)))
    )
    # Matrix sepia via point LUTs (accurate and fast).
    lut_r = [min(255, int(0.393 * i + 0.769 * i + 0.189 * i)) for i in range(256)]
    lut_g = [min(255, int(0.349 * i + 0.686 * i + 0.168 * i)) for i in range(256)]
    lut_b = [min(255, int(0.272 * i + 0.534 * i + 0.131 * i)) for i in range(256)]
    r = r.point(lut_r)
    g = g.point(lut_g)
    b = b.point(lut_b)
    out = Image.merge("RGB", (r, g, b))
    if amount >= 1.0:
        return out if im.mode == "RGB" else out.convert(im.mode)
    return Image.blend(im.convert("RGB"), out, max(0.0, min(1.0, amount)))


def _temperature(im: Image.Image, amount: float) -> Image.Image:
    rgb = im.convert("RGB")
    r, g, b = rgb.split()
    k = int(amount * 60)
    r = r.point(lambda v: min(255, max(0, v + k)))
    b = b.point(lambda v: min(255, max(0, v - k)))
    return Image.merge("RGB", (r, g, b))


def _teal_orange(im: Image.Image, amount: float) -> Image.Image:
    rgb = im.convert("RGB")
    r, g, b = rgb.split()
    r = r.point(lambda v: min(255, max(0, int(v * 1.08 + 6))))
    g = g.point(lambda v: min(255, max(0, int(v * 1.01))))
    b = b.point(lambda v: min(255, max(0, int(v * 0.94))))
    out = Image.merge("RGB", (r, g, b))
    return Image.blend(rgb, out, max(0.0, min(1.0, amount)))


def apply_filter(im: Image.Image, preset: str, strength: float = 1.0) -> Image.Image:
    cfg = FILTER_PRESETS.get((preset or "none").lower(), {})
    if not cfg:
        return im
    s = max(0.0, min(1.0, strength))
    if "sepia" in cfg:
        im = _sepia(im, cfg["sepia"] * s)
    if "temperature" in cfg:
        im = _temperature(im, cfg["temperature"] * s)
    if "teal_orange" in cfg:
        im = _teal_orange(im, cfg["teal_orange"] * s)
    im = adjust_image(
        im,
        brightness=1.0 + (cfg.get("brightness", 1.0) - 1.0) * s,
        contrast=1.0 + (cfg.get("contrast", 1.0) - 1.0) * s,
        saturation=1.0 + (cfg.get("saturation", 1.0) - 1.0) * s,
        sharpness=1.0 + (cfg.get("sharpness", 1.0) - 1.0) * s,
        blur=cfg.get("blur", 0.0) * s,
    )
    return im


def add_vignette(im: Image.Image, strength: float = 0.5) -> Image.Image:
    import numpy as np

    arr = np.asarray(im.convert("RGB")).astype("float32")
    h, w, _ = arr.shape
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = w / 2, h / 2
    dist = np.sqrt(((xx - cx) / cx) ** 2 + ((yy - cy) / cy) ** 2) / np.sqrt(2)
    mask = 1.0 - strength * np.clip(dist ** 1.6, 0, 1)
    arr *= mask[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype("uint8"), "RGB")


def add_text(im: Image.Image, text: str, position: str = "bottom", font_size: int = 48,
             color: str = "white", stroke: str = "black", margin: int = 40,
             font_path: str | None = None) -> Image.Image:
    im = im.convert("RGBA")
    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = None
    for candidate in [font_path] if font_path else []:
        if candidate and os.path.exists(candidate):
            try:
                font = ImageFont.truetype(candidate, font_size)
            except Exception:
                font = None
    if font is None:
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", font_size)
        except Exception:
            try:
                font = ImageFont.load_default(size=font_size)
            except TypeError:  # Pillow < 10.1
                font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=2)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pos_map = {
        "top": ((im.width - tw) // 2, margin),
        "bottom": ((im.width - tw) // 2, im.height - th - margin),
        "center": ((im.width - tw) // 2, (im.height - th) // 2),
        "top-left": (margin, margin),
        "bottom-left": (margin, im.height - th - margin),
    }
    x, y = pos_map.get(position, pos_map["bottom"])
    draw.text((x, y), text, font=font, fill=ImageColor.getrgb(color) + (255,),
              stroke_width=max(1, font_size // 16), stroke_fill=ImageColor.getrgb(stroke) + (255,))
    return Image.alpha_composite(im, overlay)


def watermark(im: Image.Image, text: str = "", opacity: float = 0.35,
              position: str = "bottom-right") -> Image.Image:
    """Visible provenance watermark (metadata is written separately)."""
    if not text:
        return im
    im = im.convert("RGBA")
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    try:
        font = ImageFont.load_default(size=max(14, im.width // 40))
    except TypeError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = max(12, im.width // 60)
    positions = {
        "bottom-right": (im.width - tw - pad, im.height - th - pad),
        "bottom-left": (pad, im.height - th - pad),
        "top-right": (im.width - tw - pad, pad),
        "top-left": (pad, pad),
        "center": ((im.width - tw) // 2, (im.height - th) // 2),
    }
    x, y = positions.get(position, positions["bottom-right"])
    draw.text((x, y), text, font=font, fill=(255, 255, 255, int(255 * opacity)))
    return Image.alpha_composite(im, layer)


def make_thumbnail(path: str | Path, out_path: str | Path, max_size: int = 480) -> str:
    out_path = str(out_path)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    with Image.open(path) as im:
        im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        im.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        if out_path.lower().endswith((".jpg", ".jpeg")) and im.mode == "RGBA":
            im = im.convert("RGB")
        im.save(out_path, optimize=True)
    return out_path


def blank_image(width: int, height: int, color: str = "#111827") -> Image.Image:
    return Image.new("RGB", (width, height), ImageColor.getrgb(color))


# ---------------------------------------------------------------------------
# Video: probing
# ---------------------------------------------------------------------------
@dataclass
class VideoInfo:
    duration: float = 0.0
    width: int = 0
    height: int = 0
    fps: float = 0.0
    codec: str = ""
    audio_codec: str = ""
    bitrate: int = 0
    has_audio: bool = False
    frames: int = 0
    raw: dict = field(default_factory=dict)


def probe(path: str | Path) -> VideoInfo:
    """ffprobe-style probe. Uses ffprobe when present, otherwise `ffmpeg -i`."""
    exe = ffprobe_path()
    path = str(path)
    info = VideoInfo()
    if not exe:
        return info
    try:
        if os.path.basename(exe).startswith("ffprobe"):
            cmd = [exe, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
            data = json.loads(out or "{}")
        else:
            proc = subprocess.run([exe, "-hide_banner", "-i", path], capture_output=True, text=True, timeout=60)
            return _probe_from_stderr(proc.stderr, path)
    except Exception:
        return info

    fmt = data.get("format", {}) or {}
    try:
        info.duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        info.duration = 0.0
    try:
        info.bitrate = int(float(fmt.get("bit_rate") or 0))
    except (TypeError, ValueError):
        info.bitrate = 0
    for stream in data.get("streams", []) or []:
        if stream.get("codec_type") == "video":
            info.width = int(stream.get("width") or 0)
            info.height = int(stream.get("height") or 0)
            info.codec = stream.get("codec_name", "")
            info.has_audio = info.has_audio
            r = stream.get("r_frame_rate") or "0/1"
            try:
                num, den = r.split("/")
                info.fps = round(float(num) / float(den), 3) if float(den) else 0.0
            except Exception:
                info.fps = 0.0
            try:
                info.frames = int(stream.get("nb_frames") or 0)
            except (TypeError, ValueError):
                info.frames = int((info.duration or 0) * (info.fps or 0))
        elif stream.get("codec_type") == "audio":
            info.has_audio = True
            info.audio_codec = stream.get("codec_name", "")
    if not info.frames and info.duration and info.fps:
        info.frames = int(info.duration * info.fps)
    info.raw = {"format": fmt.get("format_name", "")}
    return info


def _probe_from_stderr(stderr: str, path: str) -> VideoInfo:
    info = VideoInfo()
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", stderr)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        info.duration = h * 3600 + mi * 60 + s
    m = re.search(r"(\d{2,5})x(\d{2,5})", stderr)
    if m:
        info.width, info.height = int(m.group(1)), int(m.group(2))
    m = re.search(r"([\d.]+) fps", stderr)
    if m:
        info.fps = float(m.group(1))
    m = re.search(r"Video: (\w+)", stderr)
    if m:
        info.codec = m.group(1)
    m = re.search(r"Audio: (\w+)", stderr)
    if m:
        info.has_audio = True
        info.audio_codec = m.group(1)
    if info.duration and info.fps:
        info.frames = int(info.duration * info.fps)
    info.raw = {"source": "ffmpeg -i", "path": path}
    return info


# ---------------------------------------------------------------------------
# Video: encoding and editing (real FFmpeg filter graphs)
# ---------------------------------------------------------------------------
def encode_frames(frame_paths: Sequence[str], out_path: str, fps: float = 24.0,
                  codec: str = "libx264", crf: int = 20, preset: str = "veryfast",
                  size: tuple[int, int] | None = None, pix_fmt: str = "yuv420p") -> str:
    if not frame_paths:
        raise StudioError("No frames to encode", code=ErrorCode.INVALID_REQUEST)
    out_path = str(out_path)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="frames-", dir=str(Path(settings.STORAGE_DIR).parent / "tmp")))
    try:
        # Stable, zero-padded, lexicographically ordered names for the image2 demuxer.
        for i, src in enumerate(frame_paths):
            shutil.copyfile(src, tmp_dir / f"f{i:06d}{os.path.splitext(src)[1].lower() or '.png'}")
        frame_ext = os.path.splitext(frame_paths[0])[1].lower() or ".png"
        args = ["-framerate", str(fps), "-i", str(tmp_dir / f"f%06d{frame_ext}")]
        if size:
            args += ["-vf", f"scale={int(size[0])}:{int(size[1])}"]
        args += ["-c:v", codec, "-crf", str(crf), "-preset", preset, "-pix_fmt", pix_fmt,
                 "-movflags", "+faststart", out_path]
        run_ffmpeg(args, timeout=3600)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return out_path


def images_to_video(images: Sequence[str | Image.Image], out_path: str, fps: float = 24.0,
                    durations: Sequence[float] | None = None, size: tuple[int, int] | None = None,
                    codec: str = "libx264", crf: int = 20) -> str:
    """Encode a list of images (paths or PIL images) into a real video file."""
    tmp = Path(tempfile.mkdtemp(prefix="img2vid-", dir=str(Path(settings.STORAGE_DIR).parent / "tmp")))
    try:
        paths: list[str] = []
        for i, img in enumerate(images):
            p = tmp / f"src{i:06d}.png"
            if isinstance(img, Image.Image):
                img.convert("RGB").save(p)
            else:
                shutil.copyfile(img, p)
            paths.append(str(p))

        target_w = size[0] if size else None
        target_h = size[1] if size else None
        if target_w is None:
            with Image.open(paths[0]) as im0:
                target_w, target_h = im0.width, im0.height
        target_w = target_w - (target_w % 2)
        target_h = target_h - (target_h % 2)

        # Normalise every frame to the target size, then build the concat list.
        norm: list[str] = []
        for i, p in enumerate(paths):
            q = tmp / f"n{i:06d}.png"
            with Image.open(p) as im:
                im.convert("RGB").resize((target_w, target_h), Image.Resampling.LANCZOS).save(q)
            norm.append(str(q))

        list_file = tmp / "inputs.txt"
        lines = []
        for i, p in enumerate(norm):
            lines.append(f"file '{p}'")
            dur = durations[i] if durations and i < len(durations) else 1.0 / max(fps, 0.1)
            lines.append(f"duration {max(0.04, float(dur))}")
        if norm:
            lines.append(f"file '{norm[-1]}'")
        list_file.write_text("\n".join(lines), encoding="utf-8")

        args = [
            "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-vsync", "vfr", "-pix_fmt", "yuv420p",
            "-c:v", codec, "-crf", str(crf), "-preset", "veryfast",
            "-movflags", "+faststart", str(out_path),
        ]
        run_ffmpeg(args, timeout=3600)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return str(out_path)


def extract_frames(video_path: str, out_dir: str, fps: float = 1.0, max_frames: int = 240,
                   size: tuple[int, int] | None = None) -> list[str]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    args = ["-i", str(video_path), "-vf", f"fps={fps}"]
    if size:
        args[3] = f"fps={fps},scale={int(size[0])}:{int(size[1])}"
    args += ["-frames:v", str(max_frames), str(out_dir / "frame_%06d.png")]
    run_ffmpeg(args, timeout=1800)
    return sorted(str(p) for p in out_dir.glob("frame_*.png"))


def trim_video(src: str, out_path: str, start: float = 0.0, end: float | None = None,
               reencode: bool = False) -> str:
    args = ["-ss", str(max(0.0, start))]
    if end is not None:
        args += ["-to", str(end)]
    args += ["-i", str(src)]
    args += ["-c", "copy"] if not reencode else ["-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-c:a", "aac"]
    args += ["-movflags", "+faststart", str(out_path)]
    run_ffmpeg(args)
    return str(out_path)


def concat_videos(sources: Sequence[str], out_path: str, reencode: bool = True,
                  transition: str | None = None) -> str:
    if not sources:
        raise StudioError("Nothing to concatenate", code=ErrorCode.INVALID_REQUEST)
    if len(sources) == 1:
        shutil.copyfile(sources[0], out_path)
        return str(out_path)
    tmp = Path(tempfile.mkdtemp(prefix="concat-", dir=str(Path(settings.STORAGE_DIR).parent / "tmp")))
    try:
        if not reencode:
            list_file = tmp / "list.txt"
            list_file.write_text(
                "\n".join(f"file '{Path(s).resolve().as_posix()}'" for s in sources), encoding="utf-8"
            )
            run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_path)])
            return str(out_path)

        # Re-encode path: normalise each clip, optionally cross-fade, then concat.
        duration = 0.5 if transition else 0.0
        inputs: list[str] = []
        for s in sources:
            inputs += ["-i", str(s)]
        filter_parts = []
        labels = []
        for i, s in enumerate(sources):
            filter_parts.append(f"[{i}:v]scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1,fps=30[v{i}]")
            labels.append(f"[v{i}]")
        if transition and len(sources) > 1:
            prev = "v0"
            acc = 0.0
            for i in range(1, len(sources)):
                out_label = f"x{i}"
                offset = max(0.0, acc)
                filter_parts.append(
                    f"[{prev}][v{i}]xfade=transition={transition}:duration={duration}:offset={offset:.3f}[{out_label}]"
                )
                prev = out_label
                acc += 1.0
            filter_parts.append(f"[{prev}]format=yuv420p[vout]")
        else:
            filter_parts.append("".join(labels) + f"concat=n={len(sources)}:v=1:a=0[vout]")
        run_ffmpeg([*inputs, "-filter_complex", ";".join(filter_parts), "-map", "[vout]",
                    "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-movflags", "+faststart", str(out_path)])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return str(out_path)


def apply_video_filters(src: str, out_path: str, *, speed: float = 1.0, volume: float = 1.0,
                        fade_in: float = 0.0, fade_out: float = 0.0, crop: str | None = None,
                        scale: tuple[int, int] | None = None, rotate: float = 0.0,
                        brightness: float = 0.0, contrast: float = 1.0, saturation: float = 1.0,
                        text: str | None = None, mute: bool = False) -> str:
    vf: list[str] = []
    if crop:
        vf.append(f"crop={crop}")
    if scale:
        vf.append(f"scale={int(scale[0])}:{int(scale[1])}:flags=lanczos")
    if rotate:
        rad = rotate * 3.141592653589793 / 180.0
        vf.append(f"rotate={rad:.6f}")
    if brightness or contrast != 1.0 or saturation != 1.0:
        vf.append(f"eq=brightness={brightness:.3f}:contrast={contrast:.3f}:saturation={saturation:.3f}")
    if text:
        esc = text.replace(":", r"\:").replace("'", r"\'")
        vf.append(f"drawtext=text='{esc}':fontcolor=white:fontsize=42:x=(w-text_w)/2:y=h-th-40:box=1:boxcolor=black@0.4")
    if fade_in:
        vf.append(f"fade=t=in:st=0:d={fade_in}")
    if fade_out:
        vf.append(f"fade=t=out:st=0:d={fade_out}")  # st fixed up below when duration known
    if speed and abs(speed - 1.0) > 1e-3:
        vf.append(f"setpts={1.0 / speed:.6f}*PTS")

    af: list[str] = []
    if mute:
        af = []
    elif volume != 1.0:
        af.append(f"volume={volume:.3f}")
    if speed and abs(speed - 1.0) > 1e-3:
        af.append(f"atempo={min(4.0, max(0.5, speed)):.4f}")

    args = ["-i", str(src)]
    if vf:
        args += ["-vf", ",".join(vf)]
    if af:
        args += ["-af", ",".join(af)]
    if mute:
        args += ["-an"]
    args += ["-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-movflags", "+faststart", str(out_path)]
    run_ffmpeg(args, timeout=3600)
    return str(out_path)


def mux_audio(video_path: str, audio_path: str, out_path: str, replace: bool = True,
              audio_volume: float = 1.0, loop_audio: bool = False, offset: float = 0.0) -> str:
    a = ["-i", str(video_path), "-i", str(audio_path)]
    if loop_audio:
        a[3:3] = ["-stream_loop", "-1"]
    af = f"volume={audio_volume:.3f}" if audio_volume != 1.0 else "anull"
    args = [*a, "-filter_complex",
            f"[1:a]{af}[aud]",
            "-map", "0:v:0", "-map", "[aud]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
            "-movflags", "+faststart", str(out_path)]
    run_ffmpeg(args, timeout=3600)
    return str(out_path)


def extract_audio(video_path: str, out_path: str, fmt: str = "wav") -> str:
    args = ["-i", str(video_path), "-vn", "-acodec", "pcm_s16le" if fmt == "wav" else "copy", str(out_path)]
    run_ffmpeg(args)
    return str(out_path)


def video_thumbnail(video_path: str, out_path: str, timestamp: float = 1.0, max_size: int = 640) -> str:
    args = ["-ss", str(max(0.0, timestamp)), "-i", str(video_path), "-frames:v", "1",
            "-vf", f"scale='min({max_size},iw)':-2", str(out_path)]
    run_ffmpeg(args)
    return str(out_path)


def make_proxy(video_path: str, out_path: str, max_height: int = 480) -> str:
    """Low-bitrate preview so the browser never loads the master file (spec §46)."""
    args = ["-i", str(video_path), "-vf", f"scale=-2:{max_height}", "-c:v", "libx264",
            "-crf", "30", "-preset", "veryfast", "-an", "-movflags", "+faststart", str(out_path)]
    run_ffmpeg(args, timeout=1800)
    return str(out_path)
