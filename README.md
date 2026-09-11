# Abhi Studio

A local-first AI media studio: generate, edit, storyboard and render images, video and
voice from one application — with an honest label on every capability.

Everything runs as a single service on one port (API + UI), backed by SQLite and a
real FFmpeg/Pillow/NumPy media engine. No GPU is required to start; GPU and cloud
model access are opt-in through adapters.

---

## Quick start

```bash
make setup     # venv + dependencies + frontend build + .env
make run       # http://localhost:8000
```

Default credentials come from `ABHI_ADMIN_USER` / `ABHI_ADMIN_PASSWORD` in `.env`
(`admin` / `abhi-admin` if you copied `.env.example` unchanged — change it).

Then open the dashboard and press **Run pipeline self-test**. It generates three
images, a narration track, a rendered film and a narrated copy — real files in
`data/media`, no configuration required.

```bash
make test      # 20 backend tests (artifacts, not just HTTP codes)
make smoke     # live end-to-end verification against a running server
make help      # all targets
```

---

## What actually works today

This project refuses to blur the line between a demo and a model. Every capability
reports one of three modes, visible in the API and in the UI:

| Mode | Meaning |
|---|---|
| **`real`** | Working end-to-end right now. |
| **`demo`** | A real algorithm standing in for a neural model. Always labelled DEMO — never presented as AI. |
| **`not_configured`** | The adapter is implemented and waiting for an API key or a GPU host. |

**Real (no configuration needed):**

* Image editing — full non-destructive engine (crop, resize, rotate, tone, filters, vignette, grain, borders, text/watermarks, posterise, pixelate, contact sheets) with derived-asset lineage
* Video rendering & editing — Ken Burns motion, 20+ transitions, burned-in captions, trim/speed/scale, audio muxing, storyboard → film (FFmpeg 7, CPU)
* Audio mixing — real multi-track mixdown with per-track gain and normalisation
* Project / character / story / shot management, asset library, thumbnails, uploads, download, soft delete, lineage
* Durable job queue — priority, progress, live logs, cancel, retry, restart recovery

**Demo engines (real algorithms, honestly labelled):**

* Image generation — seeded procedural artwork (deterministic: same seed ⇒ identical bytes)
* Voice — formant speech synthesiser with six voice profiles, pitch and rate control
* Story structuring — deterministic beat-sheet template producing scenes, shots, framing, motion and shot prompts

**Adapters implemented, awaiting a key or a GPU host:**

* Images: OpenAI, Stability AI, Replicate (FLUX), ComfyUI, AUTOMATIC1111/Forge
* Video: Replicate (Wan/Kling), Runway Gen-3, ComfyUI (Wan/AnimateDiff)
* Voice: ElevenLabs, OpenAI Speech
* Story/LLM: OpenAI, Anthropic Claude, Ollama (local models)
* Lip sync: Replicate, local Wav2Lip, local SadTalker

Requesting an unconfigured capability fails with the exact environment variable
needed — it never fabricates output.

---

## Unlocking model-grade generation

Copy `.env.example` to `.env` and fill in any subset:

| Goal | Set |
|---|---|
| Photoreal images | `OPENAI_API_KEY`, `STABILITY_API_KEY` or `REPLICATE_API_TOKEN` |
| Your own GPU (SDXL/FLUX, Wan video) | `ABHI_COMFYUI_URL` or `ABHI_A1111_URL` |
| Neural narration | `ELEVENLABS_API_KEY` or `OPENAI_API_KEY` |
| LLM story writing | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` or `ABHI_OLLAMA_URL` |
| Lip sync | `ABHI_WAV2LIP_DIR`, `ABHI_SADTALKER_DIR` or `REPLICATE_API_TOKEN` |

The **System & Models** screen shows each provider, whether it is reachable, and
precisely what unlocks it.

---

## Architecture

```
frontend/            React 18 + TypeScript + Vite SPA (built into frontend/dist)
  src/api.ts         typed client; encodes the real/demo/not_configured contract

backend/app
  main.py            app factory: logging, boot, routers, SPA + media serving
  config.py          env-driven settings; everything optional, fail-soft
  db.py              SQLite (WAL) + migration runner; stdlib only
  security.py        scrypt passwords, revocable sessions, API keys
  storage.py         content-addressed media store, thumbnails, probing
  jobs.py            durable queue: 2 worker threads, progress/cancel/retry/logs
  pipelines.py       16 job handlers (the work)
  services.py        asset persistence, prompt composition, character locking
  providers/         base registry + local engines + cloud APIs + GPU hosts
  media/             images.py (Pillow) · audio.py (NumPy DSP) · video.py (FFmpeg)
  routes/            9 routers: system, auth, projects, assets, jobs, generate,
                     characters, stories, workflows
```

**Design decisions**

* **Single origin.** The API serves the built SPA, so one port covers everything —
  required by the preview proxy and simplest in containers. `make dev` runs Vite with
  a proxy for hot reload.
* **SQLite for both data and queue.** No Redis/Postgres to install; WAL + short
  transactions handle many readers and a small writer pool. A Postgres path is a
  single module if it is ever needed.
* **Fail-soft boot.** A missing GPU, FFmpeg or API key downgrades one capability and
  never stops the server.
* **Content-addressed media.** Identical output deduplicates on disk while keeping
  human-readable filenames.
* **Honesty as an API contract.** `/api/system/capabilities` returns the mode plus a
  `reason` and `requires[]` for every unavailable provider, which is what the UI
  renders directly.

---

## API sketch

```
POST /api/auth/login                 → bearer token
GET  /api/system/capabilities        → modes + per-provider requirements
POST /api/generate/image?wait=true   → {assets:[...]} real files on disk
POST /api/generate/story?wait=true   → story + shot list
POST /api/stories/{id}/generate      → bulk shot generation (links images to shots)
POST /api/stories/{id}/render?wait=true → rendered film + narrated mux
POST /api/generate/demo?wait=true    → full-stack self-test
GET  /api/jobs,  /api/jobs/{id}      → queue inspection, logs, cancel, retry
```

Interactive docs at `/api/docs` once running. API keys (created in **System &
Models**) allow scripted access via the `X-API-Key` header.

---

## Repository docs

* [`BUILD_STATUS.md`](BUILD_STATUS.md) — what works, what is demo, what is blocked, and how it was verified
* [`FAILURE_ANALYSIS.md`](FAILURE_ANALYSIS.md) — why this project previously had no implementation
* [`NEXT_BUILD_PLAN.md`](NEXT_BUILD_PLAN.md) — phased plan and external blockers

## Requirements

Python 3.10+, Node 18+ (build-time only). FFmpeg is bundled via `imageio-ffmpeg`, so
no system packages are needed. Optional: an NVIDIA GPU host running ComfyUI/A1111 for
local diffusion models.
