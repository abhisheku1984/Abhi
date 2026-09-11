"""Story → scenes → shots → storyboard (§11, §12).

Every scene can be regenerated on its own without touching the others.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.security import CurrentUser, get_current_user
from app.db.models import Asset, Project, Scene, Shot
from app.db.session import get_db
from app.jobs import queue
from app.services import story as story_service

log = get_logger("api.story")
router = APIRouter(prefix="/story", tags=["story"])


class StoryIn(BaseModel):
    prompt: str
    duration_sec: int = Field(default=60, ge=10, le=600)
    scene_count: Optional[int] = Field(default=None, ge=1, le=24)
    audience: str = "children"
    language: str = "en"
    project_id: Optional[str] = None
    target: str = "image"  # image | video
    generate_now: bool = True


class ScenePatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    script: Optional[str] = None
    narration: Optional[str] = None
    dialogue: Optional[str] = None
    prompt: Optional[str] = None
    video_prompt: Optional[str] = None
    duration_sec: Optional[float] = None
    locked: Optional[bool] = None


class ShotPatch(BaseModel):
    description: Optional[str] = None
    camera: Optional[str] = None
    lens: Optional[str] = None
    motion: Optional[str] = None
    lighting: Optional[str] = None
    duration_sec: Optional[float] = None
    transition: Optional[str] = None
    dialogue: Optional[str] = None
    character_id: Optional[str] = None
    voice_id: Optional[str] = None
    locked: Optional[bool] = None
    index: Optional[int] = None


def _shot_out(db: Session, shot: Shot) -> dict[str, Any]:
    image_url = video_url = None
    if shot.image_asset_id:
        asset = db.get(Asset, shot.image_asset_id)
        image_url = f"/api/v1/files/{asset.thumbnail_key or asset.storage_key}" if asset else None
    if shot.video_asset_id:
        asset = db.get(Asset, shot.video_asset_id)
        video_url = f"/api/v1/files/{asset.storage_key}" if asset else None
    return {**shot.to_dict(exclude=set()), "image_url": image_url, "video_url": video_url,
            "created_at": shot.created_at.isoformat() if shot.created_at else None}


def _scene_out(db: Session, scene: Scene) -> dict[str, Any]:
    image_url = video_url = audio_url = None
    if scene.image_asset_id:
        asset = db.get(Asset, scene.image_asset_id)
        image_url = f"/api/v1/files/{asset.thumbnail_key or asset.storage_key}" if asset else None
    if scene.video_asset_id:
        asset = db.get(Asset, scene.video_asset_id)
        video_url = f"/api/v1/files/{asset.storage_key}" if asset else None
    if scene.audio_asset_id:
        asset = db.get(Asset, scene.audio_asset_id)
        audio_url = f"/api/v1/files/{asset.storage_key}" if asset else None
    shots = db.query(Shot).filter(Shot.scene_id == scene.id).order_by(Shot.index).all()
    return {
        **scene.to_dict(exclude=set()),
        "image_url": image_url, "video_url": video_url, "audio_url": audio_url,
        "shots": [_shot_out(db, s) for s in shots],
        "created_at": scene.created_at.isoformat() if scene.created_at else None,
    }


def _project_for(db: Session, project_id: Optional[str], current: CurrentUser) -> Project:
    if project_id:
        project = db.get(Project, project_id)
        if not project or project.owner_id != current.id:
            raise NotFoundError("Project not found.")
        return project
    project = Project(id=new_id("prj_"), owner_id=current.id, name="Untitled story", kind="story")
    db.add(project)
    db.flush()
    return project


@router.post("/generate")
def generate_story(payload: StoryIn, db: Session = Depends(get_db),
                   current: CurrentUser = Depends(get_current_user)) -> Any:
    current.require("story:write")
    project = _project_for(db, payload.project_id, current)
    plan = story_service.plan_story(
        payload.prompt, duration_sec=payload.duration_sec, scene_count=payload.scene_count,
        audience=payload.audience, language=payload.language, seed=hash(payload.prompt) % 10000,
    )
    project.name = plan["title"][:200]
    project.kind = "story"
    project.settings = {**(project.settings or {}), "story": {"language": payload.language,
                                                              "audience": payload.audience}}
    db.flush()

    if payload.generate_now:
        job = queue.enqueue(db, owner_id=current.id, type="story", mode="story-to-video",
                            project_id=project.id,
                            params={"project_id": project.id, "plan": plan, "target": payload.target,
                                    "params": {"aspect": "16:9", "resolution": "768"}})
        db.commit()
        return {"project_id": project.id, "job_id": job.id, "plan": plan, "queued": True}

    story_service.persist_story(db, project.id, plan)
    db.commit()
    return {"project_id": project.id, "plan": plan, "queued": False}


@router.get("/{project_id}")
def get_story(project_id: str, db: Session = Depends(get_db),
              current: CurrentUser = Depends(get_current_user)) -> Any:
    scenes = db.query(Scene).filter(Scene.project_id == project_id).order_by(Scene.index).all()
    return {"project_id": project_id, "scenes": [_scene_out(db, s) for s in scenes], "total": len(scenes)}


@router.patch("/scenes/{scene_id}")
def patch_scene(scene_id: str, payload: ScenePatch, db: Session = Depends(get_db),
                current: CurrentUser = Depends(get_current_user)) -> Any:
    scene = db.get(Scene, scene_id)
    if not scene:
        raise NotFoundError("Scene not found.")
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(scene, key, value)
    db.commit()
    return _scene_out(db, scene)


@router.post("/scenes/{scene_id}/regenerate")
def regenerate_scene(scene_id: str, payload: dict | None = None, db: Session = Depends(get_db),
                     current: CurrentUser = Depends(get_current_user)) -> Any:
    scene = db.get(Scene, scene_id)
    if not scene:
        raise NotFoundError("Scene not found.")
    target = str((payload or {}).get("target", "image"))
    job = queue.enqueue(db, owner_id=current.id, type="story", mode="regenerate-scene",
                        project_id=scene.project_id,
                        params={"project_id": scene.project_id, "scene_ids": [scene_id],
                                "target": target, "params": (payload or {}).get("params", {})})
    return {"job_id": job.id, "scene_id": scene_id, "status": "queued"}


@router.delete("/scenes/{scene_id}")
def delete_scene(scene_id: str, db: Session = Depends(get_db),
                 current: CurrentUser = Depends(get_current_user)) -> Any:
    scene = db.get(Scene, scene_id)
    if not scene:
        raise NotFoundError("Scene not found.")
    db.query(Shot).filter(Shot.scene_id == scene_id).delete()
    db.delete(scene)
    db.commit()
    return {"ok": True, "id": scene_id}


@router.patch("/shots/{shot_id}")
def patch_shot(shot_id: str, payload: ShotPatch, db: Session = Depends(get_db),
               current: CurrentUser = Depends(get_current_user)) -> Any:
    shot = db.get(Shot, shot_id)
    if not shot:
        raise NotFoundError("Shot not found.")
    for key, value in payload.model_dump(exclude_none=True).items():
        setattr(shot, key, value)
    db.commit()
    return _shot_out(db, shot)


@router.post("/shots/{shot_id}/duplicate")
def duplicate_shot(shot_id: str, db: Session = Depends(get_db),
                   current: CurrentUser = Depends(get_current_user)) -> Any:
    shot = db.get(Shot, shot_id)
    if not shot:
        raise NotFoundError("Shot not found.")
    copy = Shot(
        id=new_id("sht_"), scene_id=shot.scene_id, project_id=shot.project_id,
        index=shot.index + 1, description=shot.description, camera=shot.camera, lens=shot.lens,
        motion=shot.motion, lighting=shot.lighting, duration_sec=shot.duration_sec,
        transition=shot.transition, character_id=shot.character_id, voice_id=shot.voice_id,
        dialogue=shot.dialogue, sfx=shot.sfx, image_asset_id=shot.image_asset_id,
    )
    db.add(copy)
    db.commit()
    return _shot_out(db, copy)


@router.delete("/shots/{shot_id}")
def delete_shot(shot_id: str, db: Session = Depends(get_db),
                current: CurrentUser = Depends(get_current_user)) -> Any:
    shot = db.get(Shot, shot_id)
    if not shot:
        raise NotFoundError("Shot not found.")
    db.delete(shot)
    db.commit()
    return {"ok": True, "id": shot_id}


@router.post("/{project_id}/render")
def render_story_video(project_id: str, payload: dict | None = None, db: Session = Depends(get_db),
                       current: CurrentUser = Depends(get_current_user)) -> Any:
    """Concatenate ready scene videos into a single cut (real FFmpeg concat)."""
    from app.services import editor

    scenes = (
        db.query(Scene)
        .filter(Scene.project_id == project_id, Scene.video_asset_id.isnot(None))
        .order_by(Scene.index)
        .all()
    )
    if not scenes:
        raise NotFoundError("No rendered scene videos yet.",
                            suggested_action="Generate scene videos first.")
    from app.storage.backends import get_storage

    storage = get_storage()
    timeline = {
        "clips": [
            {"track": "video", "asset_id": s.video_asset_id, "start": 0,
             "transition": s.transition or "cut"}
            for s in scenes
        ],
        "captions": [
            {"start": i * 5.0, "end": (i + 1) * 5.0, "text": s.narration[:180]}
            for i, s in enumerate(scenes)
        ] if (payload or {}).get("captions") else [],
    }
    job = queue.enqueue(db, owner_id=current.id, type="render", mode="story-render",
                        project_id=project_id,
                        params={"timeline": timeline, "preset": (payload or {}).get("preset", "youtube"),
                                "name": (payload or {}).get("name") or "Story export"})
    return {"job_id": job.id, "status": "queued", "scenes": len(scenes)}
