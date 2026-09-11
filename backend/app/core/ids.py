from __future__ import annotations

import secrets
import uuid

try:  # python-ulid is a declared dependency; the fallback keeps the app runnable
    from ulid import ULID as _ULID

    def new_id(prefix: str = "") -> str:
        return f"{prefix}{_ULID()}" if prefix else str(_ULID())
except Exception:  # pragma: no cover
    def new_id(prefix: str = "") -> str:  # type: ignore[misc]
        return f"{prefix}{uuid.uuid4().hex}" if prefix else uuid.uuid4().hex


def short_id(n: int = 8) -> str:
    return secrets.token_hex(n // 2 + 1)[:n]
