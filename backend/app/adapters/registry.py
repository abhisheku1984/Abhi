"""Adapter registry - the plug-in point for every AI model (spec §12, §35, §36).

Adding a model later:
    1. drop a file in app/adapters/implementations/
    2. subclass one of the typed adapters
    3. restart
No engine, route or frontend change is required.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
import threading
from pathlib import Path
from typing import Any, Iterable

from app.adapters.base import AdapterStatus, BaseModelAdapter
from app.core.errors import ErrorCode, StudioError


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, BaseModelAdapter] = {}
        self._lock = threading.RLock()

    # --- registration -------------------------------------------------------
    def register(self, adapter: BaseModelAdapter) -> None:
        with self._lock:
            self._adapters[adapter.key] = adapter

    def unregister(self, key: str) -> None:
        with self._lock:
            self._adapters.pop(key, None)

    def discover(self, package: str = "app.adapters.implementations") -> list[str]:
        """Import every module in the implementations package."""
        loaded: list[str] = []
        pkg = importlib.import_module(package)
        for mod in pkgutil.iter_modules([str(Path(pkg.__file__).parent)]):
            full = f"{package}.{mod.name}"
            try:
                module = importlib.import_module(full)
            except Exception as exc:  # keep the app alive if an optional dep is broken
                loaded.append(f"{full}:ERROR:{exc}")
                continue
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, BaseModelAdapter)
                    and obj is not BaseModelAdapter
                    and not inspect.isabstract(obj)
                    and obj.__module__ == full
                    and obj.key
                    and obj.key != "base"
                ):
                    self.register(obj())
                    loaded.append(obj.key)
        return loaded

    # --- lookup -------------------------------------------------------------
    def get(self, key: str) -> BaseModelAdapter:
        with self._lock:
            adapter = self._adapters.get(key)
        if not adapter:
            raise StudioError(
                f"Unknown model adapter '{key}'",
                code=ErrorCode.NOT_FOUND,
                status_code=404,
                details={"known_adapters": sorted(self._adapters)},
            )
        return adapter

    def all(self) -> list[BaseModelAdapter]:
        with self._lock:
            return list(self._adapters.values())

    def by_capability(self, capability: str) -> list[BaseModelAdapter]:
        return [a for a in self.all() if capability in (a.capabilities or (a.capability,))]

    def available_by_capability(self, capability: str) -> list[BaseModelAdapter]:
        return [a for a in self.by_capability(capability) if a.status().available]

    def keys(self) -> list[str]:
        return sorted(self._adapters)

    # --- resolution ---------------------------------------------------------
    def resolve(self, capability: str, model_id: str | None = None,
                allow_auto: bool = True) -> BaseModelAdapter:
        """Pick the adapter to run.

        * explicit model_id  -> that adapter, or a precise error (never a silent
          downgrade: the user must know their choice is unavailable)
        * no model_id        -> best available adapter by declared quality
        """
        candidates = self.by_capability(capability)
        if not candidates:
            raise StudioError(
                f"No adapter implements capability '{capability}'.",
                code=ErrorCode.MODEL_NOT_AVAILABLE, status_code=404,
                remediation="Install a model for this capability in Model Manager.",
            )
        if model_id and model_id not in ("auto", "", None):
            adapter = next((a for a in candidates if a.key == model_id), None)
            if adapter is None:
                raise StudioError(
                    f"Model '{model_id}' is not registered for capability '{capability}'.",
                    code=ErrorCode.NOT_FOUND, status_code=404,
                    details={"available": [a.key for a in candidates]},
                )
            adapter.ensure_ready()
            return adapter

        available = [a for a in candidates if a.status().available]
        if not available:
            if not allow_auto:
                raise StudioError(
                    f"No available model for '{capability}'.", code=ErrorCode.MODEL_NOT_AVAILABLE, status_code=503,
                    details={"candidates": [a.status().to_dict() for a in candidates]},
                )
            raise StudioError(
                f"No available model for '{capability}'.",
                code=ErrorCode.MODEL_NOT_AVAILABLE,
                status_code=503,
                details={"candidates": [a.status().to_dict() for a in candidates]},
                remediation="Open Model Manager to see what is blocking each model (missing dependency, weights or hardware).",
            )
        order = {"reference": 3, "high": 2, "standard": 1, "draft": 0}
        available.sort(key=lambda a: order.get(a.quality, 0), reverse=True)
        return available[0]

    # --- reporting ----------------------------------------------------------
    def status_report(self, refresh: bool = False) -> list[dict]:
        rows = []
        for adapter in sorted(self.all(), key=lambda a: (a.capability, a.key)):
            st: AdapterStatus = adapter.status(refresh=refresh)
            row = adapter.info()
            row.update(st.to_dict())
            row.pop("hardware", None)  # hardware is reported once, not per model
            rows.append(row)
        return rows

    def summary(self) -> dict[str, Any]:
        rows = self.status_report()
        by_cap: dict[str, dict[str, int]] = {}
        for r in rows:
            cap = r["capability"]
            bucket = by_cap.setdefault(cap, {"total": 0, "available": 0})
            bucket["total"] += 1
            if r["available"]:
                bucket["available"] += 1
        return {
            "total": len(rows),
            "available": sum(1 for r in rows if r["available"]),
            "by_capability": by_cap,
            "adapters": rows,
        }


registry = AdapterRegistry()
