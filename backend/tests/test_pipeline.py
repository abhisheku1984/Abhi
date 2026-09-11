"""
Pipeline tests.

These assert on *artifacts*, not on HTTP 200s: generated images decode to real
pixels at the requested size, audio decodes to the right duration, and rendered
videos are real MP4s with the expected stream layout and duration.
"""

from __future__ import annotations

import io
import wave
from pathlib import Path

import pytest

from .conftest import wait_for_job


def _image_size(payload: bytes) -> tuple[int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(payload)) as image:
        return image.size


def test_image_generation_produces_a_real_png(client, auth, project):
    queued = client.post(
        "/api/generate/image",
        json={"prompt": "a lighthouse in a storm", "width": 640, "height": 360, "project_id": project["id"]},
        headers=auth,
    ).json()
    job = wait_for_job(client, auth, queued["job_id"])
    assert job["status"] == "succeeded", job.get("error")

    asset_id = job["result"]["asset_ids"][0]
    asset = client.get(f"/api/assets/{asset_id}", headers=auth).json()
    assert asset["kind"] == "image"
    assert asset["width"] == 640 and asset["height"] == 360

    download = client.get(f"/api/assets/{asset_id}/download", headers=auth)
    assert download.status_code == 200
    assert _image_size(download.content) == (640, 360)
    assert asset["thumb_url"], "a thumbnail should have been created"


def test_generation_is_deterministic_for_a_fixed_seed(client, auth):
    """Same prompt + seed must give byte-identical output (reproducibility)."""
    payload = {"prompt": "determinism check", "width": 256, "height": 256, "seed": 1234}
    first = client.post("/api/generate/image?wait=true", json=payload, headers=auth).json()
    second = client.post("/api/generate/image?wait=true", json=payload, headers=auth).json()
    first_asset = first["assets"][0]
    second_asset = second["assets"][0]
    assert first_asset["id"] != second_asset["id"]
    # content-addressed store: identical bytes produce identical file hashes
    assert first_asset["sha256"] == second_asset["sha256"]


def test_image_edit_creates_a_linked_derived_asset(client, auth, project):
    generated = client.post(
        "/api/generate/image?wait=true",
        json={"prompt": "edit source", "width": 400, "height": 300, "project_id": project["id"]},
        headers=auth,
    ).json()
    source = generated["assets"][0]

    response = client.post(
        f"/api/assets/{source['id']}/edit",
        json={"ops": [{"op": "resize", "width": 200, "height": 150}, {"op": "grayscale"}]},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    edited = response.json()["asset"]
    assert edited["parent_asset_id"] == source["id"]
    assert (edited["width"], edited["height"]) == (200, 150)

    lineage = client.get(f"/api/assets/{edited['id']}/lineage", headers=auth).json()
    assert lineage["ancestors"][0]["id"] == edited["id"]
    assert any(entry["id"] == source["id"] for entry in lineage["ancestors"])


def test_invalid_edit_operation_is_rejected(client, auth, project):
    generated = client.post(
        "/api/generate/image?wait=true",
        json={"prompt": "bad ops", "width": 200, "height": 200, "project_id": project["id"]},
        headers=auth,
    ).json()
    response = client.post(
        f"/api/assets/{generated['assets'][0]['id']}/edit", json={"ops": []}, headers=auth
    )
    assert response.status_code == 400


def test_voice_generation_writes_a_valid_wav(client, auth, project):
    queued = client.post(
        "/api/generate/voice",
        json={"text": "Every great film begins with a single frame.", "voice": "aria", "project_id": project["id"]},
        headers=auth,
    ).json()
    job = wait_for_job(client, auth, queued["job_id"])
    assert job["status"] == "succeeded", job.get("error")

    asset_id = job["result"]["asset_ids"][0]
    payload = client.get(f"/api/assets/{asset_id}/download", headers=auth).content
    with wave.open(io.BytesIO(payload), "rb") as handle:
        assert handle.getframerate() > 8000
        duration = handle.getnframes() / handle.getframerate()
    assert duration > 3.0, f"narration too short ({duration:.2f}s)"
    assert job["result"]["quality"] == "demo", "the local synthesiser must be labelled demo"


def test_story_generation_creates_shots_and_storyboard(client, auth, project):
    queued = client.post(
        "/api/generate/story",
        json={
            "premise": "A cartographer maps a city that moves at night",
            "project_id": project["id"],
            "scenes": 3,
            "shots_per_scene": 2,
        },
        headers=auth,
    ).json()
    job = wait_for_job(client, auth, queued["job_id"])
    assert job["status"] == "succeeded", job.get("error")
    story_id = job["result"]["story_id"]

    board = client.get(f"/api/stories/{story_id}/storyboard", headers=auth).json()
    assert board["stats"]["shot_count"] == 6
    assert board["stats"]["total_duration_s"] > 0
    assert all(shot["prompt"] for shot in board["shots"]), "every shot needs a generation prompt"


def test_bulk_shot_generation_links_images_back_to_shots(client, auth, project):
    """The storyboard must fill in: generated images attach to their shot row."""
    story = client.post(
        "/api/generate/story?wait=true",
        json={"premise": "Two rival mapmakers race to chart a storm", "project_id": project["id"], "scenes": 2, "shots_per_scene": 2},
        headers=auth,
    ).json()["result"]
    story_id = story["story_id"]

    queued = client.post(
        f"/api/stories/{story_id}/generate", json={"only_missing": True}, headers=auth
    ).json()
    assert queued["count"] == 4

    for job_id in queued["job_ids"]:
        job = wait_for_job(client, auth, job_id)
        assert job["status"] == "succeeded", job.get("error")

    board = client.get(f"/api/stories/{story_id}/storyboard", headers=auth).json()
    assert board["stats"]["with_images"] == board["stats"]["shot_count"] == 4
    assert all(shot["image"] for shot in board["shots"])

    # regenerating with only_missing must skip the completed shots
    again = client.post(f"/api/stories/{story_id}/generate", json={"only_missing": True}, headers=auth)
    assert again.status_code == 400


@pytest.mark.timeout(600)
def test_storyboard_renders_a_real_video_with_expected_duration(client, auth, project):
    """End-to-end: storyboard -> MP4 with the timeline duration the editor implies."""
    story = client.post(
        "/api/generate/story?wait=true",
        json={"premise": "A signal from a drowned city", "project_id": project["id"], "scenes": 2, "shots_per_scene": 2},
        headers=auth,
    ).json()["result"]
    story_id = story["story_id"]

    queued = client.post(f"/api/stories/{story_id}/generate", json={"only_missing": True}, headers=auth).json()
    for job_id in queued["job_ids"]:
        assert wait_for_job(client, auth, job_id)["status"] == "succeeded"

    board = client.get(f"/api/stories/{story_id}/storyboard", headers=auth).json()
    expected = board["stats"]["total_duration_s"]

    render = client.post(
        f"/api/stories/{story_id}/render?wait=true",
        json={"width": 480, "height": 270, "fps": 24, "transition": "fade", "transition_duration": 0.6},
        headers=auth,
    )
    assert render.status_code == 200, render.text
    assets = render.json()["assets"]
    video = next(asset for asset in assets if asset["kind"] == "video")

    assert video["engine"] == "ffmpeg-render"
    assert video["mime"] == "video/mp4"
    # fade transitions shorten the timeline by (clips - 1) * transition_duration
    shorter_by = (board["stats"]["shot_count"] - 1) * 0.6
    assert abs(video["duration_s"] - (expected - shorter_by)) < 0.5

    download = client.get(f"/api/assets/{video['id']}/download", headers=auth)
    assert download.status_code == 200
    assert download.content[4:8] == b"ftyp", "output must be a real MP4 container"
    assert len(download.content) > 20_000


@pytest.mark.timeout(600)
def test_demo_pipeline_produces_images_audio_and_a_narrated_film(client, auth, project):
    """The self-test button must actually prove the whole stack."""
    queued = client.post(f"/api/generate/demo?project_id={project['id']}&prompt=self%20test", headers=auth).json()
    job = wait_for_job(client, auth, queued["job_id"], timeout=600)
    assert job["status"] == "succeeded", job.get("error")

    kinds = [asset["kind"] for asset in job["result"]["assets"]]
    assert kinds.count("image") >= 3
    assert "audio" in kinds
    assert kinds.count("video") >= 2, "expected a plain render plus a narrated mux"
    assert job["result"]["quality"] == "demo"


def test_unconfigured_provider_fails_honestly(client, auth, project):
    """
    Video generation has no local model: requesting it must fail with an
    actionable configuration message, never a fabricated result.
    """
    response = client.post(
        "/api/generate/video?wait=true",
        json={"prompt": "a whale breaching", "project_id": project["id"]},
        headers=auth,
    )
    assert response.status_code in (422, 409), response.text
    detail = response.json()["detail"]
    assert detail["status"] == "failed"
    error = (detail.get("error") or "").lower()
    assert "replicate" in error or "runway" in error or "comfyui" in error or "not configured" in error
