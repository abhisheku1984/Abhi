"""
Provider abstraction.

Every generation capability (image, video, tts, llm, lipsync) resolves through a
provider chosen at runtime. The three provider classes are:

``local``   — real computation on this machine. Two flavours:
              * ``quality="real"``  deterministic, production-grade (FFmpeg render,
                Pillow edit, contact sheets) — no honesty caveat needed.
              * ``quality="demo"``  genuine algorithms standing in for a neural
                model (procedural art, formant speech, template story). These are
                ALWAYS labelled ``demo`` so the UI can never imply they are AI.
``remote``  — your own GPU box (ComfyUI, A1111, Ollama, Wav2Lip). ``quality="real"``.
``cloud``   — a paid API (OpenAI, Stability, Replicate, Runway, ElevenLabs).
              ``quality="real"``, requires credentials.

A provider that is not configured must raise ``ProviderNotConfigured``; it must
never silently return fabricated output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from ..config import Settings, get_settings

Capability = Literal["image", "image_edit", "video", "tts", "llm", "lipsync", "music"]
ProviderKind = Literal["local", "remote", "cloud"]
ProviderQuality = Literal["real", "demo"]

#: Capabilities a user can route to a provider. ``auto`` picks the best
#: available one, preferring a real provider over a demo engine.
SELECTABLE_CAPABILITIES: tuple[str, ...] = ("image", "video", "tts", "llm", "lipsync")

#: Capabilities that are always local, deterministic and non-generative
#: (image editing, FFmpeg rendering). They are never chosen by ``auto`` for a
#: generative request — they are invoked directly by the pipelines.
UTILITY_CAPABILITIES: tuple[str, ...] = ("image_edit", "image_util", "video_render", "audio_mix")

CAPABILITIES: tuple[str, ...] = SELECTABLE_CAPABILITIES + UTILITY_CAPABILITIES


class ProviderNotConfigured(RuntimeError):
    """Raised when a provider is selected but lacks credentials / a reachable host."""

    def __init__(self, provider: str, capability: str, remediation: str) -> None:
        self.provider = provider
        self.capability = capability
        self.remediation = remediation
        super().__init__(f"{provider} cannot serve '{capability}': {remediation}")


class ProviderError(RuntimeError):
    """Raised when a configured provider fails at run time."""


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    label: str
    capability: str
    kind: ProviderKind
    quality: ProviderQuality
    engine: str
    #: env var(s) that unlock this provider, for actionable errors
    requires: tuple[str, ...] = ()
    describes: str = ""
    docs_url: str = ""
    #: ``True`` when runnable right now
    available: bool = False
    #: why it is unavailable (populated for available=False)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "capability": self.capability,
            "kind": self.kind,
            "quality": self.quality,
            "engine": self.engine,
            "requires": list(self.requires),
            "describes": self.describes,
            "docs_url": self.docs_url,
            "available": self.available,
            "reason": self.reason,
        }


@dataclass
class Provider:
    info: ProviderInfo
    #: (params, ctx) -> dict result; must raise ProviderError on failure
    handler: Callable[[dict, dict], dict] | None = field(default=None, repr=False)

    @property
    def id(self) -> str:
        return self.info.id

    @property
    def capability(self) -> str:
        return self.info.capability

    @property
    def available(self) -> bool:
        return self.info.available

    def require_available(self) -> None:
        if not self.info.available:
            raise ProviderNotConfigured(
                self.info.label, self.info.capability, self.info.reason or "not configured"
            )

    def run(self, params: dict, ctx: dict | None = None) -> dict:
        if self.handler is None:
            raise ProviderNotConfigured(
                self.info.label, self.info.capability, "provider has no implementation"
            )
        self.require_available()
        try:
            return self.handler(params, ctx or {})
        except (ProviderNotConfigured, ProviderError):
            raise
        except Exception as exc:  # normalise unexpected failures
            raise ProviderError(f"{self.info.label}: {exc}") from exc


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------
_REGISTRY: dict[str, dict[str, Provider]] = {}
_DEFAULT: dict[str, str] = {}


def register(provider: Provider, *, default: bool = False) -> Provider:
    _REGISTRY.setdefault(provider.capability, {})[provider.id] = provider
    if default or provider.capability not in _DEFAULT:
        _DEFAULT[provider.capability] = provider.id
    return provider


def providers_for(capability: str) -> list[Provider]:
    return list(_REGISTRY.get(capability, {}).values())


def get_provider(capability: str, provider_id: str | None) -> Provider:
    options = _REGISTRY.get(capability, {})
    if provider_id and provider_id != "auto":
        provider = options.get(provider_id)
        if not provider:
            raise ProviderError(f"unknown {capability} provider '{provider_id}'")
        return provider
    # auto: prefer a real provider, then remote/cloud over local demo engines
    ranked = sorted(
        options.values(),
        key=lambda p: (
            0 if (p.available and p.info.quality == "real") else 1,
            0 if p.available else 1,
            0 if p.info.kind == "cloud" else 1,
        ),
    )
    if not ranked:
        raise ProviderError(f"no {capability} providers registered")
    return ranked[0]


def provider_status() -> dict[str, Any]:
    """Full capability matrix for the Model Manager screen."""
    out: dict[str, Any] = {}
    for capability in CAPABILITIES:
        options = _REGISTRY.get(capability, {})
        if not options:
            continue
        selected = get_provider(capability, None)
        out[capability] = {
            "selected": selected.id,
            "selected_label": selected.info.label,
            "usable": selected.available,
            "quality": selected.info.quality if selected.available else "unavailable",
            "mode": _mode_for(capability, selected),
            "selectable": capability in SELECTABLE_CAPABILITIES,
            "providers": [p.info.to_dict() for p in options.values()],
        }
    return out


def _mode_for(capability: str, provider: Provider) -> str:
    if not provider.available:
        return "not_configured"
    if provider.info.quality == "demo":
        return "demo"
    return "real"


def capability_summary() -> dict[str, str]:
    summary: dict[str, str] = {}
    for capability in CAPABILITIES:
        if not _REGISTRY.get(capability):
            continue  # capability with no registered provider is simply absent
        summary[capability] = _mode_for(capability, get_provider(capability, None))
    return summary


def configured_provider_ids() -> dict[str, str]:
    return {cap: get_provider(cap, None).id for cap in CAPABILITIES}


def _settings() -> Settings:
    return get_settings()
