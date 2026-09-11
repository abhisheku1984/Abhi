# User guide

This guide walks the product screen by screen. Windows commands are shown first (PowerShell); the `.sh` equivalents work on Linux/macOS.

## 1. First run

```powershell
.\scripts\INSTALL.ps1
.\scripts\START.ps1
```

Open **http://localhost:5173**. Sign in with `admin@studio.ai` / `Admin@12345` (or whatever `backend/.env` says) and **change the password** straight away: *Account → Password*.

The **Dashboard** shows recent jobs, storage use, engine readiness and any system warnings (for example “FFmpeg missing” or “no GPU detected — CPU mode”).

## 2. Create — one screen for every modality

`Create` is the workhorse. Pick a tab, describe what you want, adjust the controls, press **Generate**.

| Tab | What you get |
|---|---|
| **Image** | text→image, image→image, inpaint/outpaint, background remove/replace, style transfer, variations |
| **Video** | text→video, image→video, extend, restyle — real MP4 (H.264 + AAC) |
| **Voice** | narration from text, 14 languages, pitch/timbre/speed, saved voices |
| **Audio** | background music (genre, mood, key, tempo, duration) and sound effects |
| **Avatar** | talking avatar from a portrait + script or audio file |
| **Lip sync** | drive an existing clip from a voice track |

Every control exists because the engine declared it — the page renders the adapter’s `param_schema()`, so when you install a new model its controls appear automatically.

**Behind Generate:** you get a job id immediately; the UI shows a progress bar and live stage text (`queued → validating → rendering → encoding → storing`). Close the tab and the job keeps running — status lives in the database.

### Seeds and determinism

Set a seed to reproduce an image exactly. Unset, a random seed is chosen and stored with the asset. Tier-A (CPU) assets always show the badge
`local-cpu · deterministic · not a diffusion model` so you know what produced them.

### Indian languages

Voice and story narration support English plus 13 Indian languages (hi, te, ta, kn, ml, mr, bn, gu, pa, or, as, ur) with per-language phonotactic defaults.

## 3. Assets

`Assets` is your library: images, video, audio, avatars, character references. Filter by kind, project, favourite, archived. Each card shows engine, seed, resolution/duration, size and creation time, plus actions:

* **Open** — full preview with metadata and provenance manifest
* **Download**, **Duplicate**, **Rename**, **Add to project**, **Delete**
* **Send to…** — upscale, editor, story, lip sync

Uploads are scanned against the moderation blocklist and stamped with provenance (`ai_generated: false` for your own uploads).

## 4. Projects

Group work by campaign, client or video. Each project has:

* **Assets** — everything created inside it
* **Story** — scene/shot breakdown (below)
* **Versions** — named snapshots you can restore at any time (`…/versions/{id}/restore`)

## 5. Story mode

Type a concept (“a 30-second teaser for a chai brand, warm and cinematic, Hindi narration”).

1. The assistant proposes **scenes** (visual description, narration, duration, mood).
2. Each scene becomes **shots**; you can edit, reorder, duplicate or regenerate any shot independently.
3. Press **Render story**: every shot is generated as its own job (parallel, with progress), then FFmpeg concatenates them with transitions and burned-in captions.
4. The result lands in Assets as one MP4 plus the individual shots.

## 6. Editor (timeline)

Drag clips onto a track. Supported operations:

* trim, split, reorder, transitions (cut/fade/wipe/dissolve)
* overlay text and images
* captions from an SRT or auto-generated from scene narration
* audio track replacement, ducking, normalisation, fade in/out
* export presets (web 1080p, social 720p square, GIF, audio-only) and custom resolution/fps/bitrate

Exports are jobs: you get a real progress bar and a real MP4 at the end.

## 7. Workflows

Save a repeatable graph — e.g. *generate image → upscale 4× → narrate → assemble video* — and run it on new input with one click. Workflow runs are jobs with per-node status; a failing node stops the run and tells you which node and why.

## 8. Characters and Voices

* **Characters** — store a reference image plus a description; the prompt engine injects a consistent character token into every generation so the same face reappears across shots.
* **Voices** — store named voice profiles (language, pitch, timbre, speed, sample). Cloning a real person’s voice requires ticking the **rights attestation** checkbox; without it the request is refused (§28).

## 9. Avatars

Create an avatar from a portrait (or a generated face), then produce talking videos from a script (voice + lip sync) or from an uploaded audio track. Lip sync uses the viseme engine by default; install Wav2Lip-class weights for neural lip sync.

## 10. Models (Model Manager)

The honest heart of the product. Each engine row shows:

* **name, family, provider, licence, size, VRAM, speed**
* **status** — `Ready` / `Model not installed` / `Provider not configured`
* **actions** — Install, Test, Activate/Deactivate, Remove

**Install** asks for confirmation first and then tells you exactly what it will download (§32/§43: nothing is ever downloaded silently). If an install requires credentials or a manual download — common for gated weights — the dialog says so and gives the command.

Tier-B models (diffusion, Real-ESRGAN, Piper/XTTS, Wav2Lip) show *Model not installed* until you install them. Tier-C providers show *Provider not configured* and list the env vars to set in `backend/.env`. Restart the API after editing `.env`.

## 11. Jobs

Every long operation appears here with id, type, engine, progress, stage, elapsed time and result. You can **cancel** a queued or running job, **retry** a failed one, and open the artifact it produced. Failed jobs show the friendly error, the suggested action and an error id you can grep in `backend/data/logs/studio.log`.

## 12. Assistant

Plain-English control: “create four product images with a dark background”, “make a 15-second Hindi promo with music”, “upscale the last render”. The assistant turns the request into a concrete job plan, shows you the plan, and only runs it after you confirm.

## 13. Settings

* **App** — engine defaults, quality, worker concurrency, safety switches, watermark text
* **Branding** — product name, company, colours, theme, footer
* **Moderation** — blocklist and thresholds
* **Providers** — read-only status of each provider (keys stay in the environment; they are never displayed)
* **Account** — profile and password

## 14. Admin

Users and roles (owner/admin/editor/viewer), permission matrix, audit log (who did what, when, from where), queue overview with cancel, feature flags, and **System health** (database, storage, FFmpeg, GPU, worker pool, disk free, uptime).

## 15. Keyboard shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl/Cmd + Enter` | Generate from the current Create tab |
| `Ctrl/Cmd + K` | Command palette / assistant |
| `Space` | Play/pause in the editor preview |
| `Delete` | Remove selected timeline clip |

## 16. Where things live

| Thing | Location |
|---|---|
| Database | `backend/data/studio.db` (SQLite) or your `DATABASE_URL` |
| Generated files | `backend/storage/` |
| Logs | `backend/data/logs/studio.log` |
| Config | `backend/.env` |
| Backups | `backups/studio-backup-*.zip` |
