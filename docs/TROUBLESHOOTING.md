# Troubleshooting

Every error the UI shows has three parts: **what happened**, **what to do**, and an **error id**. Grep the error id in `backend/data/logs/studio.log` for the full detail (stack traces go only to the log — §37).

```powershell
Select-String -Path backend\data\logs\studio.log -Pattern "err_01J..."
```
```bash
grep err_01J... backend/data/logs/studio.log
```

Start here:

```powershell
.\scripts\HEALTHCHECK.ps1
```

---

## Startup

**“Backend virtual environment is missing”**
Run `.\scripts\INSTALL.ps1` (or `./scripts/install.sh`). The scripts never touch a system Python.

**`python` not recognised / wrong version**
Install Python 3.11+ from python.microsoft.com or python.org and tick *Add to PATH*. Check with `python --version`.

**Port 8000 or 5173 already in use**
Stop the other process or change the port: `uvicorn … --port 8100`, and set `PORT=8100` before `npm run dev` (Vite) — then update `VITE_API_TARGET` in `frontend/.env` if you moved the API.

**`ModuleNotFoundError: No module named 'app'`**
Always run uvicorn from `backend/`: `cd backend; .venv\Scripts\python -m uvicorn app.main:app`.

**Database locked (SQLite)**
Another process holds the write lock. Stop duplicate instances, or move to PostgreSQL. WAL mode is already enabled.

---

## Media

**“FFmpeg not found” (503)**
Resolution order is `FFMPEG_BINARY` → `PATH` → the bundled `imageio-ffmpeg` binary. Fixes:

1. `ffmpeg -version` — if missing, install FFmpeg (Windows: `winget install Gyan.FFmpeg`, or unzip a build and set `FFMPEG_BINARY=C:\ffmpeg\bin\ffmpeg.exe`).
2. Restart the API after changing `.env`.

**Video has no audio / captions missing**
Story renders attach the narration track and burn captions only when the scene has narration text. Add narration, or export from the Editor with a caption file.

**Export fails with “Output file does not contain any stream”**
The timeline ended up empty (all clips trimmed to zero) or an input asset was deleted. Re-add the clips and export again; the job error names the offending asset id.

**Generated image looks stylised, not photographic**
That is the honest Tier-A CPU engine, badged `local-cpu · deterministic · not a diffusion model`. Install a diffusion model in **Model Manager** for photoreal output.

**Voice sounds synthetic**
Tier-A voice is formant synthesis and is flagged `intelligible: false, placeholder: true`. Install `espeak-tts` (intelligible) or configure `http-tts-provider` / `voice-clone` for natural speech.

---

## Models

**“Model not installed”**
Open **Model Manager** → the row → **Install**. The dialog tells you size, licence, disk and VRAM required and asks for confirmation. Nothing downloads automatically (§32/§43).

**Install fails: “GPU extras missing”**
Run `.\scripts\GPU-SETUP.ps1` (installs torch/diffusers/accelerate), then restart and install the model.

**Install fails: “not enough VRAM / disk”**
The estimate is real. Use a smaller model, enable CPU offload, or run on a larger GPU. Required VRAM is shown on the model row.

**Gated model needs a licence**
Accept the licence in the model vendor’s UI, download the weights manually, set the path in `.env` (e.g. `DIFFUSERS_IMAGE_MODEL=C:\models\sdxl`), and restart.

**“Provider not configured”**
Set the env vars listed on the model row (for example `IMAGE_API_BASE_URL`, `IMAGE_API_KEY`) in `backend/.env` and restart the API. Keys are never accepted from the browser and never shown back to it.

**Installed a model but the UI still says not installed**
Restart the API so the registry re-syncs, then `HEALTHCHECK`. Check the log for a load error (wrong torch version, corrupted file, missing tokenizer).

---

## Jobs

**Job stuck in “queued”**
No worker is running: set `ENABLE_BACKGROUND_WORKER=true` or start another API process. Check **Admin → Jobs**.

**Job stuck in “processing” with no progress**
The worker may have died; the queue reclaims it after the heartbeat timeout and retries (up to `JOB_MAX_ATTEMPTS`). Look for the adapter’s error in the log.

**Job failed — what now?**
Open the job row: it shows the friendly message, the suggested action and the error id. Press **Retry** once you have fixed the cause; retries re-run with identical params and seed.

**Everything is slow**
Lower `WORKER_CONCURRENCY` if you are VRAM-bound, or raise it for CPU engines. Reduce resolution/duration, or use the `enhance` preset instead of a full diffusion pass.

---

## Frontend

**Blank page / “Cannot connect to API”**
Is the API running? `curl http://localhost:8000/api/health`. In development, Vite proxies `/api` to `127.0.0.1:8000` — check `VITE_API_TARGET` in `frontend/.env`.

**401 after a while**
The access token expired; the client refreshes automatically. If refresh also fails you are signed out — sign in again. Clock skew between client and server breaks JWT expiry: sync the clock.

**Build fails with TypeScript errors**
Run `npm ci` then `npm run build`. Do not edit `node_modules`.

**Preview iframe blocked / mixed content**
Serve both app and API over the same scheme, or set the dev server’s `allowedHosts`/CORS. In production, terminate TLS once in front.

---

## Data

**Reset everything**
`.\scripts\RESET.ps1` (wipes database + assets; backups are kept). Then restart.

**Recover a deleted project**
`Projects → Versions → Restore`. Without a version snapshot, restore from `scripts/BACKUP.ps1` archives.

**Disk filling up**
`Assets` shows total size. Archive or delete old assets, prune `backend/storage/tmp`, and keep nightly backups off-host.

**Move to another machine**
`BACKUP` on the old machine, copy the zip, `INSTALL` then `RESTORE` on the new one.
