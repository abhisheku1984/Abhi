"""Asset service: persists produced/uploaded files as Assets with metadata.

Responsibilities:
  * content-addressed storage (dedup + integrity)
  * media probing (dimensions, duration, fps)
  * thumbnail and video proxy generation (never stream master files to the UI)
  * provenance + safety metadata
  * storage quota accounting
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ErrorCode, StudioError
from app.db.models import Asset, User, UsageRecord
from app.services import media, storage
from app.services.storage import guess_mime, put_local_file, sha256_of

THUMB_MAX = 480


def _thumb_dir() -> Path:
    p = settings.storage_path / "_derived" / "thumbs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _proxy_dir() -> Path:
    p = settings.storage_path / "_derived" / "proxies"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _mime_for(path: str, kind: str) -> str:
    ext = Path(path).suffix.lower()
    mime = guess_mime(path)
    if mime == "application/octet-stream":
        return {"image": "image/png", "video": "video/mp4", "audio": "audio/wav"}.get(kind, mime)
    return mime


def probe_media(path: str, kind: str) -> dict:
    info: dict = {"width": None, "height": None, "duration_seconds": None, "fps": None, "has_alpha": False}
    try:
        if kind == "image":
            with Image.open(path) as im:
                info.update(width=im.width, height=im.height, has_alpha="A" in im.getbands())
        elif kind == "video":
            v = media.probe(path)
            info.update(width=v.width, height=v.height, duration_seconds=v.duration, fps=v.fps)
        elif kind == "audio":
            from app.services import audio as audio_svc

            try:
                info["duration_seconds"] = audio_svc.duration_of(path)
            except Exception:
                pass
    except Exception:
        pass
    return info


def create_asset(
    db: Session,
    *,
    user: User,
    path: str | Path,
    kind: str,
    name: str | None = None,
    project_id: str | None = None,
    source: str = "generated",
    parent_asset_id: str | None = None,
    previous_version_id: str | None = None,
    provenance: dict | None = None,
    safety: dict | None = None,
    meta: dict | None = None,
    make_thumbnail: bool = True,
    make_proxy: bool = True,
    move_file: bool = False,
    mime: str = "",
) -> Asset:
    """Store a file and return the persisted Asset row."""
    path = Path(path)
    if not path.exists():
        raise StudioError(f"File not found: {path}", code=ErrorCode.NOT_FOUND, status_code=404)

    digest = sha256_of(open(path, "rb"))
    existing = db.query(Asset).filter(Asset.sha256 == digest, Asset.owner_id == user.id).first()
    if existing and not move_file:
        # Dedup: identical bytes already stored - reuse instead of duplicating.
        return existing

    stored = put_local_file(path, kind, filename=name or path.name, mime=mime or _mime_for(str(path), kind),
                            keep_original=not move_file)
    info = probe_media(stored.path, kind)

    thumb_id = None
    if make_thumbnail and kind in ("image", "video"):
        try:
            thumb_path = _thumb_dir() / f"{digest[:24]}.jpg"
            if kind == "image":
                media.make_thumbnail(stored.path, thumb_path, THUMB_MAX)
            else:
                media.video_thumbnail(stored.path, thumb_path)
            if Path(thumb_path).exists() and Path(thumb_path).stat().st_size > 0:
                thumb_id = digest[:24] + ".jpg"
        except Exception:
            thumb_id = None

    proxy_id = None
    if make_proxy and kind == "video" and media.ffmpeg_available():
        try:
            proxy_path = _proxy_dir() / f"{digest[:24]}.mp4"
            media.make_proxy(stored.path, str(proxy_path))
            if Path(proxy_path).exists():
                proxy_id = digest[:24] + ".mp4"
        except Exception:
            proxy_id = None

    asset = Asset(
        owner_id=user.id,
        project_id=project_id,
        parent_asset_id=parent_asset_id,
        previous_version_id=previous_version_id,
        name=(name or path.name)[:255],
        kind=kind,
        source=source,
        storage_key=stored.storage_key,
        storage_backend=stored.backend,
        sha256=stored.sha256,
        relative_path=stored.storage_key,
        mime_type=stored.mime_type,
        size_bytes=stored.size_bytes,
        width=info.get("width"),
        height=info.get("height"),
        duration_seconds=info.get("duration_seconds"),
        fps=info.get("fps"),
        has_alpha=bool(info.get("has_alpha")),
        thumbnail_asset_id=thumb_id,
        preview_asset_id=proxy_id,
        provenance=provenance or {},
        safety=safety or {},
        meta=meta or {},
    )
    db.add(asset)
    db.flush()

    db.add(UsageRecord(user_id=user.id, kind="storage", storage_bytes=stored.size_bytes,
                       meta={"asset_id": asset.id, "kind": kind}))
    db.flush()
    return asset


def asset_abs_path(asset: Asset) -> str:
    """Absolute path for serving/processing. Validated against the storage root."""
    p = storage.storage.get_path(asset.storage_key)
    if not p:
        raise StudioError("Stored file is missing from the storage backend.",
                          code=ErrorCode.NOT_FOUND, status_code=404)
    return p


def thumb_abs_path(asset: Asset) -> str | None:
    if not asset.thumbnail_asset_id:
        return None
    p = _thumb_dir() / asset.thumbnail_asset_id
    return str(p) if p.exists() else None


def proxy_abs_path(asset: Asset) -> str | None:
    if not asset.preview_asset_id:
        return None
    p = _proxy_dir() / asset.preview_asset_id
    return str(p) if p.exists() else None


def usage_bytes(db: Session, user_id: str) -> int:
    from sqlalchemy import func

    total = db.query(func.coalesce(func.sum(Asset.size_bytes), 0)).filter(
        Asset.owner_id == user_id, Asset.is_deleted.is_(False)
    ).scalar()
    return int(total or 0)


def check_quota(db: Session, user: User, extra_bytes: int = 0) -> None:
    used = usage_bytes(db, user.id) + extra_bytes
    limit = int(user.storage_quota_mb) * 1024 * 1024
    if used > limit:
        raise StudioError(
            f"Storage quota exceeded ({used / 1048576:.1f} MB of {user.storage_quota_mb} MB).",
            code=ErrorCode.FORBIDDEN, status_code=413,
            remediation="Delete unused assets or raise the quota in Admin → Users.",
        )


def duplicate_asset(db: Session, user: User, asset: Asset, name: str | None = None) -> Asset:
    """Create an independent copy (used by 'Duplicate' in the UI)."""
    src = asset_abs_path(asset)
    path = Path(src)
    stored = storage.put_bytes(open(path, "rb"), asset.kind, filename=name or f"copy-{asset.name}",
                               mime=asset.mime_type)
    copy = Asset(
        owner_id=user.id,
        project_id=asset.project_id,
        parent_asset_id=asset.id,
        name=(name or f"Copy of {asset.name}")[:255],
        kind=asset.kind,
        source="derived",
        storage_key=stored.storage_key,
        storage_backend=stored.backend,
        sha256=stored.sha256,
        relative_path=stored.storage_key,
        mime_type=stored.mime_type,
        size_bytes=stored.size_bytes,
        width=asset.width, height=asset.height,
        duration_seconds=asset.duration_seconds, fps=asset.fps,
        has_alpha=asset.has_alpha,
        thumbnail_asset_id=asset.thumbnail_asset_id,
        preview_asset_id=asset.preview_asset_id,
        provenance={**(asset.provenance or {}), "duplicated_from": asset.id},
        meta=asset.meta or {},
    )
    db.add(copy)
    db.flush()
    return copy


def create_new_version(db: Session, user: User, asset: Asset, new_path: str | Path,
                       name: str | None = None, provenance: dict | None = None) -> Asset:
    """Version history: link the new asset back to the one it replaced."""
    new_asset = create_asset(
        db, user=user, path=new_path, kind=asset.kind, name=name or asset.name,
        project_id=asset.project_id, source="edited",
        parent_asset_id=asset.parent_asset_id or asset.id,
        previous_version_id=asset.id,
        provenance=provenance or asset.provenance,
        meta=asset.meta,
    )
    new_asset.version = int(asset.version or 1) + 1
    db.flush()
    return new_asset
