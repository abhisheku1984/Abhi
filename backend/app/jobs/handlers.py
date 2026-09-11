"""Job execution handlers — the bridge from queued jobs to model adapters."""

from __future__ import annotations

import shutil
import time
import traceback
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import JobCancelled, StudioError
from app.core.logging import get_logger
from app.db.models import Asset, Character, Generation, Job, Voice
from app.db.session import session_scope
from app.engines.base import GenerateRequest, JobContext, Reference
from app.engines.registry import registry
from app.jobs import queue
from app.safety import moderation
from app.services import assets as asset_service

log = get_logger("jobs.handlers")


# --------------------------------------------------------------------------- #
# Request assembly
# --------------------------------------------------------------------------- #

def _resolve_references(db: Session, raw: list[dict[str, Any]]) -> list[Reference]:
    refs: list[Reference] = []
    storage = None
    from app.storage.backends import get_storage

    storage = get_storage()
    for item in raw or []:
        asset_id = item.get("asset_id")
        path = item.get("path")
        if asset_id and not path:
            asset = db.get(Asset, asset_id)
            if asset is None:
                log.warning("reference_asset_missing", asset_id=asset_id)
                continue
            path = storage.local_path(asset.storage_key)
        if not path:
            continue
        refs.append(
            Reference(
                asset_id=asset_id,
                path=path,
                kind=item.get("kind", "image"),
                weight=float(item.get("weight", 1.0) or 1.0),
                role=item.get("role", "reference"),
            )
        )
    return refs


def build_request(db: Session, job: Job) -> GenerateRequest:
    params: dict[str, Any] = dict(job.params or {})
    character = None
    voice = None
    character_id = params.pop("character_id", None)
    voice_id = params.pop("voice_id", None)
    if character_id:
        char = db.get(Character, character_id)
        if char:
            character = {
                "id": char.id,
                "name": char.name,
                "profile": char.profile,
                "locks": char.locks,
                "reference_asset_ids": char.reference_asset_ids,
                "voice_id": char.voice_id,
            }
    if voice_id:
        v = db.get(Voice, voice_id)
        if v:
            voice = {
                "id": v.id, "name": v.name, "engine": v.engine, "language": v.language,
                "accent": v.accent, "style": v.style, "emotion": v.emotion,
                "speed": v.speed, "pitch": v.pitch, "is_cloned": v.is_cloned, "consent": v.consent,
                "sample_asset_id": v.sample_asset_id,
            }

    return GenerateRequest(
        mode=job.mode or params.get("mode", "text-to-image"),
        prompt=params.get("prompt", ""),
        negative_prompt=params.get("negative_prompt", ""),
        params={k: v for k, v in params.items() if k not in ("prompt", "negative_prompt")},
        references=_resolve_references(db, params.get("references", [])),
        character=character,
        voice=voice,
        model_id=job.model_id,
        owner_id=job.owner_id,
        project_id=job.project_id,
    )


def validate_request(db: Session, job: Job) -> list[str]:
    """Run synchronously at enqueue time so the UI can show real errors."""
    errors: list[str] = []
    request = build_request(db, job)
    decision = moderation.check_generation(request.prompt, negative_prompt=request.negative_prompt,
                                           voice=request.voice)
    if not decision.allowed:
        errors.extend(decision.reasons)
    try:
        adapter = registry.resolve(job.type, request.mode, job.model_id)
    except StudioError as exc:
        errors.append(exc.user_message)
        return errors
    result = adapter.validate(request)
    errors.extend(result.errors)
    return errors


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #

def execute_job(job_id: str) -> None:
    started = time.time()
    work_dir = settings.data_root / "jobs" / job_id
    try:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                log.error("job_missing", job_id=job_id)
                return
            if job.status == "cancelled":
                return
            request = build_request(db, job)
            engine = job.engine
            model_id = job.model_id

        adapter = registry.resolve(job.type, request.mode, model_id)

        decision = moderation.check_generation(request.prompt, negative_prompt=request.negative_prompt,
                                               voice=request.voice)
        if not decision.allowed:
            raise StudioError(
                "This request was blocked by the safety policy.",
                detail="; ".join(decision.reasons),
                suggested_action="Edit your prompt, or ask an administrator to review the moderation settings.",
                code="safety_blocked",
            )

        ctx = JobContext(
            job_id=job_id,
            owner_id=request.owner_id,
            project_id=request.project_id,
            work_dir=work_dir,
            progress_cb=lambda value, stage: queue.progress(job_id, value, stage),
            cancel_cb=lambda: queue.is_cancelled(job_id),
        )

        with session_scope() as db:
            pending = db.get(Job, job_id)
            if pending is not None and pending.started_at is None:
                # Jobs executed inline (tests, story children) still record a start time.
                pending.started_at = queue.utcnow()
                pending.worker_id = pending.worker_id or "inline"
        queue.progress(job_id, 3, "starting")
        artifacts = adapter.generate(request, ctx)

        asset_ids: list[str] = []
        with session_scope() as db:
            job = db.get(Job, job_id)
            for artifact in artifacts:
                if not artifact.exists():
                    log.error("artifact_missing", job_id=job_id, path=artifact.path)
                    continue
                meta = dict(artifact.meta)
                meta["character_id"] = (request.character or {}).get("id")
                meta["voice_id"] = (request.voice or {}).get("id")
                meta["warnings"] = decision.warnings
                asset = asset_service.create_asset(
                    db,
                    owner_id=request.owner_id,
                    path=artifact.path,
                    kind=artifact.kind,
                    name=artifact.name or f"{job.type}-{job.mode}",
                    project_id=request.project_id,
                    meta=meta,
                )
                asset_ids.append(asset.id)

                generation = Generation(
                    id=f"gen_{asset.id}",
                    owner_id=request.owner_id,
                    project_id=request.project_id,
                    job_id=job_id,
                    kind=job.type,
                    mode=job.mode,
                    engine=artifact.meta.get("engine", engine),
                    model_id=model_id,
                    prompt=request.prompt,
                    negative_prompt=request.negative_prompt,
                    params=request.params,
                    seed=artifact.meta.get("seed"),
                    status="completed",
                    asset_id=asset.id,
                    duration_ms=int((time.time() - started) * 1000),
                    credits=1.0,
                )
                db.add(generation)
            job = db.get(Job, job_id)

        queue.complete(job_id, {"asset_ids": asset_ids, "count": len(asset_ids)})
        _track_usage(request.owner_id, job.type, len(asset_ids))

    except JobCancelled:
        log.info("job_cancelled_during_run", job_id=job_id)
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job and job.status != "cancelled":
                job.status = "cancelled"
                job.stage = "cancelled"
                job.finished_at = queue.utcnow()
        queue.progress(job_id, 100, "cancelled")
    except StudioError as exc:
        log.error("job_studio_error", job_id=job_id, code=exc.code, detail=exc.detail)
        queue.fail(job_id, {
            "code": exc.code,
            "message": exc.user_message,
            "suggested_action": exc.suggested_action,
            "meta": exc.meta,
        }, retry=exc.code in ("internal_error",))
    except Exception as exc:  # noqa: BLE001 - never leak a traceback to the user
        log.exception("job_exception", job_id=job_id, error=str(exc))
        queue.fail(job_id, {
            "code": "generation_failed",
            "message": "Generation failed.",
            "suggested_action": "Retry / Change model / Check GPU",
            "detail": str(exc),
        }, retry=True)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _track_usage(owner_id: str, kind: str, count: int) -> None:
    from datetime import date

    from app.db.models import Usage

    with session_scope() as db:
        day = date.today().isoformat()
        row = (
            db.query(Usage)
            .filter(Usage.owner_id == owner_id, Usage.day == day, Usage.metric == kind)
            .one_or_none()
        )
        if row is None:
            row = Usage(id=f"use_{owner_id}_{day}_{kind}", owner_id=owner_id, day=day, metric=kind, value=0)
            db.add(row)
        row.value = float(row.value or 0) + count


def wait_for_job(job_id: str, *, timeout: int = 1800, poll: float = 0.4) -> Optional[Job]:
    """Block until a queued job reaches a terminal state."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = queue.get(job_id)
        if job and job.status in ("completed", "failed", "cancelled"):
            return job
        time.sleep(poll)
    return queue.get(job_id)


def run_job_sync(*, owner_id: str, type: str, mode: str, project_id: Optional[str] = None,
                 params: Optional[dict] = None, model_id: Optional[str] = None,
                 timeout: int = 1800) -> Optional[Job]:
    """Enqueue a child job and wait for the worker pool to finish it.

    Never execute inline while workers are running: that would run the job twice
    (once by the pool, once inline) and corrupt its output files.
    """
    from app.jobs.worker import pool

    job = queue.enqueue(None, owner_id=owner_id, type=type, mode=mode, project_id=project_id,
                        params=params or {}, model_id=model_id)
    if not pool.workers:
        dispatch(job)
    return wait_for_job(job.id, timeout=timeout)


def handle_story_job(job_id: str) -> None:
    from app.services.story import run_story_job

    run_story_job(job_id)


def handle_render_job(job_id: str) -> None:
    from app.services.editor import run_render_job

    run_render_job(job_id)


def handle_workflow_job(job_id: str) -> None:
    from app.services.workflow import run_workflow_job

    run_workflow_job(job_id)


HANDLERS = {
    "story": handle_story_job,
    "storyboard": handle_story_job,
    "render": handle_render_job,
    "edit": handle_render_job,
    "workflow": handle_workflow_job,
}


def dispatch(job: Job) -> None:
    handler = HANDLERS.get(job.type, execute_job)
    handler(job.id)
