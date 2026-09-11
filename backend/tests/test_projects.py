"""Project CRUD, ownership and version history (§21)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_create_list_update_delete(client: TestClient, auth_headers: dict) -> None:
    created = client.post("/api/v1/projects",
                          json={"name": "Alpha", "description": "first", "kind": "video"},
                          headers=auth_headers)
    assert created.status_code == 201
    project = created.json()
    assert project["name"] == "Alpha"

    listed = client.get("/api/v1/projects", headers=auth_headers)
    assert listed.status_code == 200
    assert any(p["id"] == project["id"] for p in listed.json()["items"])

    updated = client.patch(f"/api/v1/projects/{project['id']}",
                           json={"name": "Alpha v2", "is_favorite": True}, headers=auth_headers)
    assert updated.status_code == 200
    assert updated.json()["name"] == "Alpha v2"
    assert updated.json()["is_favorite"] is True

    deleted = client.delete(f"/api/v1/projects/{project['id']}", headers=auth_headers)
    assert deleted.status_code == 200
    assert client.get(f"/api/v1/projects/{project['id']}", headers=auth_headers).status_code == 404


def test_project_search_filter(client: TestClient, auth_headers: dict) -> None:
    client.post("/api/v1/projects", json={"name": "Zebra campaign"}, headers=auth_headers)
    response = client.get("/api/v1/projects", params={"q": "Zebra"}, headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["total"] >= 1
    assert all("zebra" in p["name"].lower() for p in response.json()["items"])


def test_version_snapshot_and_restore(client: TestClient, auth_headers: dict, project: dict) -> None:
    project_id = project["id"]
    client.patch(f"/api/v1/projects/{project_id}", json={"name": "Before snapshot"},
                 headers=auth_headers)

    snapshot = client.post(f"/api/v1/projects/{project_id}/versions",
                           json={"label": "v1"}, headers=auth_headers)
    assert snapshot.status_code == 200
    version = snapshot.json()

    client.patch(f"/api/v1/projects/{project_id}", json={"name": "After snapshot"},
                 headers=auth_headers)
    assert client.get(f"/api/v1/projects/{project_id}", headers=auth_headers).json()["name"] == "After snapshot"

    versions = client.get(f"/api/v1/projects/{project_id}/versions", headers=auth_headers)
    assert versions.status_code == 200
    assert any(v["id"] == version["id"] for v in versions.json()["items"])

    restored = client.post(
        f"/api/v1/projects/{project_id}/versions/{version['id']}/restore", headers=auth_headers)
    assert restored.status_code == 200


def test_users_cannot_access_other_projects(client: TestClient, auth_headers: dict) -> None:
    """Ownership is enforced server-side (§34)."""
    import uuid

    other_email = f"other-{uuid.uuid4().hex[:8]}@studio.ai"
    registered = client.post("/api/v1/auth/register",
                             json={"email": other_email, "password": "Other@12345", "name": "Other"})
    other_headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}

    mine = client.post("/api/v1/projects", json={"name": "Private project"}, headers=auth_headers).json()

    forbidden = client.get(f"/api/v1/projects/{mine['id']}", headers=other_headers)
    assert forbidden.status_code in (403, 404)

    listing = client.get("/api/v1/projects", headers=other_headers)
    assert all(p["id"] != mine["id"] for p in listing.json()["items"])
