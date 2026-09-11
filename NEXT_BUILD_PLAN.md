# NEXT_BUILD_PLAN.md

**Baseline:** empty repository (1 commit, `README.md` only).
**Rule for this plan:** it contains *only* the work that must be done next, in priority order, derived from the measured state. Nothing here assumes work that does not exist. Nothing here re-plans completed work.

**Governing principle for this project:** every capability is labelled by what it *actually does* —
`REAL` (neural model or true algorithm), `LOCAL` (real deterministic compute — Pillow/FFmpeg/numpy), `DEMO` (honest placeholder), `NOT_CONFIGURED` (adapter exists, credentials/GPU missing). The UI and API must never present `DEMO` as AI generation.

---

## PHASE 0 — FOUNDATION (this session, blocks everything else)

Target: **a running application with real, verifiable end-to-end functionality.**

0.1 Repo hygiene: `.gitignore`, `.env.example`, `README.md`, `Makefile`, `scripts/` (setup, dev, smoke-test).
0.2 Backend core: env-driven config, SQLite + migration runner, filesystem storage, structured logging, error envelope.
0.3 Auth: users table, scrypt password hashing, opaque bearer sessions, API keys, `require_auth` dependency.
0.4 Durable job queue: `jobs` table, async worker pool, progress, cancel, retry, logs, restart recovery.
0.5 Storage/asset layer: content-addressed files, sha256, MIME + media probing, thumbnails, static media serving.
0.6 Local real engines:
  * `media/images.py` — procedural generator (real PNG/JPEG), full edit engine (crop/resize/rotate/adjust/filters/watermark/format), thumbnailer, montage
  * `media/audio.py` — real WAV synthesis + waveform PNG + FFmpeg mux helpers
  * `media/video.py` — FFmpeg 7 engine: images→MP4 (Ken Burns, crossfade, captions), timeline render, audio mux, probe
0.7 Provider abstraction: capability registry + adapters (image: openai/stability/replicate/comfyui/a1111; video: replicate/runway/comfyui; tts: openai/elevenlabs/piper; lipsync: replicate/wav2lip; llm: openai/anthropic/ollama/template). Unconfigured ⇒ `not_configured`, never a silent fake.
0.8 API surface: `/api/health`, `/api/system/*`, `/api/auth/*`, `/api/projects`, `/api/assets`, `/api/jobs`, `/api/models`, `/api/generate/*`, `/api/edit/image`, `/api/characters`, `/api/stories`, `/api/shots`.
0.9 Frontend: React + TS + Vite SPA served from the API origin (single-port, proxy-safe): Dashboard, Projects, Assets, Image Studio, Video Studio, Avatar/Voice Studio, Story Studio, Jobs, Models & System. Typed API client, live job polling, real error surface.
0.10 Tests: pytest API + pipeline tests (real file output assertions) + shell smoke test.
0.11 Validation: boot server on `0.0.0.0`, run smoke test, confirm real `.png` and `.mp4` artifacts on disk, confirm SPA loads.

---

## PHASE 1 — CORE STUDIO FUNCTIONALITY (next, no external input required)

1.1 Character system wiring: reference images, seeds, style/prompt locking, character sheets, consistency injection into every shot prompt.
1.2 Story Studio: story → scenes → shot list with durations, editable shots, per-shot prompt/character binding, bulk "generate all images", storyboard grid view.
1.3 Storyboard → video: render the whole board into an MP4 with narration track and captions; per-shot regeneration.
1.4 Video editor: clip list, trim/order/duration, transitions, text overlays, audio bed, render endpoint (all FFmpeg-real), preview player.
1.5 Audio workspace: TTS job → asset, voice list per provider, audio trimming/normalisation, waveform display.
1.6 Workflow builder: node registry (mirrors the job kinds), DAG validation, saved workflows, run-with-variables, run history bound to jobs.
1.7 Model manager: per-capability provider selection, credential vault (write-only), connection test, capability matrix UI showing REAL/DEMO/NOT_CONFIGURED.
1.8 GPU/System panel: CPU/RAM/disk, ffmpeg presence, GPU detection when a GPU host is configured, remote GPU host registration (ComfyUI URL + token).

## PHASE 2 — AI INTEGRATION (requires one of: API keys, or a reachable GPU host)

2.1 Wire the first real image provider (whichever credential/GPU the user supplies) and validate a real generated image lands as an asset.
2.2 Real video provider (Replicate/Runway/ComfyUI Wan or AnimateDiff) validated end-to-end.
2.3 Real TTS provider validated into the narration track.
2.4 Real lip-sync provider validated (Replicate or local Wav2Lip/SadTalker on a GPU host).
2.5 Real LLM story generation with structured JSON shot output.

## PHASE 3 — POLISH & OPS

3.1 Pagination/virtualisation for large asset libraries; background thumbnail backfill.
3.2 Auth hardening (rate limits, session expiry UI, key rotation).
3.3 Docker image + `docker-compose` (app + optional Postgres/Redis) for GPU-host deployment.
3.4 Observability: job metrics, log retention, export bundle for bug reports.
3.5 Postgres option behind a DB URL abstraction (SQLite default stays).

---

## BLOCKERS (require the user — cannot be solved by code here)

| # | Blocker | Why it cannot be fixed in-repo | Options |
|---|---|---|---|
| B1 | **No GPU, 2 vCPU, 3.8 GiB RAM, 20 GB disk** | Neural diffusion/video/lip-sync inference physically cannot run | (a) point the app at a cloud GPU (RunPod/Vast/Lightning) running ComfyUI; (b) use paid cloud APIs (Replicate/Runway/OpenAI/Stability); (c) accept `DEMO`/`LOCAL` engines for now |
| B2 | **No API keys** | Paid/hosted inference needs credentials | Provide keys for any subset; app degrades per capability until then |
| B3 | **Multi-GB checkpoints** | Disk + VRAM insufficient | Keep models on the GPU host, not in this environment |
| B4 | **Product spec** | The repo contains no written spec; the feature matrix came from the mandate text | Confirm the mandate matrix is the authoritative feature list, or supply a spec doc |

**Everything in Phase 0 and Phase 1 is buildable without the user's input.** Phase 2 is where confirmation is genuinely required.
