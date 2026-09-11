"""In-process event bus for live job progress (WebSocket fan-out)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Optional

from app.core.logging import get_logger

log = get_logger("jobs.events")


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def subscribe(self, channel: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers.setdefault(channel, set()).add(q)
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue) -> None:
        self._subscribers.get(channel, set()).discard(q)

    async def publish(self, channel: str, payload: dict[str, Any]) -> None:
        for q in list(self._subscribers.get(channel, set())):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:  # keep the freshest state, drop the rest
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except Exception:
                    pass
        # A global channel lets the UI watch everything at once.
        if channel != "jobs":
            for q in list(self._subscribers.get("jobs", set())):
                try:
                    q.put_nowait({"channel": channel, **payload})
                except asyncio.QueueFull:
                    pass

    def publish_threadsafe(self, channel: str, payload: dict[str, Any]) -> None:
        """Called from worker threads."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.publish(channel, payload), loop)
        except Exception as exc:  # pragma: no cover
            log.debug("event_publish_failed", error=str(exc))

    def subscriber_count(self) -> int:
        return sum(len(v) for v in self._subscribers.values())


bus = EventBus()
