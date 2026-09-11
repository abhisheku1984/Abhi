from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.core.config import settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "viewer": {
        "project:read", "asset:read", "job:read", "model:read",
        "character:read", "voice:read", "avatar:read",
    },
    "editor": {
        "project:read", "project:write", "asset:read", "asset:write", "job:read", "job:write",
        "model:read", "character:read", "character:write", "voice:read", "voice:write",
        "avatar:read", "avatar:write", "story:read", "story:write",
        "workflow:read", "workflow:write", "generation:create",
    },
    "admin": {"*"},
    "owner": {"*"},
}


def hash_password(password: str, *, salt: Optional[str] = None) -> str:
    """PBKDF2-HMAC-SHA256 — no native dependency required (Windows friendly)."""
    if len(password) < settings.PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must be at least {settings.PASSWORD_MIN_LENGTH} characters.")
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 210_000)
    return f"pbkdf2_sha256$210000${salt}${dk.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, iterations, salt, digest = encoded.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), int(iterations))
        return hmac.compare_digest(dk.hex(), digest)
    except Exception:
        return False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_token(
    subject: str,
    *,
    expires_delta: timedelta,
    extra: Optional[dict[str, Any]] = None,
    token_type: str = "access",
) -> str:
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": int(_utcnow().timestamp()),
        "exp": int((_utcnow() + expires_delta).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(subject: str, extra: Optional[dict[str, Any]] = None) -> str:
    return create_token(
        subject,
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        extra=extra,
        token_type="access",
    )


def create_refresh_token(subject: str) -> str:
    return create_token(
        subject,
        expires_delta=timedelta(minutes=settings.REFRESH_TOKEN_EXPIRE_MINUTES),
        token_type="refresh",
    )


def decode_token(token: str) -> dict[str, Any]:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


class CurrentUser:
    __slots__ = ("id", "email", "role", "name")

    def __init__(self, id: str, email: str, role: str, name: str) -> None:
        self.id = id
        self.email = email
        self.role = role
        self.name = name

    def has(self, permission: str) -> bool:
        perms = ROLE_PERMISSIONS.get(self.role, set())
        return "*" in perms or permission in perms

    def require(self, permission: str) -> None:
        if not self.has(permission):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "forbidden", "message": "You do not have permission to do that."}},
            )

    @property
    def is_admin(self) -> bool:
        return self.role in ("admin", "owner")


def get_current_user(token: Optional[str] = Depends(oauth2_scheme)) -> CurrentUser:
    from app.core.errors import AuthError

    if not token:
        raise AuthError("Sign in to continue.", suggested_action="Log in to your account.")
    try:
        payload = decode_token(token)
        if payload.get("type") not in (None, "access"):
            raise AuthError("Invalid token type.", suggested_action="Log in again.")
        return CurrentUser(
            id=str(payload.get("uid") or payload.get("sub")),
            email=str(payload.get("email") or ""),
            role=str(payload.get("role") or "editor"),
            name=str(payload.get("name") or ""),
        )
    except jwt.PyJWTError as exc:
        raise AuthError("Your session has expired.", suggested_action="Log in again.", detail=str(exc))


def require_permission(permission: str):
    def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        user.require(permission)
        return user

    return _dep
