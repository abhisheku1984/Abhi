#!/usr/bin/env bash
# Reset local data
# Deletes the database and generated assets (backups are kept).
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

if [ "${FORCE:-0}" != "1" ]; then
  printf 'This deletes the database and every generated asset. Type RESET to continue: '
  read -r answer
  [ "$answer" = "RESET" ] || { echo "Cancelled."; exit 0; }
fi
step "Removing local state"
rm -f "$BACKEND/data/studio.db"
rm -rf "$BACKEND/storage" "$BACKEND/data/jobs" "$FRONTEND/dist"
ok "Local data reset"
