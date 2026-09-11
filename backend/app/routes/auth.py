"""Authentication routes: login, logout, session info, password change, API keys."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from ..config import get_settings
from ..security import (
    AuthUser,
    authenticate,
    change_password,
    create_api_key,
    create_session,
    list_api_keys,
    revoke_api_key,
    revoke_session,
    session_token_from_request,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _session_payload(user: dict) -> dict:
    created = create_session(user["id"])
    return {
        "token": created["token"],
        "expires_at": created["expires_at"],
        "user": {"id": user["id"], "username": user["username"], "role": user["role"]},
    }


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=6)


class ApiKeyRequest(BaseModel):
    name: str = "default"


@router.post("/login")
def login(payload: LoginRequest) -> dict:
    user = authenticate(payload.username, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password.",
        )
    return _session_payload(user)


@router.post("/logout")
def logout(authorization: str | None = Header(default=None)) -> dict:
    token = session_token_from_request(authorization)
    if token:
        revoke_session(token)
    return {"ok": True}


@router.get("/me")
def me(user: dict = AuthUser) -> dict:
    return {"user": user, "settings": {"session_ttl_hours": get_settings().session_ttl_hours}}


@router.post("/password")
def update_password(payload: PasswordChange, user: dict = AuthUser) -> dict:
    if not change_password(user["id"], payload.current_password, payload.new_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    return {"ok": True}


@router.post("/whoami")
def whoami(authorization: str | None = Header(default=None)) -> dict:
    """Cheap token check used by the frontend session bootstrap."""
    from ..security import user_for_token

    token = session_token_from_request(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="No session token.")
    user = user_for_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Session expired.")
    return {"user": user}


@router.get("/keys")
def keys(user: dict = AuthUser) -> dict:
    return {"items": list_api_keys()}


@router.post("/keys")
def add_key(payload: ApiKeyRequest, user: dict = AuthUser) -> dict:
    created = create_api_key(payload.name)
    return {
        "id": created["id"],
        "name": created["name"],
        "prefix": created["prefix"],
        "key": created["key"],
        "warning": "Store this key now — it is not shown again.",
    }


@router.delete("/keys/{key_id}")
def remove_key(key_id: str, user: dict = AuthUser) -> dict:
    revoke_api_key(key_id)
    return {"ok": True}
