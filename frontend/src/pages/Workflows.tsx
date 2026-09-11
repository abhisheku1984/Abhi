import { useCallback, useEffect, useState } from "react";
import { api, timeAgo, type Project, type Workflow } from "../api";
import { Card, Empty, StatusBadge, useToast } from "../components/ui";

const TEMPLATE = {
  nodes: [
    {
      id: "art",
      kind: "image.generate",
      label: "Generate artwork",
      params: { prompt: "$prompt", width: 1280, height: 720, character_ids: [] },
    },
    {
      id: "sheet",
      kind: "image.montage",
      label: "Contact sheet",
      params: { asset_ids: "$art", columns: 2, cell: 320 },
    },
  ],
  edges: [{ from: "art", to: "sheet" }],
};

export default function Workflows() {
  const [items, setItems] = useState<Workflow[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [selected, setSelected] = useState<Workflow | null>(null);
  const [graphText, setGraphText] = useState(JSON.stringify(TEMPLATE, null, 2));
  const [variables, setVariables] = useState('{"prompt": "a weathered lighthouse at dusk"}');
  const [name, setName] = useState("My workflow");
  const [run, setRun] = useState<any>(null);
  const [kinds, setKinds] = useState<{ kind: string; doc: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const { push } = useToast();

  const load = useCallback(async () => {
    try {
      const [workflowList, projectList, kindList] = await Promise.all([
        api.listWorkflows(projectId || undefined),
        api.listProjects(),
        api.jobKinds(),
      ]);
      setItems(workflowList.items);
      setProjects(projectList.items);
      setKinds(kindList.kinds);
    } catch (error: any) {
      push("error", error.message);
    }
  }, [projectId, push]);

  useEffect(() => {
    load();
  }, [load]);

  function parseGraph(): any | null {
    try {
      return JSON.parse(graphText);
    } catch (error: any) {
      push("error", `Graph JSON is invalid: ${error.message}`);
      return null;
    }
  }

  async function save() {
    const graph = parseGraph();
    if (!graph) return;
    setBusy(true);
    try {
      const workflow = selected
        ? await api.updateWorkflow(selected.id, { graph, name })
        : await api.createWorkflow({ name, graph, project_id: projectId || null });
      setSelected(workflow);
      push("ok", `Workflow saved${workflow.validation.valid ? "" : " — validation has errors"}`);
      load();
    } catch (error: any) {
      push("error", error.message);
    } finally {
      setBusy(false);
    }
  }

  async function validate() {
    if (!selected) return push("error", "Save the workflow first");
    try {
      const result = await api.validateWorkflow(selected.id);
      push(result.valid ? "ok" : "error", result.valid ? `Valid — execution order: ${result.order.join(" → ")}` : result.errors.join("; "));
    } catch (error: any) {
      push("error", error.message);
    }
  }

  async function execute(dryRun: boolean) {
    if (!selected) return push("error", "Save the workflow first");
    setBusy(true);
    try {
      const parsedVariables = JSON.parse(variables || "{}");
      const result = await api.runWorkflow(selected.id, { variables: parsedVariables, dry_run: dryRun });
      setRun(result);
      push(result.status === "failed" ? "error" : "ok", dryRun ? "Dry run complete" : `Run started with ${result.steps.length} step(s)`);
      load();
    } catch (error: any) {
      push("error", error.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page stack" style={{ gap: 16 }}>
      <div className="spread">
        <div>
          <h1>Workflow builder</h1>
          <p className="muted" style={{ marginBottom: 0 }}>
            Compose registered job kinds into a DAG. <span className="mono">$step_id</span> passes a step's asset ids to
            the next.
          </p>
        </div>
        <div className="row">
          <select value={projectId} onChange={(event) => setProjectId(event.target.value)} style={{ width: 200 }}>
            <option value="">All projects</option>
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
          <button
            onClick={() => {
              setSelected(null);
              setName("My workflow");
              setGraphText(JSON.stringify(TEMPLATE, null, 2));
              setRun(null);
            }}
          >
            + New
          </button>
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: "minmax(300px, 360px) 1fr", alignItems: "start" }}>
        <div className="stack">
          <Card title={`Saved (${items.length})`}>
            {items.length === 0 ? (
              <Empty>No workflows yet.</Empty>
            ) : (
              <div className="stack">
                {items.map((workflow) => (
                  <button
                    key={workflow.id}
                    className={selected?.id === workflow.id ? "primary" : ""}
                    style={{ justifyContent: "space-between" }}
                    onClick={() => {
                      setSelected(workflow);
                      setName(workflow.name);
                      setGraphText(JSON.stringify(workflow.graph, null, 2));
                      setRun(null);
                    }}
                  >
                    <span className="truncate" style={{ maxWidth: 200 }}>
                      {workflow.name}
                    </span>
                    <span className="faint">{workflow.node_count} nodes</span>
                  </button>
                ))}
              </div>
            )}
          </Card>

          <Card title="Available node kinds">
            <div className="stack" style={{ maxHeight: 320, overflowY: "auto" }}>
              {kinds.map((kind) => (
                <div key={kind.kind}>
                  <span className="mono">{kind.kind}</span>
                  {kind.doc && <div className="faint">{kind.doc}</div>}
                </div>
              ))}
            </div>
          </Card>
        </div>

        <div className="stack">
          <Card
            title="Graph"
            actions={
              <div className="row">
                <input value={name} onChange={(event) => setName(event.target.value)} style={{ width: 200 }} />
                <button className="sm" onClick={validate}>
                  Validate
                </button>
                <button className="sm primary" onClick={save} disabled={busy}>
                  Save
                </button>
              </div>
            }
          >
            <textarea value={graphText} onChange={(event) => setGraphText(event.target.value)} rows={16} className="mono" />
            {selected && (
              <div className="row" style={{ marginTop: 10 }}>
                <span className={`badge ${selected.validation.valid ? "succeeded" : "failed"}`}>
                  {selected.validation.valid ? "valid" : "invalid"}
                </span>
                {selected.validation.errors.map((error) => (
                  <span key={error} className="faint" style={{ color: "#fca5a5" }}>
                    {error}
                  </span>
                ))}
              </div>
            )}
          </Card>

          <Card title="Run">
            <div className="field">
              <label>Variables (JSON) — referenced as $name in node params</label>
              <textarea value={variables} onChange={(event) => setVariables(event.target.value)} rows={3} className="mono" />
            </div>
            <div className="row">
              <button onClick={() => execute(true)} disabled={busy || !selected}>
                Dry run
              </button>
              <button className="primary" onClick={() => execute(false)} disabled={busy || !selected}>
                ▶ Run workflow
              </button>
            </div>
            {run && (
              <table style={{ marginTop: 12 }}>
                <thead>
                  <tr>
                    <th>Step</th>
                    <th>Kind</th>
                    <th>Status</th>
                    <th>Job</th>
                  </tr>
                </thead>
                <tbody>
                  {run.steps.map((step: any) => (
                    <tr key={step.id}>
                      <td>{step.label}</td>
                      <td className="mono">{step.kind}</td>
                      <td>
                        <StatusBadge status={step.status} />
                      </td>
                      <td className="mono faint">{step.job_id?.slice(0, 12) ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>

          {selected?.runs?.length ? (
            <Card title="Recent runs">
              <table>
                <tbody>
                  {selected.runs.map((entry) => (
                    <tr key={entry.id}>
                      <td className="mono">{entry.id.slice(0, 12)}</td>
                      <td>
                        <StatusBadge status={entry.status} />
                      </td>
                      <td className="faint">{timeAgo(entry.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Card>
          ) : null}
        </div>
      </div>
    </div>
  );
}
