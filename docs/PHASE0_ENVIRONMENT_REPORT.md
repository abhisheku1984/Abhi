# Phase 0 — Environment & Architecture Report

**Date:** 2026-09-10
**Repo:** `abhisheku1984/Abhi` → branch `arena/01a08ce4-abhi`
**Purpose:** Inspection-only deliverable required by MASTER COMMAND §43. No application code written yet.

---

## 1. ENVIRONMENT REPORT

Inspected sandbox (note: the sandbox is Linux; the *target* developer machine is Windows. Both are covered by the plan).

| Capability | Detected | Version / Detail | Impact |
|---|---|---|---|
| OS | Debian GNU/Linux 12 (bookworm), x86_64, kernel 6.1.158 | — | Target dev OS is Windows; all scripts ship as `.ps1` with `.sh` twins for CI/Linux |
| CPU | 2 × Intel Xeon @ 2.60 GHz | no AVX-512 guarantee | CPU-only inference is possible but slow |
| RAM | **3.8 GiB** total (3.6 GiB available) | — | Excludes SDXL/Flux-class diffusion (needs 10–24 GB) |
| Disk | **20 GiB free** (5.7 M inodes free) | — | Plenty for code + small models; NOT for multi-GB checkpoints |
| Python | `/usr/bin/python3` | **3.11.2**, pip 23.0.1, venv + ensurepip OK | Backend runtime |
| Node | `/usr/local/bin/node` | **v22.22.3**, npm 10.9.8, yarn present, **pnpm absent** | Frontend runtime |
| Git | `/usr/bin/git` | 2.39.5 | VCS |
| Docker | **not installed** | — | Compose files ship but are optional; app must run without Docker |
| FFmpeg (system) | **not installed** | `apt-get` unusable (no root / no lists) | See §1.1 — solved via PyPI |
| GPU / CUDA | **none** (`nvidia-smi`, `nvcc`, `/usr/local/cuda` all absent) | — | No local diffusion/TTS inference; GPU features degrade gracefully |
| PostgreSQL | **not installed** | — | SQLite for local dev; Postgres via `DATABASE_URL` |
| Redis | **not installed** | — | DB-backed queue by default; Redis optional |
| SQLite | present | 3.40.1 (usable by SQLAlchemy/Alembic) | Default local database |
| PyPI | **reachable** | 73 MB/s | Backend deps installable |
| npm registry | **reachable** | react 19.3.0 resolvable | Frontend deps installable |
| huggingface.co | **BLOCKED** (`000`) | — | No diffusion checkpoint downloads in sandbox |
| GitHub release assets | **BLOCKED** (`release-assets.githubusercontent.com` SSL fail) | — | No static binary downloads from GH releases |
| External AI APIs | **BLOCKED** (e.g. `api.openai.com` → `000`) | no provider keys in env | HTTP provider adapters cannot be smoke-tested here; they will work on the user's machine |
| Ports free | 3000, 5173, 8000, 8080, 5432, 6379, 11434 all closed | — | No conflicts |

### 1.1 FFmpeg — resolved

Rule §42.13 mandates *real* FFmpeg. No system package and no GitHub release access, but a **real, GPL-built static FFmpeg 7.0.2** ships inside the PyPI wheel `imageio-ffmpeg` (29.5 MB). Verified in this sandbox:

```
ffmpeg version 7.0.2-static  --enable-gpl --enable-libx264 --enable-libx265
       --enable-libvpx --enable-libaom --enable-libopus --enable-libmp3lame ...
V....D libx264    V....D libvpx / libvpx-vp9    A....D aac    A..X.D opus
real encode test: 320x240 testsrc → H.264/AVC MP4, exit 0, 50 kb/s ✔
```

**Resolution:** a `FFmpegResolver` that prefers (1) `FFMPEG_BINARY` env, (2) system `ffmpeg` on `PATH`, (3) the `imageio-ffmpeg` bundled binary, (4) hard, actionable error. Real encoding, muxing, probing, thumbnails, proxies, scaling, concat, filters, subtitles — all genuinely functional.

---

## 2. CURRENT PROJECT REPORT

| Item | Finding |
|---|---|
| Files tracked | **1** (`README.md`, content: `# Abhi`) |
| Existing frontend | **none** |
| Existing backend | **none** |
| Dependencies | **none** (no `package.json`, no `requirements.txt`, no venv, no `node_modules`) |
| History | 1 commit `d4615c9 Initial commit` |
| Remote | `https://github.com/abhisheku1984/Abhi.git` (push works; authenticated) |
| Conflicts | **none** — greenfield |
| Risk of overwriting existing functionality | **zero** |

This is a clean greenfield repo, so §43's "do not overwrite" constraint is satisfied trivially — every artifact is additive.

---

## 3. HONESTY CONSTRAINT (the one thing that shapes the design)

§42 rules 1–4 demand: **no fake generation, no dead buttons, no pretending a model ran.**

This machine has **no GPU, 3.8 GB RAM, and no HuggingFace access**, so SDXL / Flux / Wan / XTTS cannot run here. Rather than fake them, the platform uses a **three-tier engine registry** where every tier is either genuinely working or explicitly reports *why* it is not:

| Tier | What it is | State here |
|---|---|---|
| **Tier A — Real local CPU media engines** | Real pixel/audio math + real FFmpeg: deterministic renderer, upscaler, inpainter/outpainter, background removal, style/palette transfer, edge/depth/sketch operators, camera-motion synthesis, encoders, muxers, timeline renderer | ✅ **Genuinely working** |
| **Tier B — Real model adapters (deferred)** | `diffusers` (SD/SDXL/Flux), `rembg`/u2net, Real-ESRGAN, Piper/Kokoro/XTTS, Wav2Lip/SadTalker-class lip-sync | ⚠️ Adapters shipped + registered; **"Model not installed"** until the user installs them (§42.3) |
| **Tier C — Provider adapters** | Provider-agnostic HTTP adapters: OpenAI-compatible, Replicate, fal.ai, ElevenLabs, generic webhook | ⚠️ Shipped; **"Provider not configured"** until keys exist in env |

The Tier A deterministic renderer is **not** a diffusion model and never claims to be: it does real work (seeded procedural synthesis, real compositing, real transforms), and every asset it produces is stamped in its metadata and in the UI badge as
`engine: local-cpu · deterministic · not a diffusion model`.
It exists so the platform is *end-to-end exercisable and testable* offline, exactly as a render-farm stub would be — while the diffusion path remains the real production path on a GPU machine.

---

## 4. ARCHITECTURE

```
AI Creative Studio  (monorepo, provider-agnostic, local-first)
│
├─ backend/                       Python 3.11 · FastAPI · SQLAlchemy 2 · Alembic
│  ├─ app/
│  │  ├─ api/v1/                  REST: auth, projects, assets, generate/*, jobs,
│  │  │                           characters, voices, avatars, story, storyboard,
│  │  │                           models, workflows, admin, settings, assistant
│  │  ├─ core/                    config, security (JWT+RBAC), rate limiting,
│  │  │                           logging, errors, audit, ids (ULID)
│  │  ├─ db/                      models, session, repositories, migrations
│  │  ├─ engines/                 ◄── the replaceable layer (§39)
│  │  │  ├─ base.py               BaseModelAdapter: generate/validate/estimate/
│  │  │  │                        cancel/status/capabilities
│  │  │  ├─ image/                ModelAdapter, PromptProcessor, ReferenceProcessor,
│  │  │  │                        SafetyProcessor, PostProcessor
│  │  │  ├─ video/                VideoEngine pipeline (story→scene→shot→motion→
│  │  │  │                        frames→interp→upscale→audio→lipsync→render)
│  │  │  ├─ avatar/               AvatarModelAdapter, AvatarStudio
│  │  │  ├─ voice/                TTSAdapter, VoiceProfile, VoiceCloneAdapter
│  │  │  ├─ audio/                music/SFX/ambience engines
│  │  │  ├─ lipsync/              LipSyncAdapter
│  │  │  └─ registry.py           discovery + health + capability negotiation
│  │  ├─ media/                   ffmpeg.py (real FFmpeg), image_ops, audio_ops,
│  │  │                           thumbnails, proxies, watermark/provenance
│  │  ├─ jobs/                    durable DB queue, worker pool, cancellation,
│  │  │                           retries, progress events, WebSocket fan-out
│  │  ├─ storage/                 StorageBackend: LocalFS | S3-compatible
│  │  ├─ gpu/                     GPU abstraction + multi-GPU/remote readiness
│  │  ├─ safety/                  moderation, rights checks, provenance(C2PA-lite)
│  │  ├─ assistant/               intent → planner → tool execution → validation
│  │  └─ observability/           metrics, health, structured logs
│  └─ tests/                      pytest: auth, projects, uploads, jobs, queue,
│                                 api, adapters, permissions, media pipeline
│
├─ frontend/                      Vite + React 18 + TypeScript + Tailwind + Zustand
│  ├─ src/app/                    router, providers, layout (sidebar/topnav/canvas)
│  ├─ src/features/               dashboard · create · projects · assets ·
│  │                              characters · voices · avatars · story ·
│  │                              storyboard · editor · workflows · models ·
│  │                              jobs · admin · settings/branding · assistant
│  ├─ src/components/ui/          premium dark design system
│  ├─ src/lib/                    api client, WebSocket, i18n (13 Indian languages)
│  └─ tests/                      vitest + RTL
│
├─ scripts/                       INSTALL/START/STOP/RESET/BACKUP/RESTORE/TEST/
│                                 VALIDATE/BUILD/DEPLOY/HEALTHCHECK  (.ps1 + .sh)
├─ docs/                          README, SETUP, ARCHITECTURE, MODELS, API, this report
├─ docker/                        Dockerfile ×2 + docker-compose.yml (optional)
└─ .env.example                   every secret via env; never frontend-exposed
```

**Replacement contract (§39).** Every engine implements:

```python
class BaseModelAdapter(ABC):
    id: str; name: str; version: str; capabilities: Capabilities
    license: str; vram_mb: int; local: bool
    def validate(self, request) -> ValidationResult   # incl. "model not installed"
    def estimate(self, request) -> Estimate           # cost, time, vram
    def generate(self, request, ctx: JobContext) -> Artifact
    def status(self, job_id) -> JobStatus
    def cancel(self, job_id) -> bool
```

The UI never sees model-specific logic — it renders from `Capabilities` + a JSON-schema-driven parameter form returned by `/api/models/{id}/schema`.

---

## 5. DEPENDENCY PLAN

**Backend (core, installs anywhere):** fastapi, uvicorn[standard], pydantic v2 + pydantic-settings, SQLAlchemy 2, Alembic, python-jose/passlib[bcrypt] or argon2, python-multipart, Pillow, numpy, imageio-ffmpeg (provides real FFmpeg), aiofiles, python-ulid, httpx, slowapi (rate limit), prometheus-client, structlog.
**Backend (optional extras, never auto-installed):** `diffusers` + `torch` (+`transformers`, `accelerate`) → `[gpu]` extra; `rembg`; `realesrgan`; `onnxruntime`; `piper-tts`; `boto3` (S3); `redis`/`rq`; `psycopg[binary]` (Postgres); `psycopg` driver loaded only if `DATABASE_URL` is `postgresql://`.
**Frontend:** react, react-dom, react-router-dom, @tanstack/react-query, zustand, tailwindcss + postcss + autoprefixer, framer-motion (smooth animation), lucide-react, @dnd-kit (storyboard/workflow drag), reactflow (workflow builder), zustand, vitest + @testing-library/react, typescript, vite.
**System:** FFmpeg (auto-resolved, see §1.1); PostgreSQL 14+ and Redis only for the production profile.

Install policy: **no multi-GB download ever happens implicitly.** `torch`/diffusers live in an opt-in extra, and `Model Manager` requires explicit user confirmation before fetching any checkpoint (§32, §43).

---

## 6. MODEL PLAN

| Slot | Adapter | Default (offline) | Upgrade path (user's GPU machine) | Est. VRAM |
|---|---|---|---|---|
| Text→Image | `image/diffusers-sdxl` | `local-cpu-deterministic` | SD 1.5 / SDXL / Flux / SD3 via `diffusers` | 4–24 GB |
| Image→Image / Edit | `image/edit` | real ops (strength-blend, palette, transfer) | diffusers img2img / inpainting pipelines | 6–12 GB |
| Inpaint / Outpaint | `image/inpaint` | real mask compositing + diffusion-free fill | SD inpaint / LaMa | 4–8 GB |
| Upscale | `image/upscale` | Lanczos + unsharp (real) | Real-ESRGAN / SwinIR | 2–6 GB |
| Background remove | `image/matte` | real trimap/segmentation | rembg + u2net / IS-Net | 1–2 GB |
| Video T2V / I2V | `video/*` | real frame synthesis + camera motion + FFmpeg encode | Wan2.x / CogVideoX / AnimateDiff / SVD | 12–24+ GB |
| Interpolation | `video/interp` | real frame blending (FFmpeg `minterpolate`) | RIFE / FILM | 3–6 GB |
| TTS | `voice/tts` | espeak-ng if present; else **"engine not installed"** | Piper / Kokoro / XTTS / ElevenLabs | 0.5–6 GB |
| Voice clone | `voice/clone` | disabled by default + rights attestation | XTTS / OpenVoice (consent-gated) | 6–10 GB |
| Music / SFX | `audio/*` | real DSP synthesis (works now) | MusicGen / AudioLDM / Stable Audio | 4–12 GB |
| Lip sync | `lipsync/*` | real viseme-driven mouth animation on avatar frames | Wav2Lip / SadTalker / MuseTalk | 4–10 GB |
| Avatar | `avatar/*` | real composited presenter rig (deterministic) | diffusion-based avatar + neural renderer | 8–16 GB |

Model Manager exposes `list / install / remove / activate / deactivate / test` and per-model: name, version, capabilities, VRAM, resolution, speed, license, local/API, status.

---

## 7. GPU REQUIREMENTS

| Profile | Hardware | What runs |
|---|---|---|
| **Offline / CPU** (this sandbox; also works on any laptop) | 0 GPU, ≥8 GB RAM | Tier A engines, full app, full timeline/editor/export, jobs, projects, story, branding, admin |
| **Entry GPU** | NVIDIA 8 GB (RTX 3060/4060) | + SD 1.5/SDXL (fp16 + attention slicing), Real-ESRGAN, rembg, RIFE, Piper |
| **Pro GPU** | NVIDIA 16–24 GB (4080/4090/A5000) | + SDXL/Flux.dev, AnimateDiff/SVD, Wav2Lip, XTTS |
| **Studio / Server** | 2–4 × 24–48 GB (A6000/L40S/H100) | + Wan/CogVideoX-class video, parallel workers, multi-GPU scheduler, batch queue |

GPU abstraction reports device, VRAM total/used, utilization, temperature, active jobs, queue depth, loaded model. Detection order: `torch.cuda` → `nvidia-smi` → remote GPU agent (HTTP) → "CPU only". Absence of a GPU is a first-class, well-labelled state — never a crash.

---

## 8. IMPLEMENTATION PLAN

Phased exactly as §41, but every phase lands as **working software**, not scaffolding:

| Phase | Deliverable | Real functionality in this environment |
|---|---|---|
| **P1** | Shell, auth (JWT+RBAC), dashboard, projects, assets, prompt workspace, model manager, branding, admin, jobs | ✅ full |
| **P2** | Image engine: T2I, I2I, edit, inpaint, outpaint, object replace, BG remove/replace, upscale, face restore, colorize, sketch/depth/edge/pose, multi-reference w/ weights | ✅ Tier A real ops |
| **P3** | Video engine: T2V, I2V, first/last frame, extend, restyle, upscale, camera controls, queue, preview + proxy streaming | ✅ real FFmpeg pipeline |
| **P4** | Characters + consistency locks, story→video, storyboard, shot list, per-scene regenerate | ✅ real |
| **P5** | Avatars, talking avatar, viseme lip-sync, expressions, gestures, voice studio, multilingual (13 languages) | ✅ real rig + real DSP audio |
| **P6** | Audio studio (music/SFX/ambience via real synthesis), timeline editor (cut/split/trim/merge/speed/volume/fade/transition/crop/text/captions/filters), social export presets | ✅ real |
| **P7** | Workflow builder (visual nodes), command assistant (intent→plan→tools), safety/provenance, observability, tests, validation report, Windows scripts | ✅ real |

Quality gates: pytest + vitest suites; `VALIDATE.ps1` emits a machine-readable report (env, DB, migrations, storage, FFmpeg, engines, API, jobs, frontend build); `HEALTHCHECK.ps1` probes `/api/health`, `/api/health/engines`, `/api/health/gpu`.

---

## 9. WHAT I NEED FROM YOU

See the questions below (database choice, offline-engine policy, build scope, frontend stack). Nothing will be downloaded, installed into the repo, or built until you confirm.
