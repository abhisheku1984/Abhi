"""Visual workflow automation (§20)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.security import CurrentUser, get_current_user
from app.db.models import Workflow
from app.db.session import get_db
from app.jobs import queue
from app.services import workflow as wf_service

log = get_logger("api.workflows")
router = APIRouter(prefix="/workflows", tags=["workflows"])


class WorkflowIn(BaseModel):
    name: str = "Untitled workflow"
    description: str = ""
    graph: dict[str, Any] = {}
    status: str = "draft"


@router.get("/catalog")
def catalog(current: CurrentUser = Depends(get_current_user)) -> Any:
    return {"node_types": wf_service.NODE_TYPES}


@router.get("")
def list_workflows(db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)) -> Any:
    items = db.query(Workflow).filter(Workflow.owner_id == current.id).order_by(Workflow.updated_at.desc()).all()
    return {"items": [w.to_dict(exclude=set()) | {"created_at": w.created_at.isoformat(),
                                                  "updated_at": w.updated_at.isoformat()} for w in items]}


@router.post("", status_code=201)
def create_workflow(payload: WorkflowIn, db: Session = Depends(get_db),
                    current: CurrentUser = Depends(get_current_user)) -> Any:
    wf = Workflow(id=new_id("wfl_"), owner_id=current.id, name=payload.name,
                  description=payload.description, graph=payload.graph or {}, status=payload.status)
    db.add(wf)
    db.commit()
    return wf.to_dict(exclude=set())


@router.get("/{workflow_id}")
def get_workflow(workflow_id: str, db: Session = Depends(get_db),
                 current: CurrentUser = Depends(get_current_user)) -> Any:
    wf = db.get(Workflow, workflow_id)
    if not wf or wf.owner_id != current.id:
        raise NotFoundError("Workflow not found.")
    return wf.to_dict(exclude=set())


@router.patch("/{workflow_id}")
def update_workflow(workflow_id: str, payload: dict, db: Session = Depends(get_db),
                    current: CurrentUser = Depends(get_current_user)) -> Any:
    wf = db.get(Workflow, workflow_id)
    if not wf or wf.owner_id != current.id:
        raise NotFoundError("Workflow not found.")
    for key in ("name", "description", "graph", "status"):
        if key in payload:
            setattr(wf, key, payload[key])
    db.commit()
    return wf.to_dict(exclude=set())


@router.delete("/{workflow_id}")
def delete_workflow(workflow_id: str, db: Session = Depends(get_db),
                    current: CurrentUser = Depends(get_current_user)) -> Any:
    wf = db.get(Workflow, workflow_id)
    if not wf or wf.owner_id != current.id:
        raise NotFoundError("Workflow not found.")
    db.delete(wf)
    db.commit()
    return {"ok": True, "id": workflow_id}


@router.post("/{workflow_id}/run")
def run_workflow(workflow_id: str, payload: dict | None = None, db: Session = Depends(get_db),
                 current: CurrentUser = Depends(get_current_user)) -> Any:
    wf = db.get(Workflow, workflow_id)
    if not wf or wf.owner_id != current.id:
        raise NotFoundError("Workflow not found.")
    graph = (payload or {}).get("graph") or wf.graph or {}
    try:
        order = wf_service.topological_order(graph.get("nodes", []), graph.get("edges", []))
    except ValueError as exc:
        raise ValidationFailed(str(exc), suggested_action="Remove the loop in your workflow.")
    job = queue.enqueue(db, owner_id=current.id, type="workflow", mode="run",
                        params={"workflow_id": workflow_id, "graph": graph})
    wf.last_run_job_id = job.id
    from datetime import datetime, timezone

    wf.last_run_at = datetime.now(timezone.utc)
    db.commit()
    return {"job_id": job.id, "status": "queued", "nodes": len(order)}
