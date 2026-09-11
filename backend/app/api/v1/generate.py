"""Generation endpoints (§26).

Every call validates first (so "Model not installed" is reported immediately),
then enqueues a real background job and returns its id (§22).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.errors import ModelNotInstalledError, ProviderNotConfiguredError, ValidationFailed
from app.engines.registry import ModelUnavailable
from app.core.logging import get_logger
from app.core.security import CurrentUser, get_current_user
from app.db.models import Job
from app.db.session import get_db
from app.engines.registry import registry
from app.jobs import handlers, queue

log = get_logger("api.generate")
router = APIRouter(prefix="/generate", tags=["generate"])

FAMILY_BY_KIND = {
    "image": "image", "upscale": "upscale", "video": "video", "avatar": "avatar",
    "voice": "voice", "audio": "audio", "lipsync": "lipsync",
}


class GenerateIn(BaseModel):
    prompt: str = ""
    negative_prompt: str = ""
    mode: str = ""
    model_id: Optional[str] = None
    project_id: Optional[str] = None
    character_id: Optional[str] = None
    voice_id: Optional[str] = None
    references: list[dict[str, Any]] = []
    params: dict[str, Any] = {}
    priority: int = 5


class EstimateIn(GenerateIn):
    kind: str = "image"


def _defaults(kind: str) -> str:
    return {
        "image": "text-to-image", "upscale": "upscale", "video": "text-to-video",
        "avatar": "talking-avatar", "voice": "text-to-speech", "audio": "background-music",
        "lipsync": "lip-sync",
    }[kind]


def _require_ready(adapter) -> None:
    """§42.3 — refuse early with the precise reason when a model cannot run.

    "Model not installed" and "Provider not configured" are different problems
    with different fixes, so they must never collapse into one vague error.
    """
    info = adapter.status() or {}
    state = info.get("status", "installed")
    if state in ("installed", "available"):
        return
    reason = info.get("reason") or ""
    # Remote adapters need credentials; local adapters need weights.
    if not getattr(adapter, "is_local", True) or state == "not_configured":
        error = ProviderNotConfiguredError(adapter.display_name)
        error.meta["reason"] = reason
        raise error
    error = ModelNotInstalledError(adapter.display_name, install_hint=reason or None)
    error.meta["reason"] = reason
    raise error


def _enqueue(kind: str, payload: GenerateIn, db: Session, current: CurrentUser) -> dict[str, Any]:
    family = FAMILY_BY_KIND[kind]
    mode = payload.mode or _defaults(kind)
    params = dict(payload.params)
    params.update({
        "prompt": payload.prompt,
        "negative_prompt": payload.negative_prompt,
        "references": payload.references,
        "character_id": payload.character_id,
        "voice_id": payload.voice_id,
    })

    try:
        adapter = registry.resolve(family, mode, payload.model_id)
    except ModelUnavailable as exc:
        raise ValidationFailed(exc.user_message, suggested_action=exc.suggested_action,
                               meta={"errors": [exc.user_message]})

    # §42.3 — say exactly what is missing instead of failing later with a
    # generic error: an absent model and an unconfigured provider are
    # different problems with different fixes.
    _require_ready(adapter)

    job = queue.enqueue(
        db,
        owner_id=current.id,
        type=family,
        mode=mode,
        engine=adapter.id,
        model_id=adapter.id,
        project_id=payload.project_id,
        params=params,
        priority=payload.priority,
    )

    errors = handlers.validate_request(db, job)
    if errors:
        # Roll the job back rather than leaving an impossible job in the queue.
        db.delete(job)
        db.commit()
        raise ValidationFailed(
            "This request cannot be generated yet.",
            suggested_action="Fix the highlighted issues and try again.",
            meta={"errors": errors},
        )

    try:
        estimate = adapter.estimate(handlers.build_request(db, job))
    except Exception:
        estimate = None

    log.info("generation_enqueued", job_id=job.id, kind=family, mode=mode, engine=adapter.id)
    return {
        "job_id": job.id,
        "status": job.status,
        "kind": family,
        "mode": mode,
        "engine": adapter.id,
        "engine_name": adapter.display_name,
        "estimate_seconds": estimate.seconds if estimate else None,
        "status_url": f"/api/v1/jobs/{job.id}",
    }


@router.get("/modes")
def modes(current: CurrentUser = Depends(get_current_user)) -> Any:
    """Capability map used to build the UI (no model logic lives in the UI)."""
    return {
        "families": {
            family: [
                {"id": a.id, "name": a.display_name, "modes": a.capabilities.modes,
                 "status": a.status().get("status"), "reason": a.status().get("reason", ""),
                 "ready": a.status().get("status") in ("installed", "available"),
                 "license": a.license, "vram_mb": a.vram_mb, "is_local": a.is_local,
                 "capabilities": a.capabilities.to_dict(), "schema": a.param_schema()}
                for a in registry.by_family(family)
            ]
            for family in ("image", "upscale", "video", "avatar", "voice", "audio", "lipsync")
        }
    }


@router.post("/estimate")
def estimate(payload: EstimateIn, db: Session = Depends(get_db),
             current: CurrentUser = Depends(get_current_user)) -> Any:
    kind = payload.kind if payload.kind in FAMILY_BY_KIND else "image"
    family = FAMILY_BY_KIND[kind]
    mode = payload.mode or _defaults(kind)
    adapter = registry.resolve(family, mode, payload.model_id)
    request = handlers.build_request(db, Job(id="tmp", owner_id=current.id, type=family, mode=mode,
                                             params={**payload.params, "prompt": payload.prompt,
                                                     "negative_prompt": payload.negative_prompt,
                                                     "references": payload.references,
                                                     "character_id": payload.character_id,
                                                     "voice_id": payload.voice_id}))
    est = adapter.estimate(request)
    validation = adapter.validate(request)
    return {
        "engine": adapter.id,
        "engine_name": adapter.display_name,
        "seconds": est.seconds,
        "vram_mb": est.vram_mb,
        "credits": est.credits,
        "notes": est.notes,
        "validation": {"ok": validation.ok, "errors": validation.errors, "warnings": validation.warnings},
    }


@router.post("/image")
def generate_image(payload: GenerateIn, db: Session = Depends(get_db),
                   current: CurrentUser = Depends(get_current_user)) -> Any:
    current.require("generation:create")
    return _enqueue("image", payload, db, current)


@router.post("/upscale")
def generate_upscale(payload: GenerateIn, db: Session = Depends(get_db),
                     current: CurrentUser = Depends(get_current_user)) -> Any:
    current.require("generation:create")
    return _enqueue("upscale", payload, db, current)


@router.post("/video")
def generate_video(payload: GenerateIn, db: Session = Depends(get_db),
                   current: CurrentUser = Depends(get_current_user)) -> Any:
    current.require("generation:create")
    return _enqueue("video", payload, db, current)


@router.post("/avatar")
def generate_avatar(payload: GenerateIn, db: Session = Depends(get_db),
                    current: CurrentUser = Depends(get_current_user)) -> Any:
    current.require("generation:create")
    return _enqueue("avatar", payload, db, current)


@router.post("/audio")
def generate_audio(payload: GenerateIn, db: Session = Depends(get_db),
                   current: CurrentUser = Depends(get_current_user)) -> Any:
    current.require("generation:create")
    return _enqueue("audio", payload, db, current)


@router.post("/voice")
def generate_voice(payload: GenerateIn, db: Session = Depends(get_db),
                   current: CurrentUser = Depends(get_current_user)) -> Any:
    current.require("generation:create")
    return _enqueue("voice", payload, db, current)


@router.post("/lipsync")
def generate_lipsync(payload: GenerateIn, db: Session = Depends(get_db),
                     current: CurrentUser = Depends(get_current_user)) -> Any:
    current.require("generation:create")
    return _enqueue("lipsync", payload, db, current)
