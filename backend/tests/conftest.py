"""
Test fixtures.

Each test session runs against an isolated temporary data directory so tests
never touch the developer's library, and the job queue is polled for completion
rather than slept through.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Configure the environment *before* the application modules are imported.
_TMP = Path(tempfile.mkdtemp(prefix="abhi-tests-"))
os.environ["ABHI_DATA_DIR"] = str(_TMP)
os.environ["ABHI_ADMIN_PASSWORD"] = "test-password"
os.environ["ABHI_JOB_WORKERS"] = "2"


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return _TMP


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def auth(client) -> dict:
    response = client.post(
        "/api/auth/login", json={"username": "admin", "password": "test-password"}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture(scope="session")
def project(client, auth) -> dict:
    response = client.post(
        "/api/projects",
        json={"name": "Test Project", "style_prompt": "cinematic, 35mm"},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    return response.json()


def wait_for_job(client, auth, job_id: str, timeout: float = 300.0) -> dict:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/api/jobs/{job_id}", headers=auth)
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] in ("succeeded", "failed", "cancelled"):
            return job
        time.sleep(0.25)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")
