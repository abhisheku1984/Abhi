"""Audio adapters: procedural music/SFX (runs now) + gated generative models."""
from __future__ import annotations

from pathlib import Path

from app.adapters.base import (
    AudioModelAdapter,
    GeneratedFile,
    GenerationRequest,
    GenerationResult,
    ProgressCallback,
    timed,
)
from app.core.errors import ErrorCode, StudioError
from app.services import audio as audio_svc


def _out(req: GenerationRequest, name: str) -> str:
    d = Path(req.output_dir)
    d.mkdir(parents=True, exist_ok=True)
    return str(d / name)


class ProceduralMusicAdapter(AudioModelAdapter):
    key = "procedural_music"
    name = "Procedural Music Composer (CPU)"
    capability = "music"
    capabilities = ("music", "background_music", "cinematic_music", "corporate_music",
                    "children_music", "ambient", "narration_bed")
    version = "1.2"
    license = "MIT (composer code) - output is royalty free"
    description = (
        "Composes real music beds (chords, bass, arpeggio, drums, reverb) as PCM audio. "
        "Deterministic and royalty free. Not a generative music model."
    )
    quality = "draft"
    ram_gb = 0.3

    @timed
    def generate(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        self.ensure_ready()
        genre = str(req.param("genre", "cinematic")).lower()
        duration = float(req.duration or 30.0)
        sr = int(req.param("sample_rate", 44100))
        seed = req.seed if req.seed is not None else (hash(req.prompt or genre) & 0xFFFF)
        progress(0.05, f"composing {genre}")
        y = audio_svc.generate_music(
            genre=genre, duration=duration, sr=sr,
            key=str(req.param("key", "C")), mood=str(req.param("mood", "")),
            tempo=req.param("tempo"), seed=int(seed),
            instruments=req.param("instruments"),
            progress=lambda p, m: progress(0.05 + 0.85 * p, m),
        )
        out = _out(req, f"music_{genre}_{int(duration)}s.wav")
        progress(0.93, "writing audio")
        audio_svc.write_wav(out, y, sr)
        progress(1.0, "complete")
        return GenerationResult(
            files=[GeneratedFile(out, "audio", "audio/wav",
                                 {"genre": genre, "duration": duration, "sample_rate": sr})],
            meta={"genre": genre, "duration": duration, "sample_rate": sr,
                  "renderer": "procedural", "royalty_free": True},
            seed=seed, model_id=self.key,
        )


class ProceduralSfxAdapter(AudioModelAdapter):
    key = "procedural_sfx"
    name = "Procedural Sound Effects (CPU)"
    capability = "sfx"
    capabilities = ("sfx", "ambient", "nature", "transitions", "foley")
    version = "1.0"
    license = "MIT (synthesiser code) - output is royalty free"
    description = "Synthesises real sound effects and ambience (whoosh, impact, rain, wind, birds, drones...)."
    quality = "draft"
    ram_gb = 0.2

    @timed
    def generate(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        self.ensure_ready()
        kind = str(req.param("sfx", req.param("kind", "whoosh"))).lower()
        duration = float(req.duration or 2.0)
        sr = int(req.param("sample_rate", 44100))
        seed = int(req.seed if req.seed is not None else 0)
        y = audio_svc.generate_sfx(kind, duration, sr, seed,
                                   progress=lambda p, m: progress(0.1 + 0.8 * p, m))
        out = _out(req, f"sfx_{kind}_{int(duration)}s.wav")
        audio_svc.write_wav(out, y, sr)
        progress(1.0, "complete")
        return GenerationResult(
            files=[GeneratedFile(out, "audio", "audio/wav", {"sfx": kind, "duration": duration})],
            meta={"sfx": kind, "duration": duration, "renderer": "procedural"},
            seed=seed, model_id=self.key,
        )


class MusicGenAdapter(AudioModelAdapter):
    key = "musicgen"
    name = "Meta MusicGen (local model)"
    capability = "music"
    capabilities = ("music", "background_music")
    version = "large"
    license = "CC-BY-NC-4.0"
    source_url = "https://github.com/facebookresearch/audiocraft"
    description = "Text-to-music transformer. Needs audiocraft and ~8 GB VRAM."
    requires = ("torch", "audiocraft")
    size_gb = 3.3
    vram_gb = 8.0
    ram_gb = 16.0
    quality = "high"
    weights_path = str(Path("storage/models/musicgen"))

    @timed
    def generate(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        self.ensure_ready()
        import torch  # noqa: WPS433
        from audiocraft.models import MusicGen  # type: ignore  # noqa: WPS433

        progress(0.15, "loading MusicGen")
        model = MusicGen.get_pretrained(str(req.param("variant", "facebook/musicgen-small")))
        model.set_generation_params(duration=float(req.duration or 20.0))
        progress(0.4, "generating music")
        wav = model.generate([req.prompt or "uplifting cinematic theme"])
        sr = model.sample_rate
        out = _out(req, "musicgen.wav")
        audio_svc.write_wav(out, wav[0].cpu().numpy().astype("float64"), sr)
        progress(1.0, "complete")
        return GenerationResult(files=[GeneratedFile(out, "audio", "audio/wav")],
                                meta={"renderer": "musicgen"}, model_id=self.key)


class AudioLDM2Adapter(AudioModelAdapter):
    key = "audioldm2"
    name = "AudioLDM 2 (local model)"
    capability = "sfx"
    capabilities = ("sfx", "ambient")
    version = "2.0"
    license = "CC-BY-NC-4.0"
    source_url = "https://github.com/haoheliu/AudioLDM2"
    description = "Text-to-audio diffusion for effects and ambience."
    requires = ("torch", "diffusers", "transformers")
    size_gb = 3.0
    vram_gb = 8.0
    ram_gb = 12.0
    quality = "high"
    weights_path = str(Path("storage/models/audioldm2"))

    @timed
    def generate(self, req: GenerationRequest, progress: ProgressCallback) -> GenerationResult:
        self.ensure_ready()
        raise StudioError(
            "AudioLDM 2 weights are not installed.",
            code=ErrorCode.MODEL_NOT_AVAILABLE,
            remediation="Install weights from https://huggingface.co/cvssp/audioldm2 into storage/models/audioldm2.",
        )
