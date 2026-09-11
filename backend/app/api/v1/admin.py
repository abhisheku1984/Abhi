"""Admin panel API (§35) — users, jobs, models, GPU, storage, logs, flags."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ForbiddenError, NotFoundError
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.security import CurrentUser, get_current_user
from app.db.models import Asset, AuditLog, FeatureFlag, Generation, Job, Project, User, Workflow
from app.db.session import get_db
from app.engines.registry import registry
from app.gpu import monitor as gpu_monitor
from app.jobs import queue
from app.jobs.worker import pool
from app.media import ffmpeg
from app.storage.backends import get_storage

log = get_logger("api.admin")
router = APIRouter(prefix="/admin", tags=["admin"])


def _admin(current: CurrentUser) -> None:
    if not current.is_admin:
        raise ForbiddenError("Administrator access required.")


@router.get("/overview")
def overview(db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)) -> Any:
    _admin(current)
    return {
        "counts": {
            "users": db.query(func.count(User.id)).scalar(),
            "projects": db.query(func.count(Project.id)).scalar(),
            "assets": db.query(func.count(Asset.id)).scalar(),
            "generations": db.query(func.count(Generation.id)).scalar(),
            "workflows": db.query(func.count(Workflow.id)).scalar(),
        },
        "jobs": queue.stats(),
        "gpu": gpu_monitor.summary(),
        "storage": _storage_stats(db),
        "engines": registry.health(),
        "worker": pool.status(),
        "ffmpeg": ffmpeg.capabilities(),
    }


def _storage_stats(db: Session) -> dict[str, Any]:
    total = db.query(func.coalesce(func.sum(Asset.size_bytes), 0)).scalar() or 0
    storage = get_storage()
    backend = storage.name
    return {
        "backend": backend,
        "asset_bytes": int(total),
        "asset_mb": round(total / 1024 / 1024, 2),
        "path": str(storage.root) if hasattr(storage, "root") else None,
        "disk": _disk_usage(),
    }


def _disk_usage() -> dict[str, Any]:
    try:
        import shutil

        total, used, free = shutil.disk_usage(settings.storage_root)
        return {"total_gb": round(total / 1e9, 1), "used_gb": round(used / 1e9, 1),
                "free_gb": round(free / 1e9, 1)}
    except Exception:
        return {}


@router.get("/users")
def list_users(db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user),
               limit: int = Query(100, ge=1, le=500)) -> Any:
    _admin(current)
    users = db.query(User).order_by(User.created_at.desc()).limit(limit).all()
    return {
        "items": [
            {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "is_active": u.is_active,
             "created_at": u.created_at.isoformat() if u.created_at else None,
             "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
             "projects": db.query(func.count(Project.id)).filter(Project.owner_id == u.id).scalar(),
             "assets": db.query(func.count(Asset.id)).filter(Asset.owner_id == u.id).scalar()}
            for u in users
        ]
    }


@router.patch("/users/{user_id}")
def update_user(user_id: str, payload: dict, db: Session = Depends(get_db),
                current: CurrentUser = Depends(get_current_user)) -> Any:
    _admin(current)
    user = db.get(User, user_id)
    if not user:
        raise NotFoundError("User not found.")
    if "role" in payload and payload["role"] in ("viewer", "editor", "admin", "owner"):
        user.role = payload["role"]
    if "is_active" in payload:
        user.is_active = bool(payload["is_active"])
    if "storage_quota_mb" in payload:
        user.storage_quota_mb = int(payload["storage_quota_mb"])
    db.add(AuditLog(id=new_id("log_"), actor_id=current.id, actor_email=current.email, action="admin.user.update",
                    entity="user", entity_id=user_id, meta=payload))
    db.commit()
    return {"id": user.id, "role": user.role, "is_active": user.is_active}


@router.get("/jobs")
def admin_jobs(*, db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user),
               status: Optional[str] = None, limit: int = Query(100, ge=1, le=500)) -> Any:
    _admin(current)
    items, total = queue.list_jobs(status=status, limit=limit)
    return {"items": [
        {"id": j.id, "type": j.type, "mode": j.mode, "engine": j.engine, "status": j.status,
         "progress": j.progress, "stage": j.stage, "owner_id": j.owner_id, "worker_id": j.worker_id,
         "attempts": j.attempts, "error": j.error,
         "queued_at": j.queued_at.isoformat() if j.queued_at else None,
         "started_at": j.started_at.isoformat() if j.started_at else None,
         "finished_at": j.finished_at.isoformat() if j.finished_at else None}
        for j in items
    ], "total": total, "stats": queue.stats(), "worker": pool.status()}


@router.post("/jobs/{job_id}/cancel")
def admin_cancel(job_id: str, current: CurrentUser = Depends(get_current_user)) -> Any:
    _admin(current)
    queue.cancel(job_id)
    return {"id": job_id, "status": "cancelling"}


@router.get("/logs")
def logs(current: CurrentUser = Depends(get_current_user), lines: int = Query(200, ge=10, le=2000)) -> Any:
    _admin(current)
    path = settings.data_root / "logs" / "studio.log"
    if not path.exists():
        return {"items": [], "path": str(path)}
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    return {"items": content, "path": str(path)}


@router.get("/audit")
def audit(db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user),
          limit: int = Query(200, ge=1, le=1000)) -> Any:
    _admin(current)
    rows = db.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()
    return {"items": [
        {"id": r.id, "actor": r.actor_email, "action": r.action, "entity": r.entity,
         "entity_id": r.entity_id, "meta": r.meta, "ip": r.ip,
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in rows
    ]}


@router.get("/feature-flags")
def feature_flags(db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)) -> Any:
    _admin(current)
    rows = db.query(FeatureFlag).all()
    return {"items": [{"key": r.key, "enabled": r.enabled, "description": r.description} for r in rows]}


@router.put("/feature-flags")
def set_feature_flag(payload: dict, db: Session = Depends(get_db),
                     current: CurrentUser = Depends(get_current_user)) -> Any:
    _admin(current)
    key = str(payload.get("key", "")).strip()
    if not key:
        raise NotFoundError("A flag key is required.")
    row = db.query(FeatureFlag).filter(FeatureFlag.key == key).one_or_none()
    if row is None:
        row = FeatureFlag(id=new_id("flg_"), key=key)
        db.add(row)
    row.enabled = bool(payload.get("enabled", True))
    row.description = str(payload.get("description", row.description or ""))
    db.commit()
    return {"key": row.key, "enabled": row.enabled}


@router.get("/system-health")
def system_health(db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)) -> Any:
    _admin(current)
    return {
        "status": "ok",
        "app": {"env": settings.APP_ENV, "debug": settings.DEBUG},
        "database": {"url": _safe_db_url(), "dialect": "sqlite" if settings.is_sqlite else "postgresql"},
        "gpu": gpu_monitor.summary(),
        "ffmpeg": ffmpeg.capabilities(),
        "engines": registry.health(),
        "queue": queue.stats(),
        "worker": pool.status(),
        "storage": _storage_stats(db),
        "safety": {"enabled": settings.SAFETY_ENABLED, "watermark": settings.WATERMARK_ENABLED},
    }


def _safe_db_url() -> str:
    url = settings.database_url
    if "@" in url:
        head, tail = url.split("@", 1)
        scheme = head.split("://")[0] + "://***"
        return f"{scheme}@{tail}"
    return url
