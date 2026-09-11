"""Asset persistence: storage, thumbnails, proxies, metadata, provenance (§24, §38)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.ids import new_id
from app.core.logging import get_logger
from app.db.models import Asset
from app.media import ffmpeg, image_ops
from app.safety import provenance
from app.storage.backends import get_storage

log = get_logger("services.assets")


def _dimensions(path: str, kind: str) -> tuple[Optional[int], Optional[float], Optional[int]]:
    """Return (width, duration, height-aware) best-effort media facts."""
    width = height = None
    duration = None
    try:
        if kind in ("image", "avatar"):
            info = image_ops.image_info(path)
            width, height = info.get("width"), info.get("height")
        elif kind in ("video", "audio"):
            info = ffmpeg.probe(path)
            width, height = info.get("width"), info.get("height")
            duration = float(info.get("duration") or 0) or None
    except Exception as exc:
        log.debug("probe_failed", path=path, error=str(exc))
    return width, duration, height


def create_asset(
    db: Session,
    *,
    owner_id: str,
    path: str,
    kind: str,
    name: str = "",
    project_id: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
    parent_asset_id: Optional[str] = None,
    storage=None,
) -> Asset:
    """Move a generated/ uploaded file into storage and register it."""
    storage = storage or get_storage()
    meta = dict(meta or {})

    if settings.WATERMARK_ENABLED:
        provenance.apply_watermark(path, kind)

    key = storage.put(path, kind=kind)
    local = storage.local_path(key)
    size = Path(local).stat().st_size
    checksum = image_ops.checksum(local)
    width, duration, height = _dimensions(local, kind)

    # Thumbnail + proxy generation (never load masters in the browser, §38)
    thumb_key = None
    preview_key = None
    try:
        if kind in ("image", "avatar"):
            thumb = image_ops.thumbnail(image_ops.load(local), width=480)
            thumb_path = str(Path(local).with_suffix(".thumb.jpg"))
            image_ops.save(thumb, thumb_path, quality=86)
            thumb_key = storage.put(thumb_path, kind="image")
            Path(thumb_path).unlink(missing_ok=True)
        elif kind == "video" and duration:
            thumb_path = str(Path(local).with_suffix(".thumb.jpg"))
            ffmpeg.thumbnail(local, thumb_path, time=min(1.0, max(0.0, duration / 3)), width=480)
            if Path(thumb_path).exists():
                thumb_key = storage.put(thumb_path, kind="image")
                Path(thumb_path).unlink(missing_ok=True)
            proxy_path = str(Path(local).with_suffix(".proxy.mp4"))
            ffmpeg.make_proxy(local, proxy_path, height=360)
            if Path(proxy_path).exists():
                preview_key = storage.put(proxy_path, kind="video")
                Path(proxy_path).unlink(missing_ok=True)
    except Exception as exc:
        log.warning("derivative_failed", key=key, error=str(exc))

    meta.setdefault("provenance", provenance.build_provenance(
        engine=str(meta.get("engine", "")),
        mode=str(meta.get("mode", "")),
        prompt=str(meta.get("prompt", "")),
        negative_prompt=str(meta.get("negative_prompt", "")),
        params=meta.get("params", {}),
        seed=meta.get("seed"),
        character_id=meta.get("character_id"),
        voice_id=meta.get("voice_id"),
        owner_id=owner_id,
        deterministic=bool(meta.get("deterministic", False)),
        diffusion=meta.get("diffusion"),
        extra={"ai_generated": bool(meta.get("ai_generated", True)),
               **(meta.get("provenance_extra", {}) or {})},
    ))

    asset = Asset(
        id=new_id("ast_"),
        owner_id=owner_id,
        project_id=project_id,
        kind=kind,
        name=name or Path(path).stem[:180],
        storage_key=key,
        storage_backend=storage.name,
        mime=_mime_for(local, kind),
        size_bytes=size,
        checksum=checksum,
        width=width,
        height=height,
        duration_sec=duration,
        thumbnail_key=thumb_key,
        preview_key=preview_key,
        meta=meta,
        parent_asset_id=parent_asset_id,
    )
    db.add(asset)
    db.flush()
    log.info("asset_created", asset_id=asset.id, kind=kind, key=key, bytes=size)
    return asset


def _mime_for(path: str, kind: str) -> str:
    ext = Path(path).suffix.lower()
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
        ".gif": "image/gif", ".bmp": "image/bmp", ".tiff": "image/tiff",
        ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime", ".avi": "video/x-msvideo",
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".flac": "audio/flac",
        ".m4a": "audio/mp4", ".aac": "audio/aac",
    }.get(ext, "application/octet-stream")


def delete_asset(db: Session, asset: Asset) -> bool:
    storage = get_storage()
    for key in (asset.storage_key, asset.thumbnail_key, asset.preview_key):
        if key:
            try:
                storage.delete(key)
            except Exception as exc:
                log.warning("asset_delete_failed", key=key, error=str(exc))
    db.delete(asset)
    return True


def duplicate_asset(db: Session, asset: Asset, *, project_id: Optional[str] = None) -> Asset:
    storage = get_storage()
    src = storage.local_path(asset.storage_key)
    new_asset = create_asset(
        db, owner_id=asset.owner_id, path=src, kind=asset.kind,
        name=f"{asset.name} (copy)", project_id=project_id or asset.project_id,
        meta={**asset.meta, "duplicated_from": asset.id}, parent_asset_id=asset.id,
    )
    return new_asset


def to_dict(asset: Asset) -> dict[str, Any]:
    return {
        "id": asset.id,
        "kind": asset.kind,
        "name": asset.name,
        "mime": asset.mime,
        "size_bytes": asset.size_bytes,
        "width": asset.width,
        "height": asset.height,
        "duration_sec": asset.duration_sec,
        "checksum": asset.checksum,
        "project_id": asset.project_id,
        "is_favorite": asset.is_favorite,
        "meta": asset.meta,
        "version": asset.version,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
        "url": f"/api/v1/files/{asset.storage_key}",
        "thumbnail_url": f"/api/v1/files/{asset.thumbnail_key}" if asset.thumbnail_key else None,
        "preview_url": f"/api/v1/files/{asset.preview_key}" if asset.preview_key else None,
    }
