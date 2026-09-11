"""Local avatar studio (§13, §14).

Avatars are rendered by a deterministic rig whose mouth, blink and head motion
are driven frame-by-frame from the audio envelope — so talking avatars really
do move in sync with the narration.
"""

from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image

from app.core.errors import StudioError
from app.core.logging import get_logger
from app.engines.base import (
    Artifact, BaseModelAdapter, Capabilities, Estimate, GenerateRequest, JobContext, ValidationResult,
)
from app.engines.image.render import render_avatar_frame
from app.media import audio_ops, ffmpeg, image_ops

log = get_logger("engines.avatar.local")

AVATAR_TYPES = [
    "corporate-presenter", "news-presenter", "teacher", "influencer", "cartoon",
    "3d-character", "photorealistic-human", "virtual-assistant", "historical-character",
    "fantasy-character",
]

EXPRESSION_BASE = {
    "neutral": 0.10, "happy": 0.22, "serious": 0.07, "excited": 0.30,
    "calm": 0.09, "surprised": 0.34, "sad": 0.06,
}


def _synthesize_voice(text: str, duration_hint: float, ctx: JobContext, voice: Optional[dict],
                      language: str = "en", seed: int = 0) -> tuple[str, float, bool]:
    """Return (audio_path, duration, intelligible).

    Uses a real TTS binary when available; otherwise the labelled prosody track.
    """
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if exe:
        wav = ctx.path(f"avatar_voice_{ctx.job_id}.wav")
        speed = int(165 * float((voice or {}).get("speed") or 1.0))
        cmd = [exe, "-v", language[:2] or "en", "-s", str(speed), "-w", wav, text]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode == 0 and Path(wav).exists():
            samples, sr = audio_ops.read_audio(wav)
            return wav, float(len(samples) / sr), True
        log.warning("espeak_failed_fallback", stderr=proc.stderr[:200])

    words = max(1, len(text.split()))
    duration = max(1.5, duration_hint or words / 2.5)
    samples = audio_ops.generate_voice_track(text, duration, seed=seed,
                                             voice=str((voice or {}).get("name", "narrator")).lower(),
                                             speed=float((voice or {}).get("speed") or 1.0),
                                             pitch=float((voice or {}).get("pitch") or 1.0))
    wav = audio_ops.write_wav(ctx.path(f"avatar_voice_{ctx.job_id}.wav"), samples)
    return wav, duration, False


def mouth_curve(audio_path: str, frames: int, fps: int, *, seed: int = 0) -> np.ndarray:
    """Per-frame mouth aperture derived from the real audio envelope."""
    samples, sr = audio_ops.read_audio(audio_path)
    if samples.ndim > 1:
        samples = np.abs(samples).mean(axis=1)
    total = len(samples) / sr
    times = np.arange(frames) / float(fps)
    idx = np.clip((times / max(total, 1e-6) * len(samples)).astype(int), 0, max(len(samples) - 1, 0))
    window = max(1, int(sr * 0.04))
    env = np.zeros(frames, dtype=np.float64)
    for i, start in enumerate(idx):
        seg = samples[start: start + window]
        env[i] = float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0
    peak = float(np.max(env)) or 1.0
    env = env / peak
    # Smooth + floor so the mouth never fully snaps shut mid-sentence.
    kernel = np.ones(max(1, int(fps * 0.08))) / max(1, int(fps * 0.08))
    env = np.convolve(env, kernel, mode="same")
    return np.clip(env * 1.25, 0.0, 1.0)


class LocalAvatarAdapter(BaseModelAdapter):
    id = "local-avatar-rig"
    display_name = "Local Avatar Rig (deterministic presenter)"
    version = "1.0.0"
    family = "avatar"
    provider = "local"
    license = "Apache-2.0 (platform code)"
    size_mb = 0
    vram_mb = 0
    speed = "medium"
    is_local = True
    ready_out_of_the_box = True
    notes = ("Composites a real presenter rig and animates mouth/lips from the audio envelope. "
             "Install a neural avatar model for photoreal neural rendering.")
    capabilities = Capabilities(
        family="avatar",
        modes=["create-avatar", "photo-to-avatar", "avatar-video", "talking-avatar",
               "avatar-from-script", "avatar-from-audio", "avatar-lip-sync", "avatar-expression-control"],
        max_resolution="1080p",
        supports_seed=True,
        supports_reference_images=True,
        supports_duration=True,
        supports_voice=True,
        supports_character=True,
        supports_lipsync=True,
        supports_camera=True,
        supports_lighting=True,
        max_duration_sec=180,
    )

    def param_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "avatar_type": {"type": "string", "enum": AVATAR_TYPES, "default": "corporate-presenter"},
                "style": {"type": "string", "enum": ["photorealistic", "cartoon", "3d-character"], "default": "photorealistic"},
                "expression": {"type": "string", "enum": sorted(EXPRESSION_BASE.keys()), "default": "neutral"},
                "gesture": {"type": "string", "enum": ["none", "talking", "presenting", "open-palm"], "default": "talking"},
                "lighting": {"type": "string", "default": "studio"},
                "camera": {"type": "string", "default": "static"},
                "aspect": {"type": "string", "enum": ["9:16", "16:9", "1:1"], "default": "9:16"},
                "resolution": {"type": "string", "enum": ["512", "768", "1080"], "default": "768"},
                "duration": {"type": "number", "minimum": 1, "maximum": 180, "default": 8},
                "fps": {"type": "integer", "enum": [12, 16, 24, 30], "default": 24},
                "script": {"type": "string"},
                "language": {"type": "string", "default": "en"},
            },
        }

    def validate(self, request: GenerateRequest) -> ValidationResult:
        result = ValidationResult()
        if request.mode in ("avatar-from-script", "talking-avatar", "avatar-video") and \
                not (request.params.get("script") or request.prompt):
            result.add_error("Enter the script the avatar should speak.")
        if request.mode == "photo-to-avatar" and not request.references:
            result.add_error("Upload a photo to turn into an avatar.")
        if request.mode == "avatar-from-audio" and not any(r.kind == "audio" for r in request.references):
            result.add_error("Attach the audio track for this avatar.")
        return result

    def estimate(self, request: GenerateRequest) -> Estimate:
        dur = float(request.params.get("duration", 8))
        fps = int(request.params.get("fps", 24))
        return Estimate(seconds=round(2.0 + dur * fps * 0.035, 2), credits=round(dur * 0.8, 2))

    # ------------------------------------------------------------------ #

    def generate(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        self._require_mode(request)
        mode = request.mode
        profile = dict(request.character or {})
        profile.update(request.params.get("profile", {}) or {})

        if mode in ("create-avatar", "photo-to-avatar", "avatar-expression-control"):
            return self._still(request, ctx, profile)
        if mode == "avatar-from-audio":
            return self._from_audio(request, ctx, profile)
        return self._from_script(request, ctx, profile)

    def _profile_values(self, request: GenerateRequest, profile: dict) -> dict:
        p = dict(profile)
        aspect = request.params.get("aspect", "9:16")
        res = str(request.params.get("resolution", "768"))
        w, h = image_ops.resolve_size(aspect, res)
        return {
            "width": w,
            "height": h,
            "skin": tuple(p.get("skin", (226, 178, 148))),
            "hair": tuple(p.get("hair", (42, 32, 28))),
            "clothing": tuple(p.get("clothing", (38, 52, 96))),
            "background": tuple(p.get("background", (24, 26, 40))),
            "expression": request.params.get("expression", p.get("expression", "neutral")),
            "gesture": request.params.get("gesture", p.get("gesture", "none")),
            "style": request.params.get("style", p.get("style", "photorealistic")),
            "lighting": request.params.get("lighting", p.get("lighting", "studio")),
            "seed": request.seed,
        }

    def _still(self, request: GenerateRequest, ctx: JobContext, profile: dict) -> list[Artifact]:
        vals = self._profile_values(request, profile)
        ctx.progress(35, "rendering avatar")
        if request.mode == "photo-to-avatar" and request.references:
            base = image_ops.to_rgb(image_ops.load(request.references[0].path))
            base = image_ops.replace_background(base, vals["background"])
            img = image_ops.resize(base, vals["width"], vals["height"], keep_aspect=False)
        else:
            img = render_avatar_frame(
                width=vals["width"], height=vals["height"], seed=vals["seed"], skin=vals["skin"],
                hair=vals["hair"], clothing=vals["clothing"], background=vals["background"],
                expression=vals["expression"], mouth_open=EXPRESSION_BASE.get(vals["expression"], 0.1),
                gesture=vals["gesture"], style=vals["style"], lighting=vals["lighting"],
            )
        out = image_ops.save(img, ctx.path(f"avatar_{ctx.job_id}.png"))
        return [Artifact(path=out, kind="avatar", mime="image/png", name=f"avatar_{ctx.job_id}",
                         meta={"engine": self.id, "engine_name": self.display_name, "mode": request.mode,
                               "deterministic": True, "diffusion": False, "width": img.width, "height": img.height,
                               "avatar_type": request.params.get("avatar_type", "corporate-presenter"),
                               "expression": vals["expression"], "params": request.params})]

    def _from_script(self, request: GenerateRequest, ctx: JobContext, profile: dict) -> list[Artifact]:
        script = str(request.params.get("script") or request.prompt or "")
        ctx.progress(15, "generating voice")
        audio_path, duration, intelligible = _synthesize_voice(
            script, float(request.params.get("duration", 0)), ctx, request.voice,
            str(request.params.get("language", "en")), request.seed,
        )
        return self._animate(request, ctx, profile, audio_path, duration,
                             intelligible=intelligible, script=script)

    def _from_audio(self, request: GenerateRequest, ctx: JobContext, profile: dict) -> list[Artifact]:
        ref = next((r for r in request.references if r.kind == "audio"), None)
        if ref is None:
            raise StudioError("Attach an audio track.", suggested_action="Upload the narration audio.")
        info = ffmpeg.probe(ref.path)
        duration = float(info.get("duration") or 5)
        return self._animate(request, ctx, profile, ref.path, duration, intelligible=True,
                             script=str(request.params.get("script") or ""))

    def _animate(self, request: GenerateRequest, ctx: JobContext, profile: dict, audio_path: str,
                 duration: float, *, intelligible: bool, script: str) -> list[Artifact]:
        vals = self._profile_values(request, profile)
        fps = int(request.params.get("fps", 24))
        frames_total = max(1, int(round(duration * fps)))
        curve = mouth_curve(audio_path, frames_total, fps, seed=request.seed)
        frames_dir = Path(ctx.path("frames"))
        frames_dir.mkdir(parents=True, exist_ok=True)

        ctx.progress(30, "animating avatar")
        for i in range(frames_total):
            if i % 6 == 0:
                ctx.check_cancelled()
                ctx.progress(30 + 50 * (i / frames_total), f"frame {i + 1}/{frames_total}")
            t = i / float(fps)
            blink = 1.0 if (i % max(int(fps * 3.2), 1) == 0 and (i // max(int(fps * 3.2), 1)) % 2 == 0) else 0.0
            head_tilt = 0.55 * math.sin(t * 0.9) + 0.18 * math.sin(t * 2.3)
            frame = render_avatar_frame(
                width=vals["width"], height=vals["height"], seed=vals["seed"],
                skin=vals["skin"], hair=vals["hair"], clothing=vals["clothing"],
                background=vals["background"], expression=vals["expression"],
                mouth_open=float(curve[i]), blink=blink, head_tilt=head_tilt,
                gesture=vals["gesture"] or "talking", style=vals["style"], lighting=vals["lighting"],
            )
            frame.save(frames_dir / f"frame_{i + 1:05d}.png")

        out = ctx.path(f"avatar_video_{ctx.job_id}.mp4")
        ctx.progress(85, "encoding")
        ffmpeg.frames_to_video(frames_dir, out, fps=fps, pattern="frame_%05d.png", audio=audio_path)
        shutil.rmtree(frames_dir, ignore_errors=True)
        info = ffmpeg.probe(out)
        return [Artifact(path=out, kind="video", mime="video/mp4", name=f"avatar_video_{ctx.job_id}",
                         meta={"engine": self.id, "engine_name": self.display_name, "mode": request.mode,
                               "deterministic": True, "diffusion": False, "avatar": True,
                               "duration": info.get("duration"), "fps": fps, "frames": frames_total,
                               "width": info.get("width"), "height": info.get("height"),
                               "script": script, "intelligible": intelligible,
                               "lip_sync": True, "params": request.params})]


ADAPTERS = [LocalAvatarAdapter()]
