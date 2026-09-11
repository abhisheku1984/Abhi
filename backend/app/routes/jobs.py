"""Job queue routes: enqueue, inspect, cancel, retry, delete."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..db import query_one
from ..jobs import (
    JOB_HANDLERS,
    available_kinds,
    cancel_job,
    delete_job,
    enqueue,
    get_job,
    list_jobs,
    queue_stats,
    retry_job,
)
from ..security import AuthUser

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


class JobCreate(BaseModel):
    kind: str
    params: dict = Field(default_factory=dict)
    project_id: str | None = None
    label: str = ""
    priority: int = 5
    provider: str = "auto"
    max_attempts: int = 1


@router.get("")
def index(
    user: dict = AuthUser,
    status: str | None = None,
    project_id: str | None = None,
    kind: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    return list_jobs(
        status=status, project_id=project_id, kind=kind, limit=limit, offset=offset
    )


@router.get("/stats")
def stats(user: dict = AuthUser) -> dict:
    return queue_stats()


@router.get("/kinds")
def kinds(user: dict = AuthUser) -> dict:
    """Registered job kinds with their parameter contract, for the workflow builder."""
    return {
        "kinds": [
            {
                "kind": kind,
                "handler": JOB_HANDLERS[kind].__name__,
                "doc": (JOB_HANDLERS[kind].__doc__ or "").strip().split("\n")[0],
            }
            for kind in available_kinds()
        ]
    }


@router.post("")
def create(payload: JobCreate, user: dict = AuthUser) -> dict:
    if payload.project_id:
        if not query_one("SELECT id FROM projects WHERE id = ?", (payload.project_id,)):
            raise HTTPException(status_code=404, detail="Project not found.")
    try:
        job = enqueue(
            payload.kind,
            payload.params,
            project_id=payload.project_id,
            label=payload.label,
            priority=payload.priority,
            provider=payload.provider,
            max_attempts=payload.max_attempts,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return get_job(job["id"])


@router.get("/{job_id}")
def detail(job_id: str, user: dict = AuthUser) -> dict:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@router.post("/{job_id}/cancel")
def cancel(job_id: str, user: dict = AuthUser) -> dict:
    job = cancel_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@router.post("/{job_id}/retry")
def retry(job_id: str, user: dict = AuthUser) -> dict:
    original = get_job(job_id)
    if not original:
        raise HTTPException(status_code=404, detail="Job not found.")
    if original["status"] not in ("failed", "cancelled"):
        raise HTTPException(status_code=400, detail="Only failed or cancelled jobs can be retried.")
    retry_job(job_id)
    jobs = list_jobs(kind=original["kind"], limit=1)
    return jobs["items"][0] if jobs["items"] else original


@router.delete("/{job_id}")
def remove(job_id: str, user: dict = AuthUser) -> dict:
    if not get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    if not delete_job(job_id):
        raise HTTPException(status_code=400, detail="Running jobs cannot be deleted. Cancel it first.")
    return {"ok": True}
