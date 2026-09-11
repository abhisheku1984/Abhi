"""Job queue, worker execution and generation endpoints (§22, §26)."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.jobs import queue


def _wait(client: TestClient, job_id: str, headers: dict, timeout: int = 180) -> dict:
    from app.jobs.handlers import execute_job

    # Background workers are disabled in tests: run the job deterministically.
    execute_job(job_id)
    return client.get(f"/api/v1/jobs/{job_id}", headers=headers).json()


def test_generate_image_creates_job_and_asset(client: TestClient, auth_headers: dict) -> None:
    response = client.post("/api/v1/generate/image",
                           json={"prompt": "a cinematic desert highway at dusk",
                                 "params": {"aspect": "16:9", "resolution": "512", "seed": 5}},
                           headers=auth_headers)
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    assert job_id

    job = _wait(client, job_id, auth_headers)
    assert job["status"] == "completed", job.get("error")
    assert (job["result"] or {}).get("asset_ids")

    assets = client.get("/api/v1/assets", params={"kind": "image"}, headers=auth_headers)
    assert any(a["meta"]["provenance"]["mode"] == "text-to-image" for a in assets.json()["items"])


def test_job_reports_real_progress_and_completion(client: TestClient, auth_headers: dict) -> None:
    job_id = client.post("/api/v1/generate/video",
                         json={"prompt": "slow dolly through a neon market",
                               "params": {"duration": 1, "fps": 12, "resolution": "512", "seed": 9}},
                         headers=auth_headers).json()["job_id"]
    job = _wait(client, job_id, auth_headers)
    assert job["status"] == "completed", job.get("error")
    assert job["progress"] == 100
    assert job["duration_seconds"] is not None


def test_unavailable_model_is_rejected_at_enqueue(client: TestClient, auth_headers: dict) -> None:
    """§42.3 — an absent model is refused with an explicit 409, not a vague 422."""
    response = client.post("/api/v1/generate/image",
                           json={"prompt": "anything", "model_id": "diffusers-image"},
                           headers=auth_headers)
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "model_not_installed"
    assert "not installed" in error["message"].lower()
    assert error["suggested_action"]


def test_unconfigured_provider_is_reported(client: TestClient, auth_headers: dict) -> None:
    """§42.3 — a provider without credentials is a different, named problem."""
    response = client.post("/api/v1/generate/image",
                           json={"prompt": "anything", "model_id": "http-image-provider"},
                           headers=auth_headers)
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "provider_not_configured"


def test_missing_source_is_reported(client: TestClient, auth_headers: dict) -> None:
    response = client.post("/api/v1/generate/upscale",
                           json={"prompt": "", "params": {"upscale_factor": 2}},
                           headers=auth_headers)
    assert response.status_code == 422


def test_job_cancel_and_retry(client: TestClient, auth_headers: dict) -> None:
    job_id = client.post("/api/v1/generate/image",
                         json={"prompt": "quiet forest path", "params": {"resolution": "512"}},
                         headers=auth_headers).json()["job_id"]

    cancelled = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=auth_headers)
    assert cancelled.status_code == 200

    retry = client.post(f"/api/v1/jobs/{job_id}/retry", headers=auth_headers)
    assert retry.status_code == 200
    assert retry.json()["status"] == "queued"


def test_queue_stats_and_listing(client: TestClient, auth_headers: dict) -> None:
    stats = client.get("/api/v1/jobs/stats", headers=auth_headers)
    assert stats.status_code == 200
    for key in ("queued", "processing", "completed", "failed"):
        assert key in stats.json()

    listing = client.get("/api/v1/jobs", params={"limit": 5}, headers=auth_headers)
    assert listing.status_code == 200
    assert listing.json()["total"] >= 1


def test_queue_lifecycle_helpers(auth_headers: dict) -> None:
    from app.db.session import session_scope
    from app.db.models import User

    with session_scope() as db:
        owner_id = db.query(User).first().id
    job = queue.enqueue(None, owner_id=owner_id, type="image", mode="text-to-image", params={"prompt": "x"})
    assert job.status == "queued"
    queue.progress(job.id, 42, "rendering")
    assert queue.get(job.id).progress == 42
    queue.complete(job.id, {"asset_ids": []})
    assert queue.get(job.id).status == "completed"

    failing = queue.enqueue(None, owner_id=owner_id, type="image", mode="text-to-image", params={})
    queue.fail(failing.id, {"code": "boom", "message": "failed"}, retry=True)
    assert queue.get(failing.id).status == "queued"


def test_users_only_see_their_own_jobs(client: TestClient, auth_headers: dict) -> None:
    job_id = client.post("/api/v1/generate/image", json={"prompt": "private render"},
                         headers=auth_headers).json()["job_id"]

    import uuid

    other = client.post("/api/v1/auth/register",
                        json={"email": f"jobs-{uuid.uuid4().hex[:8]}@studio.ai",
                              "password": "Other@12345", "name": "Other"}).json()
    response = client.get(f"/api/v1/jobs/{job_id}",
                          headers={"Authorization": f"Bearer {other['access_token']}"})
    assert response.status_code == 403
