"""
Generation endpoints.

Every route here enqueues a job (never blocks the request) and returns the job
id so the UI can stream progress. The `wait` query flag exists for scripts and
tests that want the finished asset ids directly.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..jobs import enqueue, get_job, wait_for
from ..security import AuthUser
from ..services import get_asset

log = logging.getLogger("abhi.routes.generate")

router = APIRouter(prefix="/api/generate", tags=["generate"])


# --------------------------------------------------------------------------
# schemas
# --------------------------------------------------------------------------
class ImageRequest(BaseModel):
    prompt: str = Field(min_length=1)
    project_id: str | None = None
    width: int = 1024
    height: int = 576
    seed: int | None = None
    style: str = "auto"
    style_suffix: str = ""
    character_ids: list[str] = []
    provider: str = "auto"
    count: int = 1


class VideoRequest(BaseModel):
    prompt: str = Field(min_length=1)
    project_id: str | None = None
    asset_id: str | None = None
    aspect_ratio: str = "16:9"
    duration: int = 5
    resolution: str = "720p"
    provider: str = "auto"
    model: str | None = None


class TtsRequest(BaseModel):
    text: str = Field(min_length=1)
    project_id: str | None = None
    voice: str = "aria"
    speed: float = 1.0
    pitch: float = 0.0
    provider: str = "auto"
    shot_id: str | None = None


class StoryRequest(BaseModel):
    premise: str = Field(min_length=1)
    project_id: str | None = None
    title: str = ""
    genre: str = ""
    tone: str = ""
    style: str = ""
    scenes: int = 5
    shots_per_scene: int = 3
    character_ids: list[str] | None = None
    provider: str = "auto"


class RenderClip(BaseModel):
    asset_id: str
    kind: str = "image"
    duration: float = 3.0
    start: float = 0.0
    motion: str = "none"
    caption: str = ""
    speed: float = 1.0


class RenderRequest(BaseModel):
    project_id: str | None = None
    clips: list[RenderClip] = []
    width: int = 1280
    height: int = 720
    fps: int = 30
    transition: str = "fade"
    transition_duration: float = 0.6
    audio_asset_id: str | None = None


class StoryboardRenderRequest(BaseModel):
    story_id: str
    project_id: str | None = None
    width: int = 1280
    height: int = 720
    fps: int = 30
    transition: str = "fade"
    transition_duration: float = 0.6
    audio_asset_id: str | None = None


class VideoEditRequest(BaseModel):
    asset_id: str
    project_id: str | None = None
    ops: list[dict] = []
    fps: int = 30


class MuxRequest(BaseModel):
    video_asset_id: str
    audio_asset_id: str
    project_id: str | None = None
    mode: str = "replace"
    audio_gain: float = 1.0
    video_gain: float = 1.0
    fade_out: float = 0.0


class MontageRequest(BaseModel):
    asset_ids: list[str]
    project_id: str | None = None
    columns: int = 4
    cell: int = 320


class LipsyncRequest(BaseModel):
    asset_id: str
    audio_asset_id: str | None = None
    text: str | None = None
    project_id: str | None = None
    provider: str = "auto"


# --------------------------------------------------------------------------
# helper
# --------------------------------------------------------------------------
def _respond(job: dict, wait: bool):
    if not wait:
        return {"job_id": job["id"], "status": job["status"], "queued": True}
    finished = wait_for(job["id"], timeout=1200)
    if not finished:
        raise HTTPException(status_code=504, detail="Job did not finish in time.")
    if finished["status"] != "succeeded":
        # surface the provider's remediation text verbatim
        raise HTTPException(
            status_code=422 if finished["status"] == "failed" else 409,
            detail={
                "job_id": finished["id"],
                "status": finished["status"],
                "error": finished.get("error"),
                "logs": [entry["message"] for entry in finished.get("logs", [])][-12:],
            },
        )
    result = finished["result"]
    assets = result.get("assets") or []
    return {
        "job_id": finished["id"],
        "status": "succeeded",
        "assets": assets,
        "asset_ids": result.get("asset_ids", []),
        "result": result,
    }


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
@router.post("/image")
def generate_image(payload: ImageRequest, user: dict = AuthUser, wait: bool = Query(default=False)):
    width, height = payload.width, payload.height
    jobs = []
    for index in range(max(1, min(8, payload.count))):
        seed = payload.seed if payload.seed else None
        if seed and payload.count > 1:
            seed = seed + index
        job = enqueue(
            "image.generate",
            {
                "prompt": payload.prompt,
                "width": width,
                "height": height,
                "seed": seed,
                "style": payload.style,
                "style_suffix": payload.style_suffix,
                "character_ids": payload.character_ids,
                "provider": payload.provider,
            },
            project_id=payload.project_id,
            label=f"Image: {payload.prompt[:48]}",
            provider=payload.provider,
        )
        jobs.append(job)
    if wait and jobs:
        results = [_respond(job, True) for job in jobs]
        if len(results) == 1:
            # single-image requests get a flat response — the common case for the UI
            return {**results[0], "results": results, "job_ids": [jobs[0]["id"]]}
        return {"results": results, "job_ids": [j["id"] for j in jobs]}
    if len(jobs) == 1:
        return {"job_id": jobs[0]["id"], "status": jobs[0]["status"], "queued": True}
    return {"job_ids": [j["id"] for j in jobs], "count": len(jobs), "queued": True}


@router.post("/video")
def generate_video(payload: VideoRequest, user: dict = AuthUser, wait: bool = Query(default=False)):
    job = enqueue(
        "video.generate",
        payload.model_dump(),
        project_id=payload.project_id,
        label=f"Video: {payload.prompt[:48]}",
        provider=payload.provider,
    )
    return _respond(job, wait)


@router.post("/video/render")
def render_video(payload: RenderRequest, user: dict = AuthUser, wait: bool = Query(default=False)):
    clips = [clip.model_dump() for clip in payload.clips]
    job = enqueue(
        "video.render",
        {
            "clips": clips,
            "width": payload.width,
            "height": payload.height,
            "fps": payload.fps,
            "transition": payload.transition,
            "transition_duration": payload.transition_duration,
            "audio_asset_id": payload.audio_asset_id,
        },
        project_id=payload.project_id,
        label=f"Render {len(clips)} clip(s)",
    )
    return _respond(job, wait)


@router.post("/video/storyboard")
def render_storyboard(
    payload: StoryboardRenderRequest, user: dict = AuthUser, wait: bool = Query(default=False)
):
    job = enqueue(
        "video.render_storyboard",
        payload.model_dump(),
        project_id=payload.project_id,
        label=f"Storyboard render: {payload.story_id}",
    )
    return _respond(job, wait)


@router.post("/video/edit")
def edit_video(payload: VideoEditRequest, user: dict = AuthUser, wait: bool = Query(default=False)):
    job = enqueue(
        "video.edit",
        payload.model_dump(),
        project_id=payload.project_id,
        label="Video edit",
    )
    return _respond(job, wait)


@router.post("/video/mux")
def mux_video(payload: MuxRequest, user: dict = AuthUser, wait: bool = Query(default=False)):
    job = enqueue(
        "video.mux", payload.model_dump(), project_id=payload.project_id, label="Mux audio"
    )
    return _respond(job, wait)


@router.post("/voice")
def generate_voice(payload: TtsRequest, user: dict = AuthUser, wait: bool = Query(default=False)):
    job = enqueue(
        "audio.tts",
        payload.model_dump(),
        project_id=payload.project_id,
        label=f"Narration ({payload.voice})",
        provider=payload.provider,
    )
    return _respond(job, wait)


@router.post("/story")
def generate_story(payload: StoryRequest, user: dict = AuthUser, wait: bool = Query(default=False)):
    job = enqueue(
        "story.generate",
        payload.model_dump(),
        project_id=payload.project_id,
        label=f"Story: {payload.premise[:48]}",
        provider=payload.provider,
    )
    return _respond(job, wait)


@router.post("/montage")
def generate_montage(payload: MontageRequest, user: dict = AuthUser, wait: bool = Query(default=False)):
    job = enqueue(
        "image.montage", payload.model_dump(), project_id=payload.project_id, label="Contact sheet"
    )
    return _respond(job, wait)


@router.post("/lipsync")
def generate_lipsync(payload: LipsyncRequest, user: dict = AuthUser, wait: bool = Query(default=False)):
    job = enqueue(
        "video.lipsync",
        payload.model_dump(),
        project_id=payload.project_id,
        label="Lip sync",
        provider=payload.provider,
    )
    return _respond(job, wait)


@router.post("/demo")
def run_demo(
    user: dict = AuthUser,
    project_id: str | None = None,
    prompt: str = "an abstract study in light and motion",
    wait: bool = Query(default=True),
):
    """
    One-click end-to-end proof: art + narration + rendered film with audio.
    Uses only local engines, so it works with zero configuration.
    """
    job = enqueue(
        "demo.render",
        {"prompt": prompt},
        project_id=project_id,
        label="Pipeline self-test",
        priority=3,
    )
    return _respond(job, wait)


@router.post("/shot/{shot_id}")
def generate_shot(
    shot_id: str,
    user: dict = AuthUser,
    provider: str = "auto",
    wait: bool = Query(default=False),
):
    """Generate the image for a single storyboard shot using its stored prompt."""
    from ..db import query_one

    shot = query_one("SELECT * FROM shots WHERE id = ?", (shot_id,))
    if not shot:
        raise HTTPException(status_code=404, detail="Shot not found.")
    if not shot["prompt"]:
        raise HTTPException(status_code=400, detail="This shot has no prompt to generate from.")
    job = enqueue(
        "image.generate",
        {
            "prompt": shot["prompt"],
            "character_ids": [],
            "width": 1280,
            "height": 720,
            "provider": provider,
            "shot_id": shot_id,
        },
        project_id=shot["project_id"],
        label=f"Shot {shot['index_no'] + 1}",
        provider=provider,
    )
    if not wait:
        return {"job_id": job["id"], "queued": True}
    response = _respond(job, True)
    if response.get("asset_ids"):
        from ..db import update

        update("shots", shot_id, {"asset_id": response["asset_ids"][0], "status": "generated"})
    return response
