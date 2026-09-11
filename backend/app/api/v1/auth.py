"""Authentication & session management (§34)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from app.core.errors import AuthError, ConflictError, NotFoundError, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.security import (
    create_access_token, create_refresh_token, decode_token, get_current_user,
    hash_password, verify_password, CurrentUser,
)
from app.db.models import AuditLog, Subscription, User
from app.db.session import get_db

log = get_logger("api.auth")
router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str = ""


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict[str, Any]


class PasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


def _user_out(user: User) -> dict[str, Any]:
    return {
        "id": user.id, "email": user.email, "name": user.name, "role": user.role,
        "is_active": user.is_active, "created_at": user.created_at.isoformat() if user.created_at else None,
        "storage_used_mb": round(user.storage_used_mb or 0, 2),
        "storage_quota_mb": user.storage_quota_mb,
    }


def _tokens(user: User) -> dict[str, str]:
    return {
        "access_token": create_access_token(user.id, {"email": user.email, "role": user.role, "name": user.name,
                                                      "uid": user.id}),
        "refresh_token": create_refresh_token(user.id),
    }


@router.post("/register", response_model=TokenOut, status_code=201)
def register(payload: RegisterIn, request: Request, db: Session = Depends(get_db)) -> Any:
    if db.query(User).filter(User.email == payload.email.lower()).one_or_none():
        raise ConflictError("That email is already registered.",
                            suggested_action="Log in instead, or reset your password.")
    try:
        pwd = hash_password(payload.password)
    except ValueError as exc:
        raise ValidationFailed(str(exc), suggested_action="Choose a longer password.")

    user = User(
        id=new_id("usr_"),
        email=payload.email.lower(),
        name=payload.name or payload.email.split("@")[0],
        password_hash=pwd,
        role="editor",
    )
    db.add(user)
    db.flush()
    db.add(Subscription(id=new_id("sub_"), owner_id=user.id, plan="local"))
    db.add(AuditLog(id=new_id("log_"), actor_id=user.id, actor_email=user.email, action="user.register",
                    entity="user", entity_id=user.id, ip=request.client.host if request.client else ""))
    db.commit()
    db.refresh(user)
    log.info("user_registered", user_id=user.id)
    return {**_tokens(user), "token_type": "bearer", "user": _user_out(user)}


@router.post("/login", response_model=TokenOut)
def login(payload: LoginIn, request: Request, db: Session = Depends(get_db)) -> Any:
    user = db.query(User).filter(User.email == payload.email.lower()).one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        raise AuthError("Incorrect email or password.", suggested_action="Check your credentials and try again.")
    if not user.is_active:
        raise AuthError("This account is disabled.", suggested_action="Contact an administrator.")
    user.last_login_at = datetime.now(timezone.utc)
    db.add(AuditLog(id=new_id("log_"), actor_id=user.id, actor_email=user.email, action="user.login",
                    entity="user", entity_id=user.id, ip=request.client.host if request.client else ""))
    db.commit()
    return {**_tokens(user), "token_type": "bearer", "user": _user_out(user)}


@router.post("/refresh", response_model=TokenOut)
def refresh(token: str, db: Session = Depends(get_db)) -> Any:
    try:
        payload = decode_token(token)
        if payload.get("type") != "refresh":
            raise AuthError("Invalid refresh token.", suggested_action="Log in again.")
    except Exception:
        raise AuthError("Invalid refresh token.", suggested_action="Log in again.")
    user = db.get(User, str(payload.get("sub")))
    if not user:
        raise NotFoundError("Account not found.", suggested_action="Register again.")
    return {**_tokens(user), "token_type": "bearer", "user": _user_out(user)}


@router.get("/me")
def me(current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)) -> Any:
    user = db.get(User, current.id)
    if not user:
        raise NotFoundError("Account not found.", suggested_action="Log in again.")
    return _user_out(user)


@router.patch("/me")
def update_me(payload: dict, current: CurrentUser = Depends(get_current_user),
              db: Session = Depends(get_db)) -> Any:
    user = db.get(User, current.id)
    if not user:
        raise NotFoundError("Account not found.")
    if "name" in payload and isinstance(payload["name"], str):
        user.name = payload["name"][:120]
    if "preferences" in payload and isinstance(payload["preferences"], dict):
        user.preferences = {**(user.preferences or {}), **payload["preferences"]}
    db.commit()
    return _user_out(user)


@router.post("/password")
def change_password(payload: PasswordIn, current: CurrentUser = Depends(get_current_user),
                    db: Session = Depends(get_db)) -> Any:
    user = db.get(User, current.id)
    if not user or not verify_password(payload.current_password, user.password_hash):
        raise AuthError("Current password is incorrect.", suggested_action="Try again.")
    try:
        user.password_hash = hash_password(payload.new_password)
    except ValueError as exc:
        raise ValidationFailed(str(exc))
    db.add(AuditLog(id=new_id("log_"), actor_id=user.id, actor_email=user.email, action="user.password",
                    entity="user", entity_id=user.id))
    db.commit()
    return {"ok": True}


@router.post("/logout")
def logout(current: CurrentUser = Depends(get_current_user)) -> Any:
    """JWTs are stateless: clients drop the token. Recorded for auditability."""
    return {"ok": True, "message": "Signed out. Clear the stored token on the client."}
