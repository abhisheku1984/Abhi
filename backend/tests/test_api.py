"""API contract tests: auth, projects, assets, jobs, capabilities."""

from __future__ import annotations

import pytest


def test_health_reports_real_environment(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["database"]["ok"] is True
    assert "ffmpeg" in body
    assert "capabilities" in body


def test_authentication_is_enforced(client, auth):
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/assets").status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    assert client.get("/api/auth/me", headers=auth).status_code == 200


def test_invalid_token_rejected(client):
    bad = {"Authorization": "Bearer not-a-real-token"}
    assert client.get("/api/auth/me", headers=bad).status_code == 401


def test_project_crud(client, auth):
    created = client.post(
        "/api/projects", json={"name": "CRUD Project", "description": "d"}, headers=auth
    ).json()
    assert created["name"] == "CRUD Project"

    patched = client.patch(
        f"/api/projects/{created['id']}", json={"description": "updated"}, headers=auth
    ).json()
    assert patched["description"] == "updated"

    listed = client.get("/api/projects", headers=auth).json()
    assert any(project["id"] == created["id"] for project in listed["items"])

    assert client.delete(f"/api/projects/{created['id']}", headers=auth).status_code == 200
    assert client.get(f"/api/projects/{created['id']}", headers=auth).status_code == 404


def test_capability_honesty_contract(client, auth):
    """
    The core product guarantee: every capability is labelled real / demo /
    not_configured, and providers that are unavailable explain what unlocks them.
    """
    body = client.get("/api/system/capabilities", headers=auth).json()
    assert set(body["modes"]) == {"real", "demo", "not_configured"}
    capabilities = body["capabilities"]
    assert capabilities, "no capabilities reported"

    for name, entry in capabilities.items():
        assert entry["mode"] in body["modes"], f"{name} has an unknown mode"
        assert entry["providers"], f"{name} exposes no providers"
        for provider in entry["providers"]:
            if not provider["available"]:
                assert provider["reason"], f"{provider['id']} is unavailable without a reason"

    # renderer/editor utilities are real and must never be marked as demo
    for utility in ("image_edit", "video_render", "audio_mix"):
        if utility in capabilities:
            assert capabilities[utility]["mode"] == "real"


def test_system_status_reports_environment(client, auth):
    body = client.get("/api/system/status", headers=auth).json()
    assert body["cpu"]["cores"] >= 1
    assert "ffmpeg" in body
    assert body["jobs"]["workers"] >= 1
    assert len(body["job_kinds"]) >= 10


def test_asset_listing_filters(client, auth, project):
    for kind in ("image", "video", "audio"):
        response = client.get(f"/api/assets?kind={kind}&limit=5", headers=auth)
        assert response.status_code == 200
        for item in response.json()["items"]:
            assert item["kind"] == kind


def test_unknown_job_kind_is_rejected(client, auth):
    response = client.post("/api/jobs", json={"kind": "does.not.exist"}, headers=auth)
    assert response.status_code == 400
    assert "unknown job kind" in response.json()["detail"]


def test_job_kinds_endpoint_lists_handlers(client, auth):
    kinds = client.get("/api/jobs/kinds", headers=auth).json()["kinds"]
    names = {kind["kind"] for kind in kinds}
    for expected in ("image.generate", "image.edit", "video.render", "audio.tts", "story.generate"):
        assert expected in names


def test_upload_rejects_empty_file(client, auth):
    response = client.post(
        "/api/assets/upload", files={"file": ("empty.png", b"", "image/png")}, headers=auth
    )
    assert response.status_code == 400
