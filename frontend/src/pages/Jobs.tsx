import { useCallback, useEffect, useState } from "react";
import { api, timeAgo, type Job } from "../api";
import { Card, Empty, JobLog, ProgressBar, StatusBadge, usePolling, useToast } from "../components/ui";

export default function JobsPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [stats, setStats] = useState<any>(null);
  const [status, setStatus] = useState("");
  const [selected, setSelected] = useState<Job | null>(null);
  const { push } = useToast();

  const load = useCallback(async () => {
    try {
      const [list, statsData] = await Promise.all([api.listJobs({ status, limit: 80 }), api.jobStats()]);
      setJobs(list.items);
      setStats(statsData);
      if (selected) {
        const fresh = await api.getJob(selected.id).catch(() => null);
        if (fresh) setSelected(fresh);
      }
    } catch {
      /* ignore transient errors while polling */
    }
  }, [status, selected?.id]);

  useEffect(() => {
    load();
  }, [load]);
  usePolling(load, 2500);

  async function act(job: Job, action: "cancel" | "retry" | "delete") {
    try {
      if (action === "cancel") await api.cancelJob(job.id);
      if (action === "retry") await api.retryJob(job.id);
      if (action === "delete") await api.deleteJob(job.id);
      push("ok", `Job ${action}led`);
      load();
    } catch (error: any) {
      push("error", error.message);
    }
  }

  const counts = stats?.counts ?? {};

  return (
    <div className="page stack" style={{ gap: 16 }}>
      <div className="spread">
        <div>
          <h1>Job queue</h1>
          <p className="muted" style={{ marginBottom: 0 }}>
            Durable SQLite-backed queue · {stats?.workers ?? 0} worker(s) · survives restarts
          </p>
        </div>
        <div className="row">
          <select value={status} onChange={(event) => setStatus(event.target.value)} style={{ width: 170 }}>
            <option value="">All statuses</option>
            <option value="queued">Queued</option>
            <option value="running">Running</option>
            <option value="succeeded">Succeeded</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
          </select>
          <button onClick={load}>⟳ Refresh</button>
        </div>
      </div>

      <div className="grid cols-4">
        {[
          ["queued", counts.queued],
          ["running", counts.running],
          ["succeeded", counts.succeeded],
          ["failed", counts.failed],
        ].map(([label, value]) => (
          <Card key={label as string} tight>
            <div className="stat">
              <span className="stat-value">{String(value ?? 0)}</span>
              <span className="stat-label">{label}</span>
            </div>
          </Card>
        ))}
      </div>

      <Card title={`Jobs (${jobs.length})`}>
        {jobs.length === 0 ? (
          <Empty>No jobs recorded yet.</Empty>
        ) : (
          <div className="scroll-x">
            <table>
              <thead>
                <tr>
                  <th>Job</th>
                  <th>Kind</th>
                  <th>Status</th>
                  <th style={{ width: 140 }}>Progress</th>
                  <th>Created</th>
                  <th>Duration</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.id} style={{ cursor: "pointer" }} onClick={() => setSelected(job)}>
                    <td className="truncate" style={{ maxWidth: 320 }}>
                      {job.label}
                      {job.error && <div className="faint" style={{ color: "#fca5a5" }}>{job.error.slice(0, 120)}</div>}
                    </td>
                    <td className="mono">{job.kind}</td>
                    <td>
                      <StatusBadge status={job.status} />
                    </td>
                    <td>
                      <ProgressBar value={job.progress} />
                    </td>
                    <td className="faint">{timeAgo(job.created_at)}</td>
                    <td className="faint">{job.duration_ms ? `${(job.duration_ms / 1000).toFixed(1)}s` : "—"}</td>
                    <td onClick={(event) => event.stopPropagation()}>
                      <div className="row">
                        {(job.status === "queued" || job.status === "running") && (
                          <button className="sm" onClick={() => act(job, "cancel")}>
                            Cancel
                          </button>
                        )}
                        {(job.status === "failed" || job.status === "cancelled") && (
                          <button className="sm" onClick={() => act(job, "retry")}>
                            Retry
                          </button>
                        )}
                        {job.status !== "running" && (
                          <button className="sm danger" onClick={() => act(job, "delete")}>
                            ✕
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {selected && (
        <Card
          title={`${selected.kind} · ${selected.status}`}
          actions={
            <button className="sm" onClick={() => setSelected(null)}>
              Close
            </button>
          }
        >
          <div className="grid cols-2">
            <div className="stack">
              <div className="kv">
                <dt>Job id</dt>
                <dd className="mono">{selected.id}</dd>
                <dt>Attempts</dt>
                <dd>
                  {selected.attempts} / {selected.max_attempts}
                </dd>
                <dt>Provider</dt>
                <dd className="mono">{String((selected as any).provider || "auto")}</dd>
                <dt>Created</dt>
                <dd>{selected.created_at}</dd>
                <dt>Finished</dt>
                <dd>{selected.finished_at ?? "—"}</dd>
              </div>
              <label>Parameters</label>
              <pre className="log" style={{ maxHeight: 180 }}>{JSON.stringify(selected.params, null, 2)}</pre>
            </div>
            <div className="stack">
              <label>Log</label>
              <JobLog job={selected} />
              {selected.result?.asset_ids?.length ? (
                <>
                  <label>Produced assets</label>
                  <div className="mono faint">{selected.result.asset_ids.join(", ")}</div>
                </>
              ) : null}
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
