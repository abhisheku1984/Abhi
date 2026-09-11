"""
Configuration for Abhi Studio.

Zero-dependency environment handling: reads a .env file (if present) then the
process environment. Everything has a safe default so the application always
boots — missing credentials downgrade a capability to `not_configured`
instead of crashing the service.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE, # comments, optional quotes)."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # real environment always wins over the file
        os.environ.setdefault(key, value)


def _env(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _env_list(key: str) -> list[str]:
    return [part.strip() for part in _env(key).split(",") if part.strip()]


def _resolve_data_dir(raw: str) -> Path:
    """Absolute stays absolute; relative is anchored to the repository root."""
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def detect_ffmpeg() -> str | None:
    """Prefer an explicit binary, then PATH, then the bundled static build."""
    explicit = _env("ABHI_FFMPEG_BIN")
    if explicit and Path(explicit).is_file():
        return explicit
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:  # bundled static build shipped with the imageio-ffmpeg wheel
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def ffmpeg_version(binary: str | None) -> str | None:
    if not binary:
        return None
    try:
        out = subprocess.run(
            [binary, "-version"], capture_output=True, text=True, timeout=10
        )
        first = (out.stdout or "").splitlines()[0]
        return first.replace("ffmpeg version ", "").split(" ")[0]
    except Exception:
        return None


def detect_gpu() -> dict:
    """Detect a local CUDA GPU. Absence is normal and handled gracefully."""
    if not shutil.which("nvidia-smi"):
        return {"available": False, "reason": "nvidia-smi not found", "devices": []}
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        devices = []
        for line in (out.stdout or "").strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                devices.append(
                    {"name": parts[0], "vram_mb": int(float(parts[1])), "driver": parts[2]}
                )
        return {"available": bool(devices), "reason": None, "devices": devices}
    except Exception as exc:  # pragma: no cover - host specific
        return {"available": False, "reason": str(exc), "devices": []}


@dataclass(frozen=True)
class Settings:
    host: str = field(default_factory=lambda: _env("ABHI_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _env_int("ABHI_PORT", 8000))
    # Resolved against the repository root (not the process CWD) so the data
    # directory is stable no matter where the server is launched from.
    data_dir: Path = field(
        default_factory=lambda: _resolve_data_dir(_env("ABHI_DATA_DIR", "./data"))
    )
    log_level: str = field(default_factory=lambda: _env("ABHI_LOG_LEVEL", "INFO").upper())
    cors_origins: list[str] = field(default_factory=lambda: _env_list("ABHI_CORS_ORIGINS"))

    admin_user: str = field(default_factory=lambda: _env("ABHI_ADMIN_USER", "admin"))
    admin_password: str = field(default_factory=lambda: _env("ABHI_ADMIN_PASSWORD", "abhi-admin"))
    session_ttl_hours: int = field(default_factory=lambda: _env_int("ABHI_SESSION_TTL_HOURS", 720))

    image_provider: str = field(default_factory=lambda: _env("ABHI_IMAGE_PROVIDER", "auto"))
    video_provider: str = field(default_factory=lambda: _env("ABHI_VIDEO_PROVIDER", "auto"))
    tts_provider: str = field(default_factory=lambda: _env("ABHI_TTS_PROVIDER", "auto"))
    llm_provider: str = field(default_factory=lambda: _env("ABHI_LLM_PROVIDER", "auto"))
    lipsync_provider: str = field(default_factory=lambda: _env("ABHI_LIPSYNC_PROVIDER", "auto"))

    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_base_url: str = field(
        default_factory=lambda: _env("OPENAI_BASE_URL", "https://api.openai.com/v1")
    )
    openai_image_model: str = field(
        default_factory=lambda: _env("OPENAI_IMAGE_MODEL", "gpt-image-1")
    )
    openai_tts_model: str = field(
        default_factory=lambda: _env("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
    )
    openai_llm_model: str = field(default_factory=lambda: _env("OPENAI_LLM_MODEL", "gpt-4o-mini"))

    stability_api_key: str = field(default_factory=lambda: _env("STABILITY_API_KEY"))
    stability_base_url: str = field(
        default_factory=lambda: _env("STABILITY_BASE_URL", "https://api.stability.ai")
    )

    replicate_api_token: str = field(default_factory=lambda: _env("REPLICATE_API_TOKEN"))
    replicate_base_url: str = field(
        default_factory=lambda: _env("REPLICATE_BASE_URL", "https://api.replicate.com/v1")
    )

    runway_api_key: str = field(default_factory=lambda: _env("RUNWAY_API_KEY"))
    runway_base_url: str = field(
        default_factory=lambda: _env("RUNWAY_BASE_URL", "https://api.dev.runwayml.com/v1")
    )

    elevenlabs_api_key: str = field(default_factory=lambda: _env("ELEVENLABS_API_KEY"))
    elevenlabs_base_url: str = field(
        default_factory=lambda: _env("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io/v1")
    )
    elevenlabs_voice_id: str = field(
        default_factory=lambda: _env("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
    )

    anthropic_api_key: str = field(default_factory=lambda: _env("ANTHROPIC_API_KEY"))

    comfyui_url: str = field(default_factory=lambda: _env("ABHI_COMFYUI_URL"))
    comfyui_token: str = field(default_factory=lambda: _env("ABHI_COMFYUI_TOKEN"))
    a1111_url: str = field(default_factory=lambda: _env("ABHI_A1111_URL"))
    a1111_token: str = field(default_factory=lambda: _env("ABHI_A1111_TOKEN"))
    ollama_url: str = field(default_factory=lambda: _env("ABHI_OLLAMA_URL"))
    ollama_model: str = field(default_factory=lambda: _env("ABHI_OLLAMA_MODEL", "llama3.1"))
    wav2lip_dir: str = field(default_factory=lambda: _env("ABHI_WAV2LIP_DIR"))
    sadtalker_dir: str = field(default_factory=lambda: _env("ABHI_SADTALKER_DIR"))

    # ---- derived paths ----
    @property
    def db_path(self) -> Path:
        return self.data_dir / "abhi.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def thumb_dir(self) -> Path:
        return self.data_dir / "thumbs"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def static_dir(self) -> Path:
        return REPO_ROOT / "frontend" / "dist"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.media_dir, self.thumb_dir, self.tmp_dir):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_dotenv(REPO_ROOT / ".env")
    settings = Settings()
    settings.ensure_dirs()
    return settings


def reset_settings_cache() -> None:
    get_settings.cache_clear()
