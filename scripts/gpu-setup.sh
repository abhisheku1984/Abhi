#!/usr/bin/env bash
# Install GPU extras
# Installs torch/diffusers for NVIDIA GPU inference.
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
step "Installing GPU requirements (large download)"
"$VENV_PYTHON" -m pip install -r "$BACKEND/requirements-gpu.txt"
printf "\nGPU extras installed. Restart the platform, then check Model Manager.\n"
