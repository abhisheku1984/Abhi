"""Model adapter contract (spec §36).

Every AI capability in this platform is reached through a BaseModelAdapter.
The frontend/engines never import torch, diffusers or a vendor SDK, and nothing
in the app hard-codes a model. Plugging in a future model = adding one class.

Each adapter implements:  info() validate() estimate() status() generate() cancel()

Status codes are honest and user facing:
    available                     -> can execute now
    dependency_missing            -> pip install <requires>
    model_not_available           -> weights not installed (needs approval)
    hardware_requirement_not_met  -> GPU/VRAM/RAM insufficient
    external_provider_required    -> needs a paid API key + explicit opt-in
"""
from __future__ import annotations

import abc
import importlib.util
import inspect
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from app.core.config import settings
from app.core.errors import ErrorCode, StudioError
from app.core.hardware import can_run_model, environment_snapshot

ProgressCallback = Callable[[float, str], None]


# ---------------------------------------------------------------------------
# Request / result types
# ---------------------------------------------------------------------------
@dataclass
class Reference:
    """A reference file resolved to a local path before the adapter runs."""

    path: str
    kind: str = "image"          # image | video | audio
    role: str = "reference"      # reference | init | mask | style | character | audio
    weight: float = 1.0
    meta: dict = field(default_factory=dict)


@dataclass
class GenerationRequest:
    prompt: str = ""
    negative_prompt: str = ""
    width: int = 1024
    height: int = 1024
    steps: int = 30
    guidance: float = 7.5
    strength: float = 0.75       # img2img / edit strength
    seed: int | None = None
    num_images: int = 1
    duration: float = 5.0        # seconds (video/audio)
    fps: int = 24
    aspect_ratio: str = "1:1"
    output_dir: str = ""
    params: dict = field(default_factory=dict)
    references: list[Reference] = field(default_factory=list)
    character_context: dict = field(default_factory=dict)
    structured_prompt: dict = field(default_factory=dict)
    cancel_event: threading.Event | None = None

    def param(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)

    def check_cancelled(self) -> None:
        if self.cancel_event and self.cancel_event.is_set():
            raise StudioError("Generation cancelled", code=ErrorCode.CANCELLED, status_code=499)


@dataclass
class GeneratedFile:
    path: str
    kind: str = "image"          # image | video | audio
    mime: str = ""
    meta: dict = field(default_factory=dict)
    is_primary: bool = True


@dataclass
class GenerationResult:
    files: list[GeneratedFile] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    seed: int | None = None
    model_id: str = ""

    @property
    def primary(self) -> GeneratedFile | None:
        for f in self.files:
            if f.is_primary:
                return f
        return self.files[0] if self.files else None


@dataclass
class Estimate:
    eta_seconds: float = 0.0
    vram_gb: float = 0.0
    ram_gb: float = 0.0
    cost_usd: float = 0.0
    device: str = "cpu"
    notes: str = ""


@dataclass
class AdapterStatus:
    key: str
    name: str
    available: bool
    code: str = ErrorCode.MODEL_NOT_AVAILABLE
    message: str = ""
    remediation: str = ""
    device: str = "cpu"
    requires: list[str] = field(default_factory=list)
    missing_requirements: list[str] = field(default_factory=list)
    hardware: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "available": self.available,
            "code": self.code,
            "message": self.message,
            "remediation": self.remediation,
            "device": self.device,
            "requires": self.requires,
            "missing_requirements": self.missing_requirements,
            "hardware": self.hardware,
        }


# ---------------------------------------------------------------------------
# Base adapter
# ---------------------------------------------------------------------------
class BaseModelAdapter(abc.ABC):
    """Abstract adapter. Subclasses describe themselves declaratively."""

    # --- identity -----------------------------------------------------------
    key: str = "base"
    name: str = "Base adapter"
    capability: str = ""
    capabilities: tuple[str, ...] = ()
    version: str = "1.0"
    license: str = ""
    source_url: str = ""
    description: str = ""
    provider: str = ""
    local: bool = True

    # --- resource profile ---------------------------------------------------
    size_gb: float = 0.0
    vram_gb: float = 0.0
    ram_gb: float = 0.0
    requires: tuple[str, ...] = ()          # python distributions
    requires_binaries: tuple[str, ...] = ()  # external executables
    weights_path: str = ""                   # where weights would live
    quality: str = "draft"                   # draft | standard | high | reference
    requires_approval: bool = False          # >5GB downloads

    def __init__(self) -> None:
        self._cancel_event = threading.Event()

    # --- helpers ------------------------------------------------------------
    def missing_packages(self) -> list[str]:
        missing = []
        for pkg in self.requires:
            module = pkg.split("[")[0].split(">")[0].split("=")[0].split(".")[0].strip()
            if importlib.util.find_spec(module) is None:
                missing.append(pkg)
        return missing

    def missing_binaries(self) -> list[str]:
        import shutil

        return [b for b in self.requires_binaries if shutil.which(b) is None]

    def weights_present(self) -> bool:
        if not self.weights_path:
            return True
        return Path(self.weights_path).exists()

    def hardware_gate(self) -> tuple[bool, str]:
        return can_run_model(self.vram_gb, self.ram_gb)

    def provider_enabled(self) -> bool:
        """External providers are opt-in only (spec §55)."""
        if self.local:
            return True
        if not settings.ENABLE_EXTERNAL_PROVIDERS:
            return False
        return bool(self._api_key())

    def _api_key(self) -> str:
        mapping = {
            "openai": settings.OPENAI_API_KEY,
            "replicate": settings.REPLICATE_API_TOKEN,
            "elevenlabs": settings.ELEVENLABS_API_KEY,
            "azure": settings.AZURE_SPEECH_KEY,
            "google": settings.GOOGLE_TTS_KEY,
            "stability": settings.STABILITY_API_KEY,
            "runway": settings.RUNWAY_API_KEY,
        }
        return mapping.get(self.provider.lower(), "")

    def device(self) -> str:
        env = environment_snapshot()
        if env["gpu"]["available"] and self.vram_gb > 0:
            return "cuda:0"
        return "cpu"

    # --- contract -----------------------------------------------------------
    def info(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "capability": self.capability,
            "capabilities": list(self.capabilities),
            "version": self.version,
            "license": self.license,
            "source_url": self.source_url,
            "description": self.description,
            "provider": self.provider,
            "local": self.local,
            "size_gb": self.size_gb,
            "vram_gb": self.vram_gb,
            "ram_gb": self.ram_gb,
            "requires": list(self.requires),
            "requires_binaries": list(self.requires_binaries),
            "quality": self.quality,
            "requires_approval": self.requires_approval or self.size_gb >= settings.MODEL_APPROVAL_THRESHOLD_GB,
            "weights_path": self.weights_path,
        }

    def status(self, refresh: bool = False) -> AdapterStatus:
        """Single source of truth for 'can this run right now, and if not, why'."""
        if refresh:
            from app.core.hardware import refresh as _refresh

            _refresh()

        requires = list(self.requires)
        missing = self.missing_packages()
        missing_bins = self.missing_binaries()
        hardware = environment_snapshot()

        if missing:
            return AdapterStatus(
                self.key, self.name, False, ErrorCode.DEPENDENCY_MISSING,
                f"Missing Python package(s): {', '.join(missing)}.",
                f"pip install {' '.join(missing)}",
                device="cpu", requires=requires, missing_requirements=missing, hardware=hardware,
            )
        if missing_bins:
            return AdapterStatus(
                self.key, self.name, False, ErrorCode.DEPENDENCY_MISSING,
                f"Missing external executable(s): {', '.join(missing_bins)}.",
                f"Install {', '.join(missing_bins)} and ensure it is on PATH.",
                device="cpu", requires=requires, missing_requirements=missing_bins, hardware=hardware,
            )
        if not self.provider_enabled():
            return AdapterStatus(
                self.key, self.name, False, ErrorCode.EXTERNAL_PROVIDER_REQUIRED,
                f"Adapter '{self.name}' uses the '{self.provider or 'external'}' provider, which is disabled.",
                "Set ENABLE_EXTERNAL_PROVIDERS=true and add the provider API key in Settings → Providers.",
                device="cpu", requires=requires, missing_requirements=[], hardware=hardware,
            )
        ok, reason = self.hardware_gate()
        if not ok:
            return AdapterStatus(
                self.key, self.name, False, ErrorCode.HARDWARE_REQUIREMENT_NOT_MET, reason,
                "Use a machine with a compatible GPU, or activate a CPU-capable adapter, or enable a cloud provider.",
                device=self.device(), requires=requires, missing_requirements=[], hardware=hardware,
            )
        if not self.weights_present() and self.weights_path:
            return AdapterStatus(
                self.key, self.name, False, ErrorCode.MODEL_NOT_AVAILABLE,
                "Model weights are not installed on this machine.",
                f"Install from {self.source_url} or run /api/models/{self.key}/install (weights are never downloaded automatically).",
                device=self.device(), requires=requires, missing_requirements=[], hardware=hardware,
            )
        return AdapterStatus(
            self.key, self.name, True, "", "Available", device=self.device(),
            requires=requires, missing_requirements=[], hardware=hardware,
        )

    def ensure_ready(self) -> AdapterStatus:
        st = self.status()
        if not st.available:
            raise StudioError(
                f"{self.name}: {st.message}",
                code=st.code,
                status_code=503,
                details={"adapter": self.key, "hardware": st.hardware, "missing": st.missing_requirements},
                remediation=st.remediation,
            )
        return st

    def validate(self, req: GenerationRequest) -> None:
        """Raise StudioError on an unusable request. Default: prompt required."""
        if not (req.prompt or "").strip() and not req.references and not req.param("allow_empty_prompt"):
            raise StudioError("A prompt (or a reference file) is required.", code=ErrorCode.INVALID_REQUEST)
        if req.width < 64 or req.height < 64:
            raise StudioError("Width and height must be at least 64 px.", code=ErrorCode.INVALID_REQUEST)

    def estimate(self, req: GenerationRequest) -> Estimate:
        return Estimate(eta_seconds=5.0, vram_gb=self.vram_gb, ram_gb=self.ram_gb, device=self.device())

    def cancel(self) -> bool:
        self._cancel_event.set()
        return True

    @property
    def cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def reset_cancel(self) -> None:
        self._cancel_event = threading.Event()

    @abc.abstractmethod
    def generate(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        """Produce real output files. Must raise StudioError on failure."""


# ---------------------------------------------------------------------------
# Typed sub-interfaces (spec §36)
# ---------------------------------------------------------------------------
class ImageModelAdapter(BaseModelAdapter):
    capability = "text_to_image"


class VideoModelAdapter(BaseModelAdapter):
    capability = "text_to_video"


class AvatarModelAdapter(BaseModelAdapter):
    capability = "avatar"


class VoiceModelAdapter(BaseModelAdapter):
    capability = "tts"


class AudioModelAdapter(BaseModelAdapter):
    capability = "music"


class LLMAdapter(BaseModelAdapter):
    capability = "llm"

    def complete(self, system: str, user: str, max_tokens: int = 1200, temperature: float = 0.7) -> str:
        raise NotImplementedError


def timed(func):
    """Decorator adding wall-clock timing to generate() results."""

    def wrapper(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        started = time.time()
        result = func(self, req, progress)
        result.meta.setdefault("duration_seconds", round(time.time() - started, 3))
        result.model_id = result.model_id or self.key
        return result

    wrapper.__wrapped__ = func  # type: ignore[attr-defined]
    return wrapper


def adapter_classes(module) -> Iterable[type[BaseModelAdapter]]:
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if issubclass(obj, BaseModelAdapter) and obj is not BaseModelAdapter and not inspect.isabstract(obj):
            yield obj
