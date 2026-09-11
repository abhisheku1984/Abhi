#!/usr/bin/env bash
# Run the test suites
# Runs backend pytest and frontend vitest.
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
step "Backend tests"
(cd "$BACKEND" && "$VENV_PYTHON" -m pytest tests -q)
step "Frontend tests"
(cd "$FRONTEND" && npm run test -- --run)
ok "All tests passed"
