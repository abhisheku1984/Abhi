"""Safety policy, consent gating and provenance metadata (spec §43).

These are real, enforced checks - not decorative banners.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.core.errors import ErrorCode, StudioError

# Categories that are refused outright.
POLICY: dict[str, tuple[str, ...]] = {
    "minor_sexual": (
        "child porn", "cp", "underage nude", "underage sex", "minor nude", "teen nude",
        "child sexual", "preteen", "loli", "shota",
    ),
    "sexual_explicit": (
        "hardcore porn", "pornography", "xxx video", "explicit sex", "penetration",
    ),
    "extreme_violence": (
        "gore", "torture", "dismember", "beheading", "snuff", "graphic murder",
        "how to kill", "make a bomb", "build a bomb",
    ),
    "illegal": (
        "how to make meth", "buy fentanyl", "child exploitation", "human trafficking",
        "counterfeit money", "hack a bank", "steal credit card",
    ),
    "hate": (
        "gas the jews", "kill all muslims", "white supremacy propaganda",
        "genocide of", "racial extermination",
    ),
    "self_harm": (
        "how to commit suicide", "best way to kill yourself", "self harm instructions",
    ),
}

# Public-figure impersonation: allowed only with an explicit rights flag.
IMPERSONATION_TERMS = (
    "president", "prime minister", "ceo of", "elon musk", "taylor swift",
    "narendra modi", "donald trump", "joe biden", "barack obama",
    "shah rukh khan", "tom cruise", "cristiano ronaldo",
)

# Soft guidance surfaced to the user (not a hard block).
ADVISORY_TERMS = ("trademark", "disney", "marvel", "nike", "apple logo", "harry potter")


@dataclass
class SafetyVerdict:
    allowed: bool
    blocked_categories: list[str] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "blocked_categories": self.blocked_categories,
            "advisories": self.advisories,
            "reasons": self.reasons,
            "score": round(self.score, 3),
        }


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())


def check_text(prompt: str, allow_impersonation: bool = False) -> SafetyVerdict:
    """Screen a prompt. Hard blocks are enforced; advisories are informational."""
    if not settings.SAFETY_ENABLED:
        return SafetyVerdict(True)

    low = _norm(prompt)
    collapsed = re.sub(r"\s+", " ", low)
    verdict = SafetyVerdict(True)

    for category, terms in POLICY.items():
        for term in terms:
            if f" {term} " in f" {collapsed} ":
                verdict.allowed = False
                if category not in verdict.blocked_categories:
                    verdict.blocked_categories.append(category)
                    verdict.reasons.append(f"Request refused: content matches policy '{category}'.")
                verdict.score = max(verdict.score, 1.0)

    # "minor"/"child" + sexual context
    if re.search(r"\b(child|children|kid|minor|underage|teen|infant)\b", collapsed) and re.search(
        r"\b(nude|naked|sexual|sex|erotic|lingerie|explicit)\b", collapsed
    ):
        verdict.allowed = False
        if "minor_sexual" not in verdict.blocked_categories:
            verdict.blocked_categories.append("minor_sexual")
            verdict.reasons.append("Request refused: sexual content involving minors is never generated.")

    if not allow_impersonation:
        for term in IMPERSONATION_TERMS:
            if term in collapsed and re.search(r"\b(voice|likeness|impersonat|deepfake|clone)\b", collapsed):
                verdict.allowed = False
                if "impersonation" not in verdict.blocked_categories:
                    verdict.blocked_categories.append("impersonation")
                    verdict.reasons.append(
                        "Request refused: impersonating a real, identifiable person requires documented rights."
                    )
                verdict.score = max(verdict.score, 0.9)

    for term in ADVISORY_TERMS:
        if term in collapsed:
            verdict.advisories.append(
                f"Prompt references '{term}'. Ensure you hold the rights to use this material."
            )
            verdict.score = max(verdict.score, 0.3)

    return verdict


def enforce(prompt: str, allow_impersonation: bool = False) -> SafetyVerdict:
    v = check_text(prompt, allow_impersonation)
    if not v.allowed:
        raise StudioError(
            v.reasons[0] if v.reasons else "Request refused by safety policy.",
            code=ErrorCode.SAFETY_BLOCKED,
            status_code=422,
            details=v.to_dict(),
            remediation="Rephrase the request so it does not describe prohibited content.",
        )
    return v


def require_voice_consent(profile_consent_at: datetime | None, request_attested: bool = False) -> None:
    if not settings.REQUIRE_VOICE_CONSENT:
        return
    if request_attested or profile_consent_at is not None:
        return
    raise StudioError(
        "Voice cloning requires a stored consent attestation for the voice owner.",
        code=ErrorCode.CONSENT_REQUIRED,
        status_code=403,
        remediation="Add a dated consent attestation to the voice profile before cloning. "
                    "Cloning a real person's voice without authorisation is prohibited.",
    )


def require_identity_rights(has_rights: bool, subject: str = "this person") -> None:
    if has_rights:
        return
    raise StudioError(
        f"Using the identity/likeness of {subject} requires documented rights.",
        code=ErrorCode.CONSENT_REQUIRED,
        status_code=403,
        remediation="Confirm you hold rights to this identity in the request (identity_rights=true).",
    )


def build_provenance(*, generator: str, adapter: str, prompt: str = "", seed: int | None = None,
                     user_id: str = "", project_id: str | None = None, parameters: dict | None = None,
                     parents: list[str] | None = None, content_sha256: str = "",
                     watermarked: bool = False, version: str = "1") -> dict[str, Any]:
    """Machine-readable provenance written into every asset (spec §43)."""
    return {
        "spec": "ai-creative-studio/provenance@1",
        "generator": generator,
        "adapter": adapter,
        "version": version,
        "created_at": datetime.now(timezone.utc).isoformat() + "Z",
        "user_id": user_id,
        "project_id": project_id,
        "prompt": prompt,
        "prompt_sha256": hashlib.sha256((prompt or "").encode("utf-8")).hexdigest(),
        "seed": seed,
        "parameters": parameters or {},
        "parent_assets": parents or [],
        "content_sha256": content_sha256,
        "watermarked": watermarked,
        "disclosure": (
            "AI-generated content. Procedurally rendered on CPU unless a generative "
            "model is recorded in 'adapter'."
        ),
    }
