"""Pluggable asset storage (§24). Local filesystem by default, S3-compatible
for production. Assets are addressed by key, never by filename alone.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.core.errors import StudioError
from app.core.ids import new_id
from app.core.logging import get_logger

log = get_logger("storage")

KIND_DIRS = {
    "image": "images", "video": "videos", "audio": "audio", "avatar": "avatars",
    "character": "characters", "document": "docs", "other": "misc",
}


class StorageBackend(ABC):
    name: str = "base"

    @abstractmethod
    def put(self, src_path: str, key: Optional[str] = None, *, kind: str = "other") -> str:
        ...

    @abstractmethod
    def local_path(self, key: str) -> str:
        ...

    @abstractmethod
    def delete(self, key: str) -> bool:
        ...

    @abstractmethod
    def exists(self, key: str) -> bool:
        ...

    def url(self, key: str) -> str:
        return f"/api/v1/files/{key}"

    def key_for(self, kind: str, filename: str) -> str:
        folder = KIND_DIRS.get(kind, "misc")
        ident = new_id()
        suffix = Path(filename).suffix or ""
        return f"{folder}/{ident[:2]}/{ident}{suffix}"


class LocalStorage(StorageBackend):
    name = "local"

    def __init__(self, root: Optional[str] = None) -> None:
        self.root = Path(root or settings.storage_root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, src_path: str, key: Optional[str] = None, *, kind: str = "other") -> str:
        key = key or self.key_for(kind, src_path)
        dest = self.root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dest)
        return key

    def local_path(self, key: str) -> str:
        return str(self.root / key)

    def delete(self, key: str) -> bool:
        p = self.root / key
        if p.exists():
            p.unlink()
            return True
        return False

    def exists(self, key: str) -> bool:
        return (self.root / key).exists()

    def usage_bytes(self) -> int:
        return sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file())


class S3Storage(StorageBackend):
    name = "s3"

    def __init__(self) -> None:
        try:
            import boto3  # type: ignore
        except ImportError as exc:
            raise StudioError(
                "S3 storage requires boto3.",
                detail=str(exc),
                suggested_action="pip install boto3, or set STORAGE_BACKEND=local",
            ) from exc
        if not settings.S3_BUCKET:
            raise StudioError("S3 storage is selected but S3_BUCKET is not set.",
                              suggested_action="Set S3_BUCKET (and credentials) or use STORAGE_BACKEND=local.")
        self.bucket = settings.S3_BUCKET
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT_URL,
            region_name=settings.S3_REGION,
            aws_access_key_id=settings.S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        )
        self._cache_root = settings.data_root / "s3cache"
        self._cache_root.mkdir(parents=True, exist_ok=True)

    def put(self, src_path: str, key: Optional[str] = None, *, kind: str = "other") -> str:
        key = key or self.key_for(kind, src_path)
        self.client.upload_file(src_path, self.bucket, key)
        return key

    def local_path(self, key: str) -> str:
        """Download to a local cache so processing code can read it."""
        cached = self._cache_root / key
        cached.parent.mkdir(parents=True, exist_ok=True)
        if not cached.exists():
            self.client.download_file(self.bucket, key, str(cached))
        return str(cached)

    def delete(self, key: str) -> bool:
        self.client.delete_object(Bucket=self.bucket, Key=key)
        return True

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False


_storage: Optional[StorageBackend] = None


def get_storage() -> StorageBackend:
    global _storage
    if _storage is None:
        _storage = S3Storage() if settings.STORAGE_BACKEND == "s3" else LocalStorage()
        log.info("storage_initialised", backend=_storage.name)
    return _storage
