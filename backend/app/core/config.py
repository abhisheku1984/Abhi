from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_HERE = Path(__file__).resolve()
BACKEND_ROOT = _HERE.parents[2]
REPO_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    """All configuration comes from environment variables / .env.

    Secrets are never hard-coded and are never exposed to the frontend: only
    values explicitly allow-listed are returned by /api/v1/settings/public.
    """

    model_config = SettingsConfigDict(
        env_file=(str(BACKEND_ROOT / ".env"), str(REPO_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- application ----
    APP_NAME: str = "AI Creative Studio"
    APP_ENV: str = Field(default="development", pattern="^(development|staging|production|test)$")
    API_PREFIX: str = "/api"
    API_VERSION: str = "v1"
    DEBUG: bool = False

    # ---- database: SQLite by default, PostgreSQL via DATABASE_URL ----
    DATABASE_URL: str = f"sqlite:///{(BACKEND_ROOT / 'data' / 'studio.db').as_posix()}"
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

    # ---- redis (optional; DB-backed queue used when absent) ----
    REDIS_URL: Optional[str] = None
    QUEUE_BACKEND: str = Field(default="auto", pattern="^(auto|database|redis)$")

    # ---- storage ----
    STORAGE_BACKEND: str = Field(default="local", pattern="^(local|s3)$")
    STORAGE_LOCAL_ROOT: str = str(BACKEND_ROOT / "storage")
    S3_ENDPOINT_URL: Optional[str] = None
    S3_BUCKET: Optional[str] = None
    S3_REGION: str = "us-east-1"
    S3_ACCESS_KEY_ID: Optional[str] = None
    S3_SECRET_ACCESS_KEY: Optional[str] = None

    # ---- security ----
    SECRET_KEY: str = "dev-only-insecure-secret-change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 14
    PASSWORD_MIN_LENGTH: int = 8
    RATE_LIMIT_PER_MINUTE: int = 600
    MAX_UPLOAD_MB: int = 512
    ALLOWED_UPLOAD_MIME: str = (
        "image/png,image/jpeg,image/webp,image/gif,image/bmp,image/tiff,"
        "video/mp4,video/webm,video/quicktime,video/x-msvideo,"
        "audio/mpeg,audio/wav,audio/x-wav,audio/ogg,audio/flac,audio/mp4,audio/aac"
    )
    CORS_ORIGINS: str = "*"

    # ---- jobs / workers ----
    WORKER_CONCURRENCY: int = 2
    WORKER_POLL_SECONDS: float = 0.4
    JOB_DEFAULT_TIMEOUT_SECONDS: int = 3600
    JOB_MAX_ATTEMPTS: int = 3
    ENABLE_BACKGROUND_WORKER: bool = True

    # ---- ffmpeg ----
    FFMPEG_BINARY: Optional[str] = None
    FFPROBE_BINARY: Optional[str] = None

    # ---- gpu ----
    GPU_ENABLED: bool = True
    GPU_REMOTE_AGENT_URL: Optional[str] = None

    # ---- safety / rights ----
    SAFETY_ENABLED: bool = True
    SAFETY_BLOCKLIST: str = ""
    REQUIRE_VOICE_CLONE_ATTESTATION: bool = True
    WATERMARK_ENABLED: bool = False
    WATERMARK_TEXT: str = "AI Creative Studio"

    # ---- engines ----
    ENGINE_ALLOWLIST: str = ""
    AUTO_DOWNLOAD_MODELS: bool = False  # §32/§43 — never auto-download weights

    # ---- branding (overridable at runtime via /settings/branding) ----
    BRAND_PRODUCT_NAME: str = "AI Creative Studio"
    BRAND_COMPANY_NAME: str = "AI Creative Studio"
    BRAND_PRIMARY_COLOR: str = "#7C5CFF"
    BRAND_SECONDARY_COLOR: str = "#22D3EE"
    BRAND_THEME: str = "dark"
    BRAND_FOOTER: str = "AI Creative Studio — local-first generative media platform"

    # ---- first-run bootstrap ----
    BOOTSTRAP_ADMIN_EMAIL: str = "admin@studio.ai"
    BOOTSTRAP_ADMIN_PASSWORD: str = "Admin@12345"
    BOOTSTRAP_ADMIN_NAME: str = "Studio Admin"

    @property
    def database_url(self) -> str:
        url = self.DATABASE_URL
        if url.startswith("sqlite"):
            return url
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def storage_root(self) -> Path:
        p = Path(self.STORAGE_LOCAL_ROOT)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def data_root(self) -> Path:
        p = BACKEND_ROOT / "data"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def allowed_upload_mime(self) -> list[str]:
        return [m.strip() for m in self.ALLOWED_UPLOAD_MIME.split(",") if m.strip()]

    @property
    def engine_allowlist(self) -> list[str]:
        return [e.strip() for e in self.ENGINE_ALLOWLIST.split(",") if e.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
