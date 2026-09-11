"""Asset library: listing, upload, download, edits, deletion."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..db import insert, jloads, query_all, query_one
from ..jobs import enqueue
from ..security import AuthUser
from ..services import (
    asset_counts,
    asset_path,
    get_asset,
    hard_delete_asset,
    hydrate_asset,
    list_assets,
    persist_file,
    restore_asset,
    soft_delete_asset,
)
from ..storage import guess_mime, kind_for_ext, probe, save_bytes, make_thumbnail

log = logging.getLogger("abhi.routes.assets")

router = APIRouter(prefix="/api/assets", tags=["assets"])

MAX_UPLOAD_BYTES = 512 * 1024 * 1024  # 512 MB


class EditRequest(BaseModel):
    ops: list[dict]
    format: str = "png"
    quality: int = 92
    async_job: bool = False


class DeleteRequest(BaseModel):
    hard: bool = False


@router.get("")
def index(
    user: dict = AuthUser,
    project_id: str | None = None,
    kind: str | None = None,
    search: str | None = None,
    engine: str | None = None,
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
    order: str = "created_at DESC",
) -> dict:
    return list_assets(
        project_id=project_id,
        kind=kind,
        search=search,
        engine=engine,
        limit=limit,
        offset=offset,
        order=order,
    )


@router.get("/stats")
def stats(user: dict = AuthUser, project_id: str | None = None) -> dict:
    counts = asset_counts(project_id)
    rows = query_all(
        "SELECT engine, COUNT(*) AS c, SUM(size_bytes) AS bytes FROM assets "
        "WHERE deleted_at IS NULL GROUP BY engine ORDER BY c DESC LIMIT 20"
    )
    return {
        "counts": counts,
        "by_engine": [
            {"engine": r["engine"] or "unknown", "count": r["c"], "size_bytes": r["bytes"] or 0}
            for r in rows
        ],
    }


@router.post("/upload")
async def upload(
    user: dict = AuthUser,
    file: UploadFile = File(...),
    project_id: str | None = Form(default=None),
) -> dict:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 512 MB upload limit.")
    filename = Path(file.filename or "upload.bin").name
    kind = kind_for_ext(Path(filename).suffix)
    stored = save_bytes(data, project_id=project_id, kind=kind, filename=filename)
    from ..db import new_id

    asset_id = new_id("as_")
    detected = probe(stored["abs_path"], kind)
    thumb = make_thumbnail(stored["abs_path"], asset_id, kind)
    row = insert(
        "assets",
        {
            "id": asset_id,
            "project_id": project_id,
            "kind": kind,
            "filename": filename,
            "rel_path": stored["rel_path"],
            "thumb_path": thumb,
            "mime": file.content_type or guess_mime(stored["abs_path"]),
            "size_bytes": stored["size_bytes"],
            "width": detected.get("width"),
            "height": detected.get("height"),
            "duration_s": detected.get("duration_s"),
            "sha256": stored["sha256"],
            "provider": "upload",
            "engine": "upload",
            "params_json": "{}",
            "meta_json": '{"origin":"upload"}',
        },
    )
    return hydrate_asset(row)


@router.get("/{asset_id}")
def detail(asset_id: str, user: dict = AuthUser) -> dict:
    asset = get_asset(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found.")
    derived = query_all(
        "SELECT * FROM assets WHERE parent_asset_id = ? AND deleted_at IS NULL "
        "ORDER BY created_at DESC LIMIT 20",
        (asset_id,),
    )
    jobs = query_all(
        "SELECT id, kind, label, status, progress, created_at, finished_at FROM jobs "
        "WHERE id = ? OR project_id = ? ORDER BY created_at DESC LIMIT 5",
        (asset.get("source_job_id"), asset.get("project_id")),
    )
    asset["derived_assets"] = [hydrate_asset(row) for row in derived]
    asset["related_jobs"] = jobs
    return asset


@router.get("/{asset_id}/download")
def download(asset_id: str, user: dict = AuthUser):
    asset = get_asset(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found.")
    path = asset_path(asset_id)
    if not path:
        raise HTTPException(status_code=410, detail="Asset file is missing from storage.")
    return FileResponse(
        path,
        media_type=asset["mime"],
        filename=asset["filename"],
    )


@router.post("/{asset_id}/edit")
def edit(asset_id: str, payload: EditRequest, user: dict = AuthUser) -> dict:
    asset = get_asset(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found.")
    if asset["kind"] != "image":
        raise HTTPException(status_code=400, detail="Only image assets can be edited with this endpoint.")
    if not payload.ops:
        raise HTTPException(status_code=400, detail="At least one edit operation is required.")
    if payload.async_job:
        job = enqueue(
            "image.edit",
            {
                "asset_id": asset_id,
                "ops": payload.ops,
                "format": payload.format,
                "quality": payload.quality,
            },
            project_id=asset["project_id"],
            label=f"Edit {asset['filename']}",
        )
        return {"job_id": job["id"], "async": True}

    from ..pipelines import image_edit  # noqa: PLC0415  (import registers pipeline handlers)

    job = enqueue(
        "image.edit",
        {"asset_id": asset_id, "ops": payload.ops, "format": payload.format, "quality": payload.quality},
        project_id=asset["project_id"],
        label=f"Edit {asset['filename']}",
    )
    from ..jobs import wait_for

    finished = wait_for(job["id"], timeout=180)
    if not finished or finished["status"] != "succeeded":
        raise HTTPException(
            status_code=500,
            detail=(finished or {}).get("error") or "Edit failed.",
        )
    return {"asset": finished["result"]["assets"][0], "job_id": job["id"], "async": False}


@router.post("/{asset_id}/thumbnail")
def rebuild_thumbnail(asset_id: str, user: dict = AuthUser) -> dict:
    from ..storage import resolve

    asset = get_asset(asset_id)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found.")
    try:
        path = resolve(asset["rel_path"])
    except ValueError:
        raise HTTPException(status_code=500, detail="Stored path is invalid.") from None
    if not path.exists():
        raise HTTPException(status_code=410, detail="Asset file missing from storage.")
    thumb = make_thumbnail(path, asset_id, asset["kind"])
    from ..db import execute

    execute("UPDATE assets SET thumb_path = ? WHERE id = ?", (thumb, asset_id))
    return get_asset(asset_id)


@router.post("/thumbnails/backfill")
def backfill_thumbnails(user: dict = AuthUser) -> dict:
    job = enqueue("asset.thumbnail", {}, label="Thumbnail backfill")
    return {"job_id": job["id"]}


@router.delete("/{asset_id}")
def remove(asset_id: str, user: dict = AuthUser, hard: bool = Query(default=False)) -> dict:
    asset = get_asset(asset_id, include_deleted=True)
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found.")
    if hard:
        hard_delete_asset(asset_id)
        return {"ok": True, "hard": True}
    soft_delete_asset(asset_id)
    return {"ok": True, "hard": False}


@router.post("/{asset_id}/restore")
def restore(asset_id: str, user: dict = AuthUser) -> dict:
    if not restore_asset(asset_id):
        raise HTTPException(status_code=404, detail="Asset not found.")
    return get_asset(asset_id)


@router.get("/{asset_id}/lineage")
def lineage(asset_id: str, user: dict = AuthUser) -> dict:
    """Walk parent/child links so the UI can show how an asset came to be."""
    chain: list[dict] = []
    current = get_asset(asset_id)
    guard = 0
    while current and guard < 25:
        chain.append(
            {
                "id": current["id"],
                "kind": current["kind"],
                "engine": current["engine"],
                "provider": current["provider"],
                "prompt": current["prompt"],
                "created_at": current["created_at"],
                "url": current["url"],
            }
        )
        current = get_asset(current["parent_asset_id"]) if current.get("parent_asset_id") else None
        guard += 1
    children = query_all(
        "SELECT id, kind, engine, created_at FROM assets WHERE parent_asset_id = ? LIMIT 50",
        (asset_id,),
    )
    return {"ancestors": chain, "children": children}
