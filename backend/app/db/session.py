"""Engine/session factory.

Works with SQLite out of the box (zero external services) and switches to
PostgreSQL the moment DATABASE_URL points at one - no code changes required.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings


def _engine_kwargs() -> dict:
    url = settings.DATABASE_URL
    if url.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False, "timeout": 30}, "pool_pre_ping": True}
    return {
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        "pool_pre_ping": True,
        "pool_recycle": 1800,
    }


engine: Engine = create_engine(settings.DATABASE_URL, echo=settings.DB_ECHO, future=True, **_engine_kwargs())
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False, future=True)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record):  # pragma: no cover - driver specific
    """SQLite needs explicit FK enforcement and WAL for concurrent workers."""
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    cur = dbapi_connection.cursor()
    try:
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=30000")
    finally:
        cur.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for background workers / scripts."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(create_extensions: bool = True) -> None:
    """Create tables.

    Alembic owns schema evolution for PostgreSQL deployments; this keeps the
    zero-config SQLite path (and the test suite) working without running a
    migration on a fresh clone.
    """
    # Importing the models module registers every table on Base.metadata.
    # Without this import create_all() would silently create nothing.
    import app.db.models  # noqa: F401  (local import avoids circulars)
    from app.db.base import Base

    Base.metadata.create_all(bind=engine)
    return sorted(Base.metadata.tables)
