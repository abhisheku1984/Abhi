"""Projects, characters, stories and shots — the creative hierarchy."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..db import execute, insert, jdumps, jloads, query_all, query_one, update, utcnow
from ..security import AuthUser
from ..services import asset_counts, hydrate_asset

log = logging.getLogger("abhi.routes.projects")

router = APIRouter(prefix="/api/projects", tags=["projects"])


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------
class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    style_prompt: str = ""
    aspect_ratio: str = "16:9"


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    style_prompt: str | None = None
    aspect_ratio: str | None = None
    status: str | None = None


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def project_or_404(project_id: str) -> dict:
    row = query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Project not found.")
    return row


def project_summary(row: dict) -> dict:
    counts = asset_counts(row["id"])
    jobs = query_one(
        "SELECT COUNT(*) AS c FROM jobs WHERE project_id = ? AND status = 'running'", (row["id"],)
    )
    stories = query_one("SELECT COUNT(*) AS c FROM stories WHERE project_id = ?", (row["id"],))
    characters = query_one("SELECT COUNT(*) AS c FROM characters WHERE project_id = ?", (row["id"],))
    shots = query_one("SELECT COUNT(*) AS c FROM shots WHERE project_id = ?", (row["id"],))
    latest = query_all(
        "SELECT * FROM assets WHERE project_id = ? AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 4",
        (row["id"],),
    )
    return {
        **row,
        "asset_counts": counts,
        "running_jobs": jobs["c"] if jobs else 0,
        "story_count": stories["c"] if stories else 0,
        "character_count": characters["c"] if characters else 0,
        "shot_count": shots["c"] if shots else 0,
        "recent_assets": [hydrate_asset(a) for a in latest],
    }


# --------------------------------------------------------------------------
# projects
# --------------------------------------------------------------------------
@router.get("")
def list_projects(
    user: dict = AuthUser,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: str | None = None,
) -> dict:
    clauses, values = [], []
    if search:
        clauses.append("(name LIKE ? OR description LIKE ?)")
        values += [f"%{search}%", f"%{search}%"]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = query_one(f"SELECT COUNT(*) AS c FROM projects {where}", tuple(values))["c"]  # noqa: S608
    rows = query_all(
        f"SELECT * FROM projects {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",  # noqa: S608
        (*values, limit, offset),
    )
    return {"total": total, "items": [project_summary(r) for r in rows]}


@router.post("")
def create_project(payload: ProjectCreate, user: dict = AuthUser) -> dict:
    row = insert(
        "projects",
        {
            "name": payload.name.strip(),
            "description": payload.description.strip(),
            "style_prompt": payload.style_prompt.strip(),
            "aspect_ratio": payload.aspect_ratio,
        },
    )
    return project_summary(row)


@router.get("/{project_id}")
def get_project(project_id: str, user: dict = AuthUser) -> dict:
    return project_summary(project_or_404(project_id))


@router.patch("/{project_id}")
def patch_project(project_id: str, payload: ProjectUpdate, user: dict = AuthUser) -> dict:
    project_or_404(project_id)
    values = {k: v for k, v in payload.model_dump(exclude_none=True).items()}
    if values:
        update("projects", project_id, values)
    return project_summary(project_or_404(project_id))


@router.delete("/{project_id}")
def delete_project(project_id: str, user: dict = AuthUser) -> dict:
    project_or_404(project_id)
    counts = asset_counts(project_id)
    execute("DELETE FROM projects WHERE id = ?", (project_id,))
    return {
        "ok": True,
        "deleted_assets": counts.get("total", 0),
        "note": "Media files remain on disk in the data directory; delete data/media to reclaim space.",
    }


# --------------------------------------------------------------------------
# characters
# --------------------------------------------------------------------------
class CharacterCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role: str = ""
    description: str = ""
    appearance: str = ""
    style: str = ""
    seed: int = 0
    ref_asset_ids: list[str] = []


class CharacterUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    description: str | None = None
    appearance: str | None = None
    style: str | None = None
    seed: int | None = None
    ref_asset_ids: list[str] | None = None


def character_payload(row: dict) -> dict:
    refs = jloads(row["ref_asset_ids"], [])
    assets = [hydrate_asset(query_one("SELECT * FROM assets WHERE id = ?", (a,))) for a in refs]
    return {**row, "ref_asset_ids": refs, "reference_assets": [a for a in assets if a]}


@router.get("/{project_id}/characters")
def list_characters(project_id: str, user: dict = AuthUser) -> dict:
    project_or_404(project_id)
    rows = query_all(
        "SELECT * FROM characters WHERE project_id = ? ORDER BY created_at", (project_id,)
    )
    return {"items": [character_payload(r) for r in rows], "total": len(rows)}


@router.post("/{project_id}/characters")
def create_character(project_id: str, payload: CharacterCreate, user: dict = AuthUser) -> dict:
    project_or_404(project_id)
    seed = payload.seed or abs(hash(f"{project_id}:{payload.name}")) % (2**31)
    row = insert(
        "characters",
        {
            "project_id": project_id,
            "name": payload.name.strip(),
            "role": payload.role.strip(),
            "description": payload.description.strip(),
            "appearance": payload.appearance.strip(),
            "style": payload.style.strip(),
            "seed": seed,
            "ref_asset_ids": jdumps(payload.ref_asset_ids),
        },
    )
    return character_payload(row)


# --------------------------------------------------------------------------
# stories + shots
# --------------------------------------------------------------------------
def story_payload(row: dict) -> dict:
    shots = query_all("SELECT * FROM shots WHERE story_id = ? ORDER BY index_no", (row["id"],))
    def _shot(s: dict) -> dict:
        return {
            **s,
            "character_ids": jloads(s["character_ids"], []),
            "image": hydrate_asset(query_one("SELECT * FROM assets WHERE id = ?", (s["asset_id"],)))
            if s["asset_id"]
            else None,
        }

    return {
        **row,
        "scenes": jloads(row["scenes_json"], []),
        "shots": [_shot(s) for s in shots],
        "shot_count": len(shots),
        "generated_shots": sum(1 for s in shots if s["asset_id"]),
        "total_duration_s": round(sum(float(s["duration_s"] or 0) for s in shots), 2),
    }


@router.get("/{project_id}/stories")
def list_stories(project_id: str, user: dict = AuthUser) -> dict:
    project_or_404(project_id)
    rows = query_all(
        "SELECT * FROM stories WHERE project_id = ? ORDER BY updated_at DESC", (project_id,)
    )
    return {"items": [story_payload(r) for r in rows], "total": len(rows)}


@router.get("/{project_id}/overview")
def project_overview(project_id: str, user: dict = AuthUser) -> dict:
    """Everything the project dashboard needs in one round trip."""
    project = project_summary(project_or_404(project_id))
    stories = query_all(
        "SELECT id, title, status, engine, created_at FROM stories WHERE project_id = ? "
        "ORDER BY updated_at DESC LIMIT 10",
        (project_id,),
    )
    jobs = query_all(
        "SELECT id, kind, label, status, progress, created_at FROM jobs WHERE project_id = ? "
        "ORDER BY created_at DESC LIMIT 10",
        (project_id,),
    )
    counts = asset_counts(project_id)
    return {
        "project": project,
        "asset_counts": counts,
        "stories": stories,
        "jobs": jobs,
    }
