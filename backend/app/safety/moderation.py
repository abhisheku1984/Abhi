"""Content safety and rights safeguards (§28).

Configurable, transparent and auditable: every decision returns the reason so
the UI can explain what happened instead of silently refusing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("safety")

# Base policy. Never used to censor legitimate creative work — it targets the
# categories named in §28 and is configurable per deployment.
DEFAULT_BLOCKED_PATTERNS = [
    (r"\b(child|minor|underage|teen)\b.{0,40}\b(nude|naked|sexual|explicit|porn)\b", "sexual content involving minors"),
    (r"\b(nude|naked|nsfw)\b.{0,30}\b(child|kid|minor|school)\b", "sexual content involving minors"),
    (r"\bhow to (make|build|synthesize)\b.{0,30}\b(bomb|explosive|nerve agent|sarin|ricin)\b", "dangerous content"),
    (r"\b(deepfake|impersonat\w*)\b.{0,40}\b(ceo|president|prime minister|politician)\b.{0,30}\b(fraud|scam|invest)\b",
     "fraudulent impersonation"),
]

IMPERSONATION_HINTS = re.compile(
    r"\b(voice|likeness|face|image)\s+of\s+[A-Z][a-z]+\s+[A-Z][a-z]+\b", re.IGNORECASE
)
CLONE_HINTS = re.compile(r"\b(clone|copy|mimic|replicate)\b.{0,30}\b(voice|speech)\b", re.IGNORECASE)


@dataclass
class SafetyDecision:
    allowed: bool = True
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    category: Optional[str] = None

    def block(self, reason: str, category: Optional[str] = None) -> None:
        self.allowed = False
        self.reasons.append(reason)
        self.category = category or self.category

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def _extra_blocklist() -> list[str]:
    return [t.strip().lower() for t in settings.SAFETY_BLOCKLIST.split(",") if t.strip()]


def check_text(prompt: str, *, negative_prompt: str = "") -> SafetyDecision:
    decision = SafetyDecision()
    if not settings.SAFETY_ENABLED:
        return decision
    text = f"{prompt} {negative_prompt}".lower()

    for pattern, category in DEFAULT_BLOCKED_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            decision.block(f"Blocked by policy: {category}.", category)
            log.warning("safety_blocked_text", category=category)

    for term in _extra_blocklist():
        if term and term in text:
            decision.block(f"Blocked term from your blocklist: '{term}'.", "blocklist")

    if CLONE_HINTS.search(prompt) and IMPERSONATION_HINTS.search(prompt):
        decision.block(
            "Cloning a named person's voice requires documented authorisation.",
            "unauthorized_voice_cloning",
        )
    elif CLONE_HINTS.search(prompt):
        decision.warn("Voice cloning is consent-gated: complete the rights attestation on the voice profile.")

    return decision


def check_voice_clone(voice: Optional[dict[str, Any]]) -> SafetyDecision:
    decision = SafetyDecision()
    if not voice or not voice.get("is_cloned"):
        return decision
    consent = voice.get("consent") or {}
    required = ("owner_attestation", "rights_holder", "authorised_by", "purpose")
    missing = [f for f in required if not consent.get(f)]
    if missing and settings.REQUIRE_VOICE_CLONE_ATTESTATION:
        decision.block(
            "Voice cloning blocked: consent attestation incomplete (missing " + ", ".join(missing) + ").",
            "unauthorized_voice_cloning",
        )
    return decision


def check_upload(filename: str, mime: str, size_bytes: int) -> SafetyDecision:
    decision = SafetyDecision()
    allowed = settings.allowed_upload_mime
    if allowed and mime and mime not in allowed:
        decision.block(f"File type '{mime}' is not allowed.", "file_type")
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if size_bytes > max_bytes:
        decision.block(f"File is larger than the {settings.MAX_UPLOAD_MB} MB limit.", "file_size")
    if filename and (".." in filename or "/" in filename or "\\" in filename):
        decision.block("Invalid file name.", "path_traversal")
    return decision


def check_generation(prompt: str, *, negative_prompt: str = "", voice: Optional[dict] = None,
                     references: Sequence[Any] = ()) -> SafetyDecision:
    decision = check_text(prompt, negative_prompt=negative_prompt)
    clone = check_voice_clone(voice)
    if not clone.allowed:
        decision.allowed = False
        decision.reasons.extend(clone.reasons)
        decision.category = clone.category
    decision.warnings.extend(clone.warnings)
    return decision
