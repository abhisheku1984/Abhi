# AI Creative Studio

A complete, local-first, provider-agnostic generative media platform: **images, video, talking avatars, voices, music and sound effects** in one product, with a real job system, a real database, real FFmpeg, real file storage and a real model manager.

Everything the app produces is *actually produced*. There are no fake spinners, no placeholder buttons, no silent successes. If a model is not installed, the app says **"Model not installed"** and tells you how to install it. If a provider is not configured, it says **"Provider not configured"**.

---

## Quick start (Windows 10/11 — PowerShell)

```powershell
git clone <your-fork-or-this-repo> AI-Creative-Studio
cd AI-Creative-Studio

.\scripts\INSTALL.ps1          # one-time: venv + deps + .env files
.\scripts\START.ps1            # starts API :8000 and web app :5173
```

Open **http://localhost:5173** and sign in with the bootstrap admin from `backend/.env`:

```
admin@studio.ai  /  Admin@12345
```

> **Change that password immediately** (Admin → Users, or *Settings → Account → Password*). The server prints a warning at startup until you do.

### Quick start (Linux / macOS)

```bash
git clone <this-repo> AI-Creative-Studio && cd AI-Creative-Studio
./scripts/install.sh
./scripts/start.sh
```

---

## What is in the box

| Area | What works today |
|---|---|
| **Images** | text→image, image→image, inpainting, outpainting, background removal, style transfer, upscaling — all real pixel work |
| **Video** | text→video and image→video (real encoded MP4), shot concatenation, transitions, captions, re-encode, concat, trim, thumbnail extraction |
| **Voice** | deterministic voice-band synthesis with pitch/timbre/speed control, 14 languages, saved named voices, voice-clone adapter (gated) |
| **Audio** | music generation (genre/key/tempo/mood/duration) and sound effects, real WAV/MP3 output |
| **Avatars & lip-sync** | deterministic avatar frame rig, talking-avatar video from a portrait + audio, lip-sync pipeline adapter for Wav2Lip-class models |
| **Story mode** | prompt → scenes → shots → rendered video, per-shot regeneration, captions |
| **Editor** | timeline with clips, transitions, overlays, captions; renders through FFmpeg with progress |
| **Workflows** | saved node graphs (generate → upscale → voice → assemble) executed as real jobs |
| **Model Manager** | every engine registered with capabilities, size, VRAM, license, install/test/activate; **nothing downloads without your confirmation** |
| **Assistant** | plain-English commands ("make a 3-scene teaser with Hindi narration") that become real job plans |
| **Admin** | users, roles, permissions, audit log, queue, feature flags, system health, moderation settings |
| **Safety** | prompt moderation, rights attestation for voice cloning, C2PA-style provenance metadata, optional watermark |

---

## Honesty model — three engine tiers

The platform never pretends a model ran when it did not. Every engine belongs to a tier, and the UI always shows which tier produced an asset.

| Tier | Meaning | Example | Status you see |
|---|---|---|---|
| **A — built-in CPU** | Deterministic, dependency-free engines that run on any machine right now. Real signal processing, not random noise. | `local-cpu-image`, `local-cpu-audio` | **Ready** |
| **B — local neural** | Real diffusion/TTS/lip-sync models you install yourself (weights never auto-download). | `diffusers-sdxl`, `piper-tts`, `wav2lip` | **Model not installed** → install instructions |
| **C — provider** | Any OpenAI-compatible HTTP endpoint you configure with a key. | `http-image-provider`, `elevenlabs`-style TTS | **Provider not configured** → which env vars to set |

Tier-A artifacts carry `deterministic: true, diffusion: false, engine: local-cpu` in their metadata and are badged in the UI as *`local-cpu · deterministic · not a diffusion model`*. Synthetic voice tracks are additionally flagged `intelligible: false, placeholder: true` so nobody ships a “voice” that is really a formant sketch.

---

## Repository layout

```
AI-Creative-Studio/
├── backend/                 FastAPI application
│   ├── app/
│   │   ├── core/            config, ids, logging, errors, security
│   │   ├── db/              SQLAlchemy base, session, 19 entities
│   │   ├── media/           ffmpeg (20 wrappers), image_ops, audio_ops
│   │   ├── engines/         BaseModelAdapter + 17 adapters (image/video/voice/audio/avatar/lipsync)
│   │   ├── jobs/            queue, worker pool, events (SSE/WS), handlers
│   │   ├── services/        assets, story, editor, workflow, prompt_engine
│   │   ├── safety/          moderation, provenance
│   │   ├── api/v1/          18 routers (117 routes)
│   │   └── main.py
│   ├── tests/               pytest suite
│   ├── requirements.txt     CPU-only, installs in ~1 min
│   ├── requirements-gpu.txt opt-in torch/diffusers (never auto-installed)
│   └── .env.example
├── frontend/                Vite + React + TypeScript + Tailwind
│   └── src/                 15 feature pages, UI kit, api client, store
├── scripts/                 INSTALL/START/STOP/BACKUP/RESTORE/TEST/BUILD/VALIDATE/DEPLOY/HEALTHCHECK
│                            (.ps1 for Windows, .sh for Linux/macOS)
└── docs/                    architecture, user guide, API, database, models, deployment, validation
```

---

## Scripts

Every script exists twice: a `.ps1` for Windows and a `.sh` for Linux/macOS.

| Windows | Linux/macOS | Purpose |
|---|---|---|
| `INSTALL.ps1` (`-GPU`) | `install.sh` (`GPU=1`) | Create venv, install deps, seed `.env` files |
| `START.ps1` | `start.sh` | Start API (8000) + web app (5173) |
| `STOP.ps1` | `stop.sh` | Stop both |
| `RESET.ps1` | `reset.sh` | Wipe database and generated assets (keeps backups) |
| `BACKUP.ps1` | `backup.sh` | Archive DB + assets + config + logs |
| `RESTORE.ps1` | `restore.sh` | Restore an archive |
| `TEST.ps1` | `test.sh` | Backend pytest + frontend vitest |
| `BUILD.ps1` | `build.sh` | Production frontend build |
| `VALIDATE.ps1` | `validate.sh` | Full validation → `docs/VALIDATION_REPORT.md` |
| `DEPLOY.ps1` | `deploy.sh` | Build + serve in production mode |
| `HEALTHCHECK.ps1` | `healthcheck.sh` | Probe API, DB, queue, models, GPU, storage |
| `GPU-SETUP.ps1` | `gpu-setup.sh` | Install neural/GPU extras on demand |

---

## Configuration

All configuration lives in `backend/.env` (copy of `backend/.env.example`). Nothing is hard-coded and no secret ever reaches the browser.

Key choices:

* **Database** — SQLite by default (`backend/data/studio.db`). Set `DATABASE_URL=postgresql+psycopg://user:pass@host:5432/studio` to switch to PostgreSQL with no code change.
* **Storage** — local directory by default; `STORAGE_BACKEND=s3` for any S3-compatible bucket.
* **Queue** — database-backed by default; set `REDIS_URL` to use Redis.
* **FFmpeg** — `FFMPEG_BINARY` → `PATH` → bundled `imageio-ffmpeg` binary. If none is found the API returns an actionable error instead of pretending to render.
* **GPU** — detected via `torch.cuda` → `nvidia-smi` → remote agent → CPU-only. CPU-only is a supported mode, not an error.

See **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** for production hardening and **[docs/MODEL_ARCHITECTURE.md](docs/MODEL_ARCHITECTURE.md)** for installing real models.

---

## Verification

```bash
./scripts/validate.sh          # environment + tests + build + optional live smoke
./scripts/healthcheck.sh       # probe a running instance
python3 scripts/smoke.py       # 20 end-to-end checks against a running API
```

`scripts/validate.py` writes `docs/VALIDATION_REPORT.md` and exits non-zero on any failure, so it doubles as a CI gate.

---

## Documentation

* [docs/PHASE0_ENVIRONMENT_REPORT.md](docs/PHASE0_ENVIRONMENT_REPORT.md) — environment inspection, dependency plan, model plan, GPU requirements
* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system design, layer responsibilities, data flow
* [docs/USER_GUIDE.md](docs/USER_GUIDE.md) — how to use every screen
* [docs/API.md](docs/API.md) — REST + SSE reference
* [docs/DATABASE.md](docs/DATABASE.md) — schema and migration guide
* [docs/MODEL_ARCHITECTURE.md](docs/MODEL_ARCHITECTURE.md) — adapter contract and how to install real models
* [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — production, Docker, PostgreSQL, S3, backups
* [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — common errors and fixes
* [docs/BUILD_SUMMARY.md](docs/BUILD_SUMMARY.md) — what was built, phase by phase

---

## Requirements

* Python 3.11+ (3.12 works)
* Node.js 20+ (for the web UI)
* FFmpeg — auto-resolved; bundled fallback included, or install from https://ffmpeg.org
* Optional: NVIDIA GPU + `scripts/GPU-SETUP` for neural models
* RAM: 4 GB minimum for CPU engines; 8 GB+ (16 GB recommended) and 8 GB+ VRAM for diffusion models

## License

Apache-2.0 style project skeleton — see [LICENSE](LICENSE). Bundled FFmpeg builds are GPL/LGPL; check your provider and model licences before commercial use.
