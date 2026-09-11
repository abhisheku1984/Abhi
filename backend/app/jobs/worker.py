"""Background worker pool (§22) — nothing heavy ever runs in a request."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.jobs import queue
from app.jobs.handlers import dispatch

log = get_logger("jobs.worker")


class Worker:
    def __init__(self, worker_id: str) -> None:
        self.worker_id = worker_id
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.current_job: Optional[str] = None
        self.jobs_done = 0
        self.last_heartbeat: float = time.time()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name=f"worker-{self.worker_id}", daemon=True)
        self._thread.start()
        log.info("worker_started", worker=self.worker_id)

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float = 5.0) -> None:
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        idle_ticks = 0
        while not self._stop.is_set():
            try:
                job = queue.claim(self.worker_id)
                if job is None:
                    idle_ticks += 1
                    self.last_heartbeat = time.time()
                    # Periodic housekeeping for crashed workers.
                    if idle_ticks % 40 == 0:
                        queue.requeue_stale()
                    time.sleep(settings.WORKER_POLL_SECONDS)
                    continue
                self.current_job = job.id
                log.info("worker_executing", worker=self.worker_id, job=job.id, type=job.type, mode=job.mode)
                dispatch(job)
                self.jobs_done += 1
            except Exception as exc:  # keep the pool alive no matter what
                log.exception("worker_iteration_failed", worker=self.worker_id, error=str(exc))
                time.sleep(1.0)
            finally:
                self.current_job = None
                self.last_heartbeat = time.time()


class WorkerPool:
    def __init__(self, size: Optional[int] = None) -> None:
        self.size = max(1, int(size or settings.WORKER_CONCURRENCY))
        self.workers: list[Worker] = []

    def start(self) -> None:
        if self.workers:
            return
        for i in range(self.size):
            w = Worker(f"{uuid.uuid4().hex[:6]}-{i}")
            w.start()
            self.workers.append(w)
        log.info("worker_pool_started", size=self.size)

    def stop(self) -> None:
        for w in self.workers:
            w.stop()
        for w in self.workers:
            w.join(timeout=3.0)
        self.workers = []
        log.info("worker_pool_stopped")

    def status(self) -> dict:
        return {
            "running": len(self.workers) > 0,
            "size": self.size,
            "workers": [
                {
                    "id": w.worker_id,
                    "alive": bool(w._thread and w._thread.is_alive()),
                    "current_job": w.current_job,
                    "jobs_done": w.jobs_done,
                    "idle_seconds": round(time.time() - w.last_heartbeat, 1),
                }
                for w in self.workers
            ],
        }


pool = WorkerPool()
