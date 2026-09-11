"""System health and capability reporting — the single source of truth for what actually works."""

from __future__ import annotations

import platform
import shutil
import sys
import time
from pathlib import Path

from fastapi import APIRouter, Depends

from .. import __version__
from ..config import detect_ffmpeg, detect_gpu, ffmpeg_version, get_settings
from ..db import query_one
from ..jobs import JOB_HANDLERS, queue_stats
from ..providers.base import (
    SELECTABLE_CAPABILITIES,
    capability_summary,
    configured_provider_ids,
    get_provider,
    provider_status,
)
from ..security import AuthUser
from ..storage import disk_usage

router = APIRouter(prefix="/api", tags=["system"])

_STARTED_AT = time.time()


@router.get("/health")
def health() -> dict:
    """Unauthenticated liveness probe (safe: no secrets, no data)."""
    settings = get_settings()
    db_ok = True
    db_error = None
    try:
        query_one("SELECT 1 AS ok")
    except Exception as exc:  # pragma: no cover
        db_ok = False
        db_error = str(exc)
    ffmpeg = detect_ffmpeg()
    return {
        "status": "ok" if db_ok else "degraded",
        "app": "abhi-studio",
        "version": __version__,
        "uptime_s": round(time.time() - _STARTED_AT, 1),
        "database": {"ok": db_ok, "engine": "sqlite", "path": str(settings.db_path), "error": db_error},
        "ffmpeg": {"available": bool(ffmpeg), "version": ffmpeg_version(ffmpeg), "binary": ffmpeg},
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "jobs": queue_stats()["counts"],
        "capabilities": capability_summary(),
    }


def _cpu_info() -> dict:
    load: tuple[float, float, float] | None = None
    try:
        load = __import__("os").getloadavg()
    except Exception:
        pass
    cores = __import__("os").cpu_count() or 1
    return {
        "cores": cores,
        "load_avg": [round(value, 2) for value in load] if load else None,
        "platform": platform.processor() or platform.machine(),
    }


def _memory_info() -> dict:
    total_kb = available_kb = None
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    total_kb = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    available_kb = int(line.split()[1])
    except OSError:
        return {"total_gb": None, "available_gb": None}
    return {
        "total_gb": round(total_kb / 1e6, 2) if total_kb else None,
        "available_gb": round(available_kb / 1e6, 2) if available_kb else None,
    }


@router.get("/system/status")
def system_status(user: dict = AuthUser) -> dict:
    settings = get_settings()
    ffmpeg = detect_ffmpeg()
    gpu = detect_gpu()
    return {
        "app": {"name": "Abhi Studio", "version": __version__, "uptime_s": round(time.time() - _STARTED_AT, 1)},
        "cpu": _cpu_info(),
        "memory": _memory_info(),
        "gpu": gpu,
        "ffmpeg": {"available": bool(ffmpeg), "version": ffmpeg_version(ffmpeg), "binary": ffmpeg},
        "storage": disk_usage(),
        "data_dir": str(settings.data_dir),
        "capabilities": capability_summary(),
        "providers": configured_provider_ids(),
        "jobs": queue_stats(),
        "job_kinds": sorted(JOB_HANDLERS),
        "python_packages": _packages(),
    }


def _packages() -> dict[str, str | None]:
    try:
        import PIL  # noqa: PLC0415

        pillow = PIL.__version__
    except Exception:
        pillow = None
    try:
        import numpy  # noqa: PLC0415

        numpy_version = numpy.__version__
    except Exception:
        numpy_version = None
    try:
        import fastapi  # noqa: PLC0415

        fastapi_version = fastapi.__version__
    except Exception:
        fastapi_version = None
    return {"pillow": pillow, "numpy": numpy_version, "fastapi": fastapi_version}


@router.get("/system/capabilities")
def capabilities(user: dict = AuthUser) -> dict:
    """
    The honesty contract, machine readable.

    ``mode`` is one of:
      * ``real``            — finished functionality (local deterministic engine
                              or a configured cloud/GPU model)
      * ``demo``            — a real algorithm standing in for a neural model;
                              never to be presented as AI generation
      * ``not_configured``  — the adapter exists and needs credentials or a GPU
    """
    selectable = {cap: (cap in SELECTABLE_CAPABILITIES) for cap in provider_status()}
    return {
        "modes": ["real", "demo", "not_configured"],
        "capabilities": provider_status(),
        "selectable": selectable,
        "legend": {
            "real": "Working end-to-end right now.",
            "demo": "Real algorithm, not a neural model (labelled as such everywhere).",
            "not_configured": "Adapter implemented; needs an API key or a GPU host.",
        },
    }


@router.get("/providers")
def providers(user: dict = AuthUser) -> dict:
    return {"capabilities": provider_status()}
