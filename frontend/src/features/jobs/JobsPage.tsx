import { useEffect, useState } from 'react';
import { RefreshCw, X } from 'lucide-react';
import { Job, endpoints } from '@/lib/api';
import { Badge, Button, Card, EmptyState, ProgressBar, SectionTitle, Tabs } from '@/components/ui';
import { relativeTime } from '@/lib/format';
import { useAppStore } from '@/app/store';
import { useJobSocket } from '@/lib/hooks';

const STATUS_TABS = [
  { id: '', label: 'All' }, { id: 'queued', label: 'Queued' }, { id: 'processing', label: 'Processing' },
  { id: 'completed', label: 'Completed' }, { id: 'failed', label: 'Failed' },
] as const;

const toneFor = (status: string) =>
  status === 'completed' ? 'success' : status === 'failed' ? 'danger'
    : status === 'cancelled' ? 'warn' : status === 'processing' ? 'brand' : 'default';

export function JobsPage() {
  const toast = useAppStore((s) => s.toast);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [status, setStatus] = useState<string>('');
  const [stats, setStats] = useState<Record<string, number>>({});

  const load = () => {
    endpoints.jobs({ status: status || undefined, limit: 60 })
      .then((r) => setJobs(r.items))
      .catch(() => setJobs([]));
    endpoints.jobStats().then((s) => setStats(s as Record<string, number>)).catch(() => setStats({}));
  };

  useEffect(load, [status]);

  useJobSocket((event) => {
    setJobs((prev) => {
      const index = prev.findIndex((j) => j.id === event.job_id);
      if (index === -1) return prev;
      const next = [...prev];
      next[index] = { ...next[index], status: event.status as Job['status'], progress: event.progress, stage: event.stage };
      return next;
    });
    if (['completed', 'failed', 'cancelled'].includes(event.status)) load();
  });

  async function cancel(id: string) {
    await endpoints.cancelJob(id);
    toast({ kind: 'info', title: 'Cancel requested' });
    load();
  }

  async function retry(id: string) {
    await endpoints.retryJob(id);
    toast({ kind: 'success', title: 'Job requeued' });
    load();
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-ink">Job queue</h1>
          <p className="text-xs text-ink-faint">
            Every generation is a durable background job with real progress and error reporting.
          </p>
        </div>
        <Button icon={<RefreshCw className="h-4 w-4" />} onClick={load}>Refresh</Button>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {[
          ['Queued', stats.queued ?? 0], ['Processing', stats.processing ?? 0],
          ['Completed', stats.completed ?? 0], ['Failed', stats.failed ?? 0], ['Cancelled', stats.cancelled ?? 0],
        ].map(([label, value]) => (
          <Card key={label as string}>
            <p className="label">{label}</p>
            <p className="mt-1 text-xl font-semibold tabular-nums text-ink">{value as number}</p>
          </Card>
        ))}
      </div>

      <Tabs tabs={STATUS_TABS.map((t) => ({ id: t.id, label: t.label }))} value={status}
        onChange={(id) => setStatus(id)} />

      {jobs.length === 0 ? (
        <EmptyState title="No jobs" description="Jobs appear here as soon as you generate something." />
      ) : (
        <Card className="space-y-2 p-2">
          {jobs.map((job) => (
            <div key={job.id} className="panel-2 space-y-1.5 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={toneFor(job.status)}>{job.status}</Badge>
                <span className="text-xs font-medium text-ink">{job.mode || job.type}</span>
                <span className="text-[11px] text-ink-faint">{job.engine || 'engine pending'}</span>
                <span className="ml-auto font-mono text-[10px] text-ink-faint">{job.id}</span>
              </div>
              <ProgressBar value={job.progress} showLabel />
              <div className="flex flex-wrap items-center gap-2 text-[10px] text-ink-faint">
                <span>{job.stage || '—'}</span>
                <span>· queued {relativeTime(job.queued_at)}</span>
                {job.duration_seconds != null && <span>· took {job.duration_seconds}s</span>}
                {job.attempts > 1 && <span>· attempt {job.attempts}</span>}
                {job.error?.message && <span className="text-red-400">· {job.error.message}</span>}
              </div>
              {job.error && (
                <div className="flex items-center justify-between gap-2 rounded-lg border border-red-500/30 bg-red-500/5 p-2">
                  <p className="text-[10px] text-red-200/80">{job.error.suggested_action}</p>
                  <Button size="sm" variant="ghost" onClick={() => void retry(job.id)}>Retry</Button>
                </div>
              )}
              {['queued', 'processing'].includes(job.status) && (
                <div className="flex justify-end">
                  <Button size="sm" variant="ghost" icon={<X className="h-3.5 w-3.5" />}
                    onClick={() => void cancel(job.id)}>Cancel</Button>
                </div>
              )}
            </div>
          ))}
        </Card>
      )}
    </div>
  );
}
