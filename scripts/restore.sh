#!/usr/bin/env bash
# Restore a backup
# Restores a backup archive (overwrites current data).
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

if [ $# -lt 1 ]; then echo "Usage: $0 <archive.zip>" >&2; exit 2; fi
step "Restoring $1"
PYTHON_BIN="${PYTHON_BIN:-$VENV_PYTHON}"
"$PYTHON_BIN" "$1" --confirm
ok "Restore complete"
