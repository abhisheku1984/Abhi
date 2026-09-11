import { useEffect, useState } from 'react';
import { Activity, Cpu, Database, HardDrive, ShieldCheck, Users2 } from 'lucide-react';
import { endpoints } from '@/lib/api';
import { Badge, Button, Card, ErrorCallout, ProgressBar, SectionTitle, Select, Skeleton, StatCard, Tabs, Toggle } from '@/components/ui';
import { bytes } from '@/lib/format';
import { useAppStore } from '@/app/store';

type Tab = 'overview' | 'users' | 'jobs' | 'health' | 'logs' | 'flags';

export function AdminPage() {
  const user = useAppStore((s) => s.user);
  const toast = useAppStore((s) => s.toast);
  const [tab, setTab] = useState<Tab>('overview');
  const [overview, setOverview] = useState<any>(null);
  const [users, setUsers] = useState<any[]>([]);
  const [jobs, setJobs] = useState<any[]>([]);
  const [health, setHealth] = useState<any>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [flags, setFlags] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    setError(null);
    Promise.all([
      endpoints.adminOverview(),
      endpoints.adminUsers(),
      endpoints.adminJobs(),
      endpoints.adminHealth(),
      endpoints.adminFlags(),
      endpoints.adminLogs(120),
    ])
      .then(([o, u, j, h, f, l]) => {
        setOverview(o); setUsers(u.items ?? []); setJobs(j.items ?? []);
        setHealth(h); setFlags(f.items ?? []); setLogs(l.items ?? []);
      })
      .catch((err) => setError((err as Error).message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { if (user?.role === 'admin' || user?.role === 'owner') load(); }, [user]);

  async function setFlag(key: string, enabled: boolean) {
    await endpoints.setFlag({ key, enabled });
    toast({ kind: 'success', title: `Flag ${enabled ? 'enabled' : 'disabled'}`, message: key });
    load();
  }

  async function updateRole(id: string, role: string) {
    await endpoints.updateUser(id, { role });
    toast({ kind: 'success', title: 'User updated' });
    load();
  }

  if (user?.role !== 'admin' && user?.role !== 'owner') {
    return (
      <Card>
        <ErrorCallout title="Administrator access required"
          message="This panel is only available to owner/admin accounts."
          action="Sign in with an admin account to continue" />
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-ink">Admin panel</h1>
          <p className="text-xs text-ink-faint">Users, jobs, models, GPU, storage, moderation and logs.</p>
        </div>
        <Button onClick={load}>Refresh</Button>
      </div>

      <Tabs
        tabs={[
          { id: 'overview', label: 'Overview' }, { id: 'users', label: 'Users' },
          { id: 'jobs', label: 'Jobs' }, { id: 'health', label: 'System health' },
          { id: 'flags', label: 'Feature flags' }, { id: 'logs', label: 'Logs' },
        ]}
        value={tab}
        onChange={(id) => setTab(id as Tab)}
      />

      {error && <ErrorCallout message={error} action="Check that the backend is running" />}

      {loading && !overview ? (
        <div className="grid gap-3 md:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-24" />)}
        </div>
      ) : tab === 'overview' && overview ? (
        <div className="space-y-4">
          <div className="grid gap-3 md:grid-cols-5">
            <StatCard label="Users" value={overview.counts?.users ?? 0} icon={<Users2 className="h-4 w-4" />} />
            <StatCard label="Projects" value={overview.counts?.projects ?? 0} />
            <StatCard label="Assets" value={overview.counts?.assets ?? 0} icon={<HardDrive className="h-4 w-4" />} />
            <StatCard label="Generations" value={overview.counts?.generations ?? 0} />
            <StatCard label="Workflows" value={overview.counts?.workflows ?? 0} />
          </div>

          <div className="grid gap-3 lg:grid-cols-3">
            <Card>
              <SectionTitle title="Queue" />
              <div className="space-y-1 text-[11px] text-ink-dim">
                {Object.entries(overview.jobs?.counts ?? {}).map(([k, v]) => (
                  <div key={k} className="flex justify-between"><span>{k}</span><span className="text-ink">{String(v)}</span></div>
                ))}
              </div>
            </Card>
            <Card>
              <SectionTitle title="GPU" />
              <div className="space-y-1 text-[11px] text-ink-dim">
                <div className="flex justify-between"><span>available</span>
                  <span className="text-ink">{String(overview.gpu?.gpu_available)}</span></div>
                <div className="flex justify-between"><span>backend</span>
                  <span className="text-ink">{String(overview.gpu?.backend)}</span></div>
                <div className="flex justify-between"><span>devices</span>
                  <span className="text-ink">{String(overview.gpu?.device_count ?? 0)}</span></div>
              </div>
            </Card>
            <Card>
              <SectionTitle title="Storage" />
              <div className="space-y-1 text-[11px] text-ink-dim">
                <div className="flex justify-between"><span>backend</span>
                  <span className="text-ink">{String(overview.storage?.backend)}</span></div>
                <div className="flex justify-between"><span>assets</span>
                  <span className="text-ink">{String(overview.storage?.asset_mb ?? 0)} MB</span></div>
                <div className="flex justify-between"><span>free disk</span>
                  <span className="text-ink">{String(overview.storage?.disk?.free_gb ?? '—')} GB</span></div>
              </div>
            </Card>
          </div>

          <Card>
            <SectionTitle title="Engines" subtitle="Adapter registry health" />
            <div className="grid gap-2 md:grid-cols-4">
              {Object.entries(overview.engines?.families ?? {}).map(([family, value]: [string, any]) => (
                <div key={family} className="panel-2 p-2.5">
                  <p className="text-xs text-ink">{family}</p>
                  <div className="mt-1 flex items-center gap-1.5">
                    <Badge tone={value.ready ? 'success' : 'warn'}>{value.ready}/{value.count} ready</Badge>
                  </div>
                </div>
              ))}
            </div>
          </Card>

          <Card>
            <SectionTitle title="FFmpeg" />
            <div className="flex flex-wrap items-center gap-2 text-[11px]">
              <Badge tone={overview.ffmpeg?.available ? 'success' : 'danger'}>
                {overview.ffmpeg?.available ? 'available' : 'missing'}
              </Badge>
              <span className="text-ink-faint">{overview.ffmpeg?.version}</span>
              <span className="text-ink-faint">· source: {overview.ffmpeg?.source}</span>
            </div>
          </Card>
        </div>
      ) : tab === 'users' ? (
        <Card className="p-0">
          <div className="divide-y divide-edge">
            {users.map((u) => (
              <div key={u.id} className="flex flex-wrap items-center justify-between gap-2 p-3">
                <div>
                  <p className="text-xs text-ink">{u.name || u.email}</p>
                  <p className="text-[10px] text-ink-faint">
                    {u.email} · {u.projects} projects · {u.assets} assets · last login {u.last_login_at ?? 'never'}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge tone={u.is_active ? 'success' : 'warn'}>{u.is_active ? 'active' : 'disabled'}</Badge>
                  <Select value={u.role} onChange={(e) => void updateRole(u.id, e.target.value)}
                    options={['viewer', 'editor', 'admin', 'owner'].map((r) => ({ value: r, label: r }))}
                    className="w-28" />
                </div>
              </div>
            ))}
          </div>
        </Card>
      ) : tab === 'jobs' ? (
        <Card className="space-y-2 p-2">
          {jobs.map((job) => (
            <div key={job.id} className="panel-2 flex flex-wrap items-center gap-2 p-2.5">
              <Badge tone={job.status === 'completed' ? 'success' : job.status === 'failed' ? 'danger' : 'brand'}>
                {job.status}
              </Badge>
              <span className="text-[11px] text-ink">{job.mode || job.type}</span>
              <span className="text-[10px] text-ink-faint">{job.engine}</span>
              <div className="ml-auto min-w-[120px] flex-1"><ProgressBar value={job.progress ?? 0} /></div>
              <span className="font-mono text-[10px] text-ink-faint">{job.id.slice(0, 12)}</span>
            </div>
          ))}
        </Card>
      ) : tab === 'health' && health ? (
        <div className="grid gap-3 lg:grid-cols-2">
          <Card>
            <SectionTitle title="System" icon={<Activity className="h-4 w-4" />} />
            <pre className="max-h-80 overflow-auto rounded-lg border border-edge bg-surface-2/40 p-2.5 text-[10px] leading-relaxed text-ink-dim scroll-thin">
{JSON.stringify(health, null, 2)}
            </pre>
          </Card>
          <Card>
            <SectionTitle title="Database" icon={<Database className="h-4 w-4" />} />
            <div className="space-y-1 text-[11px] text-ink-dim">
              <p>dialect: {health.database?.dialect}</p>
              <p>url: {health.database?.url}</p>
            </div>
            <SectionTitle title="Safety" />
            <div className="space-y-1 text-[11px] text-ink-dim">
              <p>moderation: {String(health.safety?.enabled)}</p>
              <p>watermark: {String(health.safety?.watermark)}</p>
            </div>
            <SectionTitle title="Worker" />
            <div className="space-y-1 text-[11px] text-ink-dim">
              <p>running: {String(health.worker?.running)}</p>
              <p>workers: {health.worker?.size}</p>
            </div>
          </Card>
        </div>
      ) : tab === 'flags' ? (
        <Card className="space-y-1">
          {flags.map((flag) => (
            <Toggle key={flag.key} label={flag.key} checked={flag.enabled}
              onChange={(v) => void setFlag(flag.key, v)} />
          ))}
          {flags.length === 0 && <p className="p-2 text-[11px] text-ink-faint">No feature flags stored yet.</p>}
        </Card>
      ) : tab === 'logs' ? (
        <Card>
          <SectionTitle title="Recent log lines" subtitle="backend/data/logs/studio.log" />
          <pre className="max-h-[520px] overflow-auto rounded-lg border border-edge bg-black/40 p-3 text-[10px] leading-relaxed text-ink-dim scroll-thin">
{logs.slice(-120).join('\n')}
          </pre>
        </Card>
      ) : null}
    </div>
  );
}
