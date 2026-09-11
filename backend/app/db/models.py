"""Core persistence model (§25).

Portable across SQLite (local-first default) and PostgreSQL (production):
only dialect-neutral column types are used, so migrations behave identically
on both. Every entity carries a ULID primary key — never a filename (§24).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import METADATA, Base, utcnow


class User(Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="editor", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    avatar_asset_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    storage_quota_mb: Mapped[int] = mapped_column(Integer, default=20480)
    storage_used_mb: Mapped[float] = mapped_column(Float, default=0.0)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), default=None)
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Project(Base):
    __tablename__ = "projects"

    owner_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), default="Untitled project")
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(32), default="general", index=True)  # image|video|avatar|story|general
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)  # active|archived
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    thumbnail_asset_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    current_version: Mapped[int] = mapped_column(Integer, default=1)

    owner = relationship("User", lazy="joined")


class ProjectVersion(Base):
    """Version history with restore support (§21)."""

    __tablename__ = "project_versions"
    __table_args__ = (Index("ix_project_versions_project_entity", "project_id", "entity"),)

    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("projects.id"), index=True, nullable=False)
    entity: Mapped[str] = mapped_column(String(64), default="project")  # project|scene|shot|character
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    label: Mapped[str] = mapped_column(String(160), default="")
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(64), default="")


class Asset(Base):
    """Anything stored on disk: image, video, audio, avatar, character ref (§24)."""

    __tablename__ = "assets"
    __table_args__ = (
        Index("ix_assets_owner_kind", "owner_id", "kind"),
        Index("ix_assets_project_kind", "project_id", "kind"),
    )

    owner_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True, nullable=False)
    project_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("projects.id"), index=True, default=None)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # image|video|audio|avatar|character|document|other
    name: Mapped[str] = mapped_column(String(200), default="")
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(32), default="local")
    mime: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str] = mapped_column(String(128), default="", index=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    height: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    duration_sec: Mapped[Optional[float]] = mapped_column(Float, default=None)
    thumbnail_key: Mapped[Optional[str]] = mapped_column(String(512), default=None)
    preview_key: Mapped[Optional[str]] = mapped_column(String(512), default=None)  # low-res video proxy (§38)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # engine, seed, prompt, provenance, placeholder flags
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_asset_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Generation(Base):
    """A single user-visible generation attempt (always backed by a Job)."""

    __tablename__ = "generations"
    __table_args__ = (Index("ix_generations_owner_kind", "owner_id", "kind"),)

    owner_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True, nullable=False)
    project_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("projects.id"), index=True, default=None)
    job_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("jobs.id"), index=True, default=None)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # image|video|avatar|audio|voice
    mode: Mapped[str] = mapped_column(String(64), default="")  # text-to-image, image-to-video, ...
    engine: Mapped[str] = mapped_column(String(120), default="", index=True)
    model_id: Mapped[Optional[str]] = mapped_column(String(120), default=None)
    prompt: Mapped[str] = mapped_column(Text, default="")
    negative_prompt: Mapped[str] = mapped_column(Text, default="")
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    seed: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    asset_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, default=None)
    credits: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=None)


class ModelRecord(Base):
    """Registry row for every adapter known to the platform (§6, §32)."""

    __tablename__ = "models"

    key: Mapped[str] = mapped_column(String(160), unique=True, index=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    family: Mapped[str] = mapped_column(String(64), index=True)  # image|video|avatar|voice|audio|lipsync|upscale
    version: Mapped[str] = mapped_column(String(64), default="0.0.0")
    provider: Mapped[str] = mapped_column(String(64), default="local")
    license: Mapped[str] = mapped_column(String(120), default="unknown")
    size_mb: Mapped[int] = mapped_column(Integer, default=0)
    vram_mb: Mapped[int] = mapped_column(Integer, default=0)
    speeds: Mapped[str] = mapped_column(String(64), default="medium")  # fast|medium|slow
    is_local: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(32), default="available", index=True)
    # available | installed | active | not_installed | disabled | error
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    install_path: Mapped[str] = mapped_column(String(512), default="")
    download_url: Mapped[str] = mapped_column(String(512), default="")
    requires_extra: Mapped[str] = mapped_column(String(64), default="")
    notes: Mapped[str] = mapped_column(Text, default="")


class Character(Base):
    """Reusable character with lockable consistency attributes (§10)."""

    __tablename__ = "characters"
    __table_args__ = (Index("ix_characters_project", "project_id"),)

    owner_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True, nullable=False)
    project_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("projects.id"), index=True, default=None)
    name: Mapped[str] = mapped_column(String(160), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # face, hair, age_appearance, body_type, clothing, accessories, personality, style
    locks: Mapped[dict[str, bool]] = mapped_column(JSON, default=dict)
    # character, face, costume, style, environment, voice
    reference_asset_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    voice_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    thumbnail_asset_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)


class Voice(Base):
    __tablename__ = "voices"

    owner_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), default="")
    engine: Mapped[str] = mapped_column(String(120), default="")
    language: Mapped[str] = mapped_column(String(32), default="en", index=True)
    accent: Mapped[str] = mapped_column(String(64), default="")
    gender: Mapped[str] = mapped_column(String(32), default="neutral")
    style: Mapped[str] = mapped_column(String(64), default="neutral")
    emotion: Mapped[str] = mapped_column(String(64), default="neutral")
    speed: Mapped[float] = mapped_column(Float, default=1.0)
    pitch: Mapped[float] = mapped_column(Float, default=1.0)
    sample_asset_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    is_cloned: Mapped[bool] = mapped_column(Boolean, default=False)
    consent: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # §28 rights attestation
    status: Mapped[str] = mapped_column(String(32), default="active")


class Avatar(Base):
    __tablename__ = "avatars"

    owner_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), default="")
    avatar_type: Mapped[str] = mapped_column(String(64), default="corporate-presenter")
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # face, hair, clothing, background, camera, lighting, expression, gesture, language, accent, speed
    voice_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    reference_asset_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    thumbnail_asset_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    status: Mapped[str] = mapped_column(String(32), default="active")


class Scene(Base):
    __tablename__ = "scenes"
    __table_args__ = (Index("ix_scenes_project_index", "project_id", "index"),)

    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("projects.id"), index=True, nullable=False)
    index: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    script: Mapped[str] = mapped_column(Text, default="")
    narration: Mapped[str] = mapped_column(Text, default="")
    dialogue: Mapped[str] = mapped_column(Text, default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    video_prompt: Mapped[str] = mapped_column(Text, default="")
    character_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    image_asset_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    video_asset_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    audio_asset_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    duration_sec: Mapped[float] = mapped_column(Float, default=5.0)
    status: Mapped[str] = mapped_column(String(32), default="draft")  # draft|ready|failed
    locked: Mapped[bool] = mapped_column(Boolean, default=False)


class Shot(Base):
    __tablename__ = "shots"
    __table_args__ = (Index("ix_shots_scene_index", "scene_id", "index"),)

    scene_id: Mapped[str] = mapped_column(String(64), ForeignKey("scenes.id"), index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("projects.id"), index=True, nullable=False)
    index: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(Text, default="")
    camera: Mapped[str] = mapped_column(String(64), default="static")
    lens: Mapped[str] = mapped_column(String(32), default="35mm")
    motion: Mapped[str] = mapped_column(String(64), default="normal")
    lighting: Mapped[str] = mapped_column(String(64), default="cinematic")
    duration_sec: Mapped[float] = mapped_column(Float, default=4.0)
    transition: Mapped[str] = mapped_column(String(64), default="cut")
    character_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    voice_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    dialogue: Mapped[str] = mapped_column(Text, default="")
    sfx: Mapped[str] = mapped_column(String(200), default="")
    image_asset_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    video_asset_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)


class Prompt(Base):
    __tablename__ = "prompts"

    owner_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True, nullable=False)
    text: Mapped[str] = mapped_column(Text, default="")
    enhanced_text: Mapped[str] = mapped_column(Text, default="")
    structured: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)


class Workflow(Base):
    __tablename__ = "workflows"

    owner_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), default="Untitled workflow")
    description: Mapped[str] = mapped_column(Text, default="")
    graph: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # nodes + edges (§20)
    status: Mapped[str] = mapped_column(String(32), default="draft")  # draft|active|archived
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), default=None)
    last_run_job_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)


class Job(Base):
    """Background job record — the single source of truth for status (§22)."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_priority", "status", "priority"),
        Index("ix_jobs_owner_status", "owner_id", "status"),
    )

    owner_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id"), index=True, nullable=False)
    project_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("projects.id"), index=True, default=None)
    type: Mapped[str] = mapped_column(String(64), index=True)  # image|video|avatar|audio|voice|story|edit|workflow
    mode: Mapped[str] = mapped_column(String(64), default="")
    engine: Mapped[str] = mapped_column(String(120), default="", index=True)
    model_id: Mapped[Optional[str]] = mapped_column(String(120), default=None)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    # queued | processing | completed | failed | cancelled
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stage: Mapped[str] = mapped_column(String(120), default="queued")
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, default=None)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    worker_id: Mapped[Optional[str]] = mapped_column(String(64), default=None)
    gpu: Mapped[Optional[str]] = mapped_column(String(120), default=None)
    eta_seconds: Mapped[Optional[float]] = mapped_column(Float, default=None)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), default=None)
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), default=None)


class Usage(Base):
    __tablename__ = "usage"
    __table_args__ = (UniqueConstraint("owner_id", "day", "metric", name="uq_usage_owner_day_metric"),)

    owner_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    day: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    metric: Mapped[str] = mapped_column(String(64), default="")  # image|video|audio|minutes|storage
    value: Mapped[float] = mapped_column(Float, default=0.0)


class Subscription(Base):
    __tablename__ = "subscriptions"

    owner_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    plan: Mapped[str] = mapped_column(String(64), default="local")
    status: Mapped[str] = mapped_column(String(32), default="active")
    seats: Mapped[int] = mapped_column(Integer, default=1)
    monthly_credits: Mapped[int] = mapped_column(Integer, default=100000)
    used_credits: Mapped[int] = mapped_column(Integer, default=0)
    renews_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), default=None)


class AppSetting(Base):
    """Runtime settings incl. branding (§1) — key/value so the UI can extend freely."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(160), unique=True, index=True, nullable=False)
    value: Mapped[Any] = mapped_column(JSON, default=None)
    scope: Mapped[str] = mapped_column(String(32), default="global")
    updated_by: Mapped[str] = mapped_column(String(64), default="")


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_actor", "actor_id"),)

    actor_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    actor_email: Mapped[str] = mapped_column(String(320), default="")
    action: Mapped[str] = mapped_column(String(120), index=True)
    entity: Mapped[str] = mapped_column(String(64), default="")
    entity_id: Mapped[str] = mapped_column(String(64), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class FeatureFlag(Base):
    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(Text, default="")


__all__ = [
    "User", "Project", "ProjectVersion", "Asset", "Generation", "ModelRecord",
    "Character", "Voice", "Avatar", "Scene", "Shot", "Prompt", "Workflow",
    "Job", "Usage", "Subscription", "AppSetting", "AuditLog", "FeatureFlag",
]
