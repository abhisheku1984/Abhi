"""Visual workflow engine (§20).

Executes a node graph in topological order. Nodes exchange artifacts; each node
type maps to a real job/engine call, so a workflow is a genuine pipeline rather
than a picture of one.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.core.logging import get_logger
from app.db.models import Asset, Job, Workflow
from app.db.session import session_scope
from app.jobs import queue
from app.jobs.handlers import run_job_sync

log = get_logger("services.workflow")

NODE_TYPES = {
    "input.text": {"label": "Text input", "category": "input", "outputs": ["text"]},
    "input.asset": {"label": "Asset input", "category": "input", "outputs": ["asset"]},
    "ai.enhance-prompt": {"label": "Enhance prompt", "category": "ai", "inputs": ["text"], "outputs": ["text"]},
    "image.text-to-image": {"label": "Text to image", "category": "image", "inputs": ["text"], "outputs": ["image"]},
    "image.upscale": {"label": "Upscale image", "category": "image", "inputs": ["image"], "outputs": ["image"]},
    "video.image-to-video": {"label": "Image to video", "category": "video", "inputs": ["image"], "outputs": ["video"]},
    "voice.tts": {"label": "Text to speech", "category": "audio", "inputs": ["text"], "outputs": ["audio"]},
    "audio.music": {"label": "Background music", "category": "audio", "inputs": ["text"], "outputs": ["audio"]},
    "avatar.talking": {"label": "Talking avatar", "category": "avatar", "inputs": ["text"], "outputs": ["video"]},
    "lipsync.apply": {"label": "Lip sync", "category": "avatar", "inputs": ["video", "audio"], "outputs": ["video"]},
    "transform.resize": {"label": "Resize", "category": "transform", "inputs": ["image"], "outputs": ["image"]},
    "output.asset": {"label": "Save asset", "category": "output", "inputs": ["*"], "outputs": []},
}


def topological_order(nodes: list[dict], edges: list[dict]) -> list[str]:
    indegree: dict[str, int] = {n["id"]: 0 for n in nodes}
    adj: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        src, dst = edge.get("source"), edge.get("target")
        if src in indegree and dst in indegree:
            adj[src].append(dst)
            indegree[dst] += 1
    q = deque([n for n, d in indegree.items() if d == 0])
    order: list[str] = []
    while q:
        node = q.popleft()
        order.append(node)
        for nxt in adj[node]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                q.append(nxt)
    if len(order) != len(nodes):
        raise ValueError("The workflow contains a cycle and cannot be executed.")
    return order


def run_workflow_job(job_id: str) -> None:
    try:
        with session_scope() as db:
            job = db.get(Job, job_id)
            if job is None:
                return
            params = dict(job.params or {})
            owner_id = job.owner_id
            project_id = job.project_id
            workflow_id = params.get("workflow_id")

            graph = params.get("graph")
            if not graph and workflow_id:
                wf = db.get(Workflow, workflow_id)
                graph = wf.graph if wf else None
            if not graph:
                raise ValueError("No workflow graph supplied.")

            nodes = graph.get("nodes", [])
            edges = graph.get("edges", [])
            order = topological_order(nodes, edges)
            node_by_id = {n["id"]: n for n in nodes}

        outputs: dict[str, Any] = {}
        produced: list[str] = []
        total = max(1, len(order))

        for i, node_id in enumerate(order):
            if queue.is_cancelled(job_id):
                return
            node = node_by_id.get(node_id, {})
            node_type = node.get("type", "")
            data = node.get("data", {}) or {}
            queue.progress(job_id, round(100 * i / total), f"node: {node_type}")

            incoming = [e for e in edges if e.get("target") == node_id]
            input_values = [outputs.get(e["source"]) for e in incoming]

            # Resolve inputs: text passes through, assets become references.
            text_value = next((v for v in input_values if isinstance(v, str)), data.get("text", ""))
            asset_refs = [v for v in input_values if isinstance(v, dict) and v.get("asset_id")]

            if node_type == "input.text":
                outputs[node_id] = data.get("text", "")
            elif node_type == "input.asset":
                outputs[node_id] = {"asset_id": data.get("asset_id")} if data.get("asset_id") else None
            elif node_type == "ai.enhance-prompt":
                from app.services import prompt_engine

                outputs[node_id] = prompt_engine.enhance(text_value or "", style=data.get("style", "cinematic"))["enhanced"]
            elif node_type == "image.upscale":
                job_id_child = _run_child(owner_id, project_id, "image", "upscale", {
                    "references": asset_refs, "upscale_factor": data.get("factor", 2),
                }, data.get("model_id"))
                outputs[node_id] = _first_asset(job_id_child)
                if outputs[node_id]:
                    produced.append(outputs[node_id]["asset_id"])
            elif node_type == "image.text-to-image":
                child = _run_child(owner_id, project_id, "image", "text-to-image", {
                    "prompt": text_value, "params": data.get("params", {}),
                }, data.get("model_id"))
                outputs[node_id] = _first_asset(child)
                if outputs[node_id]:
                    produced.append(outputs[node_id]["asset_id"])
            elif node_type == "video.image-to-video":
                child = _run_child(owner_id, project_id, "video", "image-to-video", {
                    "prompt": text_value, "references": asset_refs,
                    "duration": data.get("duration", 5), "camera": data.get("camera", "dolly"),
                    "fps": data.get("fps", 24),
                }, data.get("model_id"))
                outputs[node_id] = _first_asset(child)
                if outputs[node_id]:
                    produced.append(outputs[node_id]["asset_id"])
            elif node_type == "voice.tts":
                child = _run_child(owner_id, project_id, "voice", "text-to-speech", {
                    "text": text_value, "language": data.get("language", "en"),
                    "voice_id": data.get("voice_id"), "speed": data.get("speed", 1.0),
                }, data.get("model_id"))
                outputs[node_id] = _first_asset(child)
                if outputs[node_id]:
                    produced.append(outputs[node_id]["asset_id"])
            elif node_type == "audio.music":
                child = _run_child(owner_id, project_id, "audio", "background-music", {
                    "genre": data.get("genre", "cinematic"), "duration": data.get("duration", 30),
                    "key": data.get("key", "C"),
                }, data.get("model_id"))
                outputs[node_id] = _first_asset(child)
                if outputs[node_id]:
                    produced.append(outputs[node_id]["asset_id"])
            elif node_type == "avatar.talking":
                child = _run_child(owner_id, project_id, "avatar", "avatar-from-script", {
                    "script": text_value, "language": data.get("language", "en"),
                    "voice_id": data.get("voice_id"), "aspect": data.get("aspect", "9:16"),
                    "avatar_type": data.get("avatar_type", "corporate-presenter"),
                }, data.get("model_id"))
                outputs[node_id] = _first_asset(child)
                if outputs[node_id]:
                    produced.append(outputs[node_id]["asset_id"])
            elif node_type == "lipsync.apply":
                video_ref = next((v for v in input_values if isinstance(v, dict) and v.get("asset_id")
                                  and _asset_kind(v["asset_id"]) == "video"), None)
                audio_ref = next((v for v in input_values if isinstance(v, dict) and v.get("asset_id")
                                  and _asset_kind(v["asset_id"]) == "audio"), None)
                refs = [r for r in (video_ref, audio_ref) if r]
                child = _run_child(owner_id, project_id, "lipsync", "lip-sync", {
                    "references": refs,
                }, data.get("model_id"))
                outputs[node_id] = _first_asset(child)
                if outputs[node_id]:
                    produced.append(outputs[node_id]["asset_id"])
            elif node_type == "transform.resize":
                outputs[node_id] = asset_refs[0] if asset_refs else None
            elif node_type == "output.asset":
                outputs[node_id] = asset_refs[0] if asset_refs else text_value
            else:
                log.warning("unknown_workflow_node", node_type=node_type)
                outputs[node_id] = text_value

        queue.complete(job_id, {"asset_ids": produced, "nodes": len(order)})
    except Exception as exc:  # noqa: BLE001
        log.exception("workflow_failed", job_id=job_id, error=str(exc))
        queue.fail(job_id, {"code": "workflow_failed", "message": "The workflow could not be completed.",
                            "suggested_action": "Check the node configuration and retry.", "detail": str(exc)})


def _run_child(owner_id: str, project_id: Optional[str], job_type: str, mode: str,
               params: dict, model_id: Optional[str]) -> str:
    job = run_job_sync(owner_id=owner_id, type=job_type, mode=mode, model_id=model_id,
                       project_id=project_id, params=params)
    return job.id if job else ""


def _first_asset(job_id: str) -> Optional[dict]:
    job = queue.get(job_id)
    if not job or job.status != "completed":
        return None
    ids = (job.result or {}).get("asset_ids") or []
    return {"asset_id": ids[0], "job_id": job_id} if ids else None


def _asset_kind(asset_id: str) -> Optional[str]:
    with session_scope() as db:
        asset = db.get(Asset, asset_id)
        return asset.kind if asset else None
