"""Timeline editor + social export (§18, §19)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.core.security import CurrentUser, get_current_user
from app.db.models import Asset
from app.db.session import get_db
from app.jobs import queue
from app.media import ffmpeg
from app.services import editor as editor_service
from app.storage.backends import get_storage

log = get_logger("api.editor")
router = APIRouter(prefix="/editor", tags=["editor"])


class RenderIn(BaseModel):
    timeline: dict[str, Any]
    preset: str = "youtube"
    project_id: Optional[str] = None
    name: Optional[str] = None


@router.get("/presets")
def presets(current: CurrentUser = Depends(get_current_user)) -> Any:
    return {
        "presets": editor_service.EXPORT_PRESETS,
        "aspects": editor_service.ASPECT_PRESETS,
        "transitions": ["cut", "fade", "dissolve", "wipeleft", "wiperight", "slideup", "slidedown",
                        "pixelize", "radial", "circleopen"],
        "tracks": ["video", "image", "voice", "music", "sfx", "text", "subtitles"],
    }


@router.post("/render")
def render(payload: RenderIn, db: Session = Depends(get_db),
           current: CurrentUser = Depends(get_current_user)) -> Any:
    current.require("project:write")
    clips = (payload.timeline or {}).get("clips", [])
    if not clips:
        raise NotFoundError("Your timeline is empty.", suggested_action="Add clips before exporting.")
    job = queue.enqueue(db, owner_id=current.id, type="render", mode="export",
                        project_id=payload.project_id,
                        params={"timeline": payload.timeline, "preset": payload.preset,
                                "name": payload.name or f"Export ({payload.preset})"})
    return {"job_id": job.id, "status": "queued", "preset": payload.preset,
            "status_url": f"/api/v1/jobs/{job.id}"}


@router.post("/probe")
def probe(payload: dict, db: Session = Depends(get_db),
          current: CurrentUser = Depends(get_current_user)) -> Any:
    asset_id = payload.get("asset_id")
    if not asset_id:
        raise NotFoundError("Provide an asset_id to inspect.")
    asset = db.get(Asset, asset_id)
    if not asset or (asset.owner_id != current.id and not current.is_admin):
        raise NotFoundError("Asset not found.")
    storage = get_storage()
    return {"asset": {"id": asset.id, "kind": asset.kind, "name": asset.name},
            "probe": ffmpeg.probe(storage.local_path(asset.storage_key))}


@router.get("/ffmpeg")
def ffmpeg_info(current: CurrentUser = Depends(get_current_user)) -> Any:
    return ffmpeg.capabilities()
