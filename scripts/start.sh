#!/usr/bin/env bash
# Start AI Creative Studio
# Starts the API (8000) and the web app (5173) in the background.
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
mkdir -p "$BACKEND/data/logs"

step "Starting backend API on http://localhost:8000"
nohup "$VENV_PYTHON" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 \
    >"$BACKEND/data/logs/backend.log" 2>&1 &
echo $! > "$BACKEND/data/backend.pid"

step "Starting web app on http://localhost:5173"
(cd "$FRONTEND" && nohup npm run dev >"$BACKEND/data/logs/frontend.log" 2>&1 & echo $! > "$BACKEND/data/frontend.pid")

sleep 5
printf "\nAI Creative Studio is starting up:\n"
printf "  Web app      http://localhost:5173\n"
printf "  API docs     http://localhost:8000/api/docs\n"
printf "  Health       http://localhost:8000/api/health\n\n"
