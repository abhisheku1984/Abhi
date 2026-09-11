"""Application configuration.

Every value is environment driven so the same image can run on a laptop
(SQLite + local files) or in production (PostgreSQL + S3 + Redis).
Secrets are never exposed to the frontend: the only setting the UI may read is
`BRANDING_*` (served through /api/settings/branding, which is public-by-design).
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]          # .../backend
ROOT_DIR = BACKEND_DIR.parent                              # repo root


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(ROOT_DIR / ".env"), str(BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- application -------------------------------------------------------
    APP_NAME: str = "AI Creative Studio"
    APP_ENV: str = "development"
    DEBUG: bool = True
    API_V1_PREFIX: str = "/api"

    # ---- security ----------------------------------------------------------
    SECRET_KEY: str = "change-me-in-production-please-use-openssl-rand-hex-32"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14
    # Comma separated list of origins allowed to call the API / open the SPA.
    CORS_ORIGINS: str = "*"
    # Rate limiting (per IP, per window)
    RATE_LIMIT_PER_MINUTE: int = 600
    RATE_LIMIT_AUTH_PER_MINUTE: int = 20

    # ---- database ----------------------------------------------------------
    # sqlite (default, zero-config) -> postgresql+psycopg://user:pass@host:5432/db
    DATABASE_URL: str = f"sqlite:///{ROOT_DIR / 'storage' / 'studio.db'}"
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

    # ---- storage -----------------------------------------------------------
    STORAGE_BACKEND: str = "local"        # local | s3
    STORAGE_DIR: str = str(ROOT_DIR / "storage" / "assets")
    S3_ENDPOINT_URL: str = ""
    S3_BUCKET: str = "ai-creative-studio"
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""
    S3_REGION: str = "us-east-1"

    # ---- queue -------------------------------------------------------------
    QUEUE_BACKEND: str = "database"       # database | redis
    REDIS_URL: str = ""
    JOB_WORKERS: int = 2
    JOB_HEARTBEAT_SECONDS: int = 5
    JOB_MAX_ATTEMPTS: int = 3
    # A worker picks up jobs whose lease expired (crash recovery).
    JOB_STUCK_TIMEOUT_SECONDS: int = 900

    # ---- upload limits -----------------------------------------------------
    MAX_UPLOAD_MB: int = 200
    ALLOWED_IMAGE_EXT: str = ".png,.jpg,.jpeg,.webp,.bmp,.gif,.tiff"
    ALLOWED_VIDEO_EXT: str = ".mp4,.mov,.mkv,.webm,.avi,.m4v"
    ALLOWED_AUDIO_EXT: str = ".wav,.mp3,.m4a,.aac,.flac,.ogg,.opus"
    ALLOWED_MODEL_EXT: str = ".safetensors,.ckpt,.pt,.pth,.bin,.onnx,.gguf"

    # ---- generation defaults ----------------------------------------------
    DEFAULT_IMAGE_WIDTH: int = 1024
    DEFAULT_IMAGE_HEIGHT: int = 1024
    DEFAULT_VIDEO_FPS: int = 24
    DEFAULT_VIDEO_DURATION: float = 5.0
    # Downloading any model bigger than this requires explicit user approval.
    MODEL_APPROVAL_THRESHOLD_GB: float = 5.0

    # ---- hardware ----------------------------------------------------------
    # Force-disable GPU detection (useful for CPU-only testing).
    DISABLE_GPU: bool = False
    # Minimum VRAM (GB) for a model to be considered runnable.
    MIN_FREE_VRAM_GB: float = 0.5

    # ---- external providers (ALL disabled by default) ----------------------
    # Nothing is ever billed or called unless you enable it and add a key.
    ENABLE_EXTERNAL_PROVIDERS: bool = False
    OPENAI_API_KEY: str = ""
    REPLICATE_API_TOKEN: str = ""
    ELEVENLABS_API_KEY: str = ""
    AZURE_SPEECH_KEY: str = ""
    GOOGLE_TTS_KEY: str = ""
    STABILITY_API_KEY: str = ""
    RUNWAY_API_KEY: str = ""

    # ---- safety ------------------------------------------------------------
    SAFETY_ENABLED: bool = True
    # Voice cloning is refused unless an explicit consent attestation is stored.
    REQUIRE_VOICE_CONSENT: bool = True
    # Watermark/provenance metadata written into every generated asset.
    PROVENANCE_ENABLED: bool = True
    WATERMARK_TEXT: str = ""

    # ---- branding (safe to expose publicly) --------------------------------
    BRANDING_PRODUCT_NAME: str = "AI Creative Studio"
    BRANDING_COMPANY_NAME: str = "Independent Studio"
    BRANDING_PRIMARY_COLOR: str = "#6366f1"
    BRANDING_SECONDARY_COLOR: str = "#a855f7"
    BRANDING_THEME: str = "dark"          # dark | light | system
    BRANDING_LOGO_URL: str = ""
    BRANDING_FAVICON_URL: str = ""
    BRANDING_FOOTER: str = "Built with AI Creative Studio"

    # ---- first run ---------------------------------------------------------
    # Creates the initial admin account if no users exist.
    BOOTSTRAP_ADMIN_EMAIL: str = "admin@studio.local"
    BOOTSTRAP_ADMIN_PASSWORD: str = "admin12345"
    # Opens signup to anyone when True (set False for private instances).
    ALLOW_PUBLIC_SIGNUP: bool = True

    @property
    def cors_origins_list(self) -> List[str]:
        raw = (self.CORS_ORIGINS or "*").strip()
        if raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def storage_path(self) -> Path:
        p = Path(self.STORAGE_DIR)
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

# Keep directories alive for the lifetime of the process.
for _d in (
    Path(settings.STORAGE_DIR),
    Path(ROOT_DIR) / "storage",
    Path(ROOT_DIR) / "storage" / "models",
    Path(ROOT_DIR) / "storage" / "exports",
    Path(ROOT_DIR) / "storage" / "tmp",
    Path(ROOT_DIR) / "logs",
):
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except Exception:  # pragma: no cover - read-only FS in exotic envs
        pass

os.environ.setdefault("TZ", "UTC")
