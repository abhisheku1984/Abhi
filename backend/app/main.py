"""
Abhi Studio — application entrypoint.

Serves, on a single port (which is what the preview proxy and any container
deployment expect):

* ``/api/*``    — JSON API
* ``/media/*``  — generated media files
* ``/``         — the built React SPA (with SPA history fallback)

Boot sequence is deliberately fail-soft: a missing GPU, FFmpeg or API key
downgrades a capability, it never stops the server from starting.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from . import pipelines as _pipelines  # noqa: F401  (import registers all job handlers)
from .config import detect_ffmpeg, ffmpeg_version, get_settings
from .db import init_db
from .jobs import JOB_HANDLERS, queue_stats, recover_interrupted_jobs, start_workers
from .providers.base import capability_summary
from .providers.cloud import register_cloud_providers
from .providers.local import register_local_providers
from .providers.remote import register_remote_providers
from .routes import ROUTERS
from .security import ensure_default_user

log = logging.getLogger("abhi")


# --------------------------------------------------------------------------
# logging
# --------------------------------------------------------------------------
def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    if any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        return
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)-18s %(message)s", datefmt="%H:%M:%S"
    )
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    root.addHandler(stream)
    try:
        log_dir = settings.data_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "abhi.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        pass  # logging to file is best effort
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


# --------------------------------------------------------------------------
# app factory
# --------------------------------------------------------------------------
def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()

    app = FastAPI(
        title="Abhi Studio API",
        version=__version__,
        description=(
            "Local-first AI media studio. Capabilities are labelled "
            "`real`, `demo` or `not_configured` — see /api/system/capabilities."
        ),
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    # ---- boot ----
    schema_version = init_db()
    user_result = ensure_default_user()
    register_local_providers()
    register_cloud_providers()
    register_remote_providers()
    log.info("  job kinds     : %d registered", len(JOB_HANDLERS))

    recovered = recover_interrupted_jobs()
    workers = int(os.environ.get("ABHI_JOB_WORKERS", "2"))
    start_workers(workers)

    ffmpeg = detect_ffmpeg()
    log.info("Abhi Studio %s starting", __version__)
    log.info("  data dir      : %s", settings.data_dir)
    log.info("  database      : %s (schema v%d)", settings.db_path, schema_version)
    log.info("  admin user    : %s%s", settings.admin_user, " (created)" if user_result["created"] else "")
    log.info("  ffmpeg        : %s", f"{ffmpeg_version(ffmpeg)} -> {ffmpeg}" if ffmpeg else "NOT FOUND")
    log.info("  capabilities  : %s", capability_summary())
    if recovered.get("requeued") or recovered.get("failed"):
        log.warning("  recovery      : %s", recovered)
    log.info("  workers       : %d", workers)

    # ---- CORS (only needed when the SPA is served from another origin) ----
    origins = settings.cors_origins or []
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        log.info("  CORS origins  : %s", origins)

    # ---- middleware ----
    @app.middleware("http")
    async def add_process_time_header(request: Request, call_next):
        started = time.time()
        response = await call_next(request)
        response.headers["X-Process-Time-Ms"] = f"{(time.time() - started) * 1000:.1f}"
        return response

    # ---- routers ----
    for router in ROUTERS:
        app.include_router(router)

    # ---- generated media (served straight off disk; filenames are content hashed) ----
    media_dir = settings.media_dir
    media_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=str(media_dir)), name="media")
    thumb_dir = settings.thumb_dir
    thumb_dir.mkdir(parents=True, exist_ok=True)

    @app.get("/media/../thumbs/{name}")  # pragma: no cover - never matched (safety net)
    def _thumbs_guard(name: str):
        return JSONResponse({"detail": "use /api/assets"}, status_code=404)

    # thumbnails live outside media/, so expose them explicitly
    app.mount("/thumbs", StaticFiles(directory=str(thumb_dir)), name="thumbs")

    # ---- SPA ----
    static_dir = settings.static_dir

    @app.get("/", include_in_schema=False)
    def index():
        index_file = static_dir / "index.html"
        if index_file.is_file():
            return FileResponse(index_file)
        return JSONResponse(
            {
                "app": "Abhi Studio",
                "version": __version__,
                "message": "API is running. The frontend has not been built yet.",
                "build_frontend": "cd frontend && npm install && npm run build",
                "documentation": "/api/docs",
                "health": "/api/health",
            }
        )

    if static_dir.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=str(static_dir / "assets")),
            name="spa-assets",
        )

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa_fallback(full_path: str):
            # never shadow the API or media routes
            if full_path.startswith(("api/", "media/", "thumbs/")):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            candidate = static_dir / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            index_file = static_dir / "index.html"
            if index_file.is_file():
                return FileResponse(index_file)
            return JSONResponse({"detail": "Not Found"}, status_code=404)

    # ---- error envelope ----
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):  # pragma: no cover
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error.",
                "error": f"{type(exc).__name__}: {exc}",
                "path": request.url.path,
            },
        )

    return app


@asynccontextmanager
async def _lifespan(_: FastAPI):
    """Modern replacement for the deprecated on_event("shutdown") hook."""
    yield
    from .jobs import stop_workers

    stop_workers()
    log.info("Abhi Studio stopped")


def build_app() -> FastAPI:
    application = create_app()
    application.router.lifespan_context = _lifespan
    return application


app = build_app()


def main() -> None:  # pragma: no cover - manual entry
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
