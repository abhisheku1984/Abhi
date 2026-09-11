"""Asset upload, storage, metadata and permissions (§24, §34)."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi.testclient import TestClient


def test_upload_stores_asset_with_metadata(client: TestClient, auth_headers: dict) -> None:
    from PIL import Image

    path = Path(f"/tmp/upload-{uuid.uuid4().hex}.png")
    Image.new("RGB", (120, 80), (200, 40, 60)).save(path)
    try:
        with path.open("rb") as handle:
            response = client.post("/api/v1/assets/upload", files={"file": ("sample.png", handle, "image/png")},
                                   headers=auth_headers)
        assert response.status_code == 201, response.text
        asset = response.json()
        assert asset["kind"] == "image"
        assert asset["width"] == 120 and asset["height"] == 80
        assert asset["size_bytes"] > 0
        assert asset["meta"]["provenance"]["ai_generated"] is False
    finally:
        path.unlink(missing_ok=True)


def test_upload_rejects_disallowed_type(client: TestClient, auth_headers: dict) -> None:
    path = Path(f"/tmp/upload-{uuid.uuid4().hex}.exe")
    path.write_bytes(b"MZ\x00\x00")
    try:
        with path.open("rb") as handle:
            response = client.post("/api/v1/assets/upload",
                                   files={"file": ("bad.exe", handle, "application/x-msdownload")},
                                   headers=auth_headers)
        assert response.status_code in (400, 422)
    finally:
        path.unlink(missing_ok=True)


def test_list_and_filter_assets(client: TestClient, auth_headers: dict) -> None:
    listed = client.get("/api/v1/assets", params={"kind": "image"}, headers=auth_headers)
    assert listed.status_code == 200
    assert all(a["kind"] == "image" for a in listed.json()["items"])


def test_favorite_duplicate_and_delete(client: TestClient, auth_headers: dict) -> None:
    from PIL import Image

    path = Path(f"/tmp/asset-{uuid.uuid4().hex}.png")
    Image.new("RGB", (32, 32), (10, 20, 30)).save(path)
    try:
        with path.open("rb") as handle:
            asset = client.post("/api/v1/assets/upload", files={"file": ("a.png", handle, "image/png")},
                                headers=auth_headers).json()

        fav = client.patch(f"/api/v1/assets/{asset['id']}", json={"is_favorite": True}, headers=auth_headers)
        assert fav.json()["is_favorite"] is True

        dup = client.post(f"/api/v1/assets/{asset['id']}/duplicate", headers=auth_headers)
        assert dup.status_code == 201
        assert dup.json()["id"] != asset["id"]

        deleted = client.delete(f"/api/v1/assets/{dup.json()['id']}", headers=auth_headers)
        assert deleted.status_code == 200
    finally:
        path.unlink(missing_ok=True)


def test_file_serving_blocks_path_traversal(client: TestClient, auth_headers: dict) -> None:
    # Encoded traversal sequences must not be collapsed by the HTTP client.
    response = client.get("/api/v1/files/%2e%2e/%2e%2e/etc/passwd", headers=auth_headers)
    assert response.status_code in (400, 404)


def test_asset_stats(client: TestClient, auth_headers: dict) -> None:
    response = client.get("/api/v1/assets/stats/summary", headers=auth_headers)
    assert response.status_code == 200
    assert "total_mb" in response.json()
