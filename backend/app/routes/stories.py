"""Story and shot routes: storyboards, shot editing, bulk generation, character linking."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..db import execute, insert, jdumps, jloads, query_all, query_one, update
from ..jobs import enqueue
from ..routes.projects import story_payload
from ..security import AuthUser
from ..services import hydrate_asset

log = logging.getLogger("abhi.routes.stories")

router = APIRouter(prefix="/api/stories", tags=["stories"])


class ShotUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    prompt: str | None = None
    scene_no: int | None = None
    index_no: int | None = None
    duration_s: float | None = None
    transition: str | None = None
    motion: str | None = None
    caption: str | None = None
    character_ids: list[str] | None = None
    asset_id: str | None = None
    audio_asset_id: str | None = None
    status: str | None = None


class ShotCreate(BaseModel):
    title: str = "New shot"
    description: str = ""
    prompt: str = ""
    scene_no: int = 1
    duration_s: float = 3.0
    transition: str = "cut"
    motion: str = "static"
    caption: str = ""
    character_ids: list[str] = []


class StoryUpdate(BaseModel):
    title: str | None = None
    logline: str | None = None
    genre: str | None = None
    tone: str | None = None
    status: str | None = None


class GenerateShotsRequest(BaseModel):
    shot_ids: list[str] = []
    only_missing: bool = True
    provider: str = "auto"
    narration: bool = False
    voice: str = "aria"


def story_or_404(story_id: str) -> dict:
    row = query_one("SELECT * FROM stories WHERE id = ?", (story_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Story not found.")
    return row


def shot_or_404(shot_id: str) -> dict:
    row = query_one("SELECT * FROM shots WHERE id = ?", (shot_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Shot not found.")
    return row


@router.get("/{story_id}")
def detail(story_id: str, user: dict = AuthUser) -> dict:
    return story_payload(story_or_404(story_id))


@router.patch("/{story_id}")
def patch(story_id: str, payload: StoryUpdate, user: dict = AuthUser) -> dict:
    story_or_404(story_id)
    values = payload.model_dump(exclude_none=True)
    if values:
        update("stories", story_id, values)
    return story_payload(story_or_404(story_id))


@router.delete("/{story_id}")
def remove(story_id: str, user: dict = AuthUser) -> dict:
    story_or_404(story_id)
    execute("DELETE FROM stories WHERE id = ?", (story_id,))
    return {"ok": True, "note": "Shots were removed with the story; generated images remain in the library."}


@router.post("/{story_id}/shots")
def add_shot(story_id: str, payload: ShotCreate, user: dict = AuthUser) -> dict:
    story = story_or_404(story_id)
    last = query_one(
        "SELECT MAX(index_no) AS last FROM shots WHERE story_id = ?", (story_id,)
    )
    index_no = (last["last"] + 1) if last and last["last"] is not None else 0
    row = insert(
        "shots",
        {
            "story_id": story_id,
            "project_id": story["project_id"],
            "index_no": index_no,
            "scene_no": payload.scene_no,
            "title": payload.title,
            "description": payload.description,
            "prompt": payload.prompt,
            "character_ids": jdumps(payload.character_ids),
            "duration_s": payload.duration_s,
            "transition": payload.transition,
            "motion": payload.motion,
            "caption": payload.caption,
            "status": "draft",
        },
    )
    return {"shot": {**row, "character_ids": jloads(row["character_ids"], [])}}


@router.patch("/shots/{shot_id}")
def patch_shot(shot_id: str, payload: ShotUpdate, user: dict = AuthUser) -> dict:
    shot_or_404(shot_id)
    values = payload.model_dump(exclude_none=True)
    if "character_ids" in values:
        values["character_ids"] = jdumps(values["character_ids"])
    if values:
        update("shots", shot_id, values)
    row = shot_or_404(shot_id)
    return {
        **row,
        "character_ids": jloads(row["character_ids"], []),
        "image": hydrate_asset(query_one("SELECT * FROM assets WHERE id = ?", (row["asset_id"],)))
        if row["asset_id"]
        else None,
    }


@router.delete("/shots/{shot_id}")
def delete_shot(shot_id: str, user: dict = AuthUser) -> dict:
    shot_or_404(shot_id)
    execute("DELETE FROM shots WHERE id = ?", (shot_id,))
    return {"ok": True}


@router.post("/shots/{shot_id}/voice")
def shot_voice(shot_id: str, user: dict = AuthUser, voice: str = "aria", provider: str = "auto"):
    """Narrate a shot: uses its caption, or the description when there is no caption."""
    shot = shot_or_404(shot_id)
    text = (shot["caption"] or "").strip()
    if not text:
        text = (shot["description"] or shot["title"] or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="This shot has no caption or description to narrate.")
    job = enqueue(
        "audio.tts",
        {"text": text, "voice": voice, "shot_id": shot_id, "provider": provider},
        project_id=shot["project_id"],
        label=f"Narration: {shot['title'][:40]}",
        provider=provider,
    )
    return {"job_id": job["id"], "queued": True, "text": text}


@router.post("/{story_id}/generate")
def generate_story_shots(
    story_id: str,
    payload: GenerateShotsRequest,
    user: dict = AuthUser,
    wait: bool = Query(default=False),
):
    """
    Queue image generation for the whole storyboard (or a subset of shots).
    Each job carries the shot's id so the pipeline links the result back.
    """
    story = story_or_404(story_id)
    if payload.shot_ids:
        placeholders = ",".join("?" for _ in payload.shot_ids)
        shots = query_all(
            f"SELECT * FROM shots WHERE story_id = ? AND id IN ({placeholders}) ORDER BY index_no",  # noqa: S608
            (story_id, *payload.shot_ids),
        )
    else:
        shots = query_all("SELECT * FROM shots WHERE story_id = ? ORDER BY index_no", (story_id,))
    if payload.only_missing:
        shots = [s for s in shots if not s["asset_id"]]
    if not shots:
        raise HTTPException(
            status_code=400,
            detail="No shots to generate. Either the storyboard is already complete or the selection is empty.",
        )

    job_ids = []
    for shot in shots:
        character_ids = jloads(shot["character_ids"], [])
        job = enqueue(
            "image.generate",
            {
                "prompt": shot["prompt"] or shot["description"] or shot["title"],
                "width": 1280,
                "height": 720,
                "character_ids": character_ids,
                "provider": payload.provider,
                "shot_id": shot["id"],
            },
            project_id=story["project_id"],
            label=f"Shot {shot['index_no'] + 1}/{len(shots)}: {shot['title'][:28]}",
            provider=payload.provider,
        )
        job_ids.append(job["id"])

    if payload.narration:
        for shot in shots:
            text = (shot["caption"] or shot["description"] or "").strip()
            if not text:
                continue
            job = enqueue(
                "audio.tts",
                {"text": text, "voice": payload.voice, "shot_id": shot["id"], "provider": payload.provider},
                project_id=story["project_id"],
                label=f"Narration: {shot['title'][:32]}",
                provider=payload.provider,
            )
            job_ids.append(job["id"])

    if wait:
        from ..jobs import wait_for

        results = [wait_for(job_id, timeout=1800) for job_id in job_ids]
        return {
            "job_ids": job_ids,
            "statuses": {
                job_id: (res["status"] if res else "unknown") for job_id, res in zip(job_ids, results)
            },
        }
    return {"job_ids": job_ids, "count": len(job_ids), "queued": True}


@router.post("/{story_id}/render")
def render_story(
    story_id: str,
    user: dict = AuthUser,
    width: int = 1280,
    height: int = 720,
    fps: int = 30,
    transition: str = "fade",
    transition_duration: float = 0.6,
    wait: bool = Query(default=False),
):
    story = story_or_404(story_id)
    job = enqueue(
        "video.render_storyboard",
        {
            "story_id": story_id,
            "width": width,
            "height": height,
            "fps": fps,
            "transition": transition,
            "transition_duration": transition_duration,
        },
        project_id=story["project_id"],
        label=f"Render story: {story['title'][:40]}",
    )
    from ..routes.generate import _respond

    return _respond(job, wait)


@router.get("/{story_id}/storyboard")
def storyboard(story_id: str, user: dict = AuthUser) -> dict:
    """Flat, UI-ready storyboard payload (grid ordering, timing, media urls)."""
    story = story_or_404(story_id)
    shots = query_all("SELECT * FROM shots WHERE story_id = ? ORDER BY index_no", (story_id,))
    items = []
    for shot in shots:
        image = hydrate_asset(query_one("SELECT * FROM assets WHERE id = ?", (shot["asset_id"],)))
        audio = hydrate_asset(query_one("SELECT * FROM assets WHERE id = ?", (shot["audio_asset_id"],)))
        items.append(
            {
                "id": shot["id"],
                "index_no": shot["index_no"],
                "scene_no": shot["scene_no"],
                "title": shot["title"],
                "description": shot["description"],
                "prompt": shot["prompt"],
                "duration_s": shot["duration_s"],
                "motion": shot["motion"],
                "transition": shot["transition"],
                "caption": shot["caption"],
                "status": shot["status"],
                "character_ids": jloads(shot["character_ids"], []),
                "image": image,
                "audio": audio,
            }
        )
    total = sum(float(i["duration_s"] or 0) for i in items)
    return {
        "story": {
            "id": story["id"],
            "title": story["title"],
            "logline": story["logline"],
            "engine": story["engine"],
            "scenes": jloads(story["scenes_json"], []),
            "status": story["status"],
        },
        "shots": items,
        "stats": {
            "shot_count": len(items),
            "with_images": sum(1 for i in items if i["image"]),
            "with_audio": sum(1 for i in items if i["audio"]),
            "total_duration_s": round(total, 2),
        },
    }
