"""Health probe for a running AI Creative Studio.

Checks the API, database, worker pool, queue depth, FFmpeg, GPU and storage.
Exits non-zero when anything critical is unhealthy.

Usage:
    python scripts/healthcheck.py [--base http://127.0.0.1:8000/api/v1] [--token <jwt>]
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from typing import Any

DEFAULT_BASE = "http://127.0.0.1:8000/api/v1"
DEFAULT_EMAIL = "admin@studio.ai"
DEFAULT_PASSWORD = "Admin@12345"


def get(url: str, token: str | None = None, timeout: int = 20) -> Any:
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def post(url: str, body: dict, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--token", default=None)
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    args = parser.parse_args()

    base = args.base.rstrip("/")
    problems: list[str] = []
    token = args.token

    try:
        health = get(f"{base}/health")
        print(f"API:        {health.get('status')}")
        print(f"Database:   {health.get('database')} ({health.get('database_type')})")
        print(f"FFmpeg:     {health.get('ffmpeg')}")
        print(f"Worker:     {health.get('worker')}")
        if health.get("status") != "ok":
            problems.append("API is not healthy")
    except Exception as exc:
        print(f"API:        unreachable — {exc}")
        return 1

    if not token:
        try:
            token = post(f"{base}/auth/login", {"email": args.email, "password": args.password})["access_token"]
        except Exception as exc:
            print(f"Auth:       could not sign in ({exc}) — continuing with public checks")

    if token:
        try:
            stats = get(f"{base}/jobs/stats", token)
            print(f"Queue:      {stats.get('queued')} queued · {stats.get('processing')} processing · "
                  f"{stats.get('failed')} failed")
        except Exception as exc:
            problems.append(f"queue stats unavailable: {exc}")
        try:
            models = get(f"{base}/models", token)
            ready = sum(1 for m in models["items"] if m.get("status") in ("installed", "available"))
            print(f"Models:     {ready}/{len(models['items'])} ready")
            gpu = models.get("gpu") or {}
            if not gpu.get("gpu_available"):
                print("GPU:        none detected — CPU-only mode (expected without an NVIDIA GPU)")
            else:
                print(f"GPU:        {gpu.get('device_count')} device(s), backend {gpu.get('backend')}")
        except Exception as exc:
            problems.append(f"model registry unavailable: {exc}")
        try:
            server_health = get(f"{base}/admin/system-health", token)
            storage = server_health.get("storage") or {}
            free = (storage.get("disk") or {}).get("free_gb")
            print(f"Storage:    {storage.get('backend')} backend, {storage.get('asset_mb')} MB assets"
                  + (f", {free} GB free" if free else ""))
            if free is not None and free < 2:
                problems.append(f"only {free} GB free disk space")
        except Exception:
            pass  # admin endpoint is optional for non-admin tokens

    if problems:
        print("\nIssues:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nAll health checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
