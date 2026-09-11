"""Prompt engine API (§5)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.ids import new_id
from app.core.security import CurrentUser, get_current_user
from app.db.models import Prompt
from app.db.session import get_db
from app.services import prompt_engine

router = APIRouter(prefix="/prompts", tags=["prompts"])


class TransformIn(BaseModel):
    prompt: str
    kind: str = "enhance"
    style: str = "cinematic"


class PromptIn(BaseModel):
    text: str
    enhanced_text: str = ""
    structured: dict[str, Any] = {}
    tags: list[str] = []
    is_favorite: bool = False


@router.get("/transforms")
def transforms(current: CurrentUser = Depends(get_current_user)) -> Any:
    return {
        "transforms": [
            {"id": "enhance", "label": "Enhance prompt", "description": "Expand into a structured cinematic prompt"},
            {"id": "shorten", "label": "Shorten prompt", "description": "Condense to the essentials"},
            {"id": "cinematic", "label": "Cinematic", "description": "Film-still styling"},
            {"id": "advertisement", "label": "Advertisement", "description": "Commercial polish"},
            {"id": "product", "label": "Product", "description": "Studio product framing"},
            {"id": "character", "label": "Character", "description": "Consistent character sheet"},
            {"id": "kids-story", "label": "Kids story", "description": "Warm storybook illustration"},
            {"id": "social-media", "label": "Social media", "description": "Punchy vertical content"},
        ],
        "styles": sorted(prompt_engine.STYLE_PACKS.keys()),
        "languages": prompt_engine.LANGUAGE_NAMES,
    }


@router.post("/transform")
def transform(payload: TransformIn, current: CurrentUser = Depends(get_current_user)) -> Any:
    result = prompt_engine.apply_transform(payload.kind, payload.prompt)
    result["structured"] = prompt_engine.structure_prompt(payload.prompt, style=payload.style)
    result["kind"] = payload.kind
    return result


@router.post("/structure")
def structure(payload: TransformIn, current: CurrentUser = Depends(get_current_user)) -> Any:
    structured = prompt_engine.structure_prompt(payload.prompt, style=payload.style)
    return {"original": payload.prompt, "structured": structured,
            "composed": prompt_engine.compose_prompt(structured)}


@router.get("")
def list_prompts(db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user),
                 favorite: Optional[bool] = None, limit: int = Query(50, ge=1, le=200)) -> Any:
    query = db.query(Prompt).filter(Prompt.owner_id == current.id)
    if favorite is not None:
        query = query.filter(Prompt.is_favorite == favorite)
    items = query.order_by(Prompt.updated_at.desc()).limit(limit).all()
    return {"items": [p.to_dict(exclude=set()) for p in items]}


@router.post("", status_code=201)
def save_prompt(payload: PromptIn, db: Session = Depends(get_db),
                current: CurrentUser = Depends(get_current_user)) -> Any:
    prompt = Prompt(
        id=new_id("prm_"), owner_id=current.id, text=payload.text,
        enhanced_text=payload.enhanced_text or prompt_engine.enhance(payload.text)["enhanced"],
        structured=payload.structured or prompt_engine.structure_prompt(payload.text),
        tags=payload.tags, is_favorite=payload.is_favorite,
    )
    db.add(prompt)
    db.commit()
    return prompt.to_dict(exclude=set())


@router.patch("/{prompt_id}")
def update_prompt(prompt_id: str, payload: dict, db: Session = Depends(get_db),
                  current: CurrentUser = Depends(get_current_user)) -> Any:
    prompt = db.get(Prompt, prompt_id)
    if not prompt or prompt.owner_id != current.id:
        raise NotFoundError("Prompt not found.")
    for key in ("text", "enhanced_text", "is_favorite"):
        if key in payload:
            setattr(prompt, key, payload[key])
    if "tags" in payload:
        prompt.tags = payload["tags"]
    db.commit()
    return prompt.to_dict(exclude=set())


@router.delete("/{prompt_id}")
def delete_prompt(prompt_id: str, db: Session = Depends(get_db),
                  current: CurrentUser = Depends(get_current_user)) -> Any:
    prompt = db.get(Prompt, prompt_id)
    if not prompt or prompt.owner_id != current.id:
        raise NotFoundError("Prompt not found.")
    db.delete(prompt)
    db.commit()
    return {"ok": True, "id": prompt_id}
