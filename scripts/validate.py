"""Cross-platform validation for AI Creative Studio.

Runs, in order:
  1. environment checks (Python, Node, FFmpeg, disk)
  2. backend tests (pytest)
  3. frontend typecheck + build
  4. live smoke test if a server is reachable (or starts one if asked)

Writes a readable report to docs/VALIDATION_REPORT.md and exits non-zero on
failure, so it can be used as a CI gate.

Usage:
    python scripts/validate.py [--skip-tests] [--skip-build] [--with-server]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
REPORT = ROOT / "docs" / "VALIDATION_REPORT.md"

IS_WINDOWS = platform.system() == "Windows"
PY = BACKEND / (".venv/Scripts/python.exe" if IS_WINDOWS else ".venv/bin/python")
VENV_PYTHON = PY if PY.exists() else Path(sys.executable)

SECTIONS: list[tuple[str, str, bool]] = []


def run(name: str, command: list[str], cwd: Path, *, timeout: int = 1800, env: dict | None = None) -> bool:
    print(f"\n▶ {name}")
    try:
        proc = subprocess.run(
            [str(c) for c in command], cwd=str(cwd), timeout=timeout,
            capture_output=True, text=True, env={**os.environ, **(env or {})},
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(f"  ✗ {exc}")
        SECTIONS.append((name, f"failed to start: {exc}", False))
        return False
    ok = proc.returncode == 0
    tail = (proc.stdout or "").strip().splitlines()[-25:]
    for line in tail:
        print(f"  {line}")
    if not ok:
        for line in (proc.stderr or "").strip().splitlines()[-15:]:
            print(f"  ! {line}")
    SECTIONS.append((name, "\n".join(tail), ok))
    print(f"  → {'PASS' if ok else 'FAIL'}")
    return ok


def check_environment() -> bool:
    print("\n▶ environment")
    rows = []
    rows.append(("Python", f"{sys.version.split()[0]} — {sys.executable}"))
    node = shutil.which("node")
    rows.append(("Node.js", node and subprocess.run(["node", "-v"], capture_output=True, text=True).stdout.strip()
                 or "not found (needed for the frontend)"))
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        bundled = VENV_PYTHON.exists() and subprocess.run(
            [str(VENV_PYTHON), "-c", "from app.media import ffmpeg; print(ffmpeg.ffmpeg_path())"],
            cwd=str(BACKEND), capture_output=True, text=True).stdout.strip()
        rows.append(("FFmpeg", f"bundled with imageio-ffmpeg: {bundled or 'checking at runtime'}"))
    else:
        rows.append(("FFmpeg", ffmpeg))
    total, used, free = shutil.disk_usage(str(ROOT))
    rows.append(("Free disk", f"{free / 1e9:.1f} GB"))
    rows.append(("Platform", platform.platform()))
    SECTIONS.append(("environment", "\n".join(f"* **{k}** — {v}" for k, v in rows), True))
    for k, v in rows:
        print(f"  {k}: {v}")
    return True


def write_report(started: float) -> None:
    passed = sum(1 for _, _, ok in SECTIONS if ok)
    total = len(SECTIONS)
    lines = [
        "# Validation report",
        "",
        f"*Generated {time.strftime('%Y-%m-%d %H:%M:%S')} on {platform.platform()} "
        f"in {time.time() - started:.1f}s.*",
        "",
        f"**Result: {passed}/{total} checks passed.**",
        "",
    ]
    for name, detail, ok in SECTIONS:
        lines.append(f"## {'✅' if ok else '❌'} {name}")
        lines.append("")
        if detail:
            lines.append("```")
            lines.append(detail[:6000])
            lines.append("```")
        lines.append("")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport written to {REPORT.relative_to(ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--with-server", action="store_true", help="start the API server for the smoke test")
    args = parser.parse_args()

    started = time.time()
    check_environment()

    if VENV_PYTHON.exists() or args.skip_tests:
        if not args.skip_tests:
            if not (BACKEND / ".venv").exists():
                SECTIONS.append(("backend tests", "skipped — run scripts/INSTALL first", False))
            else:
                run("backend tests", [VENV_PYTHON, "-m", "pytest", "tests", "-q"], BACKEND, timeout=1800)
    else:
        SECTIONS.append(("backend tests", "skipped — virtualenv missing", False))

    if not args.skip_build:
        if (FRONTEND / "node_modules").exists():
            run("frontend tests", ["npm", "run", "test", "--", "--run"], FRONTEND, timeout=1200)
            run("frontend build", ["npm", "run", "build"], FRONTEND, timeout=1200)
        else:
            SECTIONS.append(("frontend build", "skipped — run scripts/INSTALL first", False))

    server_proc = None
    base = os.environ.get("STUDIO_BASE", "http://127.0.0.1:8000/api/v1")
    if args.with_server and VENV_PYTHON.exists():
        print("\n▶ starting API server for the smoke test")
        server_proc = subprocess.Popen(
            [str(VENV_PYTHON), "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000",
             "--log-level", "warning"],
            cwd=str(BACKEND), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(60):
            try:
                import urllib.request

                with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=2) as _:
                    break
            except Exception:
                time.sleep(1)

    if (ROOT / "scripts" / "smoke.py").exists():
        try:
            import urllib.request

            with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=3):
                run("end-to-end smoke test", [sys.executable, "scripts/smoke.py", "--base", base], ROOT,
                    timeout=1800)
        except Exception:
            SECTIONS.append(("end-to-end smoke test",
                             "skipped — no server on http://127.0.0.1:8000 (start it with scripts/START)", False))

    if server_proc:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server_proc.kill()

    write_report(started)
    failed = [name for name, _, ok in SECTIONS if not ok]
    if failed:
        print("\nFailed: " + ", ".join(failed))
        return 1
    print("\nAll validation checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
