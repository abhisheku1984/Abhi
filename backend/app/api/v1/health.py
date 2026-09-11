"""Health, engine status, GPU and Prometheus metrics (§33)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import CurrentUser, get_current_user
from app.db.session import get_db
from app.engines.registry import registry
from app.gpu import monitor as gpu_monitor
from app.jobs import queue
from app.jobs.worker import pool
from app.media import ffmpeg

log = get_logger("api.health")
router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
def health(db: Session = Depends(get_db)) -> Any:
    try:
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
        db_ok = True
    except Exception as exc:  # pragma: no cover
        log.error("db_health_check_failed", error=str(exc))
        db_ok = False
    return {
        "status": "ok" if db_ok else "degraded",
        "app": settings.APP_NAME,
        "env": settings.APP_ENV,
        "database": "ok" if db_ok else "error",
        "database_type": "sqlite" if settings.is_sqlite else "postgresql",
        "ffmpeg": ffmpeg.capabilities().get("available", False),
        "worker": pool.status().get("running", False),
        "queue": queue.stats(),
    }


@router.get("/engines")
def engines(current: CurrentUser = Depends(get_current_user)) -> Any:
    return {"engines": registry.snapshot(), "health": registry.health()}


@router.get("/gpu")
def gpu(current: CurrentUser = Depends(get_current_user)) -> Any:
    summary = gpu_monitor.summary()
    summary["recommended_profile"] = gpu_monitor.recommended_profile()
    return summary


@router.get("/ready")
def ready(db: Session = Depends(get_db)) -> Any:
    return {"ready": True}


@router.get("/metrics")
def metrics() -> Response:
    """Prometheus exposition format (§33)."""
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
