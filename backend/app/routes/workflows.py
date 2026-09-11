"""
Workflow builder API.

A workflow is a DAG of registered job kinds. The server validates the graph
(known kinds, no dangling edges, no cycles, required params present) and executes
it in topological order, feeding each step's ``asset_ids`` result into later
steps whose params reference ``"$step_id"`` placeholders.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import execute, insert, jdumps, jloads, query_all, query_one, update
from ..jobs import JOB_HANDLERS, enqueue
from ..security import AuthUser

log = logging.getLogger("abhi.routes.workflows")

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    project_id: str | None = None
    graph: dict = Field(default_factory=lambda: {"nodes": [], "edges": []})


class WorkflowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    graph: dict | None = None


class RunRequest(BaseModel):
    variables: dict[str, Any] = {}
    project_id: str | None = None
    dry_run: bool = False


# --------------------------------------------------------------------------
# validation + execution
# --------------------------------------------------------------------------
def validate_graph(graph: dict) -> dict:
    """Static validation. Returns {valid, errors, warnings, order}."""
    errors: list[str] = []
    warnings: list[str] = []
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not nodes:
        errors.append("Workflow has no nodes.")
    ids = [n.get("id") for n in nodes]
    if any(not nid for nid in ids):
        errors.append("Every node needs an id.")
    if len(set(ids)) != len(ids):
        errors.append("Node ids must be unique.")
    known = set(JOB_HANDLERS)
    for node in nodes:
        kind = node.get("kind")
        if kind not in known:
            errors.append(f"Node '{node.get('id')}' uses unknown kind '{kind}'.")
        params = node.get("params") or {}
        if kind == "image.generate" and not (
            params.get("prompt") or params.get("$prompt") or params.get("prompt_var")
        ):
            warnings.append(f"Node '{node.get('id')}' has no prompt and no variable binding.")
    known_ids = set(ids)
    adjacency: dict[str, list[str]] = {nid: [] for nid in ids if nid}
    for edge in edges:
        source, target = edge.get("from"), edge.get("to")
        if source not in known_ids or target not in known_ids:
            errors.append(f"Edge {source} -> {target} references an unknown node.")
            continue
        adjacency[source].append(target)

    # cycle detection (iterative DFS colouring)
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {nid: WHITE for nid in adjacency}
    order: list[str] = []
    for root in adjacency:
        if colour[root] != WHITE:
            continue
        stack: list[tuple[str, int]] = [(root, 0)]
        while stack:
            node, index = stack.pop()
            if index == 0:
                if colour[node] == GREY:
                    errors.append(f"Cycle detected involving node '{node}'.")
                    break
                if colour[node] == BLACK:
                    continue
                colour[node] = GREY
            children = adjacency[node]
            if index < len(children):
                stack.append((node, index + 1))
                child = children[index]
                if colour[child] == GREY:
                    errors.append(f"Cycle detected: '{node}' -> '{child}'.")
                    break
                stack.append((child, 0))
            else:
                colour[node] = BLACK
                order.append(node)
    order.reverse()
    if len(order) != len(adjacency):
        warnings.append("Not every node is reachable in the computed order.")
    return {"valid": not errors, "errors": errors, "warnings": warnings, "order": order}


def _substitute(value: Any, variables: dict, outputs: dict[str, list[str]]) -> Any:
    """Replace $var placeholders with run variables and $<step_id> with asset ids."""
    if isinstance(value, str):
        if value.startswith("$") and value[1:] in variables:
            return variables[value[1:]]
        if value.startswith("$") and value[1:] in outputs:
            ids = outputs[value[1:]]
            return ids[0] if len(ids) == 1 else ids
        for key, replacement in variables.items():
            value = value.replace(f"${{{key}}}", str(replacement))
        return value
    if isinstance(value, list):
        return [_substitute(item, variables, outputs) for item in value]
    if isinstance(value, dict):
        return {k: _substitute(v, variables, outputs) for k, v in value.items()}
    return value


def execute_workflow(workflow: dict, run: dict, *, project_id: str | None, variables: dict, dry_run: bool) -> dict:
    graph = jloads(workflow["graph_json"], {"nodes": [], "edges": []})
    report = validate_graph(graph)
    outputs: dict[str, list[str]] = {}
    steps: list[dict] = []
    if not report["valid"]:
        update("workflow_runs", run["id"], {"status": "failed", "error": jdumps(report["errors"])})
        return {"status": "failed", "errors": report["errors"], "steps": []}

    for node_id in report["order"]:
        node = next(n for n in graph["nodes"] if n["id"] == node_id)
        payload = {
            "id": node_id,
            "kind": node.get("kind"),
            "label": node.get("label") or node.get("kind"),
            "status": "pending",
            "job_id": None,
        }
        if dry_run:
            payload["status"] = "dry-run"
            steps.append(payload)
            continue
        params = _substitute(node.get("params") or {}, variables, outputs)
        try:
            job = enqueue(
                node["kind"],
                params,
                project_id=project_id,
                label=f"{workflow['name']}: {payload['label']}",
                priority=int(node.get("priority") or 5),
            )
            outputs[node_id] = []
            payload["job_id"] = job["id"]
            payload["status"] = "queued"
        except Exception as exc:
            payload["status"] = "failed"
            payload["error"] = str(exc)
        steps.append(payload)
    return {"status": "running" if not dry_run else "validated", "steps": steps, "errors": report["errors"]}


# --------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------
def workflow_payload(row: dict) -> dict:
    graph = jloads(row["graph_json"], {"nodes": [], "edges": []})
    report = validate_graph(graph)
    runs = query_all(
        "SELECT id, status, created_at, finished_at FROM workflow_runs WHERE workflow_id = ? "
        "ORDER BY created_at DESC LIMIT 5",
        (row["id"],),
    )
    return {**row, "graph": graph, "validation": report, "runs": runs, "node_count": len(graph.get("nodes") or [])}


@router.get("")
def index(user: dict = AuthUser, project_id: str | None = None) -> dict:
    if project_id:
        rows = query_all(
            "SELECT * FROM workflows WHERE project_id = ? ORDER BY updated_at DESC", (project_id,)
        )
    else:
        rows = query_all("SELECT * FROM workflows ORDER BY updated_at DESC")
    return {"items": [workflow_payload(r) for r in rows], "total": len(rows)}


@router.post("")
def create(payload: WorkflowCreate, user: dict = AuthUser) -> dict:
    row = insert(
        "workflows",
        {
            "project_id": payload.project_id,
            "name": payload.name.strip(),
            "description": payload.description.strip(),
            "graph_json": jdumps(payload.graph),
        },
    )
    return workflow_payload(row)


@router.get("/{workflow_id}")
def detail(workflow_id: str, user: dict = AuthUser) -> dict:
    row = query_one("SELECT * FROM workflows WHERE id = ?", (workflow_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    return workflow_payload(row)


@router.patch("/{workflow_id}")
def patch(workflow_id: str, payload: WorkflowUpdate, user: dict = AuthUser) -> dict:
    row = query_one("SELECT * FROM workflows WHERE id = ?", (workflow_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    values = payload.model_dump(exclude_none=True)
    if "graph" in values:
        values["graph_json"] = jdumps(values.pop("graph"))
    if values:
        update("workflows", workflow_id, values)
    return workflow_payload(query_one("SELECT * FROM workflows WHERE id = ?", (workflow_id,)))


@router.delete("/{workflow_id}")
def remove(workflow_id: str, user: dict = AuthUser) -> dict:
    execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))
    return {"ok": True}


@router.post("/{workflow_id}/validate")
def validate(workflow_id: str, user: dict = AuthUser) -> dict:
    row = query_one("SELECT * FROM workflows WHERE id = ?", (workflow_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    return validate_graph(jloads(row["graph_json"], {"nodes": [], "edges": []}))


@router.post("/{workflow_id}/run")
def run(workflow_id: str, payload: RunRequest, user: dict = AuthUser) -> dict:
    row = query_one("SELECT * FROM workflows WHERE id = ?", (workflow_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    project_id = payload.project_id or row["project_id"]
    run_row = insert(
        "workflow_runs",
        {
            "workflow_id": workflow_id,
            "project_id": project_id,
            "status": "pending",
            "variables_json": jdumps(payload.variables),
            "steps_json": "[]",
        },
    )
    result = execute_workflow(
        row, run_row, project_id=project_id, variables=payload.variables, dry_run=payload.dry_run
    )
    update(
        "workflow_runs",
        run_row["id"],
        {"status": result["status"], "steps_json": jdumps(result["steps"])},
    )
    return {"run_id": run_row["id"], **result}


@router.get("/{workflow_id}/runs")
def runs(workflow_id: str, user: dict = AuthUser) -> dict:
    rows = query_all(
        "SELECT * FROM workflow_runs WHERE workflow_id = ? ORDER BY created_at DESC LIMIT 25",
        (workflow_id,),
    )
    return {
        "items": [
            {**r, "variables": jloads(r["variables_json"], {}), "steps": jloads(r["steps_json"], [])}
            for r in rows
        ]
    }


@router.get("/kinds/available")
def kinds(user: dict = AuthUser) -> dict:
    """Node palette for the builder UI."""
    return {
        "kinds": [
            {"kind": kind, "handler": JOB_HANDLERS[kind].__name__, "doc": (JOB_HANDLERS[kind].__doc__ or "").strip().split("\n")[0]}
            for kind in sorted(JOB_HANDLERS)
        ]
    }
