"""
Authentication: password login, opaque bearer sessions, API keys.

Chosen deliberately over JWT: sessions are revocable, stored server-side, and
need no signing secret to leak. Passwords are hashed with scrypt (stdlib).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException, status

from .config import get_settings
from .db import execute, insert, jloads, query_one, utcnow

log = logging.getLogger("abhi.auth")

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=bytes.fromhex(salt),
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=32,
    )
    return f"scrypt${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hash_password(password, salt).split("$")[2]
        return hmac.compare_digest(candidate, digest)
    except (ValueError, AttributeError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _expires_at(hours: int) -> str:
    moment = datetime.now(timezone.utc) + timedelta(hours=hours)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def is_expired(expires_at: str) -> bool:
    try:
        moment = datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        return moment < datetime.now(timezone.utc)
    except ValueError:
        return True


# --------------------------------------------------------------------------
# users & sessions
# --------------------------------------------------------------------------
def ensure_default_user() -> dict:
    """Create the admin user on first boot. Returns {created: bool, user: row}."""
    settings = get_settings()
    existing = query_one("SELECT * FROM users WHERE username = ?", (settings.admin_user,))
    if existing:
        return {"created": False, "user": existing}
    password = settings.admin_password or secrets.token_urlsafe(12)
    user = insert(
        "users",
        {
            "username": settings.admin_user,
            "password_hash": hash_password(password),
            "role": "admin",
        },
    )
    log.warning(
        "Created admin user '%s'. Password source: %s",
        settings.admin_user,
        "ABHI_ADMIN_PASSWORD" if settings.admin_password else f"generated -> {password}",
    )
    return {"created": True, "user": user}


def create_session(user_id: str) -> dict:
    settings = get_settings()
    token = secrets.token_urlsafe(32)
    row = insert(
        "sessions",
        {
            "token": token,
            "user_id": user_id,
            "expires_at": _expires_at(settings.session_ttl_hours),
        },
    )
    return {"token": token, "expires_at": row["expires_at"]}


def authenticate(username: str, password: str) -> dict | None:
    user = query_one("SELECT * FROM users WHERE username = ?", (username,))
    if not user or not verify_password(password, user["password_hash"]):
        return None
    execute("UPDATE users SET last_login_at = ? WHERE id = ?", (utcnow(), user["id"]))
    return user


def change_password(user_id: str, current: str, new_password: str) -> bool:
    user = query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user or not verify_password(current, user["password_hash"]):
        return False
    execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (hash_password(new_password), user_id),
    )
    return True


def revoke_session(token: str) -> None:
    execute("DELETE FROM sessions WHERE token = ?", (token,))


def user_for_token(token: str) -> dict | None:
    session = query_one("SELECT * FROM sessions WHERE token = ?", (token,))
    if not session:
        return None
    if is_expired(session["expires_at"]):
        execute("DELETE FROM sessions WHERE token = ?", (token,))
        return None
    return query_one("SELECT id, username, role, created_at FROM users WHERE id = ?", (session["user_id"],))


# --------------------------------------------------------------------------
# api keys
# --------------------------------------------------------------------------
def create_api_key(name: str) -> dict:
    raw = f"abhi_{secrets.token_urlsafe(24)}"
    row = insert(
        "api_keys",
        {"name": name or "key", "key_hash": hash_token(raw), "prefix": raw[:12]},
    )
    return {"id": row["id"], "name": row["name"], "prefix": row["prefix"], "key": raw}


def list_api_keys() -> list[dict]:
    rows = query_all_safe()
    return rows


def query_all_safe() -> list[dict]:
    from .db import query_all

    return query_all(
        "SELECT id, name, prefix, created_at, last_used_at, revoked_at FROM api_keys ORDER BY created_at DESC"
    )


def revoke_api_key(key_id: str) -> None:
    execute("UPDATE api_keys SET revoked_at = ? WHERE id = ?", (utcnow(), key_id))


def user_for_api_key(raw_key: str) -> dict | None:
    row = query_one("SELECT * FROM api_keys WHERE key_hash = ?", (hash_token(raw_key),))
    if not row or row["revoked_at"]:
        return None
    execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (utcnow(), row["id"]))
    return {"id": row["id"], "username": f"api-key:{row['name']}", "role": "api"}


# --------------------------------------------------------------------------
# FastAPI dependency
# --------------------------------------------------------------------------
def _extract_token(authorization: str | None, api_key: str | None) -> tuple[str | None, str]:
    if api_key:
        return api_key, "api_key"
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1], "bearer"
    return None, "none"


def current_user(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
) -> dict:
    token, mode = _extract_token(authorization, x_api_key)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing credentials. Send 'Authorization: Bearer <token>' or 'X-API-Key: <key>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if mode == "api_key":
        user = user_for_api_key(token)
    else:
        user = user_for_token(token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


AuthUser = Depends(current_user)


def session_token_from_request(authorization: str | None) -> str | None:
    token, mode = _extract_token(authorization, None)
    return token if mode == "bearer" else None


__all__ = [
    "AuthUser",
    "authenticate",
    "change_password",
    "create_api_key",
    "create_session",
    "current_user",
    "ensure_default_user",
    "hash_password",
    "list_api_keys",
    "revoke_api_key",
    "revoke_session",
    "session_token_from_request",
    "user_for_token",
    "verify_password",
    "jloads",
]
