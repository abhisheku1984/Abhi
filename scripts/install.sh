#!/usr/bin/env bash
# Install AI Creative Studio
# Creates the Python virtual environment, installs dependencies and seeds .env files.
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

step "Checking Python"
PYTHON="$(command -v python3 || command -v python || true)"
if [ -z "$PYTHON" ]; then echo "Python 3.11+ is required." >&2; exit 1; fi
ok "Using $("$PYTHON" --version)"

step "Creating backend virtual environment"
if [ ! -d "$BACKEND/.venv" ]; then "$PYTHON" -m venv "$BACKEND/.venv"; fi
ok "venv ready"

step "Installing backend dependencies"
"$VENV_PYTHON" -m pip install --upgrade pip --quiet
"$VENV_PYTHON" -m pip install -r "$BACKEND/requirements.txt"
if [ "${GPU:-0}" = "1" ]; then
  step "Installing GPU extras"
  "$VENV_PYTHON" -m pip install -r "$BACKEND/requirements-gpu.txt"
fi
ok "backend dependencies installed"

step "Installing frontend dependencies"
if ! command -v npm >/dev/null 2>&1; then echo "Node.js 20+ is required for the web app." >&2; exit 1; fi
(cd "$FRONTEND" && npm install --no-audit --no-fund)
ok "frontend dependencies installed"

step "Preparing configuration"
[ -f "$BACKEND/.env" ] || { cp "$BACKEND/.env.example" "$BACKEND/.env"; ok "created backend/.env"; }
[ -f "$FRONTEND/.env" ] || { cp "$FRONTEND/.env.example" "$FRONTEND/.env"; ok "created frontend/.env"; }

printf "\nInstallation complete.\nNext: ./scripts/start.sh  (then open http://localhost:5173)\n"
