"""Hardware capability detection.

Answers one question for the whole platform: "can model X actually run here?"
Used by the Model Manager, the GPU page, adapters and the environment report.

Works on Windows and Linux without requiring torch to be installed:
  1. `nvidia-smi` (present with any NVIDIA driver)
  2. torch.cuda (only if torch happens to be installed)
  3. platform-specific fallbacks
If nothing is found the machine is reported as CPU-only with the real RAM/VRAM
numbers so adapters can fail with a precise HARDWARE_REQUIREMENT_NOT_MET reason.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
from functools import lru_cache
from typing import Any

from app.core.config import settings

BYTES_PER_GB = 1024 ** 3


def _run(cmd: list[str], timeout: int = 8) -> str:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=0,  # noqa: S603 - fixed argv, no shell
        )
        return out.stdout or ""
    except Exception:
        return ""


def _system_ram_gb() -> float:
    try:
        import psutil  # type: ignore

        return round(psutil.virtual_memory().total / BYTES_PER_GB, 2)
    except Exception:
        pass
    try:
        if os.name == "nt":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))  # type: ignore[attr-defined]
            return round(stat.ullTotalPhys / BYTES_PER_GB, 2)
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) * 1024 / BYTES_PER_GB, 2)
    except Exception:
        pass
    return 0.0


def _disk_free_gb(path: str | None = None) -> float:
    try:
        total, used, free = shutil.disk_usage(path or os.getcwd())
        return round(free / BYTES_PER_GB, 2)
    except Exception:
        return 0.0


def _nvidia_smi_gpus() -> list[dict[str, Any]]:
    smi = shutil.which("nvidia-smi")
    if not smi:
        # Windows default install location
        alt = r"C:\Windows\System32\nvidia-smi.exe"
        smi = alt if os.path.exists(alt) else ""
        if not smi:
            return []
    query = "index,name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu,driver_version"
    out = _run([smi, f"--query-gpu={query}", "--format=csv,noheader,nounits"])
    gpus: list[dict[str, Any]] = []
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 8:
            continue

        def num(v: str) -> float:
            try:
                return float(v)
            except Exception:
                return 0.0

        gpus.append(
            {
                "index": int(num(parts[0])),
                "name": parts[1],
                "memory_total_mb": num(parts[2]),
                "memory_used_mb": num(parts[3]),
                "memory_free_mb": num(parts[4]),
                "utilization_percent": num(parts[5]),
                "temperature_c": num(parts[6]),
                "driver_version": parts[7],
                "vendor": "nvidia",
                "detected_via": "nvidia-smi",
            }
        )
    return gpus


def _torch_gpus() -> list[dict[str, Any]]:
    if settings.DISABLE_GPU:
        return []
    try:
        import torch  # type: ignore

        if not torch.cuda.is_available():
            return []
        gpus = []
        for i in range(torch.cuda.device_count()):
            props = torch.cuda.get_device_properties(i)
            free, total = torch.cuda.mem_get_info(i) if hasattr(torch.cuda, "mem_get_info") else (0, props.total_memory)
            gpus.append(
                {
                    "index": i,
                    "name": props.name,
                    "memory_total_mb": round(total / 1024 ** 2, 1),
                    "memory_used_mb": round((total - free) / 1024 ** 2, 1),
                    "memory_free_mb": round(free / 1024 ** 2, 1),
                    "utilization_percent": 0.0,
                    "temperature_c": 0.0,
                    "driver_version": getattr(torch.version, "cuda", "") or "",
                    "vendor": "nvidia",
                    "detected_via": "torch.cuda",
                }
            )
        return gpus
    except Exception:
        return []


def detect_gpus() -> list[dict[str, Any]]:
    if settings.DISABLE_GPU:
        return []
    gpus = _nvidia_smi_gpus()
    if gpus:
        return gpus
    return _torch_gpus()


def cuda_available() -> bool:
    if settings.DISABLE_GPU:
        return False
    if shutil.which("nvcc"):
        return True
    try:
        import torch  # type: ignore

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def torch_version() -> str:
    try:
        import torch  # type: ignore

        return torch.__version__
    except Exception:
        return ""


@lru_cache(maxsize=1)
def environment_snapshot() -> dict[str, Any]:
    """Full environment report (served at GET /api/system/environment)."""
    gpus = detect_gpus()
    total_vram = round(sum(g["memory_total_mb"] for g in gpus) / 1024, 2)
    free_vram = round(sum(g["memory_free_mb"] for g in gpus) / 1024, 2)
    return {
        "os": f"{platform.system()} {platform.release()}",
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu": {
            "cores": os.cpu_count() or 1,
            "model": _cpu_model(),
        },
        "memory": {
            "ram_total_gb": _system_ram_gb(),
            "ram_available_gb": _ram_available_gb(),
        },
        "disk": {"free_gb": _disk_free_gb(str(settings.storage_path))},
        "gpu": {
            "available": bool(gpus),
            "count": len(gpus),
            "devices": gpus,
            "total_vram_gb": total_vram,
            "free_vram_gb": free_vram,
            "cuda": cuda_available(),
        },
        "torch": torch_version(),
        "ffmpeg": ffmpeg_available(),
    }


def _cpu_model() -> str:
    try:
        if platform.system() == "Linux":
            with open("/proc/cpuinfo") as fh:
                for line in fh:
                    if "model name" in line:
                        return line.split(":", 1)[1].strip()
        elif platform.system() == "Windows":
            return platform.processor() or ""
        elif platform.system() == "Darwin":
            return _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
    except Exception:
        pass
    return platform.processor() or ""


def _ram_available_gb() -> float:
    try:
        import psutil  # type: ignore

        return round(psutil.virtual_memory().available / BYTES_PER_GB, 2)
    except Exception:
        return _system_ram_gb()


FFMPEG_PATH: str | None = None


def _resolve_ffmpeg() -> str | None:
    # 1. explicit override
    env_path = os.environ.get("FFMPEG_BINARY", "").strip()
    if env_path and os.path.exists(env_path):
        return env_path
    # 2. system PATH
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    found = shutil.which(exe)
    if found:
        return found
    # 3. static binary shipped by imageio-ffmpeg (works offline, cross-platform)
    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def ffmpeg_path() -> str | None:
    """Absolute path to a usable ffmpeg binary, or None when unavailable."""
    global FFMPEG_PATH
    if FFMPEG_PATH is None:
        FFMPEG_PATH = _resolve_ffmpeg() or ""
    return FFMPEG_PATH or None


def ffmpeg_available() -> bool:
    return ffmpeg_path() is not None


def ffprobe_path() -> str | None:
    """imageio-ffmpeg ships ffmpeg only; derive a probe-capable binary if present."""
    env_path = os.environ.get("FFPROBE_BINARY", "").strip()
    if env_path and os.path.exists(env_path):
        return env_path
    exe = "ffprobe.exe" if os.name == "nt" else "ffprobe"
    found = shutil.which(exe)
    if found:
        return found
    return ffmpeg_path()  # ffmpeg can report stream info via -i on stderr


def ffmpeg_version() -> str:
    p = ffmpeg_path()
    if not p:
        return ""
    out = _run([p, "-version"])
    m = re.search(r"ffmpeg version (\S+)", out)
    return m.group(1) if m else (out.splitlines()[0] if out else "")


def can_run_model(vram_gb: float, ram_gb: float) -> tuple[bool, str]:
    """Hardware gate used by every model adapter before doing any work."""
    env = environment_snapshot()
    if vram_gb > 0:
        if not env["gpu"]["available"]:
            return False, (
                f"Model requires {vram_gb:g} GB VRAM but no GPU was detected on this machine."
            )
        if env["gpu"]["free_vram_gb"] + 1e-6 < vram_gb:
            return False, (
                f"Model requires {vram_gb:g} GB VRAM; {env['gpu']['free_vram_gb']:g} GB is free "
                f"across {env['gpu']['count']} GPU(s)."
            )
        return True, ""
    # CPU-only models: check RAM headroom.
    available = env["memory"]["ram_available_gb"]
    if ram_gb > 0 and available > 0 and available < ram_gb:
        return False, (
            f"Model needs about {ram_gb:g} GB RAM; only {available:g} GB is available."
        )
    return True, ""


def refresh() -> None:
    """Clear cached detection (call after installing drivers/models)."""
    environment_snapshot.cache_clear()
    global FFMPEG_PATH
    FFMPEG_PATH = None
