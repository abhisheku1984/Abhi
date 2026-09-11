"""WebSocket streaming for live job status (§26, §38)."""

from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from starlette.websockets import WebSocketState

from app.core.logging import get_logger
from app.core.security import decode_token
from app.jobs import queue
from app.jobs.events import bus

log = get_logger("api.ws")
router = APIRouter(tags=["ws"])


async def _authenticate(token: Optional[str]) -> Optional[dict]:
    if not token:
        return None
    try:
        return decode_token(token)
    except Exception:
        return None


@router.websocket("/ws/jobs")
async def jobs_ws(websocket: WebSocket, token: Optional[str] = Query(default=None)):
    """Live feed for every job of the authenticated user."""
    await websocket.accept()
    payload = await _authenticate(token)
    user_id = str(payload.get("uid") or payload.get("sub")) if payload else None
    if not user_id:
        await websocket.send_json({"error": "unauthorized"})
        await websocket.close(code=4401)
        return

    channel = "jobs"
    q = await bus.subscribe(channel)
    log.info("ws_connected", channel=channel, user=user_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=25)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
                continue
            owner = _owner_of_event(event)
            if owner and owner != user_id:
                payload_user = await _authenticate(token)
                is_admin = bool(payload_user and payload_user.get("role") in ("admin", "owner"))
                if not is_admin:
                    continue
            await websocket.send_json({"type": "job", **event})
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover
        log.debug("ws_error", error=str(exc))
    finally:
        bus.unsubscribe(channel, q)
        log.info("ws_disconnected", channel=channel)


@router.websocket("/ws/jobs/{job_id}")
async def job_ws(websocket: WebSocket, job_id: str, token: Optional[str] = Query(default=None)):
    await websocket.accept()
    payload = await _authenticate(token)
    user_id = str(payload.get("uid") or payload.get("sub")) if payload else None
    if not user_id:
        await websocket.send_json({"error": "unauthorized"})
        await websocket.close(code=4401)
        return

    job = queue.get(job_id)
    if job and job.owner_id != user_id and not (payload or {}).get("role") in ("admin", "owner"):
        await websocket.send_json({"error": "forbidden"})
        await websocket.close(code=4403)
        return

    # Send current state immediately so the UI never has to guess.
    if job:
        await websocket.send_json({
            "type": "job", "job_id": job.id, "status": job.status, "progress": job.progress,
            "stage": job.stage, "error": job.error, "result": job.result,
        })

    channel = queue.job_channel(job_id)
    q = await bus.subscribe(channel)
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=25)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
                continue
            await websocket.send_json({"type": "job", **event})
            if event.get("status") in ("completed", "failed", "cancelled"):
                break
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(channel, q)


def _owner_of_event(event: dict) -> Optional[str]:
    job_id = event.get("job_id")
    if not job_id:
        return None
    job = queue.get(job_id)
    return job.owner_id if job else None
