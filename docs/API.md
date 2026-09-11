# API reference

Base URL: `http://localhost:8000/api/v1`
Interactive docs: **http://localhost:8000/api/docs** (OpenAPI JSON at `/api/openapi.json`)

* Auth: `Authorization: Bearer <access_token>` (JWT). `POST /auth/login` → `{access_token, refresh_token, user}`.
* Errors: `{ "error": { "code", "message", "suggested_action", "error_id", "details" } }`. Stack traces are **never** returned (§37); the `error_id` matches the log line.
* IDs are ULIDs with a prefix (`img_01J…`, `job_01J…`). Never rely on filenames.

---

## Health & observability

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | status, database type, ffmpeg path, worker state |
| GET | `/api/v1/health` | app version, uptime |
| GET | `/api/v1/health/engines` | per-adapter readiness |
| GET | `/api/v1/health/gpu` | GPU/VRAM or CPU-only explanation |
| GET | `/api/v1/health/ready` | readiness probe |
| GET | `/api/v1/health/metrics` | Prometheus text format |

## Auth & users

| Method | Path | Notes |
|---|---|---|
| POST | `/auth/register` | first user becomes `owner` |
| POST | `/auth/login` | email + password |
| POST | `/auth/refresh` | refresh token → new access token |
| POST | `/auth/logout` | revokes refresh token |
| GET | `/auth/me` | current user + permissions |
| PATCH | `/auth/me` | update profile |
| POST | `/auth/password` | change password |

## Projects

| Method | Path | Notes |
|---|---|---|
| GET/POST | `/projects` | list (filters, paging) / create |
| GET/PATCH/DELETE | `/projects/{id}` | |
| GET | `/projects/{id}/assets` | |
| GET/POST | `/projects/{id}/versions` | snapshots |
| POST | `/projects/{id}/versions/{version_id}/restore` | |

## Assets

| Method | Path | Notes |
|---|---|---|
| GET | `/assets` | filter by kind, project, favorite, archived, search |
| POST | `/assets/upload` | multipart; MIME-checked, size-checked, checksummed |
| GET/PATCH/DELETE | `/assets/{id}` | |
| POST | `/assets/{id}/duplicate` | |
| GET | `/assets/stats/summary` | counts + bytes by kind |
| GET | `/api/v1/files/{key}` | streams stored bytes (local or S3) |

## Generation

| Method | Path | Body highlights |
|---|---|---|
| GET | `/generate/modes` | every family → mode → adapters that support it |
| POST | `/generate/estimate` | `{family, mode, engine, params}` → seconds, vram, credits |
| POST | `/generate/image` | `{prompt, negative_prompt, width, height, steps, seed, engine, mode, reference_asset_ids, project_id}` → job |
| POST | `/generate/upscale` | `{asset_id, scale, engine}` |
| POST | `/generate/video` | `{prompt, duration_sec, fps, width, height, source_asset_id, mode, seed}` |
| POST | `/generate/voice` | `{text, language, voice_id, pitch, speed, timbre}` |
| POST | `/generate/audio` | `{kind: music\|sfx, genre, mood, key, bpm, duration_sec}` |
| POST | `/generate/avatar` | `{avatar_id, script\|audio_asset_id, language, voice_id}` |
| POST | `/generate/lipsync` | `{video_asset_id, audio_asset_id}` |

Every one of these returns immediately with a job:

```json
{ "job_id": "job_01J…", "status": "queued", "engine": "local-cpu-image",
  "estimate": { "seconds": 4.2, "credits": 1.0 }, "events_url": "/api/v1/jobs/job_01J…/events" }
```

## Jobs

| Method | Path | Notes |
|---|---|---|
| GET | `/jobs` | filter by status/type/project |
| GET | `/jobs/stats` | queued / processing / failed counts |
| GET | `/jobs/{id}` | full record with progress, stage, result, error |
| POST | `/jobs/{id}/cancel` | cooperative cancellation |
| POST | `/jobs/{id}/retry` | re-enqueue a failed job |
| GET | `/jobs/{id}/events` | **SSE** stream (`progress`, `stage`, `completed`, `failed`) |
| WS | `/ws/jobs/{id}` | same events over WebSocket |

## Models (Model Manager)

| Method | Path | Notes |
|---|---|---|
| GET | `/models` | all adapters + status + GPU summary |
| GET | `/models/health` | readiness roll-up |
| GET | `/models/{adapter_id}` | capabilities + `param_schema()` |
| POST | `/models/{adapter_id}/install` | **requires** `{confirm: true}`; validates disk/VRAM first |
| POST | `/models/{adapter_id}/test` | tiny real generation |
| POST | `/models/{adapter_id}/activate` / `/deactivate` | |
| DELETE | `/models/{adapter_id}` | remove local weights |

## Story, editor, workflows, prompts, assistant

| Method | Path | Notes |
|---|---|---|
| GET | `/story/{project_id}` | scenes + shots |
| POST | `/story/generate` | script → scene/shot plan |
| POST | `/story/{project_id}/render` | renders all shots + assembly |
| POST/PATCH/DELETE | `/story/scenes/{id}`, `/story/shots/{id}` | edit |
| POST | `/story/scenes/{id}/regenerate`, `/story/shots/{id}/duplicate` | |
| GET | `/editor/presets`, `/editor/ffmpeg` | export presets, FFmpeg info |
| POST | `/editor/probe` | real ffprobe output |
| POST | `/editor/render` | timeline → job |
| GET/POST | `/workflows`, GET `/workflows/catalog` | |
| POST | `/workflows/{id}/run` | DAG execution as a job |
| GET/POST | `/prompts`, PATCH/DELETE `/prompts/{id}` | |
| POST | `/prompts/structure`, `/prompts/transform`, GET `/prompts/transforms` | |
| POST | `/assistant/command` | NL → proposed job plan (executes only on confirm) |

## Library entities

`GET/POST /characters`, `PATCH/DELETE /characters/{id}`, `POST /characters/{id}/prompt`
`GET/POST /voices`, `PATCH/DELETE /voices/{id}`
`GET/POST /avatars`, `PATCH/DELETE /avatars/{id}`

Voice cloning requires `attested_rights: true` in the body; otherwise the request is refused with a safety error (§28).

## Settings & admin (admin role)

| Method | Path | Notes |
|---|---|---|
| GET/PUT | `/settings/app`, `/settings/branding`, `/settings/moderation` | |
| GET | `/settings/providers` | configured / not configured — **never** returns key values |
| GET | `/settings/public` | the only unauthenticated settings (branding, public flags) |
| GET | `/admin/overview`, `/admin/system-health` | DB, storage, FFmpeg, GPU, workers, disk |
| GET/PATCH | `/admin/users`, `/admin/users/{id}` | roles, activation |
| GET | `/admin/jobs`, POST `/admin/jobs/{id}/cancel` | queue control |
| GET | `/admin/audit` | audit log |
| GET/PUT | `/admin/feature-flags` | |
| GET | `/admin/logs` | tail of the structured log |

## Rate limiting

`RATE_LIMIT_PER_MINUTE` (default 600) per IP on mutating routes; exceeded requests get `429` with a retry hint.

## Example: generate an image end to end

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@studio.ai","password":"Admin@12345"}' | jq -r .access_token)

JOB=$(curl -s -X POST localhost:8000/api/v1/generate/image \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"prompt":"a tea stall at dawn, cinematic","width":768,"height":768,"seed":7}' | jq -r .job_id)

curl -s -N localhost:8000/api/v1/jobs/$JOB/events -H "Authorization: Bearer $TOKEN"
curl -s localhost:8000/api/v1/jobs/$JOB -H "Authorization: Bearer $TOKEN" | jq .result
```
