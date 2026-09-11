"""Domain model for AI Creative Studio.

Entities (spec §37): User, Project, Asset, Generation, Model, Character, Voice,
Avatar, Scene, Shot, Prompt, Workflow, Job, Subscription, Usage, Settings.
Plus AuditLog, FeatureFlag, RefreshToken and WorkflowRun for observability,
RBAC and workflow execution history.

Design notes
------------
* Public ids are UUID strings - never sequential ints, never filenames.
* Every asset is content addressed (sha256) in addition to its UUID so identical
  bytes are stored once and integrity is verifiable on read.
* Project-scoped rows carry (project_id, created_at) indexes because the UI
  always lists "things inside a project, newest first".
* Version history: Asset/Scene/Shot keep a monotonic `version` and
  `previous_version_id`, enabling restoration without duplicating rows.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Index,
    CheckConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import AuditMixin, Base, TimestampMixin, UUIDMixin

# ---------------------------------------------------------------------------
# Enums (stored as constrained strings so SQLite/PostgreSQL behave identically)
# ---------------------------------------------------------------------------
USER_ROLES = ("user", "admin", "moderator")
ASSET_KINDS = ("image", "video", "audio", "avatar", "character_ref", "document", "other")
ASSET_SOURCES = ("generated", "uploaded", "edited", "derived", "imported")
JOB_STATUSES = ("queued", "processing", "completed", "failed", "cancelled", "blocked")
JOB_KINDS = (
    "image.generate", "image.edit", "image.upscale", "image.transform",
    "video.generate", "video.edit", "video.render", "video.upscale",
    "avatar.generate", "avatar.talking", "avatar.lipsync",
    "voice.synthesize", "voice.clone",
    "audio.generate", "audio.edit",
    "story.generate", "storyboard.generate", "scene.render", "shot.render",
    "workflow.run", "export.render", "asset.import",
)
MODEL_CAPS = (
    "text_to_image", "image_to_image", "inpainting", "upscale", "background_removal",
    "text_to_video", "image_to_video", "video_to_video", "first_last_frame",
    "avatar", "lipsync", "tts", "voice_clone", "music", "sfx", "llm",
)
MODEL_STATUS = ("available", "installed", "not_installed", "missing_dependency",
                "hardware_requirement_not_met", "external_provider_required", "error")


def _json() -> dict[str, Any]:
    return {}


# ---------------------------------------------------------------------------
# Identity & access
# ---------------------------------------------------------------------------
class User(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "users"
    # NOTE: `role` already gets ix_users_role from index=True below - declaring the
    # same index name here would make CREATE TABLE fail with "index already exists".
    __table_args__ = (
        CheckConstraint("role in ('user','admin','moderator')", name="ck_users_role"),
    )

    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default="user", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    avatar_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    storage_quota_mb: Mapped[int] = mapped_column(Integer, default=10240, nullable=False)
    # Consent attestation for voice cloning (safety §43): cloning is refused
    # unless the profile carries a stored, dated attestation.
    voice_consent_attested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    projects: Mapped[list["Project"]] = relationship(back_populates="owner", cascade="all, delete-orphan")
    subscription: Mapped["Subscription | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )


class RefreshToken(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_user_revoked", "user_id", "is_revoked"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Subscription(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "subscriptions"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    plan: Mapped[str] = mapped_column(String(40), default="free", nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    monthly_credits: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    used_credits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    renews_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship(back_populates="subscription")


class UsageRecord(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "usage_records"
    __table_args__ = (Index("ix_usage_user_created", "user_id", "created_at"), Index("ix_usage_kind", "kind"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)          # job kind or 'storage'
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    model_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    credits: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    storage_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)


# ---------------------------------------------------------------------------
# Projects & content
# ---------------------------------------------------------------------------
class Project(UUIDMixin, TimestampMixin, AuditMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_owner_updated", "owner_id", "updated_at"),
        Index("ix_projects_status", "status"),
    )

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)  # draft|active|archived
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    thumbnail_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # Story pipeline output (title, logline, script, global style, characters…)
    story: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)
    settings: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    owner: Mapped["User"] = relationship(back_populates="projects")
    assets: Mapped[list["Asset"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    scenes: Mapped[list["Scene"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    characters: Mapped[list["Character"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    generations: Mapped[list["Generation"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Asset(UUIDMixin, TimestampMixin, AuditMixin, Base):
    """Any stored binary (image/video/audio/avatar reference/document)."""

    __tablename__ = "assets"
    __table_args__ = (
        Index("ix_assets_project_kind", "project_id", "kind", "created_at"),
        Index("ix_assets_owner_kind", "owner_id", "kind", "created_at"),
        # sha256 already has index=True below (ix_assets_sha256) - not redeclared
        # here to avoid a duplicate CREATE INDEX name.
        Index("ix_assets_favorite", "owner_id", "is_favorite"),
        CheckConstraint(
            "kind in ('image','video','audio','avatar','character_ref','document','other')",
            name="ck_assets_kind",
        ),
    )

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    parent_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)  # derived-from
    previous_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="image", nullable=False)
    source: Mapped[str] = mapped_column(String(20), default="generated", nullable=False)

    # Storage is content addressed: `storage_key` is derived from sha256, never
    # from a user supplied filename (path traversal protection, §42).
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    storage_backend: Mapped[str] = mapped_column(String(20), default="local", nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    relative_path: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream", nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Media metadata
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_alpha: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    thumbnail_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    preview_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    # Provenance & safety (§43)
    provenance: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)
    safety: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)

    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    project: Mapped["Project | None"] = relationship(back_populates="assets")


class Generation(UUIDMixin, TimestampMixin, AuditMixin, Base):
    """A single generation request and its outputs (lineage for regeneration)."""

    __tablename__ = "generations"
    __table_args__ = (
        Index("ix_generations_project_created", "project_id", "created_at"),
        Index("ix_generations_owner_kind", "owner_id", "kind", "created_at"),
    )

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)   # image.generate|video.generate|...
    model_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    adapter: Mapped[str | None] = mapped_column(String(64), nullable=True)

    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    negative_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    parameters: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)
    seed: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Structured prompt (AI prompt engine, §29)
    structured_prompt: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)
    character_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    reference_asset_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False, index=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_asset_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    project: Mapped["Project | None"] = relationship(back_populates="generations")


class Job(UUIDMixin, TimestampMixin, Base):
    """Asynchronous unit of work. Never blocks a request handler (§33)."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_priority", "status", "priority", "created_at"),
        Index("ix_jobs_owner_created", "owner_id", "created_at"),
        Index("ix_jobs_kind_status", "kind", "status"),
        CheckConstraint(
            "status in ('queued','processing','completed','failed','cancelled','blocked')",
            name="ck_jobs_status",
        ),
    )

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    generation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    workflow_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=5, nullable=False)

    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    stage: Mapped[str] = mapped_column(String(80), default="queued", nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    model_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    adapter: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device: Mapped[str | None] = mapped_column(String(32), nullable=True)      # cpu|cuda:0|...
    gpu_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    params: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_trace: Mapped[str | None] = mapped_column(Text, nullable=True)

    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    queued_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)


# ---------------------------------------------------------------------------
# Characters / voices / avatars
# ---------------------------------------------------------------------------
class Character(UUIDMixin, TimestampMixin, AuditMixin, Base):
    __tablename__ = "characters"
    __table_args__ = (
        Index("ix_characters_project_created", "project_id", "created_at"),
        Index("ix_characters_owner", "owner_id", "created_at"),
    )

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    face: Mapped[str | None] = mapped_column(Text, nullable=True)
    hair: Mapped[str | None] = mapped_column(Text, nullable=True)
    age_appearance: Mapped[str | None] = mapped_column(String(60), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    clothing: Mapped[str | None] = mapped_column(Text, nullable=True)
    accessories: Mapped[str | None] = mapped_column(Text, nullable=True)
    personality: Mapped[str | None] = mapped_column(Text, nullable=True)
    style: Mapped[str | None] = mapped_column(Text, nullable=True)
    environment: Mapped[str | None] = mapped_column(Text, nullable=True)

    voice_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    reference_asset_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    embedding_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Consistency locks (§20)
    locks: Mapped[dict] = mapped_column(
        JSON,
        default=lambda: {
            "face": True, "body": True, "clothing": True, "age": True,
            "hair": True, "style": True, "environment": True, "voice": True,
        },
        nullable=False,
    )
    # Deterministic identity seed: keeps a character recognisable across shots
    # even when different renderers are used.
    identity_seed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    palette: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    prompt_fragment: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    project: Mapped["Project | None"] = relationship(back_populates="characters")


class VoiceProfile(UUIDMixin, TimestampMixin, AuditMixin, Base):
    __tablename__ = "voice_profiles"
    __table_args__ = (Index("ix_voices_owner", "owner_id", "created_at"),)

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    engine: Mapped[str] = mapped_column(String(64), default="", nullable=False)   # adapter id
    language: Mapped[str] = mapped_column(String(16), default="en", nullable=False)
    accent: Mapped[str] = mapped_column(String(32), default="", nullable=False)
    gender: Mapped[str] = mapped_column(String(20), default="", nullable=False)
    style: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    emotion: Mapped[str] = mapped_column(String(60), default="neutral", nullable=False)
    pitch: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    speed: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    is_clone: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    consent_attested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consent_document: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    preview_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)


class Avatar(UUIDMixin, TimestampMixin, AuditMixin, Base):
    __tablename__ = "avatars"
    __table_args__ = (Index("ix_avatars_owner", "owner_id", "created_at"),)

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    character_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    avatar_type: Mapped[str] = mapped_column(String(40), default="photorealistic_human", nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    thumbnail_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    voice_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    personality: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)


# ---------------------------------------------------------------------------
# Story / scenes / shots
# ---------------------------------------------------------------------------
class Scene(UUIDMixin, TimestampMixin, AuditMixin, Base):
    __tablename__ = "scenes"
    __table_args__ = (
        Index("ix_scenes_project_order", "project_id", "order_index"),
        UniqueConstraint("project_id", "order_index", name="uq_scene_order"),
    )

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    title: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    narration: Mapped[str | None] = mapped_column(Text, nullable=True)
    dialogue: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    time_of_day: Mapped[str | None] = mapped_column(String(60), nullable=True)
    mood: Mapped[str | None] = mapped_column(String(80), nullable=True)
    image_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    video_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    audio_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    character_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="planned", nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)

    project: Mapped["Project | None"] = relationship(back_populates="scenes")
    shots: Mapped[list["Shot"]] = relationship(back_populates="scene", cascade="all, delete-orphan")


class Shot(UUIDMixin, TimestampMixin, AuditMixin, Base):
    __tablename__ = "shots"
    __table_args__ = (
        Index("ix_shots_scene_order", "scene_id", "order_index"),
        UniqueConstraint("scene_id", "order_index", name="uq_shot_order"),
    )

    scene_id: Mapped[str] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    shot_number: Mapped[str] = mapped_column(String(20), default="1", nullable=False)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    video_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    camera: Mapped[str] = mapped_column(String(40), default="static", nullable=False)
    lens: Mapped[str] = mapped_column(String(20), default="35mm", nullable=False)
    motion: Mapped[str] = mapped_column(String(30), default="normal", nullable=False)
    lighting: Mapped[str] = mapped_column(String(30), default="cinematic", nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, default=4.0, nullable=False)
    transition: Mapped[str] = mapped_column(String(30), default="cut", nullable=False)

    character_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    dialogue: Mapped[str | None] = mapped_column(Text, nullable=True)
    voice_profile_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    narration: Mapped[str | None] = mapped_column(Text, nullable=True)
    sound_effects: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    music_cue: Mapped[str | None] = mapped_column(String(200), nullable=True)

    image_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    video_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    audio_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="planned", nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)

    scene: Mapped["Scene | None"] = relationship(back_populates="shots")


class PromptTemplate(UUIDMixin, TimestampMixin, AuditMixin, Base):
    __tablename__ = "prompt_templates"
    __table_args__ = (Index("ix_prompts_owner_kind", "owner_id", "kind"),)

    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), default="general", nullable=False)
    category: Mapped[str] = mapped_column(String(40), default="general", nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    negative_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    variables: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)


# ---------------------------------------------------------------------------
# Models & workflows
# ---------------------------------------------------------------------------
class ModelRecord(UUIDMixin, TimestampMixin, Base):
    """Model Manager catalogue (§35). Rows are metadata; weights live elsewhere."""

    __tablename__ = "models"
    __table_args__ = (
        # Distinct name: ix_models_capability is taken by the column-level index.
        Index("ix_models_capability_status", "capability", "status"),
        UniqueConstraint("model_id", name="uq_model_id"),
    )

    model_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)   # e.g. sdxl-base-1.0
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    adapter: Mapped[str] = mapped_column(String(64), nullable=False)                  # adapter class key
    capability: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(40), default="1.0", nullable=False)
    license: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    source_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    size_gb: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    vram_gb: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    ram_gb: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    local: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)        # False = API only
    provider: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="not_installed", nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    install_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    capabilities: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    requires: Mapped[list] = mapped_column(JSON, default=list, nullable=False)        # python packages
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)


class Workflow(UUIDMixin, TimestampMixin, AuditMixin, Base):
    __tablename__ = "workflows"
    __table_args__ = (Index("ix_workflows_owner", "owner_id", "created_at"),)

    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    graph: Mapped[dict] = mapped_column(JSON, default=lambda: {"nodes": [], "edges": []}, nullable=False)
    is_template: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    meta: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)

    runs: Mapped[list["WorkflowRun"]] = relationship(back_populates="workflow", cascade="all, delete-orphan")


class WorkflowRun(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (Index("ix_workflow_runs_workflow", "workflow_id", "created_at"),)

    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", nullable=False, index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    node_results: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)
    node_status: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    workflow: Mapped["Workflow | None"] = relationship(back_populates="runs")


# ---------------------------------------------------------------------------
# Platform: settings, flags, audit
# ---------------------------------------------------------------------------
class Setting(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "settings"
    __table_args__ = (UniqueConstraint("scope", "key", "user_id", name="uq_setting_scope_key_user"),)

    scope: Mapped[str] = mapped_column(String(30), default="global", nullable=False, index=True)  # global|user|branding
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    key: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    value: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)


class FeatureFlag(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "feature_flags"
    __table_args__ = (UniqueConstraint("key", name="uq_feature_flag_key"),)

    key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)


class AuditLog(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_user_created", "user_id", "created_at"),
        Index("ix_audit_action_created", "action", "created_at"),
    )

    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    severity: Mapped[str] = mapped_column(String(20), default="info", nullable=False)
    details: Mapped[dict] = mapped_column(JSON, default=_json, nullable=False)
