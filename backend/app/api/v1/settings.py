"""Runtime settings and branding (§1). Secrets never leave the server (§34)."""

from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ForbiddenError
from app.core.logging import get_logger
from app.core.security import CurrentUser, get_current_user
from app.db.models import AppSetting
from app.db.session import get_db

log = get_logger("api.settings")
router = APIRouter(prefix="/settings", tags=["settings"])

DEFAULT_BRANDING = {
    "product_name": settings.BRAND_PRODUCT_NAME,
    "company_name": settings.BRAND_COMPANY_NAME,
    "primary_color": settings.BRAND_PRIMARY_COLOR,
    "secondary_color": settings.BRAND_SECONDARY_COLOR,
    "theme": settings.BRAND_THEME,
    "footer": settings.BRAND_FOOTER,
    "logo_url": "",
    "favicon_url": "",
    "login_headline": "Create anything with AI",
    "login_subheadline": "Image, video, avatar, voice and audio — in one studio.",
}

PROVIDER_ENV = {
    "image": ("IMAGE_API_BASE_URL", "IMAGE_API_KEY", "IMAGE_API_MODEL"),
    "video": ("VIDEO_API_BASE_URL", "VIDEO_API_KEY", "VIDEO_API_MODEL"),
    "voice": ("TTS_API_BASE_URL", "TTS_API_KEY", "TTS_API_MODEL"),
    "audio": ("AUDIO_API_BASE_URL", "AUDIO_API_KEY", "AUDIO_API_MODEL"),
    "avatar": ("AVATAR_API_BASE_URL", "AVATAR_API_KEY", "AVATAR_API_MODEL"),
    "diffusion-image": ("DIFFUSERS_IMAGE_MODEL", "", ""),
    "diffusion-video": ("DIFFUSERS_VIDEO_MODEL", "", ""),
    "lipsync": ("LIPSYNC_RUNNER", "LIPSYNC_CHECKPOINT", ""),
    "voice-clone": ("VOICE_CLONE_API_URL", "VOICE_CLONE_MODEL", ""),
}


class BrandingIn(BaseModel):
    product_name: Optional[str] = None
    company_name: Optional[str] = None
    primary_color: Optional[str] = None
    secondary_color: Optional[str] = None
    theme: Optional[str] = None
    footer: Optional[str] = None
    logo_url: Optional[str] = None
    favicon_url: Optional[str] = None
    login_headline: Optional[str] = None
    login_subheadline: Optional[str] = None


def _get(db: Session, key: str, default: Any) -> Any:
    row = db.query(AppSetting).filter(AppSetting.key == key).one_or_none()
    if row is None:
        return default
    value = row.value
    if isinstance(default, dict) and isinstance(value, dict):
        return {**default, **value}
    return value


def _set(db: Session, key: str, value: Any, actor: str) -> None:
    row = db.query(AppSetting).filter(AppSetting.key == key).one_or_none()
    if row is None:
        row = AppSetting(id=f"set_{key}", key=key)
        db.add(row)
    row.value = value
    row.updated_by = actor
    db.commit()


@router.get("/public")
def public_settings(db: Session = Depends(get_db)) -> Any:
    """Unauthenticated: everything the login/branding screen needs. No secrets."""
    branding = _get(db, "branding", DEFAULT_BRANDING)
    return {
        "branding": branding,
        "app": {"env": settings.APP_ENV, "version": "0.1.0", "api": settings.API_VERSION},
    }


@router.get("/branding")
def get_branding(db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)) -> Any:
    return _get(db, "branding", DEFAULT_BRANDING)


@router.put("/branding")
def update_branding(payload: BrandingIn, db: Session = Depends(get_db),
                    current: CurrentUser = Depends(get_current_user)) -> Any:
    if not current.is_admin:
        raise ForbiddenError("Only administrators can change branding.")
    branding = _get(db, "branding", DEFAULT_BRANDING)
    branding.update({k: v for k, v in payload.model_dump(exclude_none=True).items()})
    _set(db, "branding", branding, current.email)
    log.info("branding_updated", by=current.email)
    return branding


@router.get("/providers")
def providers(current: CurrentUser = Depends(get_current_user)) -> Any:
    """Shows which providers are configured — never the key values themselves."""
    return {
        "providers": [
            {
                "id": key,
                "configured": all(bool(os.getenv(env)) for env in envs if env),
                "env": [e for e in envs if e],
            }
            for key, envs in PROVIDER_ENV.items()
        ],
        "note": "Set these in backend/.env and restart. Keys are never sent to the browser.",
    }


@router.get("/moderation")
def get_moderation(db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)) -> Any:
    if not current.is_admin:
        raise ForbiddenError("Only administrators can view moderation settings.")
    return _get(db, "moderation", {"enabled": settings.SAFETY_ENABLED, "blocklist": settings.SAFETY_BLOCKLIST,
                                   "watermark": settings.WATERMARK_ENABLED,
                                   "watermark_text": settings.WATERMARK_TEXT,
                                   "require_voice_consent": settings.REQUIRE_VOICE_CLONE_ATTESTATION})


@router.put("/moderation")
def update_moderation(payload: dict, db: Session = Depends(get_db),
                      current: CurrentUser = Depends(get_current_user)) -> Any:
    if not current.is_admin:
        raise ForbiddenError("Only administrators can change moderation settings.")
    current_cfg = _get(db, "moderation", {})
    allowed = {"enabled", "blocklist", "watermark", "watermark_text", "require_voice_consent"}
    current_cfg.update({k: v for k, v in payload.items() if k in allowed})
    _set(db, "moderation", current_cfg, current.email)
    if "blocklist" in current_cfg:
        settings.SAFETY_BLOCKLIST = str(current_cfg["blocklist"])
    if "enabled" in current_cfg:
        settings.SAFETY_ENABLED = bool(current_cfg["enabled"])
    if "watermark" in current_cfg:
        settings.WATERMARK_ENABLED = bool(current_cfg["watermark"])
    if "watermark_text" in current_cfg:
        settings.WATERMARK_TEXT = str(current_cfg["watermark_text"])
    log.info("moderation_updated", by=current.email)
    return current_cfg


@router.get("/app")
def app_settings(db: Session = Depends(get_db), current: CurrentUser = Depends(get_current_user)) -> Any:
    return _get(db, "general", {"worker_concurrency": settings.WORKER_CONCURRENCY,
                                "max_upload_mb": settings.MAX_UPLOAD_MB,
                                "auto_download_models": settings.AUTO_DOWNLOAD_MODELS})


@router.put("/app")
def update_app_settings(payload: dict, db: Session = Depends(get_db),
                        current: CurrentUser = Depends(get_current_user)) -> Any:
    if not current.is_admin:
        raise ForbiddenError("Only administrators can change application settings.")
    cfg = _get(db, "general", {})
    allowed = {"worker_concurrency", "max_upload_mb", "auto_download_models"}
    cfg.update({k: v for k, v in payload.items() if k in allowed})
    _set(db, "general", cfg, current.email)
    if "auto_download_models" in cfg:
        settings.AUTO_DOWNLOAD_MODELS = bool(cfg["auto_download_models"])
    return cfg
