#!/usr/bin/env bash
# Production deployment
# Builds the frontend and runs the API in production mode.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
VENV_PYTHON="$BACKEND/.venv/bin/python"

step() { printf "\n==> %s\n" "$1"; }
ok()   { printf "    %s\n" "$1"; }
need_venv() {
    if [ ! -x "$VENV_PYTHON" ]; then
        echo "Backend virtual environment is missing. Run ./scripts/install.sh first." >&2
        exit 1
    fi
}

need_venv
step "Building frontend"
(cd "$FRONTEND" && npm run build)
step "Starting production server on http://0.0.0.0:8000"
exec "$VENV_PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
