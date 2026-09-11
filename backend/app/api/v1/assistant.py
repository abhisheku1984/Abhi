"""Command-based assistant endpoint (§27)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.assistant.engine import execute, plan
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.security import CurrentUser, get_current_user
from app.db.models import AuditLog
from app.db.session import get_db

log = get_logger("api.assistant")
router = APIRouter(prefix="/assistant", tags=["assistant"])


class CommandIn(BaseModel):
    command: str
    context: dict[str, Any] = {}
    confirm: bool = False
    dry_run: bool = False


@router.post("/command")
def run_command(payload: CommandIn, db: Session = Depends(get_db),
                current: CurrentUser = Depends(get_current_user)) -> Any:
    intent_plan = plan(payload.command, context=payload.context)
    if payload.dry_run:
        return {"planned": intent_plan.to_dict(), "executed": False}

    result = execute(intent_plan, user_id=current.id,
                     project_id=(payload.context or {}).get("project_id"),
                     context=payload.context, confirmed=payload.confirm)

    if result.get("executed") and not result.get("needs_confirmation"):
        db.add(AuditLog(id=new_id("log_"), actor_id=current.id, actor_email=current.email,
                        action=f"assistant.{intent_plan.intent}", entity="assistant",
                        meta={"command": payload.command[:400], "result": str(result)[:400]}))
        db.commit()
    log.info("assistant_command", intent=intent_plan.intent, user=current.email,
             executed=result.get("executed"), needs_confirmation=result.get("needs_confirmation"))
    return {"planned": intent_plan.to_dict(), **result}
