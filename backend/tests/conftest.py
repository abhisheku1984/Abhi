"""Pytest fixtures: isolated SQLite database, no background workers."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

TEST_DB = BACKEND_ROOT / "data" / "test.db"
TEST_STORAGE = BACKEND_ROOT / "data" / "test_storage"

os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB.as_posix()}"
os.environ["STORAGE_LOCAL_ROOT"] = str(TEST_STORAGE)
os.environ["ENABLE_BACKGROUND_WORKER"] = "false"
os.environ["APP_ENV"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["BOOTSTRAP_ADMIN_EMAIL"] = "admin@studio.ai"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "Admin@12345"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="session", autouse=True)
def _register_engines():
    """Ensure adapters are registered even for tests that never boot the app."""
    from app.engines.registry import registry

    if not registry.all():
        from app.engines.registry import register_core_adapters

        register_core_adapters()
    return registry


@pytest.fixture(scope="session")
def admin_token(client: TestClient) -> str:
    response = client.post("/api/v1/auth/login",
                           json={"email": "admin@studio.ai", "password": "Admin@12345"})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture(scope="session")
def auth_headers(admin_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="session")
def project(client: TestClient, auth_headers: dict) -> dict:
    response = client.post("/api/v1/projects", json={"name": "Test project", "kind": "image"},
                           headers=auth_headers)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture()
def sample_png(tmp_path: Path) -> str:
    from PIL import Image

    path = tmp_path / "sample.png"
    Image.new("RGB", (64, 48), (120, 90, 200)).save(path)
    return str(path)
