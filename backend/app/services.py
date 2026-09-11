"""
Domain services: asset persistence, project helpers, prompt composition.

Providers return *file payloads*; this module is what turns them into durable
assets (bytes on disk + a row in the database + a thumbnail) so the rest of the
application only ever deals with asset ids.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .config import get_settings
from .db import jdumps, jloads, query_all, query_one, update, insert
from .storage import (
    delete_stored,
    kind_for_ext,
    make_thumbnail,
    probe,
    resolve,
    save_bytes,
)

log = logging.getLogger("abhi.services")


# --------------------------------------------------------------------------
# assets
# --------------------------------------------------------------------------
def persist_file(
    payload: dict,
    *,
    project_id: str | None,
    provider: str = "",
    engine: str = "",
    prompt: str = "",
    params: dict | None = None,
    meta: dict | None = None,
    job_id: str | None = None,
    parent_asset_id: str | None = None,
) -> dict:
    """Write one provider file payload to the store and register an asset row."""
    data: bytes = payload["data"]
    filename: str = payload.get("filename") or "asset.bin"
    kind: str = payload.get("kind") or kind_for_ext(Path(filename).suffix)
    stored = save_bytes(data, project_id=project_id, kind=kind, filename=filename)
    asset_id = _new_asset_id()
    detected = probe(stored["abs_path"], kind)

    width = payload.get("width") or detected.get("width")
    height = payload.get("height") or detected.get("height")
    duration = payload.get("duration_s") or detected.get("duration_s")

    thumb_rel = make_thumbnail(stored["abs_path"], asset_id, kind)
    asset = insert(
        "assets",
        {
            "id": asset_id,
            "project_id": project_id,
            "kind": kind,
            "filename": filename,
            "rel_path": stored["rel_path"],
            "thumb_path": thumb_rel,
            "mime": payload.get("mime") or stored["mime"],
            "size_bytes": stored["size_bytes"],
            "width": int(width) if width else None,
            "height": int(height) if height else None,
            "duration_s": float(duration) if duration else None,
            "sha256": stored["sha256"],
            "provider": provider,
            "engine": engine,
            "prompt": prompt or "",
            "params_json": jdumps(params or {}),
            "source_job_id": job_id,
            "parent_asset_id": parent_asset_id,
            "meta_json": jdumps(meta or {}),
        },
    )
    log.info("stored asset %s (%s, %.1f KB)", asset_id, kind, stored["size_bytes"] / 1024)
    return hydrate_asset(asset)


def _new_asset_id() -> str:
    from .db import new_id

    return new_id("as_")


def hydrate_asset(row: dict | None) -> dict | None:
    if not row:
        return None
    out = dict(row)
    out["params"] = jloads(row.get("params_json"), {})
    out["meta"] = jloads(row.get("meta_json"), {})
    out.pop("params_json", None)
    out.pop("meta_json", None)
    # rel_path is relative to the data dir and already begins with "media/" or
    # "thumbs/" (both are mounted at the same name by main.py).
    out["url"] = f"/{row['rel_path']}"
    out["thumb_url"] = f"/{row['thumb_path']}" if row.get("thumb_path") else None
    out["download_url"] = f"/api/assets/{row['id']}/download"
    return out


def get_asset(asset_id: str, *, include_deleted: bool = False) -> dict | None:
    row = query_one("SELECT * FROM assets WHERE id = ?", (asset_id,))
    if not row:
        return None
    if row["deleted_at"] and not include_deleted:
        return None
    return hydrate_asset(row)


def list_assets(
    *,
    project_id: str | None = None,
    kind: str | None = None,
    search: str | None = None,
    engine: str | None = None,
    limit: int = 60,
    offset: int = 0,
    order: str = "created_at DESC",
) -> dict:
    clauses = ["deleted_at IS NULL"]
    values: list[Any] = []
    if project_id:
        clauses.append("project_id = ?")
        values.append(project_id)
    if kind:
        clauses.append("kind = ?")
        values.append(kind)
    if engine:
        clauses.append("engine = ?")
        values.append(engine)
    if search:
        clauses.append("(prompt LIKE ? OR filename LIKE ?)")
        values += [f"%{search}%", f"%{search}%"]
    where = " AND ".join(clauses)
    order_sql = {
        "created_at DESC": "created_at DESC",
        "created_at ASC": "created_at ASC",
        "size DESC": "size_bytes DESC",
    }.get(order, "created_at DESC")
    total = query_one(f"SELECT COUNT(*) AS c FROM assets WHERE {where}", tuple(values))["c"]  # noqa: S608
    rows = query_all(
        f"SELECT * FROM assets WHERE {where} ORDER BY {order_sql} LIMIT ? OFFSET ?",  # noqa: S608
        (*values, int(limit), int(offset)),
    )
    return {
        "total": total,
        "items": [hydrate_asset(r) for r in rows],
        "limit": limit,
        "offset": offset,
    }


def asset_path(asset_id: str) -> Path | None:
    asset = get_asset(asset_id)
    if not asset:
        return None
    try:
        path = resolve(asset["rel_path"])
    except ValueError:
        return None
    return path if path.exists() else None


def soft_delete_asset(asset_id: str) -> bool:
    from .db import utcnow

    asset = get_asset(asset_id, include_deleted=True)
    if not asset:
        return False
    update("assets", asset_id, {"deleted_at": utcnow()})
    return True


def hard_delete_asset(asset_id: str) -> bool:
    asset = get_asset(asset_id, include_deleted=True)
    if not asset:
        return False
    delete_stored(asset["rel_path"])
    delete_stored(asset["thumb_path"])
    from .db import execute

    execute("DELETE FROM assets WHERE id = ?", (asset_id,))
    return True


def restore_asset(asset_id: str) -> bool:
    asset = get_asset(asset_id, include_deleted=True)
    if not asset:
        return False
    update("assets", asset_id, {"deleted_at": None})
    return True


def asset_counts(project_id: str | None = None) -> dict[str, int]:
    where = "deleted_at IS NULL" + (" AND project_id = ?" if project_id else "")
    params = (project_id,) if project_id else ()
    rows = query_all(
        f"SELECT kind, COUNT(*) AS c FROM assets WHERE {where} GROUP BY kind", params  # noqa: S608
    )
    counts = {row["kind"]: row["c"] for row in rows}
    counts["total"] = sum(counts.values())
    return counts


# --------------------------------------------------------------------------
# prompt composition (character consistency)
# --------------------------------------------------------------------------
def compose_prompt(
    base_prompt: str,
    *,
    project_id: str | None,
    character_ids: list[str] | None = None,
    style_suffix: str = "",
) -> tuple[str, dict]:
    """
    Build the final generation prompt, injecting locked character appearance so
    the same character reads consistently across shots.
    """
    parts = [base_prompt.strip()]
    used: list[dict] = []
    for character_id in character_ids or []:
        row = query_one("SELECT * FROM characters WHERE id = ?", (character_id,))
        if not row:
            continue
        appearance = (row["appearance"] or row["description"] or "").strip()
        if appearance:
            parts.append(f"{row['name']} ({appearance})")
        used.append({"id": row["id"], "name": row["name"], "seed": row["seed"]})
    style = style_suffix.strip()
    if not style and project_id:
        project = query_one("SELECT style_prompt FROM projects WHERE id = ?", (project_id,))
        style = (project or {}).get("style_prompt") or ""
    if style:
        parts.append(style)
    return ", ".join(p for p in parts if p), {"characters": used, "style": style}


def project_seed_bias(project_id: str | None, character_ids: list[str] | None = None) -> int:
    """Deterministic seed derived from locked characters (consistency aid)."""
    seeds = []
    for character_id in character_ids or []:
        row = query_one("SELECT seed FROM characters WHERE id = ?", (character_id,))
        if row and row["seed"]:
            seeds.append(int(row["seed"]))
    if seeds:
        return sum(seeds) % (2**31)
    if project_id:
        return abs(hash(project_id)) % (2**31)
    return 0
