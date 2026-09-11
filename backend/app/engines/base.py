"""Shared plumbing for every generation engine.

An engine:
  1. builds a GenerationRequest from API parameters (resolving assets -> files)
  2. resolves + validates a model adapter (never silently downgrades)
  3. runs it with progress + cancellation
  4. persists every produced file as an Asset with provenance
  5. returns asset ids so the UI can render results immediately

If any step cannot be performed for real, it raises a StudioError carrying a
machine readable code (MODEL_NOT_AVAILABLE / HARDWARE_REQUIREMENT_NOT_MET /
EXTERNAL_PROVIDER_REQUIRED / DEPENDENCY_MISSING) - never a fake result.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.adapters.base import GenerationRequest, GenerationResult, Reference
from app.adapters.base import BaseModelAdapter
from app.core.config import settings
from app.core.errors import ErrorCode, StudioError
from app.db.models import Asset, Character, Generation, User
from app.services import assets as asset_svc
from app.services import safety
from app.services.procedural_render import build_identity


def workdir(prefix: str = "gen") -> Path:
    root = Path(settings.STORAGE_DIR).parent / "tmp"
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{prefix}-", dir=str(root)))


def resolve_references(db: Session, user: User, refs: list[dict] | None) -> list[Reference]:
    """Convert {asset_id, role} payloads into local file paths."""
    out: list[Reference] = []
    for item in refs or []:
        asset_id = item.get("asset_id") or item.get("id")
        if not asset_id:
            continue
        asset = db.get(Asset, asset_id)
        if not asset or asset.owner_id != user.id:
            raise StudioError(f"Reference asset '{asset_id}' not found.", code=ErrorCode.NOT_FOUND,
                              status_code=404)
        out.append(Reference(
            path=asset_svc.asset_abs_path(asset),
            kind=asset.kind if asset.kind in ("image", "video", "audio") else "image",
            role=item.get("role", "reference"),
            weight=float(item.get("weight", 1.0)),
            meta={"asset_id": asset.id, "name": asset.name},
        ))
    return out


def character_context(db: Session, character_ids: list[str] | None) -> dict:
    """Build the consistency context passed to adapters (spec §20)."""
    if not character_ids:
        return {}
    specs = []
    for cid in character_ids:
        ch = db.get(Character, cid)
        if not ch:
            continue
        ident = build_identity(
            ch.name,
            f"{ch.description or ''} {ch.clothing or ''} {ch.hair or ''} {ch.age_appearance or ''}",
            seed=ch.identity_seed,
        )
        if not ch.identity_seed:
            ch.identity_seed = ident.seed
        specs.append({
            "id": ch.id,
            "name": ch.name,
            "description": ch.description or "",
            "face": ch.face or "",
            "hair": ch.hair or "",
            "age_appearance": ch.age_appearance or "",
            "body": ch.body or "",
            "clothing": ch.clothing or "",
            "accessories": ch.accessories or "",
            "personality": ch.personality or "",
            "style": ch.style or "",
            "environment": ch.environment or "",
            "identity_seed": ch.identity_seed,
            "locks": ch.locks or {},
            "palette": ident.palette,
            "prompt_fragment": ch.prompt_fragment or "",
            "reference_asset_ids": ch.reference_asset_ids or [],
        })
    return {"characters": specs} if specs else {}


def build_request(
    db: Session,
    user: User,
    params: dict,
    output_dir: str,
    capability: str,
    references: list[dict] | None = None,
) -> GenerationRequest:
    width = int(params.get("width") or settings.DEFAULT_IMAGE_WIDTH)
    height = int(params.get("height") or settings.DEFAULT_IMAGE_HEIGHT)
    aspect = str(params.get("aspect_ratio") or "1:1")
    try:
        aw, ah = (float(x) for x in aspect.split(":"))
        if aw and ah:
            # Honour the requested aspect ratio by fitting within the pixel budget.
            budget = width * height
            height = int(round((budget / (aw / ah)) ** 0.5))
            width = int(round(height * (aw / ah)))
    except Exception:
        pass
    width = max(64, min(width, 4096)) - (max(64, min(width, 4096)) % 2)
    height = max(64, min(height, 4096)) - (max(64, min(height, 4096)) % 2)

    return GenerationRequest(
        prompt=params.get("prompt", ""),
        negative_prompt=params.get("negative_prompt", ""),
        width=width,
        height=height,
        steps=int(params.get("steps", 30) or 30),
        guidance=float(params.get("guidance", 7.5) or 7.5),
        strength=float(params.get("strength", 0.7) or 0.7),
        seed=params.get("seed"),
        num_images=int(params.get("num_images", 1) or 1),
        duration=float(params.get("duration", settings.DEFAULT_VIDEO_DURATION) or settings.DEFAULT_VIDEO_DURATION),
        fps=int(params.get("fps", settings.DEFAULT_VIDEO_FPS) or settings.DEFAULT_VIDEO_FPS),
        aspect_ratio=aspect,
        output_dir=output_dir,
        params=params,
        references=resolve_references(db, user, references or params.get("references")),
        character_context=character_context(db, params.get("character_ids")),
        structured_prompt=params.get("structured_prompt") or {},
    )


def persist_result(
    db: Session,
    *,
    user: User,
    result: GenerationResult,
    generation: Generation | None,
    adapter: BaseModelAdapter,
    project_id: str | None = None,
    parent_asset_id: str | None = None,
    kind_override: str | None = None,
    asset_meta: dict | None = None,
) -> list[Asset]:
    """Store every produced file and link it to the Generation row."""
    created: list[Asset] = []
    for gf in result.files:
        kind = kind_override or gf.kind
        provenance = safety.build_provenance(
            generator="AI Creative Studio",
            adapter=adapter.key,
            prompt=getattr(generation, "prompt", "") or "",
            seed=result.seed,
            user_id=user.id,
            project_id=project_id,
            parameters=getattr(generation, "parameters", {}) or {},
            parents=([parent_asset_id] if parent_asset_id else []),
            watermarked=bool(gf.meta.get("watermarked")) if gf.meta else False,
        )
        asset = asset_svc.create_asset(
            db, user=user, path=gf.path, kind=kind,
            name=Path(gf.path).name,
            project_id=project_id,
            source="generated",
            parent_asset_id=parent_asset_id,
            provenance=provenance,
            safety={"checked": True, "renderer": adapter.key},
            meta={**(asset_meta or {}), **(gf.meta or {})},
        )
        created.append(asset)
    if generation is not None:
        generation.output_asset_ids = [a.id for a in created]
        generation.status = "completed"
        generation.model_id = adapter.key
        generation.adapter = adapter.key
        generation.seed = result.seed
        generation.duration_seconds = result.meta.get("duration_seconds")
        db.flush()
    return created


def mark_generation_failed(db: Session, generation: Generation | None, code: str, message: str) -> None:
    if generation is None:
        return
    generation.status = "failed"
    generation.error_code = code
    generation.error_message = message[:4000]
    db.flush()
