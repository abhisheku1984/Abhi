"""Local audio adapter — real DSP synthesis of music, ambience and SFX (§17)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.errors import StudioError
from app.core.logging import get_logger
from app.engines.base import (
    Artifact, BaseModelAdapter, Capabilities, Estimate, GenerateRequest, JobContext, ValidationResult,
)
from app.media import audio_ops, ffmpeg

log = get_logger("engines.audio.local")

GENRES = sorted(audio_ops.GENRE_PRESETS.keys())
SFX_KINDS = ["whoosh", "transition", "swish", "impact", "hit", "boom", "riser", "tension", "pop", "click",
             "ui", "nature", "ambient", "wind", "water", "rain", "crowd", "city", "magic", "sparkle",
             "footstep", "step"]


class LocalSynthAudioAdapter(BaseModelAdapter):
    id = "local-audio-synth"
    display_name = "Local Audio Synthesiser (music, ambience, SFX)"
    version = "1.0.0"
    family = "audio"
    provider = "local"
    license = "Apache-2.0 (platform code)"
    size_mb = 0
    vram_mb = 0
    speed = "fast"
    is_local = True
    ready_out_of_the_box = True
    notes = "Deterministic synthesiser: chords, arpeggios, bass, percussion, ambience and SFX — all real DSP."
    capabilities = Capabilities(
        family="audio",
        modes=["background-music", "cinematic-music", "kids-music", "corporate-music", "ambient",
               "nature-sounds", "sound-effects", "transitions", "voice-narration", "audio-enhancement"],
        supports_seed=True,
        supports_duration=True,
        supports_voice=True,
        max_duration_sec=600,
    )

    def param_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "genre": {"type": "string", "enum": GENRES, "default": "cinematic"},
                "duration": {"type": "number", "minimum": 1, "maximum": 600, "default": 30},
                "key": {"type": "string", "enum": ["C", "D", "E", "F", "G", "A", "B"], "default": "C"},
                "sfx": {"type": "string", "enum": SFX_KINDS, "default": "whoosh"},
                "sfx_count": {"type": "integer", "minimum": 1, "maximum": 12, "default": 1},
                "loop": {"type": "boolean", "default": True},
                "volume": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.9},
            },
        }

    def validate(self, request: GenerateRequest) -> ValidationResult:
        result = ValidationResult()
        dur = float(request.params.get("duration", 30) or 30)
        if dur > 600:
            result.add_error("Maximum audio length is 600 seconds.")
        if request.mode == "audio-enhancement" and not any(r.kind == "audio" for r in request.references):
            result.add_error("Attach an audio file to enhance.")
        return result

    def estimate(self, request: GenerateRequest) -> Estimate:
        dur = float(request.params.get("duration", 30))
        return Estimate(seconds=round(1.0 + dur * 0.05, 2), credits=round(dur * 0.02, 2))

    def generate(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        self._require_mode(request)
        mode = request.mode
        seed = request.seed
        duration = float(request.params.get("duration", 30) or 30)

        if mode == "audio-enhancement":
            return self._enhance(request, ctx)
        if mode == "voice-narration":
            return self._narration(request, ctx, duration, seed)
        if mode in ("sound-effects", "transitions", "nature-sounds"):
            return self._sfx(request, ctx, mode, seed)
        return self._music(request, ctx, mode, duration, seed)

    # ------------------------------------------------------------------ #

    def _music(self, request: GenerateRequest, ctx: JobContext, mode: str, duration: float, seed: int) -> list[Artifact]:
        genre = str(request.params.get("genre") or ("kids" if mode == "kids-music"
                                                    else "corporate" if mode == "corporate-music"
                                                    else "ambient" if mode == "ambient"
                                                    else "cinematic")).lower()
        if genre not in audio_ops.GENRE_PRESETS:
            genre = "cinematic"
        ctx.progress(30, f"synthesising {genre} music")
        samples = audio_ops.generate_music(genre=genre, seconds=duration, key=str(request.params.get("key", "C")),
                                           seed=seed)
        samples = audio_ops.normalize(samples, target=float(request.params.get("volume", 0.9)))
        wav = audio_ops.write_wav(ctx.path(f"music_{ctx.job_id}.wav"), samples)
        out = ctx.path(f"music_{ctx.job_id}.mp3")
        audio_ops.encode(wav, out)
        return [self._artifact(out, "audio/mpeg", request, mode,
                               {"genre": genre, "duration": duration, "seed": seed, "key": request.params.get("key", "C")})]

    def _sfx(self, request: GenerateRequest, ctx: JobContext, mode: str, seed: int) -> list[Artifact]:
        kind = str(request.params.get("sfx") or ("nature" if mode == "nature-sounds" else
                                                 "whoosh" if mode == "transitions" else "impact")).lower()
        count = max(1, min(12, int(request.params.get("sfx_count", 1))))
        seconds = float(request.params.get("duration", 2) or 2)
        clips = [audio_ops.generate_sfx(kind, seconds=seconds, seed=seed + i * 17) for i in range(count)]
        ctx.progress(45, "synthesising effects")
        mixed = audio_ops.mix_tracks(clips) if count > 1 else clips[0]
        wav = audio_ops.write_wav(ctx.path(f"sfx_{ctx.job_id}.wav"), mixed)
        out = ctx.path(f"sfx_{ctx.job_id}.mp3")
        audio_ops.encode(wav, out)
        return [self._artifact(out, "audio/mpeg", request, mode,
                               {"sfx": kind, "count": count, "seconds": seconds, "seed": seed})]

    def _narration(self, request: GenerateRequest, ctx: JobContext, duration: float, seed: int) -> list[Artifact]:
        text = str(request.params.get("text") or request.prompt or "")
        if not text.strip():
            raise StudioError("Enter narration text.", suggested_action="Type the script you want narrated.")
        words = max(1, len(text.split()))
        duration = duration or max(2.0, words / 2.5)
        ctx.progress(35, "synthesising narration")
        samples = audio_ops.generate_voice_track(text, duration, seed=seed,
                                                 voice=str((request.voice or {}).get("name", "narrator")).lower())
        wav = audio_ops.write_wav(ctx.path(f"narration_{ctx.job_id}.wav"), samples)
        out = ctx.path(f"narration_{ctx.job_id}.mp3")
        audio_ops.encode(wav, out)
        return [self._artifact(out, "audio/mpeg", request, "voice-narration",
                               {"text": text, "duration": duration, "intelligible": False, "placeholder": True})]

    def _enhance(self, request: GenerateRequest, ctx: JobContext) -> list[Artifact]:
        ref = next((r for r in request.references if r.kind == "audio"), None)
        if ref is None:
            raise StudioError("Attach an audio file to enhance.", suggested_action="Upload a WAV or MP3 file.")
        ctx.progress(35, "enhancing audio")
        samples, sr = audio_ops.read_audio(ref.path)
        if samples.ndim > 1:
            samples = samples.mean(axis=1)
        enhanced = audio_ops.highpass(samples, 80, sr)
        enhanced = audio_ops.normalize(enhanced, target=0.92)
        enhanced = audio_ops.lowpass(enhanced, min(16000, sr / 2 - 100), sr)
        # gentle compression: soft-knee limiting of peaks
        enhanced = np_tanh_limit(enhanced)
        wav = audio_ops.write_wav(ctx.path(f"enhanced_{ctx.job_id}.wav"), enhanced, sr)
        out = ctx.path(f"enhanced_{ctx.job_id}.mp3")
        audio_ops.encode(wav, out)
        return [self._artifact(out, "audio/mpeg", request, "audio-enhancement", {"sample_rate": sr})]

    def _artifact(self, path: str, mime: str, request: GenerateRequest, mode: str, extra: dict) -> Artifact:
        info = ffmpeg.probe(path)
        return Artifact(
            path=path, kind="audio", mime=mime, name=Path(path).stem,
            meta={"engine": self.id, "engine_name": self.display_name, "mode": mode,
                  "deterministic": True, "diffusion": False, "duration": info.get("duration"),
                  "prompt": request.prompt, "params": request.params, **extra},
        )


def np_tanh_limit(x):
    import numpy as np

    return np.tanh(x * 1.25) * 0.95


ADAPTERS = [LocalSynthAudioAdapter()]
