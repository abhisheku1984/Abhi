"""Model adapter contract, registry and honesty rules (§39, §42)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.engines.base import GenerateRequest, JobContext
from app.engines.registry import registry
from app.engines.image.local_cpu import LocalCPUImageAdapter
from app.engines.image.neural import DiffusersImageAdapter
from app.engines.audio.local import LocalSynthAudioAdapter
from app.engines.video.local_cpu import LocalCPUVideoAdapter
from app.core.errors import ValidationFailed


def _ctx(tmp_path) -> JobContext:
    return JobContext(job_id="job_test", owner_id="usr_test", work_dir=str(tmp_path))


def test_registry_exposes_every_family() -> None:
    families = {a.family for a in registry.all()}
    for expected in ("image", "video", "voice", "audio", "avatar", "lipsync"):
        assert expected in families


def test_every_adapter_implements_the_contract() -> None:
    for adapter in registry.all():
        assert adapter.id and adapter.display_name
        assert adapter.capabilities.modes, f"{adapter.id} declares no modes"
        info = adapter.info()
        assert info["status"] in ("installed", "available", "not_installed", "disabled", "error")
        assert adapter.estimate(GenerateRequest(mode=adapter.capabilities.modes[0])).seconds >= 0


def test_uninstalled_model_reports_not_installed() -> None:
    adapter = DiffusersImageAdapter()
    status = adapter.status()
    if status["status"] != "installed":
        assert status["reason"], "A not-installed model must explain why"
        with pytest.raises(Exception):
            adapter.generate(GenerateRequest(mode="text-to-image", prompt="x"), _ctx("/tmp"))


def test_image_adapter_renders_real_pixels(tmp_path) -> None:
    adapter = LocalCPUImageAdapter()
    request = GenerateRequest(mode="text-to-image", prompt="a cinematic mountain range at sunset",
                              params={"aspect": "16:9", "resolution": "512", "seed": 7})
    artifacts = adapter.generate(request, _ctx(tmp_path))
    assert artifacts and Path(artifacts[0].path).exists()
    image = Image.open(artifacts[0].path)
    assert image.width > 16 and image.height > 16
    array = np.asarray(image)
    assert array.std() > 1.0, "The renderer must produce real variation, not a flat fill"
    assert artifacts[0].meta["diffusion"] is False
    assert artifacts[0].meta["deterministic"] is True


def test_image_adapter_is_deterministic_for_a_seed(tmp_path) -> None:
    adapter = LocalCPUImageAdapter()
    def render() -> np.ndarray:
        artifacts = adapter.generate(
            GenerateRequest(mode="text-to-image", prompt="neon city at night",
                            params={"aspect": "1:1", "resolution": "512", "seed": 12345}),
            _ctx(tmp_path),
        )
        return np.asarray(Image.open(artifacts[0].path))
    assert np.array_equal(render(), render())


def test_image_adapter_validation_requires_source(tmp_path) -> None:
    adapter = LocalCPUImageAdapter()
    result = adapter.validate(GenerateRequest(mode="upscale", prompt=""))
    assert not result.ok
    assert any("source image" in error for error in result.errors)


def test_unsupported_mode_is_rejected(tmp_path) -> None:
    from app.core.errors import StudioError

    adapter = LocalCPUImageAdapter()
    with pytest.raises(StudioError):
        adapter.generate(GenerateRequest(mode="not-a-real-mode", prompt="x"), _ctx(tmp_path))


def test_upscale_increases_resolution(tmp_path) -> None:
    from app.engines.image.local_cpu import LocalCPUUpscaleAdapter
    from app.media import image_ops

    source = Image.new("RGB", (32, 24), (80, 120, 160))
    source_path = tmp_path / "src.png"
    image_ops.save(source, source_path)

    adapter = LocalCPUUpscaleAdapter()
    artifacts = adapter.generate(
        GenerateRequest(mode="upscale", prompt="",
                        references=[__import__("app.engines.base", fromlist=["Reference"]).Reference(
                            path=str(source_path), kind="image")],
                        params={"upscale_factor": 2}),
        _ctx(tmp_path),
    )
    result = Image.open(artifacts[0].path)
    assert result.width == 64 and result.height == 48


def test_audio_adapter_synthesises_real_audio(tmp_path) -> None:
    from app.media import audio_ops

    adapter = LocalSynthAudioAdapter()
    artifacts = adapter.generate(
        GenerateRequest(mode="background-music", prompt="uplifting corporate bed",
                        params={"genre": "corporate", "duration": 3}),
        _ctx(tmp_path),
    )
    assert Path(artifacts[0].path).exists()
    samples, _ = audio_ops.read_audio(artifacts[0].path)
    assert len(samples) > 1000
    assert float(np.max(np.abs(samples))) > 0.05


def test_video_adapter_produces_encoded_file(tmp_path) -> None:
    from app.media import ffmpeg

    adapter = LocalCPUVideoAdapter()
    artifacts = adapter.generate(
        GenerateRequest(mode="text-to-video", prompt="drone shot over a coastline",
                        params={"duration": 1, "fps": 12, "aspect": "16:9", "resolution": "512", "seed": 3}),
        _ctx(tmp_path),
    )
    path = artifacts[0].path
    assert Path(path).exists()
    probe = ffmpeg.probe(path)
    assert probe.get("duration", 0) > 0
    assert probe.get("width", 0) > 0


def test_video_adapter_validates_inputs() -> None:
    adapter = LocalCPUVideoAdapter()
    result = adapter.validate(GenerateRequest(mode="image-to-video", prompt="animate this"))
    assert not result.ok, "image-to-video must require a source image"


def test_voice_adapter_flags_placeholder_audio(tmp_path) -> None:
    from app.engines.voice.local import ProsodyTtsAdapter

    adapter = ProsodyTtsAdapter()
    artifacts = adapter.generate(
        GenerateRequest(mode="text-to-speech", prompt="Welcome to the studio",
                        params={"text": "Welcome to the studio", "duration": 2}),
        _ctx(tmp_path),
    )
    assert artifacts[0].meta["intelligible"] is False
    assert artifacts[0].meta["placeholder"] is True


def test_avatar_adapter_animates_mouth_from_audio(tmp_path) -> None:
    from app.engines.avatar.local import LocalAvatarAdapter, mouth_curve
    from app.media import audio_ops

    adapter = LocalAvatarAdapter()
    artifacts = adapter.generate(
        GenerateRequest(mode="talking-avatar", prompt="",
                        params={"script": "Hello there", "aspect": "9:16", "resolution": "512",
                                "fps": 12, "duration": 1}),
        _ctx(tmp_path),
    )
    assert artifacts[0].meta["lip_sync"] is True
    assert artifacts[0].meta["avatar"] is True
