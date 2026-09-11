"""Character routes: read/update single characters, generate references and sheets."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import jdumps, jloads, query_one, update
from ..jobs import enqueue
from ..routes.projects import character_payload
from ..security import AuthUser
from ..services import hydrate_asset

router = APIRouter(prefix="/api/characters", tags=["characters"])


class CharacterUpdate(BaseModel):
    name: str | None = None
    role: str | None = None
    description: str | None = None
    appearance: str | None = None
    style: str | None = None
    seed: int | None = None
    ref_asset_ids: list[str] | None = None


class ReferenceRequest(BaseModel):
    prompt: str = ""
    count: int = 1
    width: int = 768
    height: int = 1024
    provider: str = "auto"


def character_or_404(character_id: str) -> dict:
    row = query_one("SELECT * FROM characters WHERE id = ?", (character_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Character not found.")
    return row


@router.get("/{character_id}")
def detail(character_id: str, user: dict = AuthUser) -> dict:
    character = character_payload(character_or_404(character_id))
    story_rows = query_one(
        "SELECT COUNT(*) AS c FROM shots WHERE character_ids LIKE ?",
        (f"%{character_id}%",),
    )
    character["shot_count"] = story_rows["c"] if story_rows else 0
    return character


@router.patch("/{character_id}")
def patch(character_id: str, payload: CharacterUpdate, user: dict = AuthUser) -> dict:
    character_or_404(character_id)
    values = payload.model_dump(exclude_none=True)
    if "ref_asset_ids" in values:
        values["ref_asset_ids"] = jdumps(values["ref_asset_ids"])
    if values:
        update("characters", character_id, values)
    return character_payload(character_or_404(character_id))


@router.delete("/{character_id}")
def remove(character_id: str, user: dict = AuthUser) -> dict:
    character_or_404(character_id)
    from ..db import execute

    execute("DELETE FROM characters WHERE id = ?", (character_id,))
    return {"ok": True}


@router.post("/{character_id}/references")
def generate_references(
    character_id: str,
    payload: ReferenceRequest,
    user: dict = AuthUser,
    wait: bool = False,
):
    """
    Generate reference images for a character and attach them automatically.

    Uses the character's locked seed so every reference shares the same
    procedural identity — the consistency mechanism the shot generator reuses.
    """
    character = character_or_404(character_id)
    base = payload.prompt or " ".join(
        part
        for part in [
            character["name"],
            character["appearance"] or character["description"],
            character["style"],
            "character reference portrait",
        ]
        if part
    )
    job = enqueue(
        "image.generate",
        {
            "prompt": base,
            "width": payload.width,
            "height": payload.height,
            "seed": character["seed"] or None,
            "character_ids": [character_id],
            "provider": payload.provider,
        },
        project_id=character["project_id"],
        label=f"References: {character['name']}",
        provider=payload.provider,
    )
    from ..routes.generate import _respond

    if not wait:
        return {"job_id": job["id"], "queued": True, "prompt": base, "seed": character["seed"]}
    response = _respond(job, True)
    new_ids = response.get("asset_ids", [])
    if new_ids:
        refs = jloads(character["ref_asset_ids"], []) + new_ids
        update("characters", character_id, {"ref_asset_ids": jdumps(refs)})
        response["character"] = character_payload(character_or_404(character_id))
    return response


@router.post("/{character_id}/sheet")
def build_sheet(character_id: str, user: dict = AuthUser, wait: bool = False):
    character = character_or_404(character_id)
    refs = jloads(character["ref_asset_ids"], [])
    if not refs:
        raise HTTPException(
            status_code=400,
            detail="Generate at least one reference image before building a character sheet.",
        )
    job = enqueue(
        "character.sheet",
        {"character_id": character_id, "columns": 3},
        project_id=character["project_id"],
        label=f"Sheet: {character['name']}",
    )
    from ..routes.generate import _respond

    return _respond(job, wait)


@router.get("/{character_id}/assets")
def character_assets(character_id: str, user: dict = AuthUser) -> dict:
    character = character_or_404(character_id)
    refs = jloads(character["ref_asset_ids"], [])
    items = [hydrate_asset(query_one("SELECT * FROM assets WHERE id = ?", (a,))) for a in refs]
    return {"items": [i for i in items if i], "total": len(refs)}
