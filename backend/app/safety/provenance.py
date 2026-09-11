"""Provenance metadata and optional watermarking (§28).

Every asset records how it was made. Watermarking is opt-in per deployment.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.media import image_ops

log = get_logger("safety.provenance")

SCHEMA_VERSION = "1.0"


def build_provenance(
    *,
    engine: str,
    mode: str,
    prompt: str = "",
    negative_prompt: str = "",
    params: Optional[dict[str, Any]] = None,
    seed: Optional[int] = None,
    references: Optional[list[str]] = None,
    character_id: Optional[str] = None,
    voice_id: Optional[str] = None,
    owner_id: str = "",
    deterministic: bool = False,
    diffusion: Optional[bool] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "engine": engine,
        "mode": mode,
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "params": params or {},
        "seed": seed,
        "references": references or [],
        "character_id": character_id,
        "voice_id": voice_id,
        "owner_id": owner_id,
        "deterministic": deterministic,
        "diffusion": diffusion,
        "ai_generated": True,
        "provenance_hash": _hash(engine, mode, prompt, str(params or {}), str(seed)),
        **(extra or {}),
    }


def _hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()[:32]


def apply_watermark(path: str, kind: str, *, text: Optional[str] = None) -> bool:
    """Stamp a visible mark when WATERMARK_ENABLED is on (opt-in)."""
    if not settings.WATERMARK_ENABLED:
        return False
    label = text or settings.WATERMARK_TEXT
    try:
        if kind == "image":
            img = image_ops.load(path)
            image_ops.save(image_ops.watermark(img, label), path)
            return True
        if kind == "video":
            from app.media import ffmpeg

            tmp = str(Path(path).with_suffix(".wm.mp4"))
            ffmpeg.draw_text(path, tmp, text=label, y="h-th-24", size=28)
            Path(tmp).replace(path)
            return True
    except Exception as exc:  # pragma: no cover - never fail a job over a mark
        log.warning("watermark_failed", error=str(exc), path=path)
    return False


def sidecar(path: str, provenance: dict[str, Any]) -> str:
    """Write the provenance record next to the asset (C2PA-lite, JSON)."""
    out = Path(path).with_suffix(Path(path).suffix + ".provenance.json")
    out.write_text(json.dumps(provenance, indent=2, default=str), encoding="utf-8")
    return str(out)
