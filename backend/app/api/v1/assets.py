"""Asset management: upload, list, favourite, duplicate, delete (§24, §34)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ForbiddenError, NotFoundError, SafetyBlockedError, StudioError
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.security import CurrentUser, get_current_user
from app.db.models import Asset, User
from app.db.session import get_db
from app.media import ffmpeg, image_ops
from app.safety import moderation
from app.services import assets as asset_service

log = get_logger("api.assets")
router = APIRouter(prefix="/assets", tags=["assets"])

KIND_BY_MIME = {
    "image/": "image", "video/": "video", "audio/": "audio",
}


def _kind_for(mime: str) -> str:
    for prefix, kind in KIND_BY_MIME.items():
        if mime.startswith(prefix):
            return kind
    return "other"


@router.get("")
def list_assets(
    *,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
    kind: Optional[str] = None,
    project_id: Optional[str] = None,
    favorite: Optional[bool] = None,
    q: Optional[str] = None,
    limit: int = Query(60, ge=1, le=300),
    offset: int = Query(0, ge=0),
) -> Any:
    query = db.query(Asset).filter(Asset.owner_id == current.id, Asset.is_archived.is_(False))
    if kind:
        query = query.filter(Asset.kind == kind)
    if project_id:
        query = query.filter(Asset.project_id == project_id)
    if favorite is not None:
        query = query.filter(Asset.is_favorite == favorite)
    if q:
        query = query.filter(Asset.name.ilike(f"%{q}%"))
    total = query.count()
    items = query.order_by(Asset.created_at.desc()).offset(offset).limit(limit).all()
    return {"items": [asset_service.to_dict(a) for a in items], "total": total, "limit": limit, "offset": offset}


@router.post("/upload", status_code=201)
async def upload_asset(
    *,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
    file: UploadFile = File(...),
    project_id: Optional[str] = Form(None),
    kind: Optional[str] = Form(None),
) -> Any:
    decision = moderation.check_upload(file.filename or "", file.content_type or "", 0)
    if not decision.allowed:
        raise SafetyBlockedError("; ".join(decision.reasons), suggested_action="Choose a different file.")

    tmp_dir = settings.data_root / "uploads"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"{new_id('up_')}_{Path(file.filename or 'upload').name}"
    size = 0
    try:
        with tmp_path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > settings.MAX_UPLOAD_MB * 1024 * 1024:
                    raise StudioError(
                        f"File exceeds the {settings.MAX_UPLOAD_MB} MB upload limit.",
                        suggested_action="Compress the file or raise MAX_UPLOAD_MB.",
                    )
                out.write(chunk)
    finally:
        await file.close()

    decision = moderation.check_upload(file.filename or "", file.content_type or "", size)
    if not decision.allowed:
        tmp_path.unlink(missing_ok=True)
        raise SafetyBlockedError("; ".join(decision.reasons), suggested_action="Choose a different file.")

    asset_kind = kind or _kind_for(file.content_type or "")
    try:
        asset = asset_service.create_asset(
            db, owner_id=current.id, path=str(tmp_path), kind=asset_kind,
            name=Path(file.filename or "upload").stem[:180], project_id=project_id,
            meta={"source": "upload", "original_filename": file.filename,
                  "engine": "upload", "mode": "upload", "ai_generated": False},
        )
        db.commit()
    finally:
        tmp_path.unlink(missing_ok=True)

    log.info("asset_uploaded", asset_id=asset.id, kind=asset_kind, bytes=size)
    return asset_service.to_dict(asset)


@router.get("/{asset_id}")
def get_asset(asset_id: str, db: Session = Depends(get_db),
              current: CurrentUser = Depends(get_current_user)) -> Any:
    asset = db.get(Asset, asset_id)
    if not asset:
        raise NotFoundError("Asset not found.", suggested_action="It may have been deleted.")
    if asset.owner_id != current.id and not current.is_admin:
        raise ForbiddenError("You do not have access to this asset.")
    return asset_service.to_dict(asset)


@router.patch("/{asset_id}")
def update_asset(asset_id: str, payload: dict, db: Session = Depends(get_db),
                 current: CurrentUser = Depends(get_current_user)) -> Any:
    asset = db.get(Asset, asset_id)
    if not asset:
        raise NotFoundError("Asset not found.")
    if asset.owner_id != current.id and not current.is_admin:
        raise ForbiddenError("You do not have access to this asset.")
    if "name" in payload:
        asset.name = str(payload["name"])[:200]
    if "is_favorite" in payload:
        asset.is_favorite = bool(payload["is_favorite"])
    if "project_id" in payload:
        asset.project_id = payload["project_id"] or None
    if "is_archived" in payload:
        asset.is_archived = bool(payload["is_archived"])
    if "meta" in payload and isinstance(payload["meta"], dict):
        asset.meta = {**(asset.meta or {}), **payload["meta"]}
    db.commit()
    return asset_service.to_dict(asset)


@router.post("/{asset_id}/duplicate", status_code=201)
def duplicate(asset_id: str, db: Session = Depends(get_db),
              current: CurrentUser = Depends(get_current_user)) -> Any:
    asset = db.get(Asset, asset_id)
    if not asset:
        raise NotFoundError("Asset not found.")
    if asset.owner_id != current.id and not current.is_admin:
        raise ForbiddenError("You do not have access to this asset.")
    copy = asset_service.duplicate_asset(db, asset)
    db.commit()
    return asset_service.to_dict(copy)


@router.delete("/{asset_id}")
def delete_asset(asset_id: str, db: Session = Depends(get_db),
                 current: CurrentUser = Depends(get_current_user)) -> Any:
    asset = db.get(Asset, asset_id)
    if not asset:
        raise NotFoundError("Asset not found.")
    if asset.owner_id != current.id and not current.is_admin:
        raise ForbiddenError("You do not have access to this asset.")
    asset_service.delete_asset(db, asset)
    db.commit()
    return {"ok": True, "id": asset_id}


@router.get("/stats/summary")
def asset_stats(db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)) -> Any:
    from sqlalchemy import func

    rows = (
        db.query(Asset.kind, func.count(Asset.id), func.sum(Asset.size_bytes))
        .filter(Asset.owner_id == current.id)
        .group_by(Asset.kind)
        .all()
    )
    items = {}
    for kind, count, total in rows:
        items[kind] = {"count": count, "bytes": int(total or 0)}
    total_bytes = sum(v["bytes"] for v in items.values())
    return {
        "by_kind": items,
        "total_bytes": total_bytes,
        "total_mb": round(total_bytes / 1024 / 1024, 2),
        "storage_used_mb": round(total_bytes / 1024 / 1024, 2),
    }
