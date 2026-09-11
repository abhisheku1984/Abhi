"""Model Manager API (§6, §32).

Listing, activation, testing and installation guidance. Huge downloads are
never triggered implicitly — `install` returns instructions unless the adapter
explicitly supports a confirmed install step.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, StudioError
from app.core.logging import get_logger
from app.core.security import CurrentUser, get_current_user
from app.db.models import ModelRecord
from app.db.session import get_db
from app.engines.registry import registry
from app.gpu import monitor as gpu_monitor

log = get_logger("api.models")
router = APIRouter(prefix="/models", tags=["models"])


def _record(db: Session, adapter_id: str) -> ModelRecord:
    rec = db.query(ModelRecord).filter(ModelRecord.key == adapter_id).one_or_none()
    if not rec:
        raise NotFoundError(f"Model '{adapter_id}' is not registered.",
                            suggested_action="Restart the server to rebuild the model registry.")
    return rec


@router.get("")
def list_models(
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
    family: Optional[str] = None,
) -> Any:
    registry.sync_to_db()
    rows = {r.key: r for r in db.query(ModelRecord).all()}
    items = []
    for adapter in registry.all():
        if family and adapter.family != family:
            continue
        rec = rows.get(adapter.id)
        info = adapter.info()
        info["db_status"] = rec.status if rec else None
        info["install_path"] = rec.install_path if rec else ""
        items.append(info)
    return {
        "items": items,
        "total": len(items),
        "gpu": gpu_monitor.summary(),
        "recommended_profile": gpu_monitor.recommended_profile(),
    }


@router.get("/health")
def health(db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)) -> Any:
    return registry.health()


@router.get("/{adapter_id}")
def get_model(adapter_id: str, db: Session = Depends(get_db),
              current: CurrentUser = Depends(get_current_user)) -> Any:
    adapter = registry.get(adapter_id)
    info = adapter.info()
    rec = db.query(ModelRecord).filter(ModelRecord.key == adapter_id).one_or_none()
    info["db"] = rec.to_dict(exclude={"capabilities"}) if rec else None
    info["schema"] = adapter.param_schema()
    return info


@router.post("/{adapter_id}/activate")
def activate(adapter_id: str, db: Session = Depends(get_db),
             current: CurrentUser = Depends(get_current_user)) -> Any:
    adapter = registry.get(adapter_id)
    rec = _record(db, adapter_id)
    status = adapter.status().get("status")
    if status not in ("installed", "available"):
        raise StudioError(f"{adapter.display_name} is not available ({status}).",
                          suggested_action=adapter.status().get("reason") or "Install it first.")
    rec.status = "active"
    db.commit()
    log.info("model_activated", adapter=adapter_id, by=current.email)
    return {"id": adapter_id, "status": "active"}


@router.post("/{adapter_id}/deactivate")
def deactivate(adapter_id: str, db: Session = Depends(get_db),
               current: CurrentUser = Depends(get_current_user)) -> Any:
    rec = _record(db, adapter_id)
    rec.status = "installed"
    db.commit()
    return {"id": adapter_id, "status": "installed"}


@router.post("/{adapter_id}/test")
def test_model(adapter_id: str, current: CurrentUser = Depends(get_current_user)) -> Any:
    adapter = registry.get(adapter_id)
    result = adapter.test()
    log.info("model_test", adapter=adapter_id, ok=result.get("ok"), by=current.email)
    return result


@router.post("/{adapter_id}/install")
def install_model(adapter_id: str, payload: dict | None = None, db: Session = Depends(get_db),
                  current: CurrentUser = Depends(get_current_user)) -> Any:
    """Never silently downloads anything (§32, §43).

    Returns instructions for manual installs. Adapters that support a confirmed
    install run it only when `confirm=true` is passed explicitly.
    """
    from app.core.config import settings

    adapter = registry.get(adapter_id)
    confirm = bool((payload or {}).get("confirm"))
    if not confirm or settings.AUTO_DOWNLOAD_MODELS is True and not confirm:
        try:
            guidance = adapter.install()
        except StudioError as exc:
            guidance = {"instructions": [exc.user_message, exc.suggested_action or ""]}
        return {
            "id": adapter_id,
            "auto": False,
            "needs_confirmation": True,
            "message": f"{adapter.display_name} requires a manual install step.",
            **guidance,
        }
    result = adapter.install()
    rec = _record(db, adapter_id)
    rec.status = "installed" if adapter.status().get("status") == "installed" else rec.status
    db.commit()
    return {"id": adapter_id, "auto": True, **result}


@router.delete("/{adapter_id}")
def remove_model(adapter_id: str, db: Session = Depends(get_db),
                 current: CurrentUser = Depends(get_current_user)) -> Any:
    adapter = registry.get(adapter_id)
    try:
        result = adapter.uninstall()
    except StudioError as exc:
        return {"id": adapter_id, "removed": False, "message": exc.user_message,
                "suggested_action": exc.suggested_action}
    rec = _record(db, adapter_id)
    rec.status = "not_installed"
    db.commit()
    return {"id": adapter_id, "removed": True, **result}
