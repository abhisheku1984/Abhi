"""Projects, versioning and project-scoped listings (§21)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, NotFoundError
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.security import CurrentUser, get_current_user
from app.db.models import Asset, Character, Project, ProjectVersion, Scene, Shot, Voice
from app.db.session import get_db
from app.services import assets as asset_service

log = get_logger("api.projects")
router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectIn(BaseModel):
    name: str = Field(default="Untitled project", max_length=200)
    description: str = ""
    kind: str = "general"
    settings: dict[str, Any] = {}
    tags: list[str] = []


def _project_out(db: Session, project: Project) -> dict[str, Any]:
    asset_count = db.query(Asset).filter(Asset.project_id == project.id).count()
    scene_count = db.query(Scene).filter(Scene.project_id == project.id).count()
    character_count = db.query(Character).filter(Character.project_id == project.id).count()
    thumb = None
    if project.thumbnail_asset_id:
        asset = db.get(Asset, project.thumbnail_asset_id)
        thumb = f"/api/v1/files/{asset.thumbnail_key or asset.storage_key}" if asset else None
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "kind": project.kind,
        "status": project.status,
        "is_favorite": project.is_favorite,
        "tags": project.tags,
        "settings": project.settings,
        "current_version": project.current_version,
        "thumbnail_url": thumb,
        "counts": {"assets": asset_count, "scenes": scene_count, "characters": character_count},
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }


def _get_owned(db: Session, project_id: str, user: CurrentUser) -> Project:
    project = db.get(Project, project_id)
    if not project:
        raise NotFoundError("Project not found.", suggested_action="It may have been deleted.")
    if project.owner_id != user.id and not user.is_admin:
        raise ForbiddenError("You do not have access to this project.")
    return project


def snapshot_project(db: Session, project: Project, *, label: str = "", actor: str = "") -> ProjectVersion:
    scenes = db.query(Scene).filter(Scene.project_id == project.id).order_by(Scene.index).all()
    shots = db.query(Shot).filter(Shot.project_id == project.id).all()
    characters = db.query(Character).filter(Character.project_id == project.id).all()
    version = ProjectVersion(
        id=new_id("ver_"),
        project_id=project.id,
        entity="project",
        entity_id=project.id,
        version=project.current_version,
        label=label or f"v{project.current_version}",
        created_by=actor,
        snapshot={
            "project": {"name": project.name, "description": project.description, "settings": project.settings},
            "scenes": [
                {"id": s.id, "index": s.index, "title": s.title, "prompt": s.prompt,
                 "video_prompt": s.video_prompt, "narration": s.narration, "image_asset_id": s.image_asset_id,
                 "video_asset_id": s.video_asset_id, "locked": s.locked, "duration_sec": s.duration_sec}
                for s in scenes
            ],
            "shots": [
                {"id": s.id, "scene_id": s.scene_id, "index": s.index, "camera": s.camera, "lens": s.lens,
                 "motion": s.motion, "lighting": s.lighting, "duration_sec": s.duration_sec,
                 "transition": s.transition, "image_asset_id": s.image_asset_id,
                 "video_asset_id": s.video_asset_id, "locked": s.locked}
                for s in shots
            ],
            "characters": [
                {"id": c.id, "name": c.name, "profile": c.profile, "locks": c.locks,
                 "reference_asset_ids": c.reference_asset_ids, "voice_id": c.voice_id}
                for c in characters
            ],
        },
    )
    db.add(version)
    project.current_version += 1
    db.flush()
    return version


@router.get("")
def list_projects(
    *,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user),
    kind: Optional[str] = None,
    favorite: Optional[bool] = None,
    q: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> Any:
    query = db.query(Project).filter(Project.owner_id == current.id)
    if kind:
        query = query.filter(Project.kind == kind)
    if favorite is not None:
        query = query.filter(Project.is_favorite == favorite)
    if q:
        query = query.filter(Project.name.ilike(f"%{q}%"))
    total = query.count()
    items = query.order_by(Project.updated_at.desc()).offset(offset).limit(limit).all()
    return {"items": [_project_out(db, p) for p in items], "total": total, "limit": limit, "offset": offset}


@router.post("", status_code=201)
def create_project(payload: ProjectIn, db: Session = Depends(get_db),
                   current: CurrentUser = Depends(get_current_user)) -> Any:
    project = Project(
        id=new_id("prj_"),
        owner_id=current.id,
        name=payload.name,
        description=payload.description,
        kind=payload.kind,
        settings=payload.settings or {},
        tags=payload.tags or [],
    )
    db.add(project)
    db.flush()
    db.commit()
    log.info("project_created", project_id=project.id)
    return _project_out(db, project)


@router.get("/{project_id}")
def get_project(project_id: str, db: Session = Depends(get_db),
                current: CurrentUser = Depends(get_current_user)) -> Any:
    project = _get_owned(db, project_id, current)
    return _project_out(db, project)


@router.patch("/{project_id}")
def update_project(project_id: str, payload: dict, db: Session = Depends(get_db),
                   current: CurrentUser = Depends(get_current_user)) -> Any:
    project = _get_owned(db, project_id, current)
    for key in ("name", "description", "kind", "status", "thumbnail_asset_id"):
        if key in payload:
            setattr(project, key, payload[key])
    if "settings" in payload and isinstance(payload["settings"], dict):
        project.settings = {**(project.settings or {}), **payload["settings"]}
    if "tags" in payload and isinstance(payload["tags"], list):
        project.tags = payload["tags"]
    if "is_favorite" in payload:
        project.is_favorite = bool(payload["is_favorite"])
    db.commit()
    return _project_out(db, project)


@router.delete("/{project_id}")
def delete_project(project_id: str, db: Session = Depends(get_db),
                   current: CurrentUser = Depends(get_current_user)) -> Any:
    project = _get_owned(db, project_id, current)
    db.delete(project)
    db.commit()
    log.info("project_deleted", project_id=project_id)
    return {"ok": True, "id": project_id}


@router.get("/{project_id}/assets")
def project_assets(project_id: str, *, db: Session = Depends(get_db),
                   current: CurrentUser = Depends(get_current_user),
                   kind: Optional[str] = None,
                   limit: int = Query(100, ge=1, le=500)) -> Any:
    _get_owned(db, project_id, current)
    query = db.query(Asset).filter(Asset.project_id == project_id)
    if kind:
        query = query.filter(Asset.kind == kind)
    items = query.order_by(Asset.created_at.desc()).limit(limit).all()
    return {"items": [asset_service.to_dict(a) for a in items], "total": len(items)}


@router.get("/{project_id}/versions")
def list_versions(project_id: str, db: Session = Depends(get_db),
                  current: CurrentUser = Depends(get_current_user)) -> Any:
    _get_owned(db, project_id, current)
    versions = (
        db.query(ProjectVersion)
        .filter(ProjectVersion.project_id == project_id)
        .order_by(ProjectVersion.version.desc())
        .all()
    )
    return {
        "items": [
            {"id": v.id, "version": v.version, "label": v.label, "entity": v.entity,
             "created_by": v.created_by, "created_at": v.created_at.isoformat() if v.created_at else None,
             "size": len(v.snapshot or {})}
            for v in versions
        ]
    }


@router.post("/{project_id}/versions")
def create_version(project_id: str, payload: dict, db: Session = Depends(get_db),
                   current: CurrentUser = Depends(get_current_user)) -> Any:
    project = _get_owned(db, project_id, current)
    version = snapshot_project(db, project, label=str(payload.get("label", "")), actor=current.email)
    db.commit()
    return {"id": version.id, "version": version.version, "label": version.label}


@router.post("/{project_id}/versions/{version_id}/restore")
def restore_version(project_id: str, version_id: str, db: Session = Depends(get_db),
                    current: CurrentUser = Depends(get_current_user)) -> Any:
    project = _get_owned(db, project_id, current)
    version = db.get(ProjectVersion, version_id)
    if not version or version.project_id != project_id:
        raise NotFoundError("Version not found.", suggested_action="Refresh the version list.")
    snapshot = version.snapshot or {}

    meta = snapshot.get("project") or {}
    project.name = meta.get("name", project.name)
    project.description = meta.get("description", project.description)
    project.settings = meta.get("settings", project.settings)

    for scene_data in snapshot.get("scenes", []):
        scene = db.get(Scene, scene_data["id"])
        if scene:
            for key in ("title", "prompt", "video_prompt", "narration", "image_asset_id",
                        "video_asset_id", "locked", "duration_sec"):
                if key in scene_data:
                    setattr(scene, key, scene_data[key])
    for shot_data in snapshot.get("shots", []):
        shot = db.get(Shot, shot_data["id"])
        if shot:
            for key in ("camera", "lens", "motion", "lighting", "duration_sec", "transition",
                        "image_asset_id", "video_asset_id", "locked"):
                if key in shot_data:
                    setattr(shot, key, shot_data[key])
    for char_data in snapshot.get("characters", []):
        char = db.get(Character, char_data["id"])
        if char:
            char.profile = char_data.get("profile", char.profile)
            char.locks = char_data.get("locks", char.locks)
            char.reference_asset_ids = char_data.get("reference_asset_ids", char.reference_asset_ids)
            char.voice_id = char_data.get("voice_id", char.voice_id)
    db.commit()
    log.info("version_restored", project_id=project_id, version=version.version)
    return {"ok": True, "restored_version": version.version}
