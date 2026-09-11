"""Durable job queue (§22).

Backed by the database so jobs survive restarts; a Redis/RQ implementation can
replace it without touching callers. All heavy AI work goes through here —
the HTTP request returns immediately with a real job id.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.ids import new_id
from app.core.logging import get_logger
from app.db.models import Job
from app.jobs.events import bus

log = get_logger("jobs.queue")

TERMINAL_STATES = {"completed", "failed", "cancelled"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def job_channel(job_id: str) -> str:
    return f"job:{job_id}"


# --------------------------------------------------------------------------- #
# Enqueue / lifecycle
# --------------------------------------------------------------------------- #

def enqueue(
    db: Optional[Session] = None,
    *,
    owner_id: str,
    type: str,
    mode: str = "",
    engine: str = "",
    model_id: Optional[str] = None,
    params: Optional[dict[str, Any]] = None,
    project_id: Optional[str] = None,
    priority: int = 5,
) -> Job:
    from app.db.session import session_scope

    def _create(session: Session) -> Job:
        job = Job(
            id=new_id("job_"),
            owner_id=owner_id,
            project_id=project_id,
            type=type,
            mode=mode,
            engine=engine,
            model_id=model_id,
            status="queued",
            progress=0.0,
            stage="queued",
            params=params or {},
            priority=priority,
            max_attempts=settings.JOB_MAX_ATTEMPTS,
            queued_at=utcnow(),
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job

    if db is not None:
        job = _create(db)
    else:
        with session_scope() as session:
            job = _create(session)
    log.info("job_enqueued", job_id=job.id, type=type, mode=mode, engine=engine)
    _publish(job)
    return job


def claim(worker_id: str) -> Optional[Job]:
    """Atomically take the next queued job (safe on SQLite and PostgreSQL)."""
    from app.db.session import session_scope
    from app.db.models import Job as _Job

    with session_scope() as db:
        job = (
            db.query(_Job)
            .filter(_Job.status == "queued")
            .order_by(_Job.priority.asc(), _Job.queued_at.asc())
            .first()
        )
        if not job:
            return None
        res = db.execute(
            update(_Job)
            .where(_Job.id == job.id, _Job.status == "queued")
            .values(status="processing", started_at=utcnow(), heartbeat_at=utcnow(),
                    worker_id=worker_id, attempts=_Job.attempts + 1, stage="starting", progress=1.0)
        )
        db.commit()
        if res.rowcount != 1:
            return None
        db.refresh(job)
        log.info("job_claimed", job_id=job.id, worker=worker_id)
        _publish(job)
        return job


def progress(job_id: str, value: float, stage: str = "") -> None:
    from app.db.session import session_scope

    with session_scope() as db:
        job = db.get(Job, job_id)
        if not job:
            return
        job.progress = max(0.0, min(100.0, float(value)))
        if stage:
            job.stage = stage
        job.heartbeat_at = utcnow()
        db.commit()
        _publish(job)


def complete(job_id: str, result: dict[str, Any]) -> None:
    from app.db.session import session_scope

    with session_scope() as db:
        job = db.get(Job, job_id)
        if not job:
            return
        job.status = "completed"
        job.progress = 100.0
        job.stage = "completed"
        job.result = result or {}
        job.finished_at = utcnow()
        db.commit()
        log.info("job_completed", job_id=job_id, duration_s=_elapsed(job))
        _publish(job)


def fail(job_id: str, error: dict[str, Any], *, retry: bool = False) -> None:
    from app.db.session import session_scope

    with session_scope() as db:
        job = db.get(Job, job_id)
        if not job:
            return
        if retry and job.attempts < job.max_attempts:
            job.status = "queued"
            job.progress = 0.0
            job.stage = "queued (retry)"
            job.error = error
            job.heartbeat_at = utcnow()
            db.commit()
            log.warning("job_retry_scheduled", job_id=job_id, attempt=job.attempts)
            _publish(job)
            return
        job.status = "failed"
        job.stage = "failed"
        job.error = error
        job.finished_at = utcnow()
        db.commit()
        log.error("job_failed", job_id=job_id, error=(error or {}).get("message"))
        _publish(job)


def cancel(job_id: str, *, owner_id: Optional[str] = None) -> bool:
    from app.db.session import session_scope

    with session_scope() as db:
        job = db.get(Job, job_id)
        if not job:
            return False
        if owner_id and job.owner_id != owner_id:
            return False
        if job.status in TERMINAL_STATES:
            return True
        job.status = "cancelled" if job.status == "queued" else job.status
        job.stage = "cancelling" if job.status == "processing" else "cancelled"
        job.finished_at = utcnow() if job.status == "cancelled" else None
        db.commit()
        log.info("job_cancel_requested", job_id=job_id, status=job.status)
        _publish(job)
        return True


def is_cancelled(job_id: str) -> bool:
    from app.db.session import session_scope

    with session_scope() as db:
        job = db.get(Job, job_id)
        return bool(job and job.status in ("cancelled", "cancelling"))


def requeue_stale(timeout_seconds: int = 900) -> int:
    """Return jobs whose worker died back to the queue (crash recovery)."""
    from app.db.session import session_scope

    cutoff = utcnow() - timedelta(seconds=timeout_seconds)
    with session_scope() as db:
        stale = (
            db.query(Job)
            .filter(Job.status == "processing", Job.heartbeat_at.isnot(None), Job.heartbeat_at < cutoff)
            .all()
        )
        count = 0
        for job in stale:
            if job.attempts >= job.max_attempts:
                job.status = "failed"
                job.error = {"code": "worker_timeout", "message": "The worker stopped responding."}
                job.finished_at = utcnow()
            else:
                job.status = "queued"
                job.stage = "queued (requeued)"
                job.progress = 0.0
            count += 1
        db.commit()
        if count:
            log.warning("jobs_requeued", count=count)
        return count


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #

def get(job_id: str) -> Optional[Job]:
    from app.db.session import session_scope

    with session_scope() as db:
        job = db.get(Job, job_id)
        if job:
            db.expunge(job)
        return job


def list_jobs(*, owner_id: Optional[str] = None, status: Optional[str] = None,
              type: Optional[str] = None, project_id: Optional[str] = None,
              limit: int = 50, offset: int = 0) -> tuple[list[Job], int]:
    from app.db.session import session_scope

    with session_scope() as db:
        q = db.query(Job)
        if owner_id:
            q = q.filter(Job.owner_id == owner_id)
        if status:
            q = q.filter(Job.status == status)
        if type:
            q = q.filter(Job.type == type)
        if project_id:
            q = q.filter(Job.project_id == project_id)
        total = q.count()
        items = q.order_by(Job.queued_at.desc()).offset(offset).limit(limit).all()
        for item in items:
            db.expunge(item)
        return items, total


def stats() -> dict[str, Any]:
    from app.db.session import session_scope

    with session_scope() as db:
        rows = db.query(Job.status, func.count(Job.id)).group_by(Job.status).all()
        counts = {status: count for status, count in rows}
        active = (
            db.query(func.count(Job.id))
            .filter(or_(Job.status == "queued", Job.status == "processing"))
            .scalar() or 0
        )
        oldest = (
            db.query(func.min(Job.queued_at))
            .filter(Job.status == "queued")
            .scalar()
        )
        return {
            "counts": counts,
            "queued": counts.get("queued", 0),
            "processing": counts.get("processing", 0),
            "completed": counts.get("completed", 0),
            "failed": counts.get("failed", 0),
            "cancelled": counts.get("cancelled", 0),
            "active": active,
            "oldest_queued_at": oldest.isoformat() if oldest else None,
        }


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLite returns naive datetimes; normalise before arithmetic."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def elapsed_seconds(job: Job) -> Optional[float]:
    start, end = _aware(job.started_at), _aware(job.finished_at)
    if start and end:
        return round((end - start).total_seconds(), 2)
    return None


def _elapsed(job: Job) -> Optional[float]:
    return elapsed_seconds(job)


def _publish(job: Job) -> None:
    payload = {
        "job_id": job.id,
        "type": job.type,
        "mode": job.mode,
        "engine": job.engine,
        "status": job.status,
        "progress": job.progress,
        "stage": job.stage,
        "error": job.error,
        "result": job.result,
        "updated_at": utcnow().isoformat(),
    }
    bus.publish_threadsafe(job_channel(job.id), payload)
    bus.publish_threadsafe("jobs", payload)
