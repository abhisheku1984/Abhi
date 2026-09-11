# Model architecture

## The adapter contract (§39)

Every generation engine — CPU heuristic, local neural network, or remote HTTP provider — implements exactly one interface. Nothing else in the system knows how a model works.

```python
class BaseModelAdapter:
    id: str
    display_name: str
    family: str          # image | video | voice | audio | avatar | lipsync | upscale
    provider: str        # local | http | <vendor>
    license: str
    size_mb: int
    vram_mb: int
    speed: str
    is_local: bool
    ready_out_of_the_box: bool
    capabilities: Capabilities   # modes, max_resolution, supports_seed, max_references, …

    def status(self) -> dict          # installed | not_installed | not_configured + reason
    def validate(self, req) -> ValidationResult
    def estimate(self, req) -> Estimate
    def generate(self, req, ctx) -> list[Artifact]
    def cancel(self, ctx) -> None
    def param_schema(self) -> dict    # JSON schema the UI renders
```

Why this shape:

* **Validate before you pay.** `validate()` runs before a job is enqueued, so bad parameters never become failed jobs.
* **Estimate before you wait.** `estimate()` feeds ETA, credits and the VRAM warning shown before you press Generate.
* **Status is a first-class answer.** `status()` is how the UI can say *Model not installed* without guessing.
* **The UI is data-driven.** `param_schema()` and `capabilities` are served to the frontend, so a newly installed model appears with the right controls and no frontend change (§42.9).

## Three tiers

| Tier | Installed by default | Runs offline | Quality | Badge in the UI |
|---|---|---|---|---|
| **A — deterministic CPU** | ✅ yes | ✅ yes | Stylised, not photographic | `local-cpu · deterministic · not a diffusion model` |
| **B — local neural** | ❌ you install it | ✅ yes | Photoreal / natural | `diffusers` / `piper-tts` / `wav2lip` … |
| **C — provider** | ❌ you configure keys | ❌ needs network | Depends on provider | `provider · http` |

Tier A is what makes the product usable the minute you install it. It is real signal processing — spectral music synthesis, formant voice synthesis, procedural scene rendering, edge-aware upscaling — not a random-number generator. It is also **honestly labelled**: every asset stores `engine`, `deterministic: true`, `diffusion: false` and `seed`, and synthetic speech additionally stores `intelligible: false, placeholder: true`.

## Shipped adapters

### Tier A — always ready (7 of 17)

| Id | Family | Does |
|---|---|---|
| `local-cpu-image` | image | 22 modes: text→image, image→image, inpaint, outpaint, background remove/replace, style transfer, variations, depth/edge/sketch/pose maps, face restore, colourise, enhance |
| `local-cpu-upscale` | upscale | Lanczos/edge-aware 2×/4× with unsharp masking |
| `local-cpu-video` | video | 12 modes: text→video, image→video, first/last frame, extend, restyle, interpolate, story scene — encoded H.264 |
| `prosody-voice` | voice | Formant/Prosody synthesis, 14 languages, pitch/timbre/speed, SSML-free pacing |
| `local-audio-synth` | audio | Harmonic+percussion music engine (genre/mood/key/tempo/duration) and SFX engine |
| `local-avatar-rig` | avatar | Deterministic facial rig: viseme-driven talking avatar from portrait + audio |
| `local-viseme-lipsync` | lipsync | Viseme timing alignment for an existing clip |

### Tier B — real models, installed on request

| Id | Needs | Notes |
|---|---|---|
| `diffusers-image` | `diffusers`, `torch`, SD/SDXL weights (`DIFFUSERS_IMAGE_MODEL`) | text→image, inpaint, img2img, reference |
| `diffusers-video` | `diffusers`, `torch`, SVD/AnimateDiff weights (`DIFFUSERS_VIDEO_MODEL`) | text→video, img2video |
| `espeak-tts` | eSpeak NG binary or `py-espeak-ng` | intelligible speech, many languages |
| `voice-clone` | XTTS/Coqui weights (`VOICE_CLONE_MODEL`) | **requires rights attestation** (§28) |
| `neural-lipsync` | Wav2Lip checkpoint (`LIPSYNC_CHECKPOINT`) | high-quality lip sync |

### Tier C — providers, configured with keys

| Id | Env vars |
|---|---|
| `http-image-provider` | `IMAGE_API_BASE_URL`, `IMAGE_API_KEY`, `IMAGE_API_MODEL` |
| `http-video-provider` | `VIDEO_API_BASE_URL`, `VIDEO_API_KEY`, `VIDEO_API_MODEL` |
| `http-tts-provider` | `TTS_API_BASE_URL`, `TTS_API_KEY`, `TTS_API_MODEL` |
| `http-audio-provider` | `AUDIO_API_BASE_URL`, `AUDIO_API_KEY` |
| `http-avatar-provider` | `AVATAR_API_BASE_URL`, `AVATAR_API_KEY` |

All of these speak an OpenAI-compatible JSON/HTTP dialect; pointing them at OpenAI, a self-hosted gateway, Replicate, fal.ai or ElevenLabs is a configuration change, not a code change.

## Installation rules (§32, §43)

1. **Nothing downloads automatically.** `AUTO_DOWNLOAD_MODELS=false` and there is no code path that fetches weights on demand.
2. **Model Manager asks first.** The install dialog states the model, licence, size, disk and VRAM required, and asks for confirmation.
3. **Requirements are checked before downloading** — free disk, VRAM, and whether the extra Python dependencies (`requirements-gpu.txt`) are present. If they are missing you are told the exact command.
4. **Gated weights are manual.** Models needing an account or licence agreement show the steps instead of a fake progress bar.
5. **After install, test.** The `Test` action runs a tiny real generation through the adapter so you know it works before you rely on it.

```powershell
.\scripts\GPU-SETUP.ps1        # installs torch/diffusers/accelerate (opt-in, large)
```

## GPU requirements

| Workload | Minimum VRAM | Recommended | Notes |
|---|---|---|---|
| Tier A (all CPU engines) | none | 4 GB RAM | Runs anywhere |
| SD 1.5 / SDXL image | 6 GB | 12 GB | FP16, enable attention slicing on ≤8 GB |
| Video diffusion (SVD / AnimateDiff) | 12 GB | 24 GB | Long clips need offloading |
| Lip sync (Wav2Lip) | 4 GB | 8 GB | Fast even on CPU for short clips |
| TTS (Piper / XTTS) | 2 GB | 4 GB | XTTS voice clone wants 4 GB+ |

Detection order in `app/gpu/monitor.py`: `torch.cuda` → `nvidia-smi` → configured remote agent (`GPU_REMOTE_AGENT_URL`) → CPU-only. CPU-only is a supported, fully functional mode, and the UI says so instead of pretending a GPU exists.

## Adding your own engine

1. Create `backend/app/engines/<family>/<name>.py` with a `BaseModelAdapter` subclass and an `ADAPTERS` list.
2. Register it in `app/engines/registry.py::register_core_adapters()`.
3. Declare `capabilities` and `param_schema()` honestly — the UI is generated from them.
4. Add tests under `backend/tests/`.
5. Restart: the registry syncs the adapter into the `models` table, and it appears in Model Manager with the correct status.

No frontend change is required.
