"""Media pipeline: FFmpeg operations and image processing (§42.13)."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from app.media import audio_ops, ffmpeg, image_ops


def test_ffmpeg_is_available() -> None:
    caps = ffmpeg.capabilities()
    assert caps["available"], "FFmpeg must be resolvable (system PATH or bundled binary)"
    assert "libx264" in caps["encoders"]


def test_image_to_video_and_probe(tmp_path) -> None:
    image = Image.new("RGB", (160, 120), (30, 90, 160))
    source = tmp_path / "frame.png"
    image_ops.save(image, source)

    out = tmp_path / "clip.mp4"
    ffmpeg.image_to_video(source, out, duration=1, fps=12)
    probe = ffmpeg.probe(out)
    assert probe["has_video"]
    assert 0.5 < probe["duration"] < 2.5


def test_thumbnail_and_proxy(tmp_path) -> None:
    video = tmp_path / "clip.mp4"
    source_path = tmp_path / "src.png"
    image_ops.save(Image.new("RGB", (160, 120), (200, 30, 30)), source_path)
    ffmpeg.image_to_video(source_path, video, duration=1, fps=12)

    thumb = tmp_path / "thumb.jpg"
    ffmpeg.thumbnail(video, thumb, time=0.2, width=160)
    assert thumb.exists()

    proxy = tmp_path / "proxy.mp4"
    ffmpeg.make_proxy(video, proxy, height=180)
    assert proxy.exists()


def test_upscale_and_inpaint() -> None:
    source = Image.new("RGB", (40, 30), (10, 120, 200))
    upscaled = image_ops.upscale(source, factor=2)
    assert upscaled.size == (80, 60)

    mask = Image.new("L", source.size, 0)
    mask.paste(255, (10, 8, 30, 22))
    repaired = image_ops.inpaint(source, mask)
    assert repaired.size == source.size
    assert repaired.getpixel((20, 15)) != (0, 0, 0)


def test_background_removal_produces_alpha() -> None:
    source = Image.new("RGB", (64, 64), (255, 255, 255))
    for x in range(24, 40):
        for y in range(16, 48):
            source.putpixel((x, y), (200, 40, 40))
    result = image_ops.remove_background(source)
    assert result.mode == "RGBA"
    alpha = np.asarray(result)[:, :, 3]
    assert alpha.max() == 255


def test_style_transfer_matches_statistics() -> None:
    content = Image.new("RGB", (64, 64), (40, 40, 40))
    reference = Image.new("RGB", (64, 64), (220, 30, 30))
    result = image_ops.style_transfer(content, reference, strength=1.0)
    pixels = np.asarray(result).reshape(-1, 3).mean(axis=0)
    assert pixels[0] > np.asarray(content).reshape(-1, 3).mean(axis=0)[0]


def test_edge_depth_and_sketch_operators() -> None:
    source = Image.new("RGB", (48, 48), (100, 120, 140))
    assert image_ops.edges(source).size == source.size
    assert image_ops.depth_map(source).size == source.size
    assert image_ops.sketch(source).size == source.size


def test_audio_synthesis_and_io(tmp_path) -> None:
    samples = audio_ops.generate_music(genre="cinematic", seconds=2, seed=1)
    assert len(samples) == 2 * audio_ops.SAMPLE_RATE
    path = audio_ops.write_wav(tmp_path / "music.wav", samples)
    decoded, rate = audio_ops.read_audio(path)
    assert rate == audio_ops.SAMPLE_RATE
    assert len(decoded) > 1000


def test_sfx_generation_varies_by_kind() -> None:
    whoosh = audio_ops.generate_sfx("whoosh", seconds=1, seed=2)
    impact = audio_ops.generate_sfx("impact", seconds=1, seed=2)
    assert len(whoosh) == len(impact)
    assert not np.allclose(whoosh, impact)


def test_mix_and_normalize() -> None:
    a = audio_ops.osc(220, 1)
    b = audio_ops.osc(330, 1)
    mixed = audio_ops.mix_tracks([a, b])
    assert abs(float(np.max(np.abs(mixed))) - 0.89) < 0.2


def test_checksum_is_stable(tmp_path) -> None:
    path = tmp_path / "data.bin"
    path.write_bytes(b"studio")
    assert image_ops.checksum(path) == image_ops.checksum(path)
