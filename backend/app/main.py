"""AI Creative Studio — FastAPI application entry point."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.security import hash_password
from app.core.ids import new_id
from app.db.base import Base
from app.db.models import FeatureFlag, Subscription, User
from app.db.session import SessionLocal, engine
from app.engines.registry import register_core_adapters, registry
from app.jobs.events import bus
from app.jobs.worker import pool

configure_logging()
log = get_logger("main")


def _bootstrap() -> None:
    """Create schema, seed the first admin user and sync the model registry."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        # Default feature flags (§35)
        defaults = {
            "image_generation": True, "video_generation": True, "avatar_studio": True,
            "voice_studio": True, "audio_studio": True, "story_engine": True,
            "timeline_editor": True, "workflow_automation": True, "assistant": True,
        }
        for key, enabled in defaults.items():
            if db.query(FeatureFlag).filter(FeatureFlag.key == key).one_or_none() is None:
                db.add(FeatureFlag(id=new_id("flg_"), key=key, enabled=enabled))

        if db.query(User).count() == 0:
            user = User(
                id=new_id("usr_"),
                email=settings.BOOTSTRAP_ADMIN_EMAIL.lower(),
                name=settings.BOOTSTRAP_ADMIN_NAME,
                password_hash=hash_password(settings.BOOTSTRAP_ADMIN_PASSWORD),
                role="owner",
            )
            db.add(user)
            db.flush()
            db.add(Subscription(id=new_id("sub_"), owner_id=user.id, plan="local"))
            log.warning(
                "bootstrap_admin_created",
                email=user.email,
                note="Change this password immediately (BOOTSTRAP_ADMIN_PASSWORD).",
            )
        db.commit()
        registry.sync_to_db()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    register_core_adapters()
    log.info(
        "starting",
        app=settings.APP_NAME,
        env=settings.APP_ENV,
        database="sqlite" if settings.is_sqlite else "postgresql",
        storage=settings.STORAGE_BACKEND,
    )
    _bootstrap()
    if settings.ENABLE_BACKGROUND_WORKER:
        pool.start()
    bus.set_loop(asyncio.get_running_loop())
    try:
        yield
    finally:
        pool.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        description=(
            "Local-first AI creative platform: image, video, avatar, voice and audio generation "
            "with a replaceable model layer and a real background job queue."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    register_exception_handlers(app)
    app.include_router(api_router, prefix=f"{settings.API_PREFIX}/{settings.API_VERSION}")

    @app.get("/api/health", tags=["meta"], include_in_schema=False)
    def _root_health():
        return {"status": "ok", "app": settings.APP_NAME, "version": "0.1.0"}

    @app.get("/api", tags=["meta"], include_in_schema=False)
    def _api_index():
        return {
            "app": settings.APP_NAME,
            "version": "0.1.0",
            "docs": "/api/docs",
            "openapi": "/api/openapi.json",
            "health": "/api/health",
            "v1": f"{settings.API_PREFIX}/{settings.API_VERSION}",
        }

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built SPA when it exists (single-process deployment)."""
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if not dist.exists():
        return
    app.mount("/assets", StaticFiles(directory=str(dist / "assets"), check_dir=False), name="spa-assets")

    @app.get("/", include_in_schema=False)
    def _index() -> FileResponse:
        return FileResponse(str(dist / "index.html"))

    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa(full_path: str):
        candidate = dist / full_path
        if candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(dist / "index.html"))


app = create_app()
