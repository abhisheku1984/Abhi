"""
Filesystem storage for generated media.

Layout (all under ABHI_DATA_DIR, which is git-ignored):

    data/
      abhi.db
      media/<project_id>/<kind>/<sha8>_<name>.<ext>
      thumbs/<asset_id>.jpg
      tmp/<job_id>/
      logs/abhi.log

Files are content-addressed by an 8-char sha256 prefix, so identical outputs are
deduplicated on disk while still keeping human-readable filenames.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import detect_ffmpeg, get_settings

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}
VIDEO_EXT = {".mp4", ".webm", ".mov", ".mkv", ".gif"}
AUDIO_EXT = {".wav", ".mp3", ".ogg", ".opus", ".m4a", ".flac", ".aac"}

_MIME_OVERRIDES = {
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".svg": "image/svg+xml",
}


def kind_for_ext(ext: str) -> str:
    ext = ext.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    return "file"


def guess_mime(path: Path) -> str:
    if path.suffix.lower() in _MIME_OVERRIDES:
        return _MIME_OVERRIDES[path.suffix.lower()]
    mime, _ = mimetypes.guess_type(path.name)
    return mime or "application/octet-stream"


def safe_name(name: str) -> str:
    stem = Path(name).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._") or "asset"
    return stem[:64]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def media_dir_for(project_id: str | None, kind: str) -> Path:
    settings = get_settings()
    folder = settings.media_dir / (project_id or "library") / kind
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def tmp_dir_for(job_id: str) -> Path:
    folder = get_settings().tmp_dir / job_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def save_bytes(
    data: bytes,
    *,
    project_id: str | None,
    kind: str,
    filename: str,
    unique_hint: str = "",
) -> dict[str, Any]:
    """Persist bytes to the media store and return path/hash metadata."""
    folder = media_dir_for(project_id, kind)
    digest = sha256_bytes(data)
    ext = Path(filename).suffix or ".bin"
    stem = safe_name(filename)
    name = f"{digest[:8]}_{stem}{ext}"
    if unique_hint:
        name = f"{digest[:8]}_{safe_name(unique_hint)}_{stem}{ext}"
    path = folder / name
    path.write_bytes(data)
    return {
        "abs_path": path,
        "rel_path": str(path.relative_to(get_settings().data_dir)),
        "sha256": digest,
        "size_bytes": len(data),
        "mime": guess_mime(path),
    }


def save_text(
    text: str, *, project_id: str | None, kind: str, filename: str
) -> dict[str, Any]:
    return save_bytes(
        text.encode("utf-8"), project_id=project_id, kind=kind, filename=filename
    )


def copy_into_store(
    source: Path, *, project_id: str | None, kind: str, filename: str | None = None
) -> dict[str, Any]:
    return save_bytes(
        source.read_bytes(),
        project_id=project_id,
        kind=kind,
        filename=filename or source.name,
    )


def resolve(rel_path: str) -> Path:
    """Resolve a stored relative path, refusing traversal outside the data dir."""
    settings = get_settings()
    candidate = (settings.data_dir / rel_path).resolve()
    root = settings.data_dir.resolve()
    if not str(candidate).startswith(str(root)):
        raise ValueError("path escapes storage root")
    return candidate


def delete_stored(rel_path: str | None) -> None:
    if not rel_path:
        return
    try:
        resolve(rel_path).unlink(missing_ok=True)
    except (ValueError, OSError):
        pass


# --------------------------------------------------------------------------
# thumbnails
# --------------------------------------------------------------------------
def thumbnail_path_for(asset_id: str) -> Path:
    folder = get_settings().thumb_dir
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{asset_id}.jpg"


def make_thumbnail(source: Path, asset_id: str, kind: str, size: int = 384) -> str | None:
    """Create a JPEG (image/video) or waveform PNG (audio) thumbnail."""
    settings = get_settings()
    target = thumbnail_path_for(asset_id)
    try:
        if kind == "image":
            from PIL import Image

            with Image.open(source) as img:
                img = img.convert("RGB")
                img.thumbnail((size, size))
                img.save(target, "JPEG", quality=82)
        elif kind == "video":
            ffmpeg = detect_ffmpeg()
            if not ffmpeg:
                return None
            subprocess.run(
                [
                    ffmpeg, "-y", "-ss", "0.6", "-i", str(source),
                    "-frames:v", "1", "-vf", f"scale={size}:-2", str(target),
                ],
                capture_output=True,
                timeout=120,
            )
            if not target.exists():
                return None
        elif kind == "audio":
            from .media.audio import waveform_png

            waveform_png(source, target.with_suffix(".png"), width=size, height=size // 2)
            target = target.with_suffix(".png")
        else:
            return None
        return str(target.relative_to(settings.data_dir))
    except Exception:
        return None


# --------------------------------------------------------------------------
# probing
# --------------------------------------------------------------------------
def probe_image(path: Path) -> dict[str, Any]:
    try:
        from PIL import Image

        with Image.open(path) as img:
            return {"width": img.width, "height": img.height, "duration_s": None}
    except Exception:
        return {"width": None, "height": None, "duration_s": None}


def probe_video(path: Path) -> dict[str, Any]:
    """Probe via ffmpeg stderr (works with the bundled static build, no ffprobe)."""
    ffmpeg = detect_ffmpeg()
    if not ffmpeg:
        return {"width": None, "height": None, "duration_s": None}
    try:
        out = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=60
        )
        text = out.stderr or ""
        duration = None
        width = height = None
        m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", text)
        if m:
            duration = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        m = re.search(r"Video: .*?(\d{2,5})x(\d{2,5})", text)
        if m:
            width, height = int(m.group(1)), int(m.group(2))
        return {"width": width, "height": height, "duration_s": duration}
    except Exception:
        return {"width": None, "height": None, "duration_s": None}


def probe_audio(path: Path) -> dict[str, Any]:
    info = probe_video(path)
    info["width"] = None
    info["height"] = None
    return info


def probe(path: Path, kind: str) -> dict[str, Any]:
    if kind == "image":
        return probe_image(path)
    if kind == "video":
        return probe_video(path)
    if kind == "audio":
        return probe_audio(path)
    return {"width": None, "height": None, "duration_s": None}


def disk_usage() -> dict[str, Any]:
    settings = get_settings()
    usage = shutil.disk_usage(str(settings.data_dir))
    media_bytes = sum(f.stat().st_size for f in settings.media_dir.rglob("*") if f.is_file())
    return {
        "data_dir": str(settings.data_dir),
        "disk_total_gb": round(usage.total / 1e9, 2),
        "disk_free_gb": round(usage.free / 1e9, 2),
        "media_store_mb": round(media_bytes / 1e6, 2),
    }
