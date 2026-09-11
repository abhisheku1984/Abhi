#!/usr/bin/env bash
# Validate the installation
# Runs environment checks, tests, build and optional live smoke test.
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

ARGS=("$ROOT/scripts/validate.py")
[ "${SKIP_TESTS:-0}" = "1" ] && ARGS+=("--skip-tests")
[ "${SKIP_BUILD:-0}" = "1" ] && ARGS+=("--skip-build")
[ "${WITH_SERVER:-0}" = "1" ] && ARGS+=("--with-server")
exec "$VENV_PYTHON" "${ARGS[@]}"
