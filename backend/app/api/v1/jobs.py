"""Job endpoints and live status (§22)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, NotFoundError, StudioError
from app.core.logging import get_logger
from app.core.security import CurrentUser, get_current_user
from app.db.models import Job
from app.db.session import get_db
from app.jobs import queue

log = get_logger("api.jobs")
router = APIRouter(prefix="/jobs", tags=["jobs"])


def _job_out(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "type": job.type,
        "mode": job.mode,
        "engine": job.engine,
        "model_id": job.model_id,
        "status": job.status,
        "progress": round(job.progress or 0, 1),
        "stage": job.stage,
        "params": job.params,
        "result": job.result,
        "error": job.error,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "worker_id": job.worker_id,
        "gpu": job.gpu,
        "project_id": job.project_id,
        "queued_at": job.queued_at.isoformat() if job.queued_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "duration_seconds": queue.elapsed_seconds(job),
    }


@router.get("")
def list_jobs(
    *,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
    status: Optional[str] = None,
    type: Optional[str] = None,
    project_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Any:
    # Admins can watch every job; users see only their own.
    owner = None if current.is_admin else current.id
    items, total = queue.list_jobs(owner_id=owner, status=status, type=type, project_id=project_id,
                                   limit=limit, offset=offset)
    return {"items": [_job_out(j) for j in items], "total": total, "limit": limit, "offset": offset}


@router.get("/stats")
def stats(current: CurrentUser = Depends(get_current_user)) -> Any:
    return queue.stats()


@router.get("/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db),
            current: CurrentUser = Depends(get_current_user)) -> Any:
    job = queue.get(job_id)
    if not job:
        raise NotFoundError("Job not found.", suggested_action="It may have been cleaned up.")
    if job.owner_id != current.id and not current.is_admin:
        raise ForbiddenError("You do not have access to this job.")
    return _job_out(job)


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, current: CurrentUser = Depends(get_current_user)) -> Any:
    job = queue.get(job_id)
    if not job:
        raise NotFoundError("Job not found.")
    if job.owner_id != current.id and not current.is_admin:
        raise ForbiddenError("You do not have access to this job.")
    owner = None if current.is_admin else current.id
    queue.cancel(job_id, owner_id=owner)
    return {"id": job_id, "status": "cancelling"}


@router.post("/{job_id}/retry")
def retry_job(job_id: str, db: Session = Depends(get_db),
              current: CurrentUser = Depends(get_current_user)) -> Any:
    job = queue.get(job_id)
    if not job:
        raise NotFoundError("Job not found.")
    if job.owner_id != current.id and not current.is_admin:
        raise ForbiddenError("You do not have access to this job.")
    if job.status not in ("failed", "cancelled"):
        raise StudioError("Only failed or cancelled jobs can be retried.",
                          suggested_action="Wait for the current job to finish.")
    queue.fail(job_id, {"code": "retry", "message": "Retry requested."}, retry=True)
    return {"id": job_id, "status": "queued"}
