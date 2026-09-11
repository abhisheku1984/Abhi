"""In-process pub/sub used to stream job progress to WebSocket clients.

Deliberately dependency free: with one backend process (the default) this is
exact and instant. When REDIS_URL is configured the queue switches to Redis and
this bus is used only for local subscribers of that process.
"""
from __future__ import annotations

import asyncio
import json
import threading
from collections import defaultdict
from queue import Empty, Queue
from typing import Any, Callable

_subscribers: dict[str, list[Callable[[dict], None]]] = defaultdict(list)
_lock = threading.RLock()
_recent: dict[str, list[dict]] = defaultdict(list)
_RECENT_MAX = 50


def subscribe(topic: str, callback: Callable[[dict], None]) -> Callable[[], None]:
    with _lock:
        _subscribers[topic].append(callback)

    def unsubscribe() -> None:
        with _lock:
            try:
                _subscribers[topic].remove(callback)
            except ValueError:
                pass

    return unsubscribe


def publish(topic: str, payload: dict[str, Any]) -> None:
    """Publish from any thread; callbacks are invoked synchronously and
    failures never break the producer."""
    with _lock:
        _recent[topic].append(payload)
        if len(_recent[topic]) > _RECENT_MAX:
            _recent[topic] = _recent[topic][-_RECENT_MAX:]
        callbacks = list(_subscribers.get(topic, []))

    for cb in callbacks:
        try:
            cb(payload)
        except Exception:  # pragma: no cover - a bad subscriber must not kill a job
            pass


def recent(topic: str) -> list[dict]:
    with _lock:
        return list(_recent.get(topic, []))


async def async_stream(topic: str, poll_seconds: float = 0.5):
    """Async generator that yields every event published on `topic`.

    Thread-safe bridge: worker threads publish synchronously, the WebSocket
    task consumes them through a queue.
    """
    q: Queue = Queue()

    def _put(payload: dict) -> None:
        q.put(payload)

    unsubscribe = subscribe(topic, _put)
    try:
        while True:
            try:
                payload = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: q.get(timeout=poll_seconds)
                )
            except Empty:
                if await asyncio.get_running_loop().run_in_executor(None, lambda: False):
                    break
                yield {"type": "ping"}
                continue
            except Exception:
                break
            yield payload
    finally:
        unsubscribe()


def topic_job(job_id: str) -> str:
    return f"job:{job_id}"


def topic_user(user_id: str) -> str:
    return f"user:{user_id}"


def topic_workflow(run_id: str) -> str:
    return f"workflow:{run_id}"


def dumps(payload: dict) -> str:
    return json.dumps(payload, default=str)
