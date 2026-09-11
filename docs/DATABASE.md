# Database

AI Creative Studio runs on **SQLite by default** and switches to **PostgreSQL** by changing one environment variable. No application code changes, no second code path.

```dotenv
# SQLite (default)
DATABASE_URL=sqlite:///./data/studio.db

# PostgreSQL
DATABASE_URL=postgresql+psycopg://studio:studio@localhost:5432/studio
```

`app/db/session.py` builds the engine per dialect:

* **SQLite** — `check_same_thread=False`, `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, `PRAGMA busy_timeout`, `StaticPool` for in-memory test URLs.
* **PostgreSQL** — `QueuePool` sized by `DB_POOL_SIZE` / `DB_MAX_OVERFLOW`, `pool_pre_ping=True`.

## Schema

19 tables. Every row gets a ULID string primary key (`id`) plus `created_at` / `updated_at` from `app/db/base.py`.

### Identity & access

| Table | Purpose | Key columns |
|---|---|---|
| `users` | accounts | `email` (unique), `name`, `password_hash`, `role`, `is_active`, `avatar_asset_id`, `preferences` |
| `subscriptions` | plan/quota | `owner_id`, `plan`, `status`, `credits`, `renews_at` |
| `audit_logs` | who did what | `actor_id`, `action`, `resource_type`, `resource_id`, `ip`, `user_agent`, `detail` |
| `feature_flags` | runtime switches | `key`, `enabled`, `scope`, `description` |

### Content

| Table | Purpose | Key columns |
|---|---|---|
| `projects` | containers | `owner_id`, `name`, `description`, `kind`, `status`, `cover_asset_id`, `meta` |
| `project_versions` | restorable snapshots | `project_id`, `label`, `snapshot` (JSON), `created_by` |
| `assets` | anything on disk | `owner_id`, `project_id`, `kind`, `storage_key`, `mime`, `size_bytes`, `checksum`, `width`, `height`, `duration_sec`, `thumbnail_key`, `preview_key`, `meta`, `is_favorite`, `is_archived`, `parent_asset_id`, `version` |
| `generations` | one generation attempt | `owner_id`, `job_id`, `kind`, `mode`, `engine`, `model_id`, `prompt`, `negative_prompt`, `params`, `seed`, `status`, `asset_id`, `duration_ms`, `credits`, `error` |
| `characters` | consistent characters | `owner_id`, `name`, `description`, `reference_asset_id`, `attributes`, `prompt_template` |
| `voices` | voice profiles | `owner_id`, `name`, `language`, `gender`, `pitch`, `timbre`, `speed`, `sample_asset_id`, `engine`, `external_id`, `attested_rights` |
| `avatars` | avatars | `owner_id`, `name`, `portrait_asset_id`, `style`, `rig` (JSON), `engine` |
| `scenes` | story scenes | `project_id`, `index`, `title`, `description`, `narration`, `duration_sec`, `mood`, `status` |
| `shots` | story shots | `scene_id`, `index`, `prompt`, `negative_prompt`, `duration_sec`, `camera`, `asset_id`, `status` |
| `prompts` | prompt library | `owner_id`, `title`, `body`, `structured` (JSON), `tags`, `favorite` |
| `workflows` | saved graphs | `owner_id`, `name`, `description`, `graph` (JSON), `is_template` |

### Execution

| Table | Purpose | Key columns |
|---|---|---|
| `jobs` | **source of truth for status** | `owner_id`, `project_id`, `type`, `mode`, `engine`, `model_id`, `status` (`queued`/`processing`/`completed`/`failed`/`cancelled`), `progress`, `stage`, `params`, `result`, `error`, `attempts`, `max_attempts`, `priority`, `worker_id`, `gpu`, `eta_seconds`, `queued_at`, `started_at`, `finished_at`, `heartbeat_at` |
| `models` | registry mirror | `adapter_id` (unique), `family`, `display_name`, `provider`, `status`, `size_mb`, `vram_mb`, `license`, `capabilities`, `install_path`, `installed_at`, `active` |
| `usage` | daily metrics | `owner_id`, `day`, `metric`, `value` (unique per owner/day/metric) |
| `app_settings` | runtime settings | `key` (unique), `value` (JSON), `scope` |

Indexes exist on every foreign key and on the hot query paths (`ix_jobs_status_priority`, `ix_jobs_owner_status`, `ix_assets_owner_kind`, `ix_assets_project_kind`, `ix_generations_owner_kind`, `ix_models_family`, …).

## Job state machine

```
                    ┌────────► cancelled
                    │
 queued ──► processing ──► completed
    │           │
    │           └────────► failed ──► (retry) ──► queued
    └──► (stale heartbeat, worker crash) ──► queued
```

* Status is **only** ever read from the `jobs` table — never from an in-memory dict.
* `attempts` / `max_attempts` (default 3) control retries; retry re-enqueues the same params.
* `heartbeat_at` lets the queue reclaim jobs whose worker died.
* Progress is a float 0–100 plus a human-readable `stage`; both stream to the UI.

## Migrations

Alembic is wired up (`backend/alembic.ini`, `backend/alembic/env.py`) and uses the same `Base.metadata` and `DATABASE_URL` as the app.

```bash
cd backend
.venv/bin/alembic revision --autogenerate -m "describe change"
.venv/bin/alembic upgrade head
```

Development also calls `Base.metadata.create_all()` at startup, so a fresh SQLite install needs no migration step. For PostgreSQL (and any environment you care about), generate the initial revision and run `upgrade head` before first boot.

## Switching storage, not schema

`assets.storage_key` is backend-agnostic. Flipping `STORAGE_BACKEND` from `local` to `s3` does not change the schema — only where the bytes live — so migrations between the two are a file copy plus a settings change.

## Backup and restore

```powershell
.\scripts\BACKUP.ps1                       # → backups/studio-backup-<timestamp>.zip
.\scripts\RESTORE.ps1 backups\studio-backup-20260101-120000.zip
```

The archive contains the database file, the whole `storage/` tree, `backend/.env` and the logs, plus a `manifest.json` describing what it holds. Restoring overwrites the current database, assets and env file, and requires `--confirm`.
