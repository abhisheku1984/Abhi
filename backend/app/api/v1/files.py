"""Secure file serving from the storage backend (§34: path-traversal safe)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from app.core.logging import get_logger
from app.core.security import CurrentUser, get_current_user
from app.storage.backends import get_storage

log = get_logger("api.files")
router = APIRouter(prefix="/files", tags=["files"])


@router.get("/{key:path}")
def serve_file(key: str, current: CurrentUser = Depends(get_current_user)) -> FileResponse:
    # Normalise and verify the key stays inside the storage root.
    if ".." in key or key.startswith("/") or key.startswith("\\"):
        raise HTTPException(400, detail="Invalid file key.")
    storage = get_storage()
    local = Path(storage.local_path(key)).resolve()
    root = Path(storage.root).resolve() if hasattr(storage, "root") else None
    if root and not str(local).startswith(str(root)):
        raise HTTPException(400, detail="Invalid file key.")
    if not local.exists() or not local.is_file():
        raise HTTPException(404, detail="File not found.")
    media_type = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
        ".mp4": "video/mp4", ".webm": "video/webm", ".mp3": "audio/mpeg", ".wav": "audio/wav",
        ".m4a": "audio/mp4", ".json": "application/json",
    }.get(local.suffix.lower(), "application/octet-stream")
    return FileResponse(str(local), media_type=media_type, filename=local.name)
