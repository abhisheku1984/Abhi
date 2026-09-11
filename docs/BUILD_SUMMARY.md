# Build summary — phases 1 to 7

Architecture decisions were settled in [PHASE0_ENVIRONMENT_REPORT.md](PHASE0_ENVIRONMENT_REPORT.md) before any code was written. This file records what was actually delivered.

**Result: 117 API routes, 17 engine adapters, 19 database tables, 15 frontend pages, 20/20 end-to-end smoke checks.**

---

## Phase 1 — Foundation & environment

* Inspected the machine first (OS, CPU, RAM, disk, Python, Node, Git, FFmpeg, GPU, ports, network reachability) and recorded it in the Phase-0 report.
* Chose **SQLite by default, PostgreSQL by `DATABASE_URL`**; **Redis optional**, database-backed queue when absent; **local storage by default, S3-compatible when configured**.
* Solved FFmpeg without root: the `imageio-ffmpeg` wheel ships a real static FFmpeg 7.0.2 (libx264/x265/vpx/aom/opus/mp3lame). Resolution order `FFMPEG_BINARY` → `PATH` → bundled binary → actionable 503.
* `core/`: settings (env-only, no hard-coded secrets), ULID ids, structlog JSON + rotating file, typed error hierarchy with friendly messages, PBKDF2 + JWT + RBAC.

## Phase 2 — Data layer

* 19 SQLAlchemy 2.0 models covering every entity in §25, with indexes on FKs and hot query paths.
* Dialect-aware session (SQLite WAL + FK pragmas; PostgreSQL pooling), `get_db` / `session_scope`.
* Alembic configured against the same metadata and `DATABASE_URL`.

## Phase 3 — Media layer (real work, no fakery)

* `media/ffmpeg.py` — binary resolver + ~20 real operations (probe, transcode, concat, trim, thumbnail, waveform, extract/mix/replace/normalise audio, fade, speed, reverse, GIF, sprite sheet, SRT burn-in, silence detection, loudness, filters).
* `media/image_ops.py` — Pillow/NumPy DSP for 22 image modes.
* `media/audio_ops.py` — sample-level music/SFX/voice synthesis plus FFmpeg I/O, resampling, mixing, loudness analysis.

## Phase 4 — Engine layer

* `engines/base.py` implements the §39 contract: `validate → estimate → generate → cancel → status`, plus `Capabilities` and `param_schema()`.
* `engines/registry.py` — registration, capability queries, DB sync, model status.
* 17 adapters across image, upscale, video, voice, audio, avatar and lip-sync in three tiers (A deterministic CPU, B local neural, C provider).
* Tier-A artifacts record `deterministic: true, diffusion: false, engine, seed`; synthetic voice additionally records `intelligible: false, placeholder: true`.

## Phase 5 — Job system & services

* `jobs/` — queue (claim, progress, complete, fail, cancel, retry, stale reaping), worker pool, SSE/WS events, and handlers for every job type.
* Composite jobs (story, workflow) create child jobs and await them **exactly once**.
* `services/` — assets (with provenance), story, editor, workflow, prompt engine, assistant.
* `safety/` — moderation, rights attestation for cloning, provenance manifests, optional watermark.
* `gpu/monitor.py` — `torch.cuda → nvidia-smi → remote agent → CPU-only`.

## Phase 6 — API & admin

* 18 routers, 117 routes (see [API.md](API.md)): generation, jobs, models, projects, assets, story, editor, workflows, prompts, characters, voices, avatars, assistant, settings, admin, health.
* RBAC enforced per route; audit log; feature flags; Prometheus metrics; rate limiting.
* Every error path returns *message + suggested action + error id* — never a stack trace.

## Phase 7 — Frontend, tooling & docs

* Vite + React + TypeScript + Tailwind, 15 pages, capability-driven controls, live job progress, honesty badges on every asset.
* PowerShell and shell twins for install/start/stop/reset/backup/restore/test/build/validate/deploy/healthcheck/GPU setup.
* `scripts/validate.py` writes `docs/VALIDATION_REPORT.md` and exits non-zero on failure (CI gate).
* `scripts/smoke.py` performs 20 real end-to-end checks against a live server.
* Documentation: architecture, user guide, API, database, model architecture, deployment, troubleshooting, this summary.

---

## Honesty rules, as enforced in code

| Rule | Enforcement |
|---|---|
| No fake generation | Every artifact is written to disk and its size/dimensions verified before the job can complete |
| No dead buttons | Every UI action resolves to a route; controls are generated from `param_schema()` |
| Real job ids | `jobs` row created before the response; status read only from the DB |
| Model not installed | `adapter.status()` → `ModelNotInstalledError` (409) + install instructions |
| Provider not configured | `ProviderNotConfiguredError` (409) + the env vars to set |
| Real FFmpeg | Actual subprocess calls; missing binary = 503 with install guidance |
| Real persistence | SQLAlchemy rows for every entity; no in-memory-only state |
| Real storage | Bytes on disk (or S3) with checksum, MIME and size validation |
| No hard-coded secrets | All config from env; `/settings/providers` reports status, never values |
| No model logic in UI | Capabilities and param schemas are API data |
| No silent success | Failures write structured logs and surface a user-facing error with an id |
| No surprise downloads | `AUTO_DOWNLOAD_MODELS=false`; installs require explicit confirmation |
