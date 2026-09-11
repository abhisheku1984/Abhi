# Architecture

AI Creative Studio is a single FastAPI service plus a single React app. There are no microservices, no message broker requirement, and no mandatory cloud dependency — the default install runs entirely on one machine.

```
                 ┌──────────────────────────────────────────────┐
   browser  ───► │  React + Vite SPA          (frontend/)        │
                 │  15 pages · zustand store · react-query       │
                 └───────────────┬──────────────────────────────┘
                                 │  REST /api/v1  ·  SSE /ws  ·  /media
                 ┌───────────────▼──────────────────────────────┐
                 │  FastAPI application         (backend/app)    │
                 │                                              │
                 │  api/v1/  ──► services/  ──► engines/         │
                 │     │            │              │             │
                 │     │            │              ▼             │
                 │     │            │        media/ (ffmpeg,     │
                 │     │            │        image_ops,          │
                 │     │            │        audio_ops)          │
                 │     │            ▼                            │
                 │     │        db/ (SQLAlchemy 2.0)             │
                 │     │        storage/ (local | s3)            │
                 │     ▼                                         │
                 │  jobs/ queue + worker pool + events           │
                 │  safety/ moderation + provenance              │
                 │  gpu/   detection                             │
                 └──────────────────────────────────────────────┘
```

## Layers

### 1. `core/` — cross-cutting concerns

| Module | Responsibility |
|---|---|
| `config.py` | pydantic-settings `Settings`; every value from env/`.env`; derived helpers (`database_url`, `is_sqlite`, `storage_root`, `engine_allowlist`) |
| `ids.py` | ULID-based `new_id("img")`, `short_id()` — no reliance on filenames (§24) |
| `logging.py` | structlog JSON to stdout + rotating file at `backend/data/logs/studio.log` |
| `errors.py` | `StudioError` hierarchy + `register_exception_handlers`. Users get a friendly message, a suggested action and an error id; the stack trace only goes to the log (§37) |
| `security.py` | PBKDF2-SHA256 password hashing, JWT access/refresh, role→permission map, `get_current_user`, `require_permission` |

### 2. `db/` — persistence

* SQLAlchemy 2.0 declarative models, 19 tables (see [DATABASE.md](DATABASE.md)).
* Dialect-aware engine: SQLite gets WAL + `foreign_keys=ON`; PostgreSQL gets a pooled QueuePool.
* `Base` supplies a ULID string primary key, `created_at`/`updated_at`, and `to_dict()`.
* Alembic is configured (`backend/alembic.ini`) for environments that need managed migrations; SQLite development installs also auto-create tables on boot.

### 3. `media/` — the real work

* **`ffmpeg.py`** — resolves the binary (`FFMPEG_BINARY` → `PATH` → bundled `imageio-ffmpeg`) and wraps ~20 operations: probe, transcode, concat, trim, thumbnail, waveform, extract audio, mix/replace/normalise audio, fade, speed, reverse, GIF, sprite sheet, SRT burn-in, silence detection, loudness measurement, subtitles, filters. Every call raises a typed error with the FFmpeg stderr attached.
* **`image_ops.py`** — Pillow + NumPy DSP: latent-free scene synthesis, upscaling (Lanczos + unsharp), inpainting, outpainting, background removal/replacement, style transfer, colour grading, denoise, sharpen, blur, depth/edge/sketch/pose maps, face restore, colourisation, variation, enhancement, watermark.
* **`audio_ops.py`** — sample-level synthesis at 24–48 kHz: harmonic/percussion music engine with key, tempo, genre and mood; SFX engine; voice-band formant synthesis; plus FFmpeg-backed read/encode/resample/mix/normalise/trim and loudness analysis.

### 4. `engines/` — the adapter contract (§39)

```
BaseModelAdapter
├── id, display_name, family, provider, license, size_mb, vram_mb, speed
├── capabilities: Capabilities(modes, max_resolution, supports_seed, …)
├── status()    → installed | not_installed | not_configured + reason
├── validate()  → ValidationResult(errors, warnings, normalised params)
├── estimate()  → Estimate(seconds, vram_mb, credits, notes)
├── generate()  → [Artifact]   (yields progress through JobContext)
├── cancel()
└── param_schema() → JSON schema the UI renders generically
```

The registry (`registry.py`) owns a dict of adapter instances, syncs them into the `models` table at boot, and answers “which adapters can do mode *X*?”. Because capabilities and `param_schema()` are data, **the frontend contains no model-specific logic** (§42.9) — it renders whatever the registry describes.

17 adapters ship today across six families:

| Family | Adapters |
|---|---|
| image | `local-cpu-image`, `diffusers-image`, `http-image-provider`, `local-cpu-upscale` |
| video | `local-cpu-video`, `diffusers-video`, `http-video-provider` |
| voice | `prosody-voice`, `espeak-tts`, `http-tts-provider`, `voice-clone` |
| audio | `local-audio-synth`, `http-audio-provider` |
| avatar | `local-avatar-rig`, `http-avatar-provider` |
| lipsync | `local-viseme-lipsync`, `neural-lipsync` |

### 5. `jobs/` — the single source of truth for status (§22)

```
POST /generate/image  ──► queue.enqueue() ──► jobs row (queued)
                                                │
                          worker pool (N threads) polls  ──► handlers.run_job()
                                                │                │
                        events.publish(progress/…) ◄─────────────┘
                                                │
                          SSE /ws + REST /jobs/{id} ──► UI progress bar
```

* `queue.py` — enqueue, claim (with SKIP LOCKED semantics where supported), progress, complete, fail, cancel, retry, stale-job reaping, `elapsed_seconds`.
* `worker.py` — a fixed pool of threads; each worker loops `claim → dispatch → heartbeat`.
* `handlers.py` — maps `job.type` to a service call: image, video, voice, audio, avatar, lipsync, upscale, story, editor render, workflow. Long composite jobs (`story`, `workflow`) create child jobs and wait for them exactly once (`run_job_sync`), so no job ever executes twice.
* `events.py` — in-process pub/sub feeding SSE and WebSocket streams; no Redis required.

### 6. `services/` — business logic

`assets` (create/duplicate/upload/provenance), `story` (script → scenes → shots → render), `editor` (timeline → FFmpeg render graph), `workflow` (DAG execution), `prompt_engine` (structure/transform/translate), and `assistant` (natural-language → job plan).

### 7. `safety/` — §28

* `moderation.py` — blocklist + pattern checks on prompts and uploads; configurable from Settings → Moderation.
* `provenance.py` — C2PA-style manifest written into asset metadata (engine, seed, prompt hash, rights attestation, watermark flag) and an optional visible watermark.
* Voice cloning requires an explicit rights attestation (`REQUIRE_VOICE_CLONE_ATTESTATION=true`) and refuses without it.

### 8. `api/v1/` — 117 routes across 18 routers

Auth, users, projects (+versions), assets, generate, jobs, models, characters, voices, avatars, story, editor, workflows, prompts, assistant, settings, admin, health.

## Request lifecycle for a generation

1. **Auth** — JWT via `get_current_user`; RBAC via `require_permission("generate:image")`.
2. **Resolve engine** — explicit `engine` id, else the registry’s default for the family+mode.
3. **Moderate** — prompt/params through `safety.moderation`.
4. **Validate** — `adapter.validate(request)`; errors become 400s with per-field messages.
5. **Estimate** — used for ETA and credits shown in the UI before a job starts.
6. **Enqueue** — a `jobs` row is written and the id returned immediately (202-style). The client never blocks on generation.
7. **Execute** — a worker claims the job, calls `adapter.generate(ctx)`, and streams progress.
8. **Persist** — artifacts are written to storage, `assets` rows created with full metadata, `generations` row records provenance, `usage` increments.
9. **Notify** — SSE/WS events; the UI polls `/jobs/{id}` on reconnect.
10. **Fail honestly** — any exception becomes a typed `StudioError`: the job is marked `failed`, the UI shows *message + suggested action + error id*, and the traceback stays in the log.

## Storage layout

```
backend/storage/
├── images/   <yyyy>/<mm>/<ulid>.png
├── videos/   <yyyy>/<mm>/<ulid>.mp4
├── audio/    <yyyy>/<mm>/<ulid>.wav
├── avatars/  …
├── thumbs/   …
└── tmp/      work directories, cleaned after each job
```

Keys are stored in the DB (`assets.storage_key`) and served by `GET /api/v1/files/{key}`; with `STORAGE_BACKEND=s3` the same keys address objects in the bucket.

## Frontend architecture

* Vite dev server proxies `/api`, `/ws` and `/media` to `127.0.0.1:8000`, so browser code only ever uses relative URLs.
* `src/lib/api.ts` — typed fetch wrapper with auth header, refresh-on-401, and a `StudioApiError` that carries the friendly message, suggested action and error id.
* `src/app/store.ts` — zustand for session/UI state; react-query for server state, cache invalidation and SSE-driven updates.
* Capability-driven UI: pages ask `/models` and `/generate/modes` what is possible and render controls from `param_schema()`. No page hard-codes a model name.
* Every asset card carries an honesty badge: `local-cpu · deterministic · not a diffusion model` for Tier A, the real engine name for Tiers B/C, and `placeholder · not intelligible speech` for synthetic voice tracks.

## Failure philosophy

* No operation reports success without a verified artifact on disk.
* Missing model → `ModelNotInstalledError` → 409 + install instructions.
* Missing provider key → `ProviderNotConfiguredError` → 409 + which env vars to set.
* Missing FFmpeg → 503 + platform-specific install instructions.
* Nothing is swallowed: every failure writes a structured log line with the error id that the user was shown.
