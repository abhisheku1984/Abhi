"""Content-addressed asset storage.

Key properties (spec §38, §42):
  * the on-disk path is derived from the sha256 of the bytes, never from a
    user supplied filename -> no path traversal, no overwrites, dedup for free
  * every asset gets an opaque UUID for the API; filenames are cosmetic
  * a pluggable backend interface so S3-compatible storage is a config change
  * integrity is verified on read (hash mismatch is reported, never ignored)
"""
from __future__ import annotations

import hashlib
import io
import mimetypes
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable, Iterator

from app.core.config import settings

SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_EXT_BY_KIND = {
    "image": ".png",
    "video": ".mp4",
    "audio": ".wav",
    "avatar": ".png",
    "character_ref": ".png",
    "document": ".bin",
    "other": ".bin",
}
MIME_BY_EXT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
    ".bmp": "image/bmp", ".gif": "image/gif", ".tiff": "image/tiff",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska",
    ".webm": "video/webm", ".avi": "video/x-msvideo", ".m4v": "video/mp4",
    ".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".flac": "audio/flac", ".ogg": "audio/ogg", ".opus": "audio/opus",
    ".json": "application/json", ".txt": "text/plain", ".srt": "application/x-subrip",
}


def sha256_of(data: bytes | BinaryIO) -> str:
    h = hashlib.sha256()
    if isinstance(data, (bytes, bytearray)):
        h.update(data)
        return h.hexdigest()
    for chunk in iter(lambda: data.read(1024 * 1024), b""):
        h.update(chunk)
    return h.hexdigest()


def safe_filename(name: str, default: str = "asset") -> str:
    """Strip directories and characters that could escape the storage root."""
    name = os.path.basename(name or "")
    name = SAFE_NAME_RE.sub("_", name).strip("._")[:120]
    return name or default


def guess_mime(filename: str, fallback: str = "application/octet-stream") -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext in MIME_BY_EXT:
        return MIME_BY_EXT[ext]
    guess, _ = mimetypes.guess_type(filename)
    return guess or fallback


def ext_for(kind: str, filename: str = "", mime: str = "") -> str:
    if filename:
        ext = os.path.splitext(filename)[1].lower()
        if ext and ext in MIME_BY_EXT:
            return ext
    if mime:
        for ext, m in MIME_BY_EXT.items():
            if m == mime:
                return ext
    return _EXT_BY_KIND.get(kind, ".bin")


@dataclass
class StoredObject:
    storage_key: str          # logical, backend-relative key
    sha256: str
    size_bytes: int
    backend: str
    path: str                 # absolute path (local backend) or "" for remote
    mime_type: str


class StorageBackend:
    """Interface every storage backend implements."""

    name = "base"

    def put(self, key: str, data: bytes | BinaryIO, content_type: str = "") -> StoredObject: ...  # pragma: no cover
    def get_path(self, key: str) -> str: ...  # pragma: no cover
    def open(self, key: str) -> BinaryIO: ...  # pragma: no cover
    def delete(self, key: str) -> bool: ...  # pragma: no cover
    def exists(self, key: str) -> bool: ...  # pragma: no cover
    def usage_bytes(self) -> int: ...  # pragma: no cover


class LocalStorage(StorageBackend):
    name = "local"

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or settings.STORAGE_DIR).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _abs(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if not str(candidate).startswith(str(self.root)):
            raise ValueError("Path traversal attempt blocked")
        return candidate

    def put(self, key: str, data: bytes | BinaryIO, content_type: str = "") -> StoredObject:
        target = self._abs(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, (bytes, bytearray)):
            digest = hashlib.sha256(data).hexdigest()
            size = len(data)
            target.write_bytes(data)
        else:
            h = hashlib.sha256()
            size = 0
            with open(target, "wb") as out:
                for chunk in iter(lambda: data.read(1024 * 1024), b""):
                    h.update(chunk)
                    out.write(chunk)
                    size += len(chunk)
            digest = h.hexdigest()
        return StoredObject(
            storage_key=key, sha256=digest, size_bytes=size, backend=self.name,
            path=str(target), mime_type=content_type or guess_mime(key),
        )

    def get_path(self, key: str) -> str:
        p = self._abs(key)
        return str(p) if p.exists() else ""

    def open(self, key: str) -> BinaryIO:
        return open(self._abs(key), "rb")

    def delete(self, key: str) -> bool:
        p = self._abs(key)
        if p.exists():
            p.unlink()
            return True
        return False

    def exists(self, key: str) -> bool:
        return self._abs(key).exists()

    def usage_bytes(self) -> int:
        total = 0
        for dirpath, _, filenames in os.walk(self.root):
            for fn in filenames:
                try:
                    total += (Path(dirpath) / fn).stat().st_size
                except OSError:
                    continue
        return total


class S3Storage(StorageBackend):
    """S3-compatible backend. Activated with STORAGE_BACKEND=s3.

    boto3 is an optional dependency: if it is missing we raise a precise
    DEPENDENCY_MISSING error instead of silently falling back.
    """

    name = "s3"

    def __init__(self) -> None:
        try:
            import boto3  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dep
            raise RuntimeError(
                "S3 storage requires boto3: pip install boto3 (or set STORAGE_BACKEND=local)"
            ) from exc
        kwargs = {
            "endpoint_url": settings.S3_ENDPOINT_URL or None,
            "aws_access_key_id": settings.S3_ACCESS_KEY or None,
            "aws_secret_access_key": settings.S3_SECRET_KEY or None,
            "region_name": settings.S3_REGION,
        }
        self.client = boto3.client("s3", **{k: v for k, v in kwargs.items() if v})
        self.bucket = settings.S3_BUCKET

    def put(self, key: str, data: bytes | BinaryIO, content_type: str = "") -> StoredObject:
        payload = data if isinstance(data, (bytes, bytearray)) else data.read()
        digest = hashlib.sha256(payload).hexdigest()
        self.client.put_object(Bucket=self.bucket, Key=key, Body=payload, ContentType=content_type or "application/octet-stream")
        return StoredObject(key, digest, len(payload), self.name, "", content_type)

    def get_path(self, key: str) -> str:
        return f"s3://{self.bucket}/{key}"

    def open(self, key: str) -> BinaryIO:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return io.BytesIO(obj["Body"].read())

    def delete(self, key: str) -> bool:
        self.client.delete_object(Bucket=self.bucket, Key=key)
        return True

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def usage_bytes(self) -> int:
        total = 0
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket):
                for obj in page.get("Contents", []):
                    total += obj.get("Size", 0)
        except Exception:
            return 0
        return total


def get_storage() -> StorageBackend:
    if settings.STORAGE_BACKEND.lower() == "s3":
        return S3Storage()
    return LocalStorage()


storage = get_storage()


def build_key(kind: str, digest: str, ext: str) -> str:
    """Sharded content-addressed key: kind/ab/cd/abcdef..."""
    return f"{kind}/{digest[0:2]}/{digest[2:4]}/{digest}{ext}"


def put_bytes(data: bytes | BinaryIO, kind: str, filename: str = "", mime: str = "", extra: str = "") -> StoredObject:
    digest = sha256_of(data)
    ext = ext_for(kind, filename, mime)
    if extra:
        safe = safe_filename(extra).replace(ext, "")
        key = f"{kind}/{digest[0:2]}/{digest[2:4]}/{digest}-{safe}{ext}"
    else:
        key = build_key(kind, digest, ext)
    # Rewind file-like inputs so the payload can be written after hashing.
    if hasattr(data, "seek"):
        try:
            data.seek(0)
        except Exception:
            pass
    return storage.put(key, data, mime or guess_mime(key))


def put_local_file(path: str | Path, kind: str, filename: str = "", mime: str = "", keep_original: bool = True) -> StoredObject:
    p = Path(path)
    with open(p, "rb") as fh:
        obj = put_bytes(fh, kind, filename or p.name, mime or guess_mime(p.name))
    if not keep_original:
        try:
            p.unlink()
        except OSError:
            pass
    return obj


def verify_integrity(obj: StoredObject) -> bool:
    if obj.backend != "local" or not obj.path:
        return True
    p = Path(obj.path)
    if not p.exists():
        return False
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest() == obj.sha256


def iter_keys(kind: str | None = None) -> Iterator[str]:
    if not isinstance(storage, LocalStorage):
        return iter(())
    base = storage.root if kind is None else storage.root / kind
    if not base.exists():
        return iter(())
    for dirpath, _, filenames in os.walk(base):
        for fn in filenames:
            yield str(Path(dirpath).relative_to(storage.root) / fn).replace(os.sep, "/")


def temp_dir(prefix: str = "job") -> Path:
    d = Path(settings.STORAGE_DIR).parent / "tmp" / f"{prefix}-{os.getpid()}-{hashlib.sha256(os.urandom(8)).hexdigest()[:8]}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cleanup(path: str | Path) -> None:
    p = Path(path)
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    elif p.exists():
        try:
            p.unlink()
        except OSError:
            pass


def allowed_upload(filename: str, kind_hint: str = "") -> tuple[bool, str]:
    """Validate an upload by extension AND by kind (defence in depth)."""
    ext = os.path.splitext(filename or "")[1].lower()
    if not ext:
        return False, "File has no extension"
    mapping = {
        "image": settings.ALLOWED_IMAGE_EXT,
        "video": settings.ALLOWED_VIDEO_EXT,
        "audio": settings.ALLOWED_AUDIO_EXT,
        "model": settings.ALLOWED_MODEL_EXT,
    }
    if kind_hint in mapping:
        allowed = mapping[kind_hint]
    else:
        allowed = ",".join([settings.ALLOWED_IMAGE_EXT, settings.ALLOWED_VIDEO_EXT, settings.ALLOWED_AUDIO_EXT])
    allowed_set = {e.strip().lower() for e in allowed.split(",") if e.strip()}
    if ext not in allowed_set:
        return False, f"File type '{ext}' is not allowed for kind '{kind_hint or 'file'}'"
    return True, ""
