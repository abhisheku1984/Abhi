"""
Local providers — real computation performed on this machine (CPU).

Quality labels matter here:
* ``real`` — the operation is genuinely the finished feature (Pillow image
  editing, FFmpeg rendering, contact sheets, audio muxing).
* ``demo`` — the algorithm faithfully exercises the whole pipeline but stands in
  for a neural model that cannot run without a GPU (procedural art instead of
  diffusion, formant synthesis instead of neural TTS, template structuring
  instead of an LLM). Reported as ``demo`` everywhere; never as AI.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path

from ..config import detect_ffmpeg, get_settings
from ..media import audio as audio_engine
from ..media import images as image_engine
from ..media import video as video_engine
from .base import Provider, ProviderError, ProviderInfo, register

# --------------------------------------------------------------------------
# image — procedural (demo) + editing (real)
# --------------------------------------------------------------------------


def _run_image_generate(params: dict, ctx: dict) -> dict:

    prompt = (params.get("prompt") or "").strip()
    if not prompt:
        raise ProviderError("prompt is required")
    width = int(params.get("width") or 1024)
    height = int(params.get("height") or 576)
    seed = int(params.get("seed") or 0) or random.randint(1, 2**31)
    style = params.get("style") or "auto"
    data = image_engine.generate_procedural(prompt, width, height, seed=seed, style=style)
    return {
        "files": [
            {
                "data": data,
                "kind": "image",
                "filename": f"gen-{seed}.png",
                "mime": "image/png",
                "width": width,
                "height": height,
            }
        ],
        "meta": {
            "engine": "procedural-local",
            "quality": "demo",
            "note": (
                "Procedural generator (seeded, deterministic). Not a neural model — "
                "configure a cloud or GPU provider for photoreal output."
            ),
            "seed": seed,
            "style": style,
            "palette_source": hashlib.sha256(prompt.encode()).hexdigest()[:8],
        },
    }


def _run_image_edit(params: dict, ctx: dict) -> dict:
    source: Path = ctx.get("source_path")
    if not source or not Path(source).exists():
        raise ProviderError("source asset not found for edit")
    ops = params.get("ops") or []
    if not isinstance(ops, list) or not ops:
        raise ProviderError("'ops' must be a non-empty list of edit operations")
    fmt = (params.get("format") or "png").lower().split("/")[-1]
    quality = int(params.get("quality") or 92)
    data = image_engine.apply_edits(Path(source), ops, output_format=fmt, quality=quality)
    return {
        "files": [
            {
                "data": data,
                "kind": "image",
                "filename": f"edit-{Path(source).stem}.{fmt}",
                "mime": f"image/{'jpeg' if fmt in ('jpg', 'jpeg') else fmt}",
            }
        ],
        "meta": {"engine": "pillow-edit", "quality": "real", "ops": len(ops)},
    }


def _run_contact_sheet(params: dict, ctx: dict) -> dict:
    sources = [Path(p) for p in (ctx.get("source_paths") or [])]
    if not sources:
        raise ProviderError("no source images provided")
    data = image_engine.contact_sheet(
        sources,
        columns=int(params.get("columns") or 4),
        cell=int(params.get("cell") or 320),
        background=params.get("background") or "#0b1220",
    )
    return {
        "files": [
            {"data": data, "kind": "image", "filename": "contact-sheet.png", "mime": "image/png"}
        ],
        "meta": {"engine": "pillow-montage", "quality": "real", "tiles": len(sources)},
    }


def _run_title_card(params: dict, ctx: dict) -> dict:
    data = image_engine.make_card(
        params.get("text") or "Untitled",
        width=int(params.get("width") or 1280),
        height=int(params.get("height") or 720),
        background=params.get("background") or "#101828",
        color=params.get("color") or "#f2f4f7",
        subtitle=params.get("subtitle") or "",
    )
    return {
        "files": [
            {"data": data, "kind": "image", "filename": "title-card.png", "mime": "image/png"}
        ],
        "meta": {"engine": "pillow-card", "quality": "real"},
    }


# --------------------------------------------------------------------------
# video — real FFmpeg rendering (this is the finished feature, not a stand-in)
# --------------------------------------------------------------------------


def _run_video_render(params: dict, ctx: dict) -> dict:
    """Render a slideshow/timeline from clips already resolved to disk paths."""
    clips = ctx.get("clips") or []
    if not clips:
        raise ProviderError("no clips resolved for rendering")
    work_dir = Path(ctx["work_dir"])
    out = work_dir / "render.mp4"
    width = int(params.get("width") or 1280)
    height = int(params.get("height") or 720)
    fps = int(params.get("fps") or 30)
    transition = params.get("transition") or "cut"
    transition_duration = float(params.get("transition_duration") or 0.6)
    result = video_engine.render_timeline(
        clips,
        out,
        width=width,
        height=height,
        fps=fps,
        transition=transition,
        transition_duration=transition_duration,
        work_dir=work_dir,
    )
    data = out.read_bytes()
    return {
        "files": [
            {
                "data": data,
                "kind": "video",
                "filename": "render.mp4",
                "mime": "video/mp4",
                "width": width,
                "height": height,
                "duration_s": result["duration_s"],
            }
        ],
        "meta": {"engine": "ffmpeg-render", "quality": "real", **result},
    }


def _run_video_edit(params: dict, ctx: dict) -> dict:
    source: Path = ctx.get("source_path")
    if not source or not Path(source).exists():
        raise ProviderError("source video not found")
    work_dir = Path(ctx["work_dir"])
    out = work_dir / "edited.mp4"
    video_engine.apply_video_ops(
        Path(source), params.get("ops") or [], out, fps=int(params.get("fps") or 30)
    )
    return {
        "files": [
            {
                "data": out.read_bytes(),
                "kind": "video",
                "filename": f"edited-{Path(source).stem}.mp4",
                "mime": "video/mp4",
                "duration_s": video_engine.media_duration(out),
            }
        ],
        "meta": {"engine": "ffmpeg-edit", "quality": "real", "ops": len(params.get("ops") or [])},
    }


def _run_video_mux(params: dict, ctx: dict) -> dict:
    video_src: Path = ctx.get("source_path")
    audio_src: Path | None = ctx.get("audio_path")
    if not video_src or not audio_src:
        raise ProviderError("muxing requires both a video and an audio asset")
    work_dir = Path(ctx["work_dir"])
    out = work_dir / "muxed.mp4"
    video_engine.mux_audio(
        Path(video_src),
        Path(audio_src),
        out,
        mode=params.get("mode") or "replace",
        audio_gain=float(params.get("audio_gain") or 1.0),
        video_gain=float(params.get("video_gain") or 1.0),
        fade_out=float(params.get("fade_out") or 0.0),
    )
    return {
        "files": [
            {
                "data": out.read_bytes(),
                "kind": "video",
                "filename": "muxed.mp4",
                "mime": "video/mp4",
                "duration_s": video_engine.media_duration(out),
            }
        ],
        "meta": {"engine": "ffmpeg-mux", "quality": "real"},
    }


# --------------------------------------------------------------------------
# tts — formant synthesis (demo)
# --------------------------------------------------------------------------


def _run_tts(params: dict, ctx: dict) -> dict:
    text = (params.get("text") or "").strip()
    if not text:
        raise ProviderError("text is required")
    voice = params.get("voice") or "aria"
    speed = float(params.get("speed") or 1.0)
    pitch = float(params.get("pitch") or 0.0)
    sample_rate = int(params.get("sample_rate") or audio_engine.DEFAULT_SR)
    samples = audio_engine.synth_voice(
        text, voice=voice, speed=speed, sample_rate=sample_rate, pitch_shift=pitch
    )
    data = audio_engine.wav_bytes(samples, sample_rate)
    duration = round(samples.size / float(sample_rate), 3)
    return {
        "files": [
            {
                "data": data,
                "kind": "audio",
                "filename": f"speech-{voice}.wav",
                "mime": "audio/wav",
                "duration_s": duration,
            }
        ],
        "meta": {
            "engine": "formant-demo",
            "quality": "demo",
            "note": (
                "Formant synthesiser: real DSP speech-like audio, not a neural TTS voice. "
                "Configure OpenAI or ElevenLabs for natural speech."
            ),
            "voice": voice,
            "sample_rate": sample_rate,
            "duration_s": duration,
        },
    }


def _run_audio_mix(params: dict, ctx: dict) -> dict:
    tracks: list[tuple] = []
    for entry in ctx.get("tracks") or []:
        samples, rate = audio_engine.read_wav(Path(entry["path"]))
        tracks.append((samples, float(entry.get("gain", 1.0))))
    if not tracks:
        raise ProviderError("no audio tracks resolved")
    mixed = audio_engine.mix_tracks(tracks)
    data = audio_engine.wav_bytes(mixed)
    return {
        "files": [
            {"data": data, "kind": "audio", "filename": "mixdown.wav", "mime": "audio/wav"}
        ],
        "meta": {"engine": "numpy-mixer", "quality": "real", "tracks": len(tracks)},
    }


# --------------------------------------------------------------------------
# llm — deterministic story structurer (demo)
# --------------------------------------------------------------------------

_BEATS = [
    ("Opening image", "Establish the world and the ordinary life of the protagonist before disruption."),
    ("Inciting incident", "An event breaks the routine and creates an irreversible goal."),
    ("Rising action", "Obstacles escalate; allies and opposition become clear."),
    ("Midpoint reversal", "What the protagonist believed is overturned; the stakes double."),
    ("Crisis", "The easiest path fails and the true cost becomes visible."),
    ("Climax", "Direct confrontation where the theme is proven through action."),
    ("Resolution", "The new normal; what changed and what it cost."),
]

_SHOT_LIBRARY = [
    ("Establishing wide shot", "wide establishing shot", "slow push in"),
    ("Medium shot", "medium shot, subject centred", "slow drift right"),
    ("Close-up", "extreme close-up, shallow depth of field", "subtle zoom in"),
    ("Over-the-shoulder", "over-the-shoulder framing, foreground blur", "static"),
    ("Low angle", "low angle looking up, imposing scale", "slow tilt up"),
    ("High angle", "high angle looking down, isolating the subject", "slow zoom out"),
    ("Insert detail", "macro insert shot of a telling detail", "static"),
    ("Reaction shot", "reaction shot, face filling frame, emotion readable", "slow push in"),
]


def _run_story_generate(params: dict, ctx: dict) -> dict:
    """
    Structure a premise into scenes and shots.

    This is deterministic narrative scaffolding (a template beat sheet). It is
    labelled ``demo`` because it is not a language model. It produces genuinely
    usable structure and prompts, which is what the downstream pipeline needs.
    """
    premise = (params.get("premise") or params.get("prompt") or "").strip()
    if not premise:
        raise ProviderError("premise is required")
    title = params.get("title") or ""
    scene_count = max(1, min(12, int(params.get("scenes") or 5)))
    shots_per_scene = max(1, min(8, int(params.get("shots_per_scene") or 3)))
    style = (params.get("style") or "cinematic").strip()
    rng = random.Random(hashlib.sha256(premise.encode()).hexdigest())

    if not title:
        words = [w for w in re.findall(r"[A-Za-z']+", premise) if len(w) > 2][:4]
        title = " ".join(w.capitalize() for w in words) or "Untitled Story"

    subjects = [w for w in re.findall(r"[A-Za-z']{3,}", premise) if w.lower() not in {
        "the", "and", "with", "that", "this", "from", "into", "about", "their", "they",
    }][:6] or ["the protagonist"]

    scenes = []
    shots: list[dict] = []
    index = 0
    total = scene_count * shots_per_scene
    for scene_index in range(scene_count):
        beat, beat_purpose = _BEATS[scene_index % len(_BEATS)]
        focus = subjects[scene_index % len(subjects)]
        scene_shots = []
        for shot_index in range(shots_per_scene):
            framing, framing_prompt, motion = _SHOT_LIBRARY[index % len(_SHOT_LIBRARY)]
            description = (
                f"{beat}: {beat_purpose} Focus on {focus}. "
                f"{framing} communicating {premise[:110].strip()}"
            )
            prompt = (
                f"{framing_prompt}, {focus}, {style} style, {premise[:160].strip()}, "
                f"dramatic lighting, high detail, film still, 35mm, depth of field"
            )
            shot = {
                "index_no": index,
                "scene_no": scene_index + 1,
                "title": f"{beat} — {framing}",
                "description": description,
                "prompt": prompt,
                "duration_s": round(rng.choice([2.5, 3.0, 3.5, 4.0]), 2),
                "motion": motion.split()[-1].replace(" ", "_"),
                "transition": "fade" if shot_index == 0 else rng.choice(["cut", "fade", "dissolve"]),
                "caption": "",
                "beat": beat,
            }
            scene_shots.append(shot)
            shots.append(shot)
            index += 1
        scenes.append(
            {
                "scene_no": scene_index + 1,
                "beat": beat,
                "purpose": beat_purpose,
                "summary": f"{beat} — {focus}. {beat_purpose}",
                "shot_count": len(scene_shots),
                "duration_s": round(sum(s["duration_s"] for s in scene_shots), 2),
            }
        )
    return {
        "story": {
            "title": title,
            "logline": premise.strip().split(".")[0][:240],
            "premise": premise.strip(),
            "scenes": scenes,
        },
        "shots": shots,
        "meta": {
            "engine": "beat-sheet-template",
            "quality": "demo",
            "note": (
                "Deterministic beat-sheet structuring, not a language model. "
                "Configure OpenAI/Anthropic/Ollama for creative prose."
            ),
            "total_shots": total,
        },
    }


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------


def register_local_providers() -> None:
    ffmpeg = detect_ffmpeg()
    register(
        Provider(
            ProviderInfo(
                id="local-procedural",
                label="Local Procedural Artwork",
                capability="image",
                kind="local",
                quality="demo",
                engine="procedural-local",
                describes=(
                    "Seeded procedural generator — deterministic abstract artwork drawn from "
                    "the prompt and seed. Works with zero setup and no GPU."
                ),
                available=True,
            ),
            _run_image_generate,
        ),
        default=True,
    )
    register(
        Provider(
            ProviderInfo(
                id="local-pillow-edit",
                label="Local Edit Engine (Pillow)",
                capability="image_edit",
                kind="local",
                quality="real",
                engine="pillow-edit",
                describes=(
                    "Full non-destructive edit engine: crop, resize, rotate, tone, filters, "
                    "vignette, grain, borders, text/watermarks, posterise, pixelate."
                ),
                available=True,
            ),
            _run_image_edit,
        ),
        default=True,
    )
    register(
        Provider(
            ProviderInfo(
                id="local-ffmpeg-render",
                label="Local Renderer (FFmpeg)",
                capability="video_render",
                kind="local",
                quality="real",
                engine="ffmpeg-render",
                describes=(
                    "Real H.264 rendering of image/video timelines: Ken Burns motion, 20+ "
                    "transitions, captions, audio muxing. Runs on CPU — no GPU required."
                ),
                available=bool(ffmpeg),
                reason="" if ffmpeg else "FFmpeg binary not found",
            ),
            _run_video_render,
        ),
        default=True,
    )
    register(
        Provider(
            ProviderInfo(
                id="local-formant-tts",
                label="Local Voice (Formant Synthesis)",
                capability="tts",
                kind="local",
                quality="demo",
                engine="formant-demo",
                describes=(
                    "Real formant speech synthesiser with six voice profiles, pitch and rate "
                    "control. Audible, word-shaped speech — not a neural voice."
                ),
                available=True,
            ),
            _run_tts,
        ),
        default=True,
    )
    register(
        Provider(
            ProviderInfo(
                id="local-template-story",
                label="Local Story Structurer (Beat Sheet)",
                capability="llm",
                kind="local",
                quality="demo",
                engine="beat-sheet-template",
                describes=(
                    "Deterministic narrative scaffolding: premise → scenes → shot list with "
                    "framing, motion and generation prompts. Not a language model."
                ),
                available=True,
            ),
            _run_story_generate,
        ),
        default=True,
    )
    register(
        Provider(
            ProviderInfo(
                id="local-mixer",
                label="Local Audio Mixer (NumPy)",
                capability="audio_mix",
                kind="local",
                quality="real",
                engine="numpy-mixer",
                describes=(
                    "Real multi-track mixdown with per-track gain, peak normalisation "
                    "and 16-bit WAV encoding."
                ),
                available=True,
            ),
            _run_audio_mix,
        ),
        default=True,
    )
    register(
        Provider(
            ProviderInfo(
                id="local-montage",
                label="Local Composition (Contact Sheets)",
                capability="image_util",
                kind="local",
                quality="real",
                engine="pillow-montage",
                describes="Grid montage for character sheets and storyboard previews.",
                available=True,
            ),
            _run_contact_sheet,
        )
    )
    register(
        Provider(
            ProviderInfo(
                id="local-title-card",
                label="Local Title Card Generator",
                capability="image_util",
                kind="local",
                quality="real",
                engine="pillow-card",
                describes="Typographic title cards for openings, end cards and placeholders.",
                available=True,
            ),
            _run_title_card,
        )
    )


__all__ = [
    "register_local_providers",
    "_run_image_edit",
    "_run_video_edit",
    "_run_video_mux",
    "_run_audio_mix",
    "_run_story_generate",
]
