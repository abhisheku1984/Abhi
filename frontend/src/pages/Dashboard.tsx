import { useCallback, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, formatBytes, pollJob, timeAgo, type Asset, type SystemStatus } from "../api";
import { AssetTile, Card, Empty, ModeBadge, Spinner, usePolling, useToast } from "../components/ui";

export default function Dashboard({ status, refresh }: { status: SystemStatus | null; refresh: () => void }) {
  const [stats, setStats] = useState<any>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const { push } = useToast();
  const navigate = useNavigate();

  const load = useCallback(async () => {
    try {
      const [assetStats, recent, projects] = await Promise.all([
        api.assetStats(),
        api.listAssets({ limit: 8, order: "created_at DESC" }),
        api.listProjects(),
      ]);
      setStats({ ...assetStats, projectCount: projects.total });
      setAssets(recent.items);
    } catch {
      /* surfaced elsewhere */
    }
  }, []);

  usePolling(load, 6000);
  usePolling(load, 60000, false); // no-op guard keeps hook order stable

  async function runDemo() {
    setBusy(true);
    setProgress("queuing self-test…");
    try {
      const queued = await api.demoPipeline();
      const job = await pollJob(queued.job_id, (tick) => setProgress(`self-test ${Math.round(tick.progress * 100)}%`));
      if (job.status === "succeeded") {
        push("ok", `Self-test complete: ${job.result.asset_ids?.length ?? 0} assets produced`);
        load();
        refresh();
      } else {
        push("error", job.error || "Self-test failed");
      }
    } catch (error: any) {
      push("error", error.message);
    } finally {
      setBusy(false);
      setProgress(null);
    }
  }

  const caps = status?.capabilities ?? {};
  const counts = stats?.counts ?? {};

  return (
    <div className="page stack" style={{ gap: 16 }}>
      <div className="spread">
        <div>
          <h1>Studio dashboard</h1>
          <p className="muted" style={{ marginBottom: 0 }}>
            Local-first pipeline: real FFmpeg rendering, Pillow image engine and a durable job queue — verified by the
            self-test below.
          </p>
        </div>
        <button className="primary" onClick={runDemo} disabled={busy}>
          {busy ? <Spinner label={progress ?? "running…"} /> : "▶ Run pipeline self-test"}
        </button>
      </div>

      <div className="grid cols-4">
        <Card tight>
          <div className="stat">
            <span className="stat-value">{counts.total ?? 0}</span>
            <span className="stat-label">assets in store</span>
          </div>
        </Card>
        <Card tight>
          <div className="stat">
            <span className="stat-value">{stats?.projectCount ?? 0}</span>
            <span className="stat-label">projects</span>
          </div>
        </Card>
        <Card tight>
          <div className="stat">
            <span className="stat-value">{formatBytes((stats?.by_engine ?? []).reduce((sum: number, e: any) => sum + (e.size_bytes || 0), 0))}</span>
            <span className="stat-label">media size</span>
          </div>
        </Card>
        <Card tight>
          <div className="stat">
            <span className="stat-value">
              {status?.jobs.counts.succeeded ?? 0}
              <span className="faint" style={{ fontSize: 14 }}>
                {" "}
                / {status?.jobs.counts.total ?? 0}
              </span>
            </span>
            <span className="stat-label">jobs succeeded</span>
          </div>
        </Card>
      </div>

      <div className="grid cols-2">
        <Card
          title="Capabilities"
          actions={
            <Link className="btn sm" to="/system">
              Configure
            </Link>
          }
        >
          <div className="stack">
            {Object.entries(caps).map(([capability, mode]) => (
              <div key={capability} className="spread">
                <span style={{ textTransform: "capitalize" }}>{capability.replace("_", " ")}</span>
                <ModeBadge mode={mode} />
              </div>
            ))}
            {!Object.keys(caps).length && <Empty>No capability data yet.</Empty>}
          </div>
        </Card>

        <Card title="Environment">
          <dl className="kv">
            <dt>FFmpeg</dt>
            <dd>{status?.ffmpeg.available ? `${status.ffmpeg.version} (static)` : "not found"}</dd>
            <dt>GPU</dt>
            <dd>{status?.gpu.available ? status.gpu.devices.map((d: any) => d.name).join(", ") : status?.gpu.reason ?? "none"}</dd>
            <dt>CPU / RAM</dt>
            <dd>
              {status?.cpu.cores} cores · {status?.memory.total_gb} GB ({status?.memory.available_gb} GB free)
            </dd>
            <dt>Disk</dt>
            <dd>
              {status?.storage.disk_free_gb} GB free of {status?.storage.disk_total_gb} GB
            </dd>
            <dt>Workers</dt>
            <dd>
              {status?.jobs.workers} of {status?.jobs.configured_workers} job workers
            </dd>
            <dt>Job kinds</dt>
            <dd className="mono">{status?.job_kinds.length ?? 0} registered</dd>
            <dt>Data dir</dt>
            <dd className="mono">{status?.storage.data_dir}</dd>
          </dl>
        </Card>
      </div>

      <Card
        title="Recent assets"
        actions={
          <Link className="btn sm" to="/library">
            Open library
          </Link>
        }
      >
        {assets.length === 0 ? (
          <Empty>
            Nothing generated yet — run the self-test above, or open{" "}
            <Link to="/image" onClick={(e) => { e.preventDefault(); navigate("/image"); }}>
              Image Studio
            </Link>
            .
          </Empty>
        ) : (
          <div className="grid assets">
            {assets.map((asset) => (
              <AssetTile key={asset.id} asset={asset} onClick={() => navigate("/library")} />
            ))}
          </div>
        )}
      </Card>

      {status?.jobs.running?.length ? (
        <Card title="Running now">
          <table>
            <thead>
              <tr>
                <th>Job</th>
                <th>Kind</th>
                <th>Progress</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {status.jobs.running.map((job: any) => (
                <tr key={job.id}>
                  <td className="truncate">{job.label}</td>
                  <td className="mono">{job.kind}</td>
                  <td style={{ minWidth: 120 }}>{Math.round((job.progress || 0) * 100)}%</td>
                  <td className="faint">{job.started_at ? timeAgo(job.started_at) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : null}
    </div>
  );
}
