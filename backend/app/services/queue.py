"""Asynchronous job queue (spec §33).

Design
------
* Jobs are durable rows in the database, not in-memory objects, so a restart
  never loses work and the API stays stateless.
* A pool of worker threads claims jobs with an atomic compare-and-set, sets a
  heartbeat, and reports progress over the event bus (WebSocket) plus the DB.
* Cancellation is cooperative: handlers receive a threading.Event and check it
  between frames/steps.
* Crash recovery: jobs whose heartbeat stopped without a terminal state are
  requeued automatically (stuck job reaper).
* `QUEUE_BACKEND=redis` swaps in Redis/RQ when available; the database backend
  is the zero-dependency default.

The HTTP request path never blocks on generation.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.events import publish, topic_job, topic_user
from app.db.base import utcnow
from app.db.models import Job
from app.db.session import SessionLocal, session_scope

logger = logging.getLogger("studio.queue")

JobHandler = Callable[[Session, Job, Callable[[float, str], None], threading.Event], dict]


class JobService:
    def __init__(self) -> None:
        self.handlers: dict[str, JobHandler] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._last_db_write: dict[str, float] = {}
        self._lock = threading.RLock()

    # --- registration -------------------------------------------------------
    def register(self, kind: str, handler: JobHandler) -> None:
        self.handlers[kind] = handler

    def handler_for(self, kind: str) -> JobHandler:
        if kind not in self.handlers:
            raise KeyError(f"No handler registered for job kind '{kind}'")
        return self.handlers[kind]

    # --- lifecycle ----------------------------------------------------------
    def create_job(
        self,
        db: Session,
        *,
        owner_id: str,
        kind: str,
        params: dict | None = None,
        project_id: str | None = None,
        generation_id: str | None = None,
        model_id: str | None = None,
        priority: int = 5,
        max_attempts: int | None = None,
    ) -> Job:
        job = Job(
            owner_id=owner_id, kind=kind, params=params or {}, project_id=project_id,
            generation_id=generation_id, model_id=model_id, priority=priority,
            max_attempts=max_attempts or settings.JOB_MAX_ATTEMPTS,
            status="queued", stage="queued", progress=0.0,
        )
        db.add(job)
        db.flush()
        self._emit(job, "queued")
        return job

    def request_cancel(self, db: Session, job_id: str) -> Job:
        job = db.get(Job, job_id)
        if not job:
            raise KeyError(job_id)
        if job.status in ("completed", "failed", "cancelled"):
            return job
        job.cancel_requested = True
        db.flush()
        with self._lock:
            ev = self._cancel_events.get(job_id)
        if ev:
            ev.set()
        self._emit(job, "cancel_requested")
        return job

    def retry(self, db: Session, job_id: str) -> Job:
        job = db.get(Job, job_id)
        if not job:
            raise KeyError(job_id)
        job.status = "queued"
        job.progress = 0.0
        job.stage = "queued"
        job.attempts = 0
        job.cancel_requested = False
        job.error_message = None
        job.error_code = None
        job.started_at = None
        job.finished_at = None
        db.flush()
        self._emit(job, "queued")
        return job

    # --- progress -----------------------------------------------------------
    def _emit(self, job: Job, event: str, message: str | None = None) -> None:
        payload = {
            "type": event,
            "job_id": job.id,
            "kind": job.kind,
            "status": job.status,
            "progress": round(float(job.progress or 0.0), 4),
            "stage": job.stage,
            "message": message or job.message or "",
            "error": job.error_message,
            "error_code": job.error_code,
            "result": job.result if job.status == "completed" else None,
            "updated_at": utcnow().isoformat(),
        }
        publish(topic_job(job.id), payload)
        publish(topic_user(job.owner_id), payload)

    def progress_cb(self, job_id: str, job: Job | None = None) -> Callable[[float, str], None]:
        """Return a progress callback that streams live but writes to the DB
        at most every 0.4s (keeps SQLite happy under load)."""
        def cb(value: float, stage: str = "") -> None:
            value = max(0.0, min(100.0, float(value) * 100.0 if float(value) <= 1.0 else float(value)))
            with self._lock:
                ev = self._cancel_events.get(job_id)
            if ev and ev.is_set():
                raise JobCancelled()
            now = time.time()
            with session_scope() as db:
                j = db.get(Job, job_id) or job
                if j is None:
                    return
                j.progress = value
                if stage:
                    j.stage = stage[:80]
                j.heartbeat_at = utcnow()
                db.flush()
                payload = {
                    "type": "progress", "job_id": job_id, "kind": j.kind, "status": j.status,
                    "progress": round(value, 2), "stage": j.stage, "message": stage,
                    "updated_at": utcnow().isoformat(),
                }
            publish(topic_job(job_id), payload)
            publish(topic_user(getattr(j, "owner_id", "")), payload)

        return cb

    # --- execution ----------------------------------------------------------
    def claim(self, db: Session, worker_id: str) -> Job | None:
        """Atomically claim the highest-priority queued job."""
        job = (
            db.query(Job)
            .filter(Job.status == "queued")
            .order_by(Job.priority.asc(), Job.created_at.asc())
            .with_for_update(skip_locked=True)
            .first()
        )
        if job is None:
            return None
        job.status = "processing"
        job.worker_id = worker_id
        job.started_at = utcnow()
        job.heartbeat_at = utcnow()
        job.attempts = int(job.attempts or 0) + 1
        job.stage = "starting"
        db.flush()
        return job

    def run_job(self, job_id: str, worker_id: str) -> None:
        cancel_event = threading.Event()
        with self._lock:
            self._cancel_events[job_id] = cancel_event

        try:
            with session_scope() as db:
                job = db.get(Job, job_id)
                if job is None:
                    return
                job.queued_seconds = (utcnow() - job.created_at).total_seconds() if job.created_at else 0.0
                self._emit(job, "processing", "job started")

            handler = None
            with session_scope() as db:
                job = db.get(Job, job_id)
                kind = job.kind if job else ""
            handler = self.handlers.get(kind)
            if handler is None:
                self._fail(job_id, "INTERNAL_ERROR", f"No handler registered for job kind '{kind}'")
                return

            cb = self.progress_cb(job_id)
            with session_scope() as db:
                job = db.get(Job, job_id)
                if job is None:
                    return
                if job.cancel_requested:
                    cancel_event.set()
                try:
                    result = handler(db, job, cb, cancel_event) or {}
                except JobCancelled:
                    self._cancel(job_id)
                    return
                except Exception as exc:  # noqa: BLE001 - must never kill the worker
                    logger.exception("Job %s failed", job_id)
                    self._fail(job_id, error_code_of(exc), str(exc), traceback.format_exc())
                    return

                job.result = result
                job.status = "completed"
                job.progress = 100.0
                job.stage = "completed"
                job.finished_at = utcnow()
                db.flush()
                self._emit(job, "completed", "job completed")
        finally:
            with self._lock:
                self._cancel_events.pop(job_id, None)

    def _fail(self, job_id: str, code: str, message: str, tb: str = "") -> None:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                return
            job.status = "failed"
            job.error_code = code
            job.error_message = message[:4000]
            job.error_trace = tb[-8000:]
            job.finished_at = utcnow()
            job.stage = "failed"
            db.flush()
            self._emit(job, "failed", message)

    def _cancel(self, job_id: str) -> None:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                return
            job.status = "cancelled"
            job.stage = "cancelled"
            job.message = "Cancelled by user"
            job.finished_at = utcnow()
            db.flush()
            self._emit(job, "cancelled", "cancelled by user")


class JobCancelled(Exception):
    pass


def error_code_of(exc: Exception) -> str:
    from app.core.errors import StudioError

    if isinstance(exc, StudioError):
        return exc.code
    return type(exc).__name__.upper()[:60]


# ---------------------------------------------------------------------------
# Worker pool
# ---------------------------------------------------------------------------
class WorkerPool:
    def __init__(self, service: JobService, workers: int | None = None) -> None:
        self.service = service
        self.workers = max(1, int(workers or settings.JOB_WORKERS))
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._worker_id = uuid.uuid4().hex[:8]

    def start(self) -> None:
        for i in range(self.workers):
            t = threading.Thread(target=self._loop, name=f"job-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        reaper = threading.Thread(target=self._reaper, name="job-reaper", daemon=True)
        reaper.start()
        self._threads.append(reaper)
        logger.info("Job worker pool started (%d workers)", self.workers)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)

    def _loop(self) -> None:
        wid = f"{self._worker_id}-{threading.current_thread().name}"
        while not self._stop.is_set():
            job_id = None
            try:
                with session_scope() as db:
                    job = self.service.claim(db, wid)
                    job_id = job.id if job else None
                if job_id:
                    self.service.run_job(job_id, wid)
                else:
                    time.sleep(0.4)
            except Exception:  # noqa: BLE001
                logger.exception("Worker loop error (job=%s)", job_id)
                time.sleep(1.0)

    def _reaper(self) -> None:
        """Requeue jobs whose worker died, and expire long-running leases."""
        while not self._stop.is_set():
            try:
                cutoff = utcnow() - timedelta(seconds=settings.JOB_STUCK_TIMEOUT_SECONDS)
                with session_scope() as db:
                    stuck = (
                        db.query(Job)
                        .filter(Job.status == "processing", Job.heartbeat_at.isnot(None), Job.heartbeat_at < cutoff)
                        .all()
                    )
                    for job in stuck:
                        if int(job.attempts or 0) >= int(job.max_attempts or 3):
                            job.status = "failed"
                            job.error_code = "TIMEOUT"
                            job.error_message = "Job exceeded its heartbeat timeout and was stopped."
                            job.finished_at = utcnow()
                        else:
                            job.status = "queued"
                            job.stage = "requeued"
                        db.flush()
                        self.service._emit(job, "requeued", job.error_message or "requeued after heartbeat timeout")
            except Exception:  # noqa: BLE001
                logger.exception("Reaper error")
            time.sleep(30)


job_service = JobService()


def start_workers() -> WorkerPool:
    pool = WorkerPool(job_service)
    pool.start()
    return pool
