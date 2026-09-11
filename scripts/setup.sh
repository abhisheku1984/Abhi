#!/usr/bin/env bash
# One-command setup: creates the Python environment, installs dependencies,
# builds the frontend, and prepares .env. Safe to re-run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> Abhi Studio setup"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "!! python3 not found. Install Python 3.10+ and re-run." >&2
  exit 1
fi
"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "!! Python 3.10+ required (found $("$PYTHON_BIN" --version))." >&2
  exit 1
}

if [ ! -d .venv ]; then
  echo "--> creating virtualenv (.venv)"
  "$PYTHON_BIN" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "--> installing backend requirements"
pip install --quiet --upgrade pip
pip install --quiet -r backend/requirements.txt

if [ -f .env ]; then
  echo "--> .env already exists (left untouched)"
else
  echo "--> creating .env from .env.example"
  cp .env.example .env
  echo "    (edit .env to add API keys or a GPU host — everything is optional)"
fi

if [ ! -d frontend/node_modules ]; then
  if command -v npm >/dev/null 2>&1; then
    echo "--> installing frontend dependencies"
    (cd frontend && npm install --silent)
  else
    echo "!! npm not found — the API will run, but the UI will not be built." >&2
  fi
fi

if command -v npm >/dev/null 2>&1 && [ -d frontend/node_modules ]; then
  echo "--> building frontend"
  (cd frontend && npm run build --silent)
fi

echo "--> verifying the backend boots"
(cd backend && python - <<'PY'
import app.main as main
print(f"    OK: {len(main.app.routes)} routes, {len(main.JOB_HANDLERS)} job kinds")
PY
)

echo
echo "Setup complete. Start the studio with:"
echo "    make run        # or: cd backend && ../.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"
echo
echo "Then open http://localhost:8000 (credentials: ABHI_ADMIN_USER / ABHI_ADMIN_PASSWORD from .env)"
