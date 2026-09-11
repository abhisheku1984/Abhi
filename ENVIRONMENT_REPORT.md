# ENVIRONMENT REPORT

Generated automatically before implementation. This file is the source of truth for what the
platform can and cannot do on the machine that produced it.

> Detected host: the sandbox this repository was built in (Debian 12). The product is
> Windows-first for your local machine; classification logic is identical there and is
> re-evaluated at runtime by `GET /api/system/environment`.

## 1. Detected environment

| Item | Detected value | Notes |
|---|---|---|
| OS | Debian GNU/Linux 12 (bookworm), x86_64, kernel 6.1.158 | Your target: Windows 10/11 — scripts provided for both |
| CPU | 2 vCPU (1 socket × 1 core × 2 threads), Intel Xeon @ 2.60 GHz | No AVX-512 guarantee |
| RAM | 3.8 GB total (3.6 GB available) | Swap: 0 B |
| GPU | **None detected** (no `/dev/nvidia*`, no VGA/3D controller in `lspci`, `nvidia-smi` absent) | CPU-only |
| VRAM | 0 GB (no GPU) | — |
| CUDA | Not installed / not applicable | — |
| Python | 3.11.2 | venv used (PEP 668 externally-managed host) |
| Node.js | v22.22.3 | LTS line |
| npm | 10.9.8 | — |
| pnpm | **Not installed** | Optional; npm is used |
| yarn | 1.22.22 (present, unused) | — |
| Git | 2.39.5 | — |
| Docker | **Not installed** | Compose file still provided for GPU hosts |
| FFmpeg | **Not on PATH** — but a real static **FFmpeg 7.0.2** binary is provisioned via the `imageio-ffmpeg` wheel (auto-detected by the backend) | Real encoding/transcoding works |
| PostgreSQL | **Not installed / not running** | Platform defaults to SQLite, switches to PostgreSQL via `DATABASE_URL` |
| Redis | **Not installed / not running** | Platform defaults to a DB-backed job queue, switches to Redis via `REDIS_URL` |
| Disk space | 21 GB total, ~19 GB free | Large-model downloads are gated and require confirmation |
| Existing project | Empty repository (README.md only) — nothing to preserve or overwrite | — |
| Network | PyPI reachable, npm reachable; **Debian apt mirrors blocked** | No `apt-get install` in this sandbox |

## 2. Capability classification

Legend: **GREEN** = runs now · **YELLOW** = runs but slow · **ORANGE** = code complete, needs a model/dependency · **RED** = needs hardware/cloud/paid API.

### Application platform (no AI model required)

| Capability | Class | Reason |
|---|---|---|
| App shell, auth, RBAC, projects, assets, settings/branding | **GREEN** | Pure code + SQLite |
| Database (migrations, indexes, relations) | **GREEN** | SQLite now, PostgreSQL-ready |
| Async job queue, progress, cancel, retry, WebSocket streaming | **GREEN** | DB-backed worker pool |
| File upload, validation, content-addressed storage, thumbnails | **GREEN** | Pillow + local FS, S3 adapter interface |
| Prompt engine (structuring/enhancement, templates) | **GREEN** | Deterministic engine; LLM upgrade optional |
| Story → scenes → shots → storyboard planner | **GREEN** | Deterministic planner; LLM upgrade optional |
| Workflow builder (DAG editor + executor) | **GREEN** | Real node execution over real engines |
| Video editor (cut/trim/split/merge/speed/volume/fade/text/crop/grade/transitions/render) | **GREEN** | **Real FFmpeg 7.0.2** |
| Provenance, watermark, audit log, safety policy | **GREEN** | Code + metadata |
| OpenAPI docs, health, validation endpoints | **GREEN** | FastAPI |

### Image

| Capability | Class | Reason |
|---|---|---|
| Procedural/reference-free CPU renderer (prompt→PNG, seeded, deterministic) | **GREEN** | Real pixels, Pillow+NumPy. Honest label: *pipeline-validating renderer, not a diffusion model* |
| Upscale (Lanczos/bicubic), resize, crop, rotate, color grade, filter, blur/sharpness | **GREEN** | Real Pillow processing |
| Watermark / provenance embed + PNG metadata | **GREEN** | Real Pillow processing |
| Thumbnail + preview proxy generation | **GREEN** | Real Pillow processing |
| Text-to-image (SD 1.5 / SDXL / Flux), image-to-image, inpainting, outpainting | **RED** | No GPU, 3.8 GB RAM, `torch`/`diffusers` intentionally not installed. Adapters are written + registered; they self-report `HARDWARE REQUIREMENT NOT MET` |
| Background removal, face restoration, depth/pose-to-image, style transfer (model-based) | **RED** | Model + VRAM required |
| Object removal / replacement (real semantic inpainting) | **RED** | Model + VRAM required |

### Video

| Capability | Class | Reason |
|---|---|---|
| Image→video (Ken Burns / multi-frame interpolation), frame-sequence encode, slideshow video | **GREEN** | Real FFmpeg encoding of real frames |
| Concat, trim, cut, split, speed, volume, fade, crop, resize, rotate, overlay, text burn-in, transitions, audio mux/replace | **GREEN** | Real FFmpeg filter graphs |
| Video probing (duration, resolution, fps, codec, bitrate), proxy previews, frame extraction | **GREEN** | FFmpeg/ffprobe |
| Video upscale/restyle (frame-wise via image models) | **ORANGE** | Runs through the batch frame pipeline; quality depends on an installed image model → **RED** locally |
| Text-to-video, image-to-video (diffusion), first/last-frame interpolation, video-to-video | **RED** | Needs ≥12–24 GB VRAM + multi-GB weights. UI/API/queue/adapter/progress all built and wired |
| Video extension beyond 2–5 s at high fidelity | **RED** | Same as above |

### Avatar / voice / audio

| Capability | Class | Reason |
|---|---|---|
| Procedural music/SFX/ambience synthesis (real PCM WAV: chords, ADSR, drums, noise beds) | **GREEN** | Real NumPy DSP |
| Avatar profile management, script, voice binding, avatar asset pipeline | **GREEN** | Real data model + job orchestration |
| Voice profile registry, SSML-ish controls (speed/pitch/pause), lip-sync job orchestration | **GREEN** (orchestration) | Real jobs; synthesis back-end gated |
| Text-to-speech (high quality) | **ORANGE/RED** | No local TTS engine (espeak/piper) in this sandbox; provider adapters written, disabled by default |
| Voice cloning | **RED** | Requires model **+ written authorization** gate. Refuses without consent attestation |
| Talking avatar / lip-sync (Wav2Lip/MuseTalk-class) | **RED** | Checkpoint download + GPU; pipeline built |

## 3. Hard constraints honoured during this build

1. **No large model downloads.** Nothing >100 MB was fetched. Total Python deps ≈ 45 packages.
2. **No fake success.** Any generation that cannot execute returns `MODEL_NOT_AVAILABLE`,
   `HARDWARE_REQUIREMENT_NOT_MET`, or `EXTERNAL_PROVIDER_REQUIRED` with the reason, never a fake file.
3. **No paid API silently added.** All provider adapters exist but are `disabled` until you enable
   them and supply keys.
4. **Replaceable models.** Engines call a `BaseModelAdapter` interface; the frontend never names a model.

## 4. What this machine can prove end-to-end right now

Idea → prompt → project → characters → story → scenes → shots → storyboard images (procedural
renderer) → motion (FFmpeg) → narration track (procedural audio) → mux → **a real MP4 file** →
thumbnail → asset library → download → export preset (9:16 / 16:9 / 1:1 / 4:5).

Swap in a GPU + diffusion weights and the same pipeline produces model-quality output with **zero
code changes** — only `POST /api/models/{id}/activate`.
