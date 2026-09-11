"""Replaceable model-adapter contract (§39).

Every image / video / avatar / voice / audio / lip-sync model in the platform
implements `BaseModelAdapter`. Nothing above this layer knows which model is
running, so new models plug in without touching the UI or the API.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Optional

from app.core.config import settings
from app.core.errors import ModelNotInstalledError, StudioError
from app.core.logging import get_logger

log = get_logger("engines.base")

FAMILIES = ("image", "video", "voice", "audio", "avatar", "lipsync", "upscale")


# --------------------------------------------------------------------------- #
# Capability declaration — drives the auto-generated UI, never hard-coded (§42.10)
# --------------------------------------------------------------------------- #

@dataclass
class Capabilities:
    family: str
    modes: list[str] = field(default_factory=list)
    max_resolution: str = "1024p"
    supports_negative_prompt: bool = False
    supports_seed: bool = True
    supports_reference_images: bool = False
    supports_reference_video: bool = False
    supports_multi_reference_weights: bool = False
    supports_mask: bool = False
    supports_duration: bool = False
    supports_camera: bool = False
    supports_lens: bool = False
    supports_motion: bool = False
    supports_lighting: bool = False
    supports_character: bool = False
    supports_voice: bool = False
    supports_interpolation: bool = False
    supports_upscale: bool = False
    supports_lipsync: bool = False
    supports_style: bool = True
    max_duration_sec: float = 0.0
    max_references: int = 0
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationResult:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.ok = False
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)


@dataclass
class Estimate:
    seconds: float = 5.0
    vram_mb: int = 0
    credits: float = 1.0
    notes: str = ""


@dataclass
class Reference:
    asset_id: Optional[str] = None
    path: str = ""
    kind: str = "image"  # image|video|audio
    weight: float = 1.0
    role: str = "reference"  # reference|character|style|first_frame|last_frame|mask|voice


@dataclass
class GenerateRequest:
    mode: str
    prompt: str = ""
    negative_prompt: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    references: list[Reference] = field(default_factory=list)
    character: Optional[dict[str, Any]] = None
    voice: Optional[dict[str, Any]] = None
    model_id: Optional[str] = None
    owner_id: str = ""
    project_id: Optional[str] = None
    output_format: str = "png"

    def get(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)

    @property
    def seed(self) -> int:
        raw = self.params.get("seed")
        if raw in (None, "", 0):
            return int(time.time() * 1000) % (2**31)
        try:
            return int(raw)
        except Exception:
            return 0

    def refs(self, role: Optional[str] = None) -> list[Reference]:
        if role is None:
            return self.references
        return [r for r in self.references if r.role == role]


@dataclass
class Artifact:
    path: str
    kind: str  # image|video|audio|avatar
    mime: str = "application/octet-stream"
    meta: dict[str, Any] = field(default_factory=dict)
    name: str = ""

    def exists(self) -> bool:
        return Path(self.path).exists()


class JobContext:
    """Everything an adapter needs while running inside a worker."""

    def __init__(
        self,
        *,
        job_id: str,
        owner_id: str,
        project_id: Optional[str] = None,
        work_dir: str | Path,
        progress_cb: Optional[Callable[[float, str], None]] = None,
        cancel_cb: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.job_id = job_id
        self.owner_id = owner_id
        self.project_id = project_id
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._progress_cb = progress_cb
        self._cancel_cb = cancel_cb
        self.started = time.time()

    def progress(self, value: float, stage: str = "") -> None:
        value = max(0.0, min(100.0, float(value)))
        if self._progress_cb:
            try:
                self._progress_cb(value, stage)
            except Exception as exc:  # never let telemetry break a generation
                log.debug("progress_callback_failed", error=str(exc))

    @property
    def cancelled(self) -> bool:
        if self._cancel_cb:
            try:
                return bool(self._cancel_cb())
            except Exception:
                return False
        return False

    def check_cancelled(self) -> None:
        if self.cancelled:
            from app.core.errors import JobCancelled

            raise JobCancelled(f"Job {self.job_id} cancelled")

    def path(self, name: str) -> str:
        p = self.work_dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        return str(p)


# --------------------------------------------------------------------------- #
# Adapter base
# --------------------------------------------------------------------------- #

class BaseModelAdapter(ABC):
    """Common interface for every model in the platform."""

    id: str = "base"
    display_name: str = "Base adapter"
    version: str = "0.0.0"
    family: str = "image"
    provider: str = "local"
    license: str = "unknown"
    size_mb: int = 0
    vram_mb: int = 0
    speed: str = "medium"
    is_local: bool = True
    requires_extra: str = ""
    download_url: str = ""
    notes: str = ""
    capabilities: Capabilities = Capabilities(family="image")
    #: True when the adapter genuinely produces the requested media without
    #: external weights/credentials. False → the UI explains what is missing.
    ready_out_of_the_box: bool = True

    # ---- lifecycle ------------------------------------------------------- #

    def info(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "version": self.version,
            "family": self.family,
            "provider": self.provider,
            "license": self.license,
            "size_mb": self.size_mb,
            "vram_mb": self.vram_mb,
            "speed": self.speed,
            "is_local": self.is_local,
            "requires_extra": self.requires_extra,
            "download_url": self.download_url,
            "notes": self.notes,
            "capabilities": self.capabilities.to_dict(),
            "status": self.status().get("status"),
            "status_reason": self.status().get("reason", ""),
            "modes": self.capabilities.modes,
        }

    def status(self) -> dict[str, Any]:
        """One of: installed | available | not_installed | disabled | error."""
        return {"status": "installed" if self.ready_out_of_the_box else "not_installed", "reason": ""}

    def ensure_ready(self) -> None:
        st = self.status()
        if st.get("status") in ("not_installed", "disabled", "error"):
            raise ModelNotInstalledError(
                self.display_name,
                install_hint=st.get("reason")
                or f"Install it from Model Manager, or pick a model that is already available.",
            )

    # ---- contract -------------------------------------------------------- #

    @abstractmethod
    def validate(self, request: GenerateRequest) -> ValidationResult:
        ...

    @abstractmethod
    def estimate(self, request: GenerateRequest) -> Estimate:
        ...

    @abstractmethod
    def generate(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        ...

    def cancel(self, job_id: str) -> bool:  # pragma: no cover - override when supported
        return False

    def param_schema(self) -> dict[str, Any]:
        """JSON-schema fragment describing this model's extra parameters."""
        return {"type": "object", "properties": {}, "additionalProperties": True}

    def install(self) -> dict[str, Any]:  # pragma: no cover - optional
        raise StudioError(
            f"{self.display_name} does not support automatic installation.",
            suggested_action="Install it manually, then re-run Model Manager → Test.",
        )

    def uninstall(self) -> dict[str, Any]:  # pragma: no cover - optional
        raise StudioError(f"{self.display_name} does not support automatic removal.")

    def test(self) -> dict[str, Any]:
        st = self.status()
        return {"id": self.id, "ok": st.get("status") in ("installed", "available"), **st}

    # ---- helpers --------------------------------------------------------- #

    def _require_mode(self, request: GenerateRequest) -> None:
        if request.mode not in self.capabilities.modes:
            raise StudioError(
                f"{self.display_name} does not support '{request.mode}'.",
                suggested_action=f"Supported modes: {', '.join(self.capabilities.modes)}.",
            )

    def _validate_prompt(self, request: GenerateRequest, result: ValidationResult) -> None:
        if not (request.prompt or "").strip() and not request.references:
            result.add_error("Enter a prompt or attach a reference image.")
