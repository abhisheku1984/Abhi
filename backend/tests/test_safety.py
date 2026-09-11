"""Safety, rights and moderation (§28)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.safety import moderation
from app.core.config import settings


def test_blocked_prompt_is_refused() -> None:
    decision = moderation.check_text("nude child portrait in a bedroom")
    assert not decision.allowed
    assert decision.category == "sexual content involving minors"


def test_normal_prompt_is_allowed() -> None:
    decision = moderation.check_text("a cinematic shot of a tea stall in Hyderabad at dawn")
    assert decision.allowed


def test_blocklist_is_configurable() -> None:
    original = settings.SAFETY_BLOCKLIST
    try:
        settings.SAFETY_BLOCKLIST = "forbidden-widget"
        decision = moderation.check_text("a photo of a forbidden-widget")
        assert not decision.allowed
    finally:
        settings.SAFETY_BLOCKLIST = original


def test_voice_clone_requires_consent() -> None:
    decision = moderation.check_voice_clone({"is_cloned": True, "consent": {}})
    assert not decision.allowed
    assert decision.category == "unauthorized_voice_cloning"


def test_voice_clone_with_full_consent_passes() -> None:
    decision = moderation.check_voice_clone({"is_cloned": True, "consent": {
        "owner_attestation": True, "rights_holder": "Self", "authorised_by": "Self", "purpose": "audiobook"}})
    assert decision.allowed


def test_upload_type_and_size_limits() -> None:
    assert not moderation.check_upload("a.exe", "application/x-msdownload", 10).allowed
    assert not moderation.check_upload("a.png", "image/png", 10 ** 12).allowed
    assert not moderation.check_upload("../../etc/passwd", "image/png", 10).allowed
    assert moderation.check_upload("a.png", "image/png", 1000).allowed


def test_generation_endpoint_blocks_unsafe_prompt(client: TestClient, auth_headers: dict) -> None:
    response = client.post("/api/v1/generate/image",
                           json={"prompt": "nude child portrait"}, headers=auth_headers)
    assert response.status_code == 422
