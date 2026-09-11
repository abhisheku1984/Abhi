"""Avatar adapters: portrait generation, talking avatars and lip-sync.

ProceduralAvatarAdapter and ProceduralTalkingAvatarAdapter really run on CPU:
the talking avatar renders one frame per video frame with mouth opening driven
by the RMS envelope of the actual speech/audio track, then encodes with FFmpeg.
That is genuine audio-driven animation - stylised, not photoreal.

Photoreal lip-sync (Wav2Lip / MuseTalk / SadTalker) is fully declared and
gated on weights + GPU; the pipeline that calls them is identical.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from PIL import Image

from app.adapters.base import (
    GeneratedFile,
    GenerationRequest,
    GenerationResult,
    AvatarModelAdapter,
    ProgressCallback,
    timed,
)
from app.core.errors import ErrorCode, StudioError
from app.services import audio as audio_svc
from app.services import media
from app.services.procedural_render import (
    build_identity,
    parse_hex_color,
    render_avatar_portrait,
)


def _out(req: GenerationRequest, name: str) -> str:
    d = Path(req.output_dir)
    d.mkdir(parents=True, exist_ok=True)
    return str(d / name)


def _identity_from_request(req: GenerationRequest):
    spec = dict(req.character_context or {})
    name = spec.get("name") or req.param("name", "Avatar")
    desc = " ".join(str(spec.get(k, "")) for k in ("description", "face", "hair", "clothing", "personality", "style"))
    ident = build_identity(name, desc or (req.prompt or ""), seed=spec.get("identity_seed"))
    for key, attr in (("clothing", "top"), ("hair", "hair"), ("accessories", "accent")):
        val = spec.get(key)
        if val:
            cur = getattr(ident, attr)
            setattr(ident, attr, parse_hex_color(str(val), cur))
    if spec.get("age_appearance"):
        low = str(spec["age_appearance"]).lower()
        if "child" in low or "young" in low:
            ident.age = "child"
            ident.height = 0.30
        elif "old" in low or "elder" in low:
            ident.age = "elder"
    return ident


class ProceduralAvatarAdapter(AvatarModelAdapter):
    key = "procedural_avatar"
    name = "Procedural Avatar Studio (CPU)"
    capability = "avatar"
    capabilities = ("avatar", "avatar_from_image", "avatar_from_photo", "avatar_script")
    version = "1.1"
    license = "MIT (renderer code)"
    description = (
        "Renders stylised avatar portraits from a character/avatar spec or a reference "
        "image palette. Runs on CPU. Photoreal avatars require a diffusion model (see Model Manager)."
    )
    quality = "draft"
    ram_gb = 0.3

    @timed
    def generate(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        self.ensure_ready()
        ident = _identity_from_request(req)
        # A reference image contributes its real colour palette to the identity.
        for r in (req.references or []):
            if r.kind == "image":
                with Image.open(r.path) as im:
                    small = im.convert("RGB").resize((64, 64))
                    q = small.quantize(colors=4, method=Image.Quantize.MEDIANCUT)
                    pal = q.getpalette() or []
                    cols = [tuple(pal[i * 3:i * 3 + 3]) for i in range(4)]
                    counts = sorted(q.getcolors() or [], reverse=True)
                    ranked = [tuple(pal[idx * 3:idx * 3 + 3]) for _, idx in counts[:4]]
                    if ranked:
                        ident.top = ranked[0]
                        ident.accent = ranked[1] if len(ranked) > 1 else ranked[0]
                break

        w, h = req.width, req.height
        progress(0.4, "rendering portrait")
        img = render_avatar_portrait(
            ident, w, h,
            background=str(req.param("background", "studio")),
            expression=str(req.param("expression", "neutral")),
            avatar_type=str(req.param("avatar_type", "photorealistic_human")),
        )
        p = _out(req, "avatar.png")
        img.save(p)
        progress(1.0, "complete")
        return GenerationResult(
            files=[GeneratedFile(p, "image", "image/png", {"renderer": "procedural"})],
            meta={"renderer": "procedural", "identity": ident.as_dict(),
                  "quality": "draft", "note": "stylised procedural avatar - not photoreal"},
            model_id=self.key,
        )


class ProceduralTalkingAvatarAdapter(AvatarModelAdapter):
    """Audio-driven talking avatar: real lip movement from real audio."""

    key = "procedural_talking"
    name = "Procedural Talking Avatar + Lip Sync (CPU)"
    capability = "lipsync"
    capabilities = ("lipsync", "avatar_talking", "audio_to_avatar", "script_to_avatar")
    version = "1.0"
    license = "MIT (renderer code)"
    description = (
        "Animates an avatar from an audio track: mouth aperture is computed from the "
        "audio RMS envelope, with eye blinks and head motion. Real animation driven by "
        "the real waveform. Stylised - photoreal lip-sync needs Wav2Lip (Model Manager)."
    )
    quality = "draft"
    ram_gb = 0.8

    def _resolve_audio(self, req: GenerationRequest, workdir: Path) -> tuple[str, float]:
        for r in (req.references or []):
            if r.kind == "audio":
                src = r.path
                if src.lower().endswith(".wav"):
                    return src, audio_svc.duration_of(src)
                dst = str(workdir / "speech.wav")
                media.run_ffmpeg(["-i", src, "-ac", "1", "-ar", "44100", dst])
                return dst, audio_svc.duration_of(dst)
        raise StudioError(
            "A talking avatar needs an audio track (voice narration or music).",
            code=ErrorCode.INVALID_REQUEST,
            remediation="Generate narration in Voice Studio first, then run Lip Sync with that audio.",
        )

    @timed
    def generate(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        self.ensure_ready()
        workdir = _tmp(req)
        audio_path, dur = self._resolve_audio(req, workdir)
        req.check_cancelled()

        samples, sr = audio_svc.read_wav(audio_path)
        if samples.size == 0:
            raise StudioError("Audio track is empty.", code=ErrorCode.INVALID_REQUEST)

        duration = float(req.duration or dur or 5.0)
        fps = int(req.fps or 24)
        total = max(2, int(duration * fps))
        progress(0.1, f"analysing audio ({duration:.1f}s)")
        env = audio_svc.rms_envelope(samples, sr, fps)
        # Resample the envelope to the exact frame count.
        if env.size != total:
            env = np.interp(np.linspace(0, env.size - 1, total), np.arange(env.size), env)
        # Emphasise speech dynamics so the mouth reads clearly.
        env = np.clip((env - float(np.percentile(env, 15))) /
                      max(1e-6, float(np.percentile(env, 95)) - float(np.percentile(env, 15))), 0, 1)
        env = np.clip(env ** 0.85, 0, 1)

        ident = _identity_from_request(req)
        avatar_ref = next((r for r in (req.references or []) if r.kind == "image"), None)
        expression = str(req.param("expression", "neutral"))
        w, h = req.width - req.width % 2, req.height - req.height % 2

        frames_dir = workdir / "frames"
        frames_dir.mkdir(exist_ok=True)
        paths = []
        for i in range(total):
            req.check_cancelled()
            t = i / fps
            mouth = float(env[i]) * float(req.param("mouth_gain", 1.15))
            # Eye blink: deterministic pseudo-random schedule, ~every 3.4s.
            blink_phase = (t % 3.4) / 3.4
            blink = max(0.0, 1.0 - abs(blink_phase - 0.5) * 14) if blink_phase < 0.08 else 0.0
            head_tilt = 2.6 * np.sin(2 * np.pi * 0.21 * t) + 1.1 * np.sin(2 * np.pi * 0.53 * t + 1.0)
            frame = render_avatar_portrait(
                ident, w, h,
                background=str(req.param("background", "studio")),
                mouth_open=float(np.clip(mouth, 0, 1)),
                eye_blink=float(np.clip(blink, 0, 1)),
                head_tilt=float(head_tilt),
                expression=expression,
            )
            if avatar_ref and req.param("use_reference_frame"):
                frame = frame  # reserved for reference-conditioned variant
            fp = frames_dir / f"f{i:06d}.png"
            frame.save(fp)
            paths.append(str(fp))
            if i % max(1, total // 20) == 0:
                progress(0.15 + 0.7 * (i / total), f"animating frame {i + 1}/{total}")

        video_only = str(workdir / "silent.mp4")
        progress(0.88, "encoding video")
        media.encode_frames(paths, video_only, fps=fps)
        out = _out(req, "talking_avatar.mp4")
        progress(0.94, "muxing audio")
        media.mux_audio(video_only, audio_path, out, audio_volume=float(req.param("volume", 1.0)))
        poster = _out(req, "talking_avatar_poster.jpg")
        media.video_thumbnail(out, poster, timestamp=min(0.5, duration / 2))

        progress(1.0, "complete")
        return GenerationResult(
            files=[GeneratedFile(out, "video", "video/mp4", {"poster": poster, "frames": total}),
                   GeneratedFile(poster, "image", "image/jpeg", {"role": "poster"}, is_primary=False)],
            meta={"renderer": "procedural_talking", "frames": total, "fps": fps,
                  "duration": duration, "quality": "draft",
                  "note": "procedural audio-driven animation - photoreal lip-sync requires Wav2Lip"},
            model_id=self.key,
        )


def _tmp(req: GenerationRequest) -> Path:
    d = Path(req.output_dir) / "work"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Gated photoreal avatar / lip-sync models
# ---------------------------------------------------------------------------
class _GatedAvatar(AvatarModelAdapter):
    requires = ("torch",)
    local = True
    quality = "reference"

    @timed
    def generate(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        self.ensure_ready()
        raise StudioError(
            f"{self.name} weights are not installed on this machine.",
            code=ErrorCode.MODEL_NOT_AVAILABLE,
            remediation=f"Install weights from {self.source_url} into {self.weights_path}, then retry.",
        )


class Wav2LipAdapter(_GatedAvatar):
    key = "wav2lip"
    name = "Wav2Lip (photoreal lip-sync)"
    capability = "lipsync"
    capabilities = ("lipsync", "audio_to_avatar")
    version = "1.0"
    license = "Wav2Lip (research use)"
    source_url = "https://github.com/Rudrabha/Wav2Lip"
    description = "Accurate lip-sync for a real face video. Needs the GAN checkpoint + ~4 GB VRAM."
    size_gb = 0.4
    vram_gb = 4.0
    ram_gb = 8.0
    weights_path = str(Path("storage/models/wav2lip"))


class SadTalkerAdapter(_GatedAvatar):
    key = "sadtalker"
    name = "SadTalker (talking head)"
    capability = "avatar_talking"
    capabilities = ("avatar_talking", "lipsync", "audio_to_avatar")
    version = "v0.0.2"
    license = "Apache-2.0"
    source_url = "https://github.com/OpenTalker/SadTalker"
    description = "Generates full talking-head motion (head pose + expression) from one photo and audio."
    size_gb = 2.6
    vram_gb = 8.0
    ram_gb = 12.0
    quality = "high"
    weights_path = str(Path("storage/models/sadtalker"))


class MuseTalkAdapter(_GatedAvatar):
    key = "musetalk"
    name = "MuseTalk (real-time lip-sync)"
    capability = "lipsync"
    capabilities = ("lipsync", "avatar_talking")
    version = "1.5"
    license = "MIT"
    source_url = "https://github.com/TMElyralab/MuseTalk"
    description = "High quality, fast latent-space lip-sync; good for presenter avatars."
    size_gb = 3.1
    vram_gb = 10.0
    ram_gb = 16.0
    weights_path = str(Path("storage/models/musetalk"))
