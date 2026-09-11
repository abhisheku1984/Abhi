"""GPU abstraction (§23).

Detection order: torch.cuda → nvidia-smi → remote GPU agent → CPU-only.
Absence of a GPU is a first-class state, never a crash.
"""

from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from typing import Any, Optional

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("gpu")


def _nvidia_smi() -> list[dict[str, Any]]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return []
    try:
        out = subprocess.run(
            [exe, "--query-gpu=index,name,memory.total,memory.used,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode != 0:
            return []
        devices = []
        for line in out.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 6:
                continue
            devices.append({
                "index": int(parts[0]),
                "name": parts[1],
                "vram_total_mb": int(float(parts[2])),
                "vram_used_mb": int(float(parts[3])),
                "utilization": int(float(parts[4])),
                "temperature": int(float(parts[5])),
                "source": "nvidia-smi",
            })
        return devices
    except Exception as exc:  # pragma: no cover
        log.debug("nvidia_smi_failed", error=str(exc))
        return []


def _torch_devices() -> list[dict[str, Any]]:
    try:
        import torch  # type: ignore

        if not torch.cuda.is_available():
            return []
        devices = []
        for i in range(torch.cuda.device_count()):
            props = torch.cuda.get_device_properties(i)
            devices.append({
                "index": i,
                "name": torch.cuda.get_device_name(i),
                "vram_total_mb": int(props.total_memory / 1024 / 1024),
                "vram_used_mb": int(torch.cuda.memory_allocated(i) / 1024 / 1024),
                "utilization": None,
                "temperature": None,
                "compute_capability": f"{props.major}.{props.minor}",
                "source": "torch.cuda",
            })
        return devices
    except Exception:
        return []


def _remote_devices() -> list[dict[str, Any]]:
    url = settings.GPU_REMOTE_AGENT_URL
    if not url:
        return []
    try:
        import httpx

        resp = httpx.get(f"{url.rstrip('/')}/gpu", timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("devices", []) if isinstance(data, dict) else []
    except Exception as exc:  # pragma: no cover
        log.debug("remote_gpu_agent_failed", error=str(exc))
    return []


def detect_devices() -> list[dict[str, Any]]:
    if not settings.GPU_ENABLED:
        return []
    for probe in (_torch_devices, _nvidia_smi, _remote_devices):
        devices = probe()
        if devices:
            return devices
    return []


@lru_cache(maxsize=1)
def _cached_detection() -> tuple[bool, str]:
    """Heavy detection is cached; `/api/v1/gpu/refresh` clears it."""
    import time

    return (True, str(int(time.time())))


def summary() -> dict[str, Any]:
    devices = detect_devices()
    gpu_available = bool(devices)
    total = sum(d.get("vram_total_mb", 0) for d in devices)
    used = sum(d.get("vram_used_mb", 0) for d in devices)
    return {
        "gpu_available": gpu_available,
        "device_count": len(devices),
        "devices": devices,
        "vram_total_mb": total,
        "vram_used_mb": used,
        "vram_free_mb": max(0, total - used),
        "backend": (devices[0]["source"] if devices else "cpu"),
        "cuda_available": any(d.get("source") == "torch.cuda" for d in devices),
        "remote_agent": settings.GPU_REMOTE_AGENT_URL or None,
        "notes": ("CPU-only machine: neural models will report 'Model not installed' unless a provider is configured."
                  if not gpu_available else ""),
    }


def recommended_profile() -> str:
    """Advise which models will fit on the detected hardware."""
    devices = detect_devices()
    if not devices:
        return "cpu"
    vram = max(d.get("vram_total_mb", 0) for d in devices)
    if vram >= 24000:
        return "studio"
    if vram >= 16000:
        return "pro"
    if vram >= 8000:
        return "entry"
    return "small"
