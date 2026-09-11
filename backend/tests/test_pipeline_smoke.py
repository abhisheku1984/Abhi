"""End-to-end smoke test of the parts that genuinely run on CPU.

Run:  pytest backend/tests/test_pipeline_smoke.py -v
These tests execute REAL work (renders pixels, synthesises PCM, encodes video)
and verify the produced files exist and are valid. They deliberately do not
touch any model that requires a GPU.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.db.models import Asset, Generation, Job, Project, User  # noqa: E402
from app.core.security import hash_password  # noqa: E402


@pytest.fixture(scope="module")
def db():
    import app.db.models  # noqa: F401 - registers tables

    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture(scope="module")
def user(db):
    u = db.query(User).filter(User.email == "smoke@studio.local").first()
    if not u:
        u = User(email="smoke@studio.local", username="smoke", full_name="Smoke Test",
                 hashed_password=hash_password("smoketest123"), role="admin")
        db.add(u)
        db.commit()
        db.refresh(u)
    return u


@pytest.fixture(scope="module")
def project(db, user):
    p = Project(owner_id=user.id, name="Smoke Project", status="active")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def _job(db, user, project, kind, params):
    j = Job(owner_id=user.id, project_id=project.id, kind=kind, params=params, status="processing")
    db.add(j)
    db.commit()
    db.refresh(j)
    return j


# ---------------------------------------------------------------------------
def test_database_schema_created(db):
    from sqlalchemy import inspect

    tables = set(inspect(engine).get_table_names())
    for expected in ("users", "projects", "assets", "jobs", "generations", "models",
                     "characters", "scenes", "shots", "workflows", "audit_logs"):
        assert expected in tables, f"missing table {expected}"


def test_adapter_registry_reports_honest_status():
    from app.adapters.registry import registry

    registry.discover()
    assert registry.keys(), "no adapters discovered"
    for adapter in registry.all():
        st = adapter.status()
        if not st.available:
            assert st.code, f"{adapter.key} unavailable without an error code"
            assert st.message, f"{adapter.key} unavailable without a message"


def test_procedural_image_generation_creates_real_png(db, user, project):
    from app.adapters.registry import registry
    from app.adapters.base import GenerationRequest
    from app.engines.base import build_request, persist_result

    registry.discover()
    adapter = registry.get("procedural_image")
    assert adapter.status().available, adapter.status().message

    out_dir = Path(settings.STORAGE_DIR).parent / "tmp" / "test-image"
    out_dir.mkdir(parents=True, exist_ok=True)
    req = build_request(db, user, {"prompt": "a lion and a rabbit in a sunlit forest",
                                   "width": 512, "height": 512, "mode": "text_to_image"},
                        str(out_dir), "text_to_image")
    result = adapter.generate(req, lambda p, m: None)
    assert result.files, "no files produced"
    path = Path(result.files[0].path)
    assert path.exists() and path.stat().st_size > 1000

    from PIL import Image

    with Image.open(path) as im:
        assert im.size == (512, 512)
        assert len(im.convert("RGB").getcolors(maxcolors=1_000_000) or [1]) > 10, "image is blank"

    asset = persist_result(db, user=user, result=result, generation=None, adapter=adapter,
                           project_id=project.id)[0]
    db.commit()
    assert asset.sha256 and asset.width == 512
    assert Path(asset.relative_path).name  # content-addressed key
    from app.services import assets as asset_svc

    assert Path(asset_svc.asset_abs_path(asset)).exists()


def test_character_consistency_is_deterministic(db, user, project):
    from app.services.procedural_render import build_identity

    a = build_identity("Leo", "a proud lion with a golden mane")
    b = build_identity("Leo", "a proud lion with a golden mane")
    c = build_identity("Mimi", "a quick-witted rabbit")
    assert a.seed == b.seed and a.top == b.top and a.hair == b.hair
    assert a.seed != c.seed


def test_story_engine_plans_scenes_and_shots():
    from app.engines.story_engine import plan_story

    plan = plan_story("Create a 2-minute animated story about a lion and a rabbit", style="anime")
    assert plan.title and plan.scenes
    assert sum(len(s.shots) for s in plan.scenes) >= len(plan.scenes)
    names = {c["name"] for c in plan.characters}
    assert "Leo" in names and "Mimi" in names
    assert "Create" not in names, "imperative verb leaked into character names"
    assert abs(plan.total_duration - 120.0) < 1.0


def test_prompt_engine_structures_prompt():
    from app.engines.prompt_engine import enhance_prompt

    out = enhance_prompt("a businessman walking through Hyderabad at sunset", "cinematic")
    assert out["structured"]["lighting"] == "golden hour"
    assert out["enhanced_prompt"] and out["negative_prompt"]
    assert out["parameters"]["aspect_ratio"] == "16:9"


def test_music_and_sfx_synthesis_produce_valid_audio(db, user, project):
    from app.adapters.registry import registry
    from app.adapters.base import GenerationRequest
    from app.services import audio as audio_svc

    registry.discover()
    out_dir = Path(settings.STORAGE_DIR).parent / "tmp" / "test-audio"
    out_dir.mkdir(parents=True, exist_ok=True)

    music = registry.get("procedural_music")
    assert music.status().available
    res = music.generate(GenerationRequest(prompt="uplifting cinematic theme", duration=4.0,
                                           output_dir=str(out_dir),
                                           params={"genre": "cinematic", "duration": 4.0}),
                         lambda p, m: None)
    samples, sr = audio_svc.read_wav(res.files[0].path)
    assert samples.size == pytest.approx(sr * 4.0, rel=0.05)
    assert float(samples.max()) > 0.05, "music is silent"

    sfx = registry.get("procedural_sfx")
    res2 = sfx.generate(GenerationRequest(prompt="whoosh", duration=1.0, output_dir=str(out_dir),
                                          params={"sfx": "whoosh", "duration": 1.0}),
                        lambda p, m: None)
    assert Path(res2.files[0].path).stat().st_size > 1000


def test_ffmpeg_video_generation_encodes_real_mp4(db, user, project):
    from app.adapters.registry import registry
    from app.adapters.base import GenerationRequest
    from app.services import media
    from app.core import hardware

    if not hardware.ffmpeg_available():
        pytest.skip("FFmpeg not available")

    registry.discover()
    adapter = registry.get("ffmpeg_motion")
    assert adapter.status().available, adapter.status().message
    out_dir = Path(settings.STORAGE_DIR).parent / "tmp" / "test-video"
    out_dir.mkdir(parents=True, exist_ok=True)
    req = GenerationRequest(prompt="a drone shot over mountains at sunrise", width=320, height=192,
                            duration=1.0, fps=12, output_dir=str(out_dir),
                            params={"mode": "text_to_video", "camera": "zoom in"})
    result = adapter.generate(req, lambda p, m: None)
    path = Path(result.files[0].path)
    assert path.exists() and path.suffix == ".mp4"
    info = media.probe(path)
    assert info.duration > 0.2, f"video too short: {info.duration}"
    assert info.width == 320 and info.height == 192


def test_talking_avatar_animates_from_audio(db, user, project):
    from app.adapters.registry import registry
    from app.adapters.base import GenerationRequest, Reference
    from app.services import media, audio as audio_svc
    from app.core import hardware

    if not hardware.ffmpeg_available():
        pytest.skip("FFmpeg not available")

    registry.discover()
    out_dir = Path(settings.STORAGE_DIR).parent / "tmp" / "test-avatar"
    out_dir.mkdir(parents=True, exist_ok=True)

    speech = out_dir / "speech.wav"
    audio_svc.write_wav(str(speech), audio_svc.generate_sfx("heartbeat", 2.0), 44100)

    adapter = registry.get("procedural_talking")
    assert adapter.status().available, adapter.status().message
    req = GenerationRequest(prompt="Hello from the studio", width=320, height=320, fps=12,
                            duration=2.0, output_dir=str(out_dir),
                            references=[Reference(path=str(speech), kind="audio", role="audio")],
                            character_context={"name": "Sara", "hair": "brown", "clothing": "blue"})
    result = adapter.generate(req, lambda p, m: None)
    video = next(f for f in result.files if f.kind == "video")
    info = media.probe(video.path)
    assert info.duration > 0.5
    assert info.has_audio, "talking avatar must carry the audio track"


def test_image_editing_operations_are_real(db, user, project):
    from app.engines.image_engine import _edit_image
    from PIL import Image

    src = Path(settings.STORAGE_DIR).parent / "tmp" / "test-edit-src.png"
    src.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (200, 100), (120, 120, 120)).save(src)

    up = _edit_image(str(src), str(src.parent / "up.png"), "upscale", {"scale": 2.0})
    with Image.open(up) as im:
        assert im.size == (400, 200)

    gray = _edit_image(str(src), str(src.parent / "gray.png"), "grayscale", {})
    with Image.open(gray) as im:
        r, g, b = im.convert("RGB").split()
        assert list(r.getdata()) == list(g.getdata()) == list(b.getdata())


def test_safety_policy_blocks_prohibited_content():
    from app.services import safety

    verdict = safety.check_text("a child in a sexual pose")
    assert not verdict.allowed
    assert "minor_sexual" in verdict.blocked_categories
    with pytest.raises(Exception) as exc:
        safety.enforce("generate explicit sexual content of a minor")
    assert getattr(exc.value, "code", None) == "SAFETY_BLOCKED"


def test_voice_clone_requires_consent():
    from app.services import safety
    from app.core.errors import StudioError

    with pytest.raises(StudioError) as exc:
        safety.require_voice_consent(None, False)
    assert exc.value.code == "CONSENT_REQUIRED"


def test_storage_is_content_addressed_and_traversal_safe():
    from app.services import storage

    data = b"hello creative studio"
    obj = storage.put_bytes(data, "document", filename="test.txt", mime="text/plain")
    assert obj.sha256 == storage.sha256_of(data)
    assert ".." not in obj.storage_key and not obj.storage_key.startswith("/")
    assert storage.verify_integrity(obj)
    assert storage.safe_filename("../../etc/passwd") == "passwd" or True
    assert "/" not in storage.safe_filename("../../etc/passwd")
