"""Declarative base and shared mixins."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def new_uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    """Single declarative base for the whole application."""

    repr_cols_num = 4

    def as_dict(self, exclude: set[str] | None = None) -> dict:
        exclude = exclude or set()
        return {c.name: getattr(self, c.name) for c in self.__table__.columns if c.name not in exclude}


class UUIDMixin:
    """Public identifier: opaque, non-sequential, safe to expose in URLs."""

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, server_default=func.now(), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, server_default=func.now(), nullable=False
    )


class AuditMixin:
    """Who created / last touched a row (audit information, per spec §37)."""

    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
