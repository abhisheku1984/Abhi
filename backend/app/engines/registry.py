"""Engine registry — discovery, health and capability negotiation.

Adding a new model = drop a subclass of BaseModelAdapter in `app/engines/**`
and call `registry.register(...)`. Nothing else in the app changes.
"""

from __future__ import annotations

from typing import Any, Optional

from app.core.config import settings
from app.core.errors import NotFoundError, StudioError
from app.core.ids import new_id
from app.core.logging import get_logger
from app.engines.base import BaseModelAdapter, Capabilities, FAMILIES

log = get_logger("engines.registry")


class EngineRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, BaseModelAdapter] = {}

    def register(self, adapter: BaseModelAdapter) -> BaseModelAdapter:
        if adapter.id in self._adapters:
            log.warning("adapter_duplicate_id", id=adapter.id)
        self._adapters[adapter.id] = adapter
        log.info("adapter_registered", id=adapter.id, family=adapter.family, modes=adapter.capabilities.modes)
        return adapter

    def get(self, adapter_id: str) -> BaseModelAdapter:
        try:
            return self._adapters[adapter_id]
        except KeyError:
            raise NotFoundError(
                f"Model '{adapter_id}' was not found.",
                suggested_action="Open Model Manager to see installed models.",
            )

    def all(self) -> list[BaseModelAdapter]:
        allow = settings.engine_allowlist
        items = list(self._adapters.values())
        if allow:
            items = [a for a in items if a.id in allow]
        return items

    def by_family(self, family: str) -> list[BaseModelAdapter]:
        return [a for a in self.all() if a.family == family]

    def supports(self, family: str, mode: str) -> list[BaseModelAdapter]:
        return [a for a in self.by_family(family) if mode in a.capabilities.modes]

    def default_for(self, family: str, mode: Optional[str] = None) -> Optional[BaseModelAdapter]:
        candidates = [a for a in self.by_family(family) if a.status().get("status") in ("installed", "available")]
        if mode:
            candidates = [a for a in candidates if mode in a.capabilities.modes]
        if not candidates:
            candidates = self.by_family(family) if not mode else self.supports(family, mode)
        if not candidates:
            return None
        # Prefer adapters that work out of the box, then the fastest.
        candidates.sort(key=lambda a: (not a.ready_out_of_the_box, {"fast": 0, "medium": 1, "slow": 2}.get(a.speed, 1)))
        return candidates[0]

    def resolve(self, family: str, mode: str, model_id: Optional[str] = None) -> BaseModelAdapter:
        if model_id:
            adapter = self._adapters.get(model_id)
            if not adapter:
                raise NotFoundError(
                    f"Model '{model_id}' is not registered.",
                    suggested_action="Open Model Manager to install or activate a model.",
                )
            if adapter.family != family:
                raise StudioError(
                    f"Model '{adapter.display_name}' cannot perform '{family}' jobs.",
                    suggested_action="Select a model from the correct category.",
                )
            return adapter
        adapter = self.default_for(family, mode)
        if not adapter:
            raise ModelUnavailable(family, mode)
        return adapter

    def snapshot(self) -> list[dict[str, Any]]:
        return [a.info() for a in sorted(self.all(), key=lambda x: (x.family, x.display_name))]

    def health(self) -> dict[str, Any]:
        out: dict[str, Any] = {"families": {}, "total": len(self.all())}
        for family in FAMILIES:
            adapters = self.by_family(family)
            out["families"][family] = {
                "count": len(adapters),
                "ready": sum(1 for a in adapters if a.status().get("status") in ("installed", "available")),
                "adapters": [
                    {"id": a.id, "name": a.display_name, "status": a.status().get("status"),
                     "reason": a.status().get("reason", "")}
                    for a in adapters
                ],
            }
        return out

    def sync_to_db(self) -> int:
        """Upsert every registry entry into the `models` table (§32)."""
        from app.db.session import session_scope
        from app.db.models import ModelRecord

        count = 0
        with session_scope() as db:
            for adapter in self.all():
                info = adapter.info()
                rec = db.query(ModelRecord).filter(ModelRecord.key == adapter.id).one_or_none()
                if rec is None:
                    rec = ModelRecord(id=new_id("mdl_"), key=adapter.id)
                    db.add(rec)
                rec.display_name = info["display_name"]
                rec.family = adapter.family
                rec.version = adapter.version
                rec.provider = adapter.provider
                rec.license = adapter.license
                rec.size_mb = adapter.size_mb
                rec.vram_mb = adapter.vram_mb
                rec.speeds = adapter.speed
                rec.is_local = adapter.is_local
                rec.capabilities = info["capabilities"]
                rec.requires_extra = adapter.requires_extra
                rec.download_url = adapter.download_url
                rec.notes = adapter.notes
                # Never downgrade an explicit user action (installed/active/disabled).
                status = info["status"] or "available"
                if rec.status in ("installed", "active", "disabled"):
                    pass
                else:
                    rec.status = status
                count += 1
        return count


class ModelUnavailable(StudioError):
    status_code = 409
    code = "model_unavailable"

    def __init__(self, family: str, mode: str) -> None:
        super().__init__(
            user_message=f"No model is available for {family} / {mode}.",
            detail=f"Registry has no adapter registered for family={family} mode={mode}",
            suggested_action="Install a model in Model Manager, or configure an AI provider in Settings.",
        )


registry = EngineRegistry()


def register_core_adapters() -> EngineRegistry:
    """Import + register every adapter shipped with the platform."""
    from app.engines.image import local_cpu as image_local
    from app.engines.image import neural as image_neural
    from app.engines.image import provider as image_provider
    from app.engines.video import local_cpu as video_local
    from app.engines.video import neural as video_neural
    from app.engines.video import provider as video_provider
    from app.engines.voice import local as voice_local
    from app.engines.voice import provider as voice_provider
    from app.engines.voice import clone as voice_clone
    from app.engines.audio import local as audio_local
    from app.engines.audio import provider as audio_provider
    from app.engines.avatar import local as avatar_local
    from app.engines.avatar import provider as avatar_provider
    from app.engines.lipsync import local as lipsync_local
    from app.engines.lipsync import neural as lipsync_neural

    for module in (
        image_local, image_neural, image_provider,
        video_local, video_neural, video_provider,
        voice_local, voice_provider, voice_clone,
        audio_local, audio_provider,
        avatar_local, avatar_provider,
        lipsync_local, lipsync_neural,
    ):
        for adapter in getattr(module, "ADAPTERS", []):
            registry.register(adapter)
    return registry
