# Deployment

## 1. Local single user (default)

```powershell
.\scripts\INSTALL.ps1
.\scripts\START.ps1
```

SQLite + local storage + database queue. Zero external services. Good for one author on one machine.

## 2. Small team on a LAN / single server

```powershell
.\scripts\BUILD.ps1      # builds the frontend into frontend/dist
.\scripts\DEPLOY.ps1     # serves API + built UI on :8000
```

Point everyone at `http://<server>:8000`. The API serves `frontend/dist` automatically when it exists.

## 3. Production checklist

| Item | What to do |
|---|---|
| Secret key | `SECRET_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(48))")` in `backend/.env` |
| Admin password | Change it at first login and delete `BOOTSTRAP_ADMIN_PASSWORD` afterwards |
| App env | `APP_ENV=production`, `DEBUG=false` |
| Database | PostgreSQL: `DATABASE_URL=postgresql+psycopg://…`, run `alembic upgrade head` |
| Queue | `REDIS_URL=redis://…` for multi-process workers |
| Storage | `STORAGE_BACKEND=s3` (+ endpoint/bucket/keys) for shared, durable assets |
| TLS | Terminate TLS in front (Caddy/Nginx/IIS ARR); forward `X-Forwarded-*` |
| CORS | `CORS_ORIGINS=https://studio.example.com` |
| Workers | `WORKER_CONCURRENCY` ≈ CPU cores for CPU engines; 1 per GPU for diffusion |
| Backups | Nightly `scripts/BACKUP.ps1`, keep off-host, test `RESTORE` quarterly |
| Logs | Ship `backend/data/logs/studio.log` (JSON lines) to your collector |
| Metrics | Scrape `/api/v1/health/metrics` with Prometheus |

## 4. PostgreSQL

```bash
createdb studio
export DATABASE_URL=postgresql+psycopg://studio:studio@localhost:5432/studio
cd backend && .venv/bin/alembic upgrade head
```

SQLite → PostgreSQL migration:

```bash
./scripts/backup.sh                       # archive the SQLite install
# point DATABASE_URL at PostgreSQL, then:
cd backend && .venv/bin/alembic upgrade head
python - <<'PY'
# copy rows with your preferred tool (pgloader, or a small SQLAlchemy script)
PY
```

Because `assets.storage_key` is backend-neutral, only rows need moving; files keep their keys.

## 5. S3-compatible storage

```dotenv
STORAGE_BACKEND=s3
S3_ENDPOINT_URL=https://s3.us-east-1.amazonaws.com   # or https://minio.internal:9000
S3_BUCKET=studio-assets
S3_REGION=us-east-1
S3_ACCESS_KEY_ID=…
S3_SECRET_ACCESS_KEY=…
```

## 6. Docker

```yaml
# docker-compose.yml
services:
  api:
    build: ./backend
    environment:
      DATABASE_URL: postgresql+psycopg://studio:studio@db:5432/studio
      REDIS_URL: redis://redis:6379/0
      SECRET_KEY: ${SECRET_KEY}
      STORAGE_BACKEND: local
    volumes: ["studio-data:/app/backend/storage", "studio-db:/app/backend/data"]
    ports: ["8000:8000"]
    depends_on: [db, redis]
  db:
    image: postgres:16
    environment: {POSTGRES_USER: studio, POSTGRES_PASSWORD: studio, POSTGRES_DB: studio}
    volumes: ["pgdata:/var/lib/postgresql/data"]
  redis:
    image: redis:7-alpine
volumes: {studio-data: {}, studio-db: {}, pgdata: {}}
```

The image installs FFmpeg from Debian packages and `pip install -r requirements.txt`; add `requirements-gpu.txt` and the NVIDIA runtime for GPU inference.

## 7. GPU hosts

```powershell
.\scripts\GPU-SETUP.ps1
```

Then confirm in **Model Manager** that `diffusers-image` / `diffusers-video` report `Ready` after you install weights. Set `WORKER_CONCURRENCY=1` per GPU to avoid VRAM contention; the engine reports required VRAM in `estimate()` and the UI warns before you launch a job that will not fit.

## 8. Scaling notes

* CPU engines are thread-friendly: raise `WORKER_CONCURRENCY` toward the core count.
* Diffusion engines are GPU-bound: one worker per GPU; the queue serialises them naturally.
* Long renders store intermediate files under `backend/storage/tmp` and clean up on completion or failure; keep a few GB free.
* The queue is database-backed, so several API processes can share it safely (row-level claim). With Redis the same API is used and throughput improves under heavy fan-out (story, workflows).

## 9. Verification after deployment

```bash
./scripts/healthcheck.sh
python3 scripts/smoke.py         # 20 end-to-end checks, real artifacts
./scripts/validate.sh            # full gate, writes docs/VALIDATION_REPORT.md
```
