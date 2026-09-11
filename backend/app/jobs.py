"""
Durable job queue.

* Jobs live in SQLite, so a restart never loses queued work.
* A small pool of daemon worker threads executes handlers from the registry in
  ``pipelines.py``.
* Every job records structured logs, progress, attempts and results, and can be
  cancelled, retried or deleted through the API.

Concurrency model: the handlers are synchronous and CPU/FFmpeg bound, so a
bounded thread pool (not asyncio tasks) is the correct fit and keeps the event
loop responsive for the API.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from typing import Any, Callable

from .db import execute, insert, jdumps, jloads, query_all, query_one, utcnow

log = logging.getLogger("abhi.jobs")

# job statuses
QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
TERMINAL = {SUCCEEDED, FAILED, CANCELLED}

#: kind -> handler(job: dict, ctx: dict) -> dict
JOB_HANDLERS: dict[str, Callable[[dict, dict], dict]] = {}

MAX_LOG_LINES = 300
DEFAULT_WORKERS = 2


class JobCancelled(RuntimeError):
    """Raised inside a handler when cancellation was requested."""


def handler(kind: str):
    def decorator(func: Callable[[dict, dict], dict]):
        JOB_HANDLERS[kind] = func
        return func

    return decorator


def register_handler(kind: str, func: Callable[[dict, dict], dict]) -> None:
    JOB_HANDLERS[kind] = func


def available_kinds() -> list[str]:
    return sorted(JOB_HANDLERS)


# --------------------------------------------------------------------------
# job context passed to handlers
# --------------------------------------------------------------------------
class JobContext:
    """Progress + logging + cancellation handle given to every handler."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id

    def progress(self, value: float, message: str = "") -> None:
        clamped = max(0.0, min(1.0, float(value)))
        execute("UPDATE jobs SET progress = ? WHERE id = ?", (clamped, self.job_id))
        if message:
            self.log(f"[{int(clamped * 100):3d}%] {message}")

    def log(self, message: str) -> None:
        row = query_one("SELECT logs_json FROM jobs WHERE id = ?", (self.job_id,))
        if not row:
            return
        lines: list[dict] = jloads(row["logs_json"], [])
        lines.append({"t": utcnow(), "message": str(message)[:2000]})
        lines = lines[-MAX_LOG_LINES:]
        execute("UPDATE jobs SET logs_json = ? WHERE id = ?", (jdumps(lines), self.job_id))
        log.debug("job %s: %s", self.job_id, message)
        # mirror into the app log at a sensible level
        text = str(message)
        if text.startswith("[error]"):
            log.error("job %s: %s", self.job_id, text)
        else:
            log.info("job %s: %s", self.job_id, text)

    def check_cancelled(self) -> None:
        row = query_one("SELECT cancel_requested FROM jobs WHERE id = ?", (self.job_id,))
        if row and row["cancel_requested"]:
            raise JobCancelled("cancelled by request")

    def asset_ids(self) -> list[str]:
        row = query_one("SELECT result_json FROM jobs WHERE id = ?", (self.job_id,))
        return jloads(row["result_json"], {}).get("asset_ids", []) if row else []


# --------------------------------------------------------------------------
# api
# --------------------------------------------------------------------------
def enqueue(
    kind: str,
    params: dict | None = None,
    *,
    project_id: str | None = None,
    label: str = "",
    priority: int = 5,
    provider: str = "auto",
    max_attempts: int = 1,
) -> dict:
    if kind not in JOB_HANDLERS:
        raise ValueError(f"unknown job kind '{kind}'. Known: {', '.join(available_kinds())}")
    job = insert(
        "jobs",
        {
            "project_id": project_id,
            "kind": kind,
            "provider": provider,
            "label": label or kind,
            "status": QUEUED,
            "priority": int(priority),
            "params_json": jdumps(params or {}),
            "max_attempts": max(1, int(max_attempts)),
        },
    )
    log.info("queued job %s (%s)", job["id"], kind)
    _wake()
    return job


def get_job(job_id: str) -> dict | None:
    job = query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    return _hydrate(job) if job else None


def list_jobs(
    *,
    status: str | None = None,
    project_id: str | None = None,
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    clauses, values = [], []
    if status:
        clauses.append("status = ?")
        values.append(status)
    if project_id:
        clauses.append("project_id = ?")
        values.append(project_id)
    if kind:
        clauses.append("kind = ?")
        values.append(kind)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    total = query_one(f"SELECT COUNT(*) AS c FROM jobs {where}", tuple(values))["c"]  # noqa: S608
    rows = query_all(
        f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",  # noqa: S608
        (*values, int(limit), int(offset)),
    )
    return {"total": total, "items": [_hydrate(r, include_logs=False) for r in rows]}


def queue_stats() -> dict[str, Any]:
    rows = query_all("SELECT status, COUNT(*) AS c FROM jobs GROUP BY status")
    counts = {row["status"]: row["c"] for row in rows}
    running = query_all(
        "SELECT id, kind, label, progress, project_id, started_at FROM jobs WHERE status = ? ORDER BY started_at",
        (RUNNING,),
    )
    return {
        "counts": {
            "queued": counts.get(QUEUED, 0),
            "running": counts.get(RUNNING, 0),
            "succeeded": counts.get(SUCCEEDED, 0),
            "failed": counts.get(FAILED, 0),
            "cancelled": counts.get(CANCELLED, 0),
            "total": sum(counts.values()),
        },
        "running": running,
        "workers": len(_workers),
        "configured_workers": _worker_count,
    }


def cancel_job(job_id: str) -> dict | None:
    job = get_job(job_id)
    if not job:
        return None
    if job["status"] in TERMINAL:
        return job
    execute("UPDATE jobs SET cancel_requested = 1 WHERE id = ?", (job_id,))
    if job["status"] == QUEUED:
        _finish(job_id, CANCELLED, error="cancelled before start")
    return get_job(job_id)


def retry_job(job_id: str) -> dict | None:
    job = get_job(job_id)
    if not job or job["status"] not in (FAILED, CANCELLED):
        return job
    insert(
        "jobs",
        {
            "project_id": job["project_id"],
            "kind": job["kind"],
            "provider": job["provider"],
            "label": f"{job['label']} (retry)",
            "status": QUEUED,
            "priority": job["priority"],
            "params_json": jdumps(job["params"]),
            "max_attempts": job["max_attempts"],
        },
    )
    _wake()
    return get_job(job_id)


def delete_job(job_id: str) -> bool:
    job = get_job(job_id)
    if not job or job["status"] == RUNNING:
        return False
    execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    return True


def _hydrate(row, include_logs: bool = True) -> dict:
    out = dict(row)  # tolerate sqlite3.Row as well as dict
    out["params"] = jloads(row.get("params_json"), {})
    out["result"] = jloads(row.get("result_json"), {})
    out["progress"] = round(float(row.get("progress") or 0.0), 3)
    if include_logs:
        out["logs"] = jloads(row.get("logs_json"), [])
    else:
        out.pop("logs_json", None)
    out.pop("params_json", None)
    out.pop("result_json", None)
    return out


def _finish(job_id: str, status: str, *, error: str | None = None, result: dict | None = None) -> None:
    values: dict[str, Any] = {
        "status": status,
        "finished_at": utcnow(),
        "error": error,
        "progress": 1.0 if status == SUCCEEDED else 0.0,
    }
    if result is not None:
        values["result_json"] = jdumps(result)
    sets = ", ".join(f"{k} = ?" for k in values)
    execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*values.values(), job_id))  # noqa: S608


# --------------------------------------------------------------------------
# worker pool
# --------------------------------------------------------------------------
_workers: list[threading.Thread] = []
_wakeup = threading.Event()
_shutdown = threading.Event()
_worker_count = DEFAULT_WORKERS


def start_workers(count: int = DEFAULT_WORKERS) -> None:
    global _worker_count
    stop_workers()
    _shutdown.clear()
    _worker_count = max(1, int(count))
    for index in range(_worker_count):
        thread = threading.Thread(
            target=_worker_loop, name=f"abhi-worker-{index}", daemon=True
        )
        thread.start()
        _workers.append(thread)
    log.info("started %d job worker(s)", _worker_count)
    _wake()


def stop_workers(timeout: float = 5.0) -> None:
    if not _workers:
        return
    _shutdown.set()
    _wakeup.set()
    for thread in list(_workers):
        thread.join(timeout=timeout / max(1, len(_workers)))
    _workers.clear()


def _wake() -> None:
    _wakeup.set()


def _claim_next() -> dict | None:
    """Atomically take the highest-priority queued job."""
    from .db import get_connection, transaction

    with transaction() as conn:
        row = conn.execute(
            """
            SELECT * FROM jobs
            WHERE status = 'queued' AND cancel_requested = 0
            ORDER BY priority ASC, created_at ASC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE jobs SET status = 'running', started_at = ?, attempts = attempts + 1, progress = 0 WHERE id = ?",
            (utcnow(), row["id"]),
        )
        fresh = conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
        return _hydrate(dict(fresh))


def _worker_loop() -> None:
    while not _shutdown.is_set():
        job = None
        try:
            job = _claim_next()
        except Exception as exc:  # pragma: no cover - sqlite contention
            log.error("worker could not claim a job: %s", exc)
        if not job:
            _wakeup.wait(timeout=0.75)
            _wakeup.clear()
            continue
        _run_job(job)


def _run_job(job: dict) -> None:
    kind = job["kind"]
    ctx = JobContext(job["id"])
    started = time.time()
    handler_fn = JOB_HANDLERS.get(kind)
    ctx.log(f"start {kind} (attempt {job['attempts']}/{job['max_attempts']})")
    if not handler_fn:
        _finish(job["id"], FAILED, error=f"no handler registered for '{kind}'")
        return
    try:
        result = handler_fn(job, ctx) or {}
        elapsed = int((time.time() - started) * 1000)
        execute(
            "UPDATE jobs SET duration_ms = ?, result_json = ? WHERE id = ?",
            (elapsed, jdumps(result), job["id"]),
        )
        _finish(job["id"], SUCCEEDED, result=result)
        ctx.log(f"done in {elapsed} ms")
    except JobCancelled:
        _finish(job["id"], CANCELLED, error="cancelled")
        ctx.log("cancelled")
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        elapsed = int((time.time() - started) * 1000)
        execute("UPDATE jobs SET duration_ms = ? WHERE id = ?", (elapsed, job["id"]))
        trace = traceback.format_exc(limit=4)
        log.error("job %s (%s) failed: %s\n%s", job["id"], kind, message, trace)
        if job["attempts"] < job["max_attempts"]:
            execute(
                "UPDATE jobs SET status = 'queued', error = ?, progress = 0, started_at = NULL WHERE id = ?",
                (message, job["id"]),
            )
            ctx.log(f"[error] attempt {job['attempts']} failed, retrying: {message}")
            _wake()
            return
        _finish(job["id"], FAILED, error=message)
        ctx.log(f"[error] {message}")


def recover_interrupted_jobs() -> dict[str, int]:
    """Called at boot: interrupted running jobs are re-queued, stale ones failed."""
    running = query_all("SELECT id, attempts, max_attempts FROM jobs WHERE status = ?", (RUNNING,))
    requeued = failed = 0
    for row in running:
        if row["attempts"] < max(1, row["max_attempts"]) or row["attempts"] < 3:
            execute(
                "UPDATE jobs SET status = 'queued', started_at = NULL, progress = 0, "
                "error = 'interrupted by restart; re-queued' WHERE id = ?",
                (row["id"],),
            )
            requeued += 1
        else:
            _finish(row["id"], FAILED, error="interrupted by restart and out of attempts")
            failed += 1
    if requeued or failed:
        log.warning("recovered %d job(s): %d re-queued, %d failed", requeued + failed, requeued, failed)
    return {"requeued": requeued, "failed": failed}


def wait_for(job_id: str, timeout: float = 120.0) -> dict | None:
    """Block until a job reaches a terminal state (used by tests and scripts)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = get_job(job_id)
        if job and job["status"] in TERMINAL:
            return job
        time.sleep(0.2)
    return get_job(job_id)
