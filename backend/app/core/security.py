"""Authentication, password hashing and role helpers.

Passwords are hashed with bcrypt. Tokens are signed JWTs; refresh tokens are
stored hashed so a database leak cannot be replayed.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.core.config import settings

# NOTE: `bcrypt` is used directly instead of passlib. passlib 1.7.4 is
# incompatible with bcrypt >= 4.1 (version detection raises ValueError during
# hashing), which would break every signup on a fresh install.
try:  # pragma: no cover - present in requirements
    import bcrypt as _bcrypt
except ImportError:  # pragma: no cover
    _bcrypt = None  # type: ignore[assignment]

_BCRYPT_MAX_BYTES = 72
_ROUNDS = 12

ROLE_HIERARCHY = {"user": 1, "moderator": 2, "admin": 3}


def hash_password(password: str) -> str:
    if not isinstance(password, str) or len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    if len(password) > 256:
        raise ValueError("Password is too long")
    if _bcrypt is None:
        raise RuntimeError("bcrypt is required: pip install bcrypt")
    pw = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return _bcrypt.hashpw(pw, _bcrypt.gensalt(rounds=_ROUNDS)).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    if not hashed or _bcrypt is None:
        return False
    try:
        return _bcrypt.checkpw(password.encode("utf-8")[:_BCRYPT_MAX_BYTES], hashed.encode("utf-8"))
    except Exception:
        return False


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_access_token(subject: str, role: str = "user", extra: dict[str, Any] | None = None) -> str:
    expires = _now() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload: dict[str, Any] = {"sub": subject, "role": role, "type": "access", "exp": expires, "iat": _now()}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(subject: str, role: str = "user") -> tuple[str, datetime]:
    expires = _now() + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {"sub": subject, "role": role, "type": "refresh", "exp": expires, "iat": _now(),
               "jti": secrets.token_hex(16)}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM), expires


def decode_token(token: str) -> dict[str, Any]:
    """Raises jwt.PyJWTError on invalid/expired tokens."""
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def hash_token(token: str) -> str:
    """Refresh tokens are stored hashed (constant-time compare on lookup)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a or "", b or "")


def has_role(user_role: str, required: str) -> bool:
    return ROLE_HIERARCHY.get(user_role or "user", 0) >= ROLE_HIERARCHY.get(required, 99)


def generate_api_key() -> tuple[str, str]:
    raw = "acs_" + secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode()).hexdigest()
