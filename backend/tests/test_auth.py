"""Authentication, sessions and permissions (§34, §36)."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient


def test_login_returns_token(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login",
                           json={"email": "admin@studio.ai", "password": "Admin@12345"})
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["user"]["role"] in ("owner", "admin")


def test_login_wrong_password_is_rejected(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login",
                           json={"email": "admin@studio.ai", "password": "wrong-password"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_protected_route_requires_token(client: TestClient) -> None:
    response = client.get("/api/v1/projects")
    assert response.status_code == 401


def test_me_returns_current_user(client: TestClient, auth_headers: dict) -> None:
    response = client.get("/api/v1/auth/me", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["email"] == "admin@studio.ai"


def test_register_creates_editor_account(client: TestClient) -> None:
    email = f"editor-{uuid.uuid4().hex[:8]}@studio.ai"
    response = client.post("/api/v1/auth/register",
                           json={"email": email, "password": "Editor@12345", "name": "Editor"})
    assert response.status_code == 201
    body = response.json()
    assert body["user"]["role"] == "editor"
    assert body["access_token"]


def test_register_rejects_duplicate_email(client: TestClient) -> None:
    response = client.post("/api/v1/auth/register",
                           json={"email": "admin@studio.ai", "password": "Another@12345"})
    assert response.status_code == 409


def test_register_rejects_short_password(client: TestClient) -> None:
    response = client.post("/api/v1/auth/register",
                           json={"email": f"short-{uuid.uuid4().hex[:6]}@studio.ai", "password": "abc"})
    assert response.status_code == 422


def test_error_response_hides_stack_traces(client: TestClient) -> None:
    """Users get a friendly message; technical detail stays in logs (§37)."""
    response = client.get("/api/v1/assets/does-not-exist", headers={"Authorization": "Bearer bad"})
    assert response.status_code == 401
    error = response.json()["error"]
    assert "Traceback" not in str(error)
    assert error["message"]
