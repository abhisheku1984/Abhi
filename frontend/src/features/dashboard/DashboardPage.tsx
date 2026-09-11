import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  AudioLines, Clapperboard, Cpu, Film, FolderKanban, HardDrive, ImageIcon, Layers,
  Mic2, Sparkles, UserSquare2, Users, Wand2,
} from 'lucide-react';
import { Asset, Job, Project, endpoints } from '@/lib/api';
import { AssetCard, AssetModal } from '@/components/assets/AssetCard';
import { Badge, Button, Card, ProgressBar, SectionTitle, Skeleton, StatCard } from '@/components/ui';
import { bytes, relativeTime } from '@/lib/format';
import { useAppStore } from '@/app/store';
import { useJobSocket } from '@/lib/hooks';

const CREATE_CARDS = [
  { label: 'Create image', to: '/create?kind=image', icon: ImageIcon, hint: 'Text, image, edit, inpaint, upscale' },
  { label: 'Create video', to: '/create?kind=video', icon: Film, hint: 'Text / image to video, camera moves' },
  { label: 'Create avatar', to: '/create?kind=avatar', icon: UserSquare2, hint: 'Talking avatar with lip sync' },
  { label: 'Create story', to: '/story', icon: Clapperboard, hint: 'Script → scenes → storyboard → video' },
  { label: 'Create character', to: '/characters', icon: Users, hint: 'Reusable consistent characters' },
  { label: 'Create voice', to: '/voices', icon: Mic2, hint: 'Narration in 13 Indian languages' },
  { label: 'Create audio', to: '/audio', icon: AudioLines, hint: 'Music, ambience and SFX' },
];

export function DashboardPage() {
  const toast = useAppStore((s) => s.toast);
  const user = useAppStore((s) => s.user);
  const [projects, setProjects] = useState<Project[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [stats, setStats] = useState<{ by_kind: Record<string, { count: number; bytes: number }>; total_mb: number } | null>(null);
  const [models, setModels] = useState<{ total: number; ready: number; gpuAvailable: boolean } | null>(null);
  const [loading, setLoading] = useState(true);
  const [openAsset, setOpenAsset] = useState<Asset | null>(null);

  const refresh = () => {
    Promise.all([
      endpoints.projects({ limit: 6 }),
      endpoints.assets({ limit: 12 }),
      endpoints.jobs({ limit: 8 }),
      endpoints.assetStats(),
      endpoints.models(),
    ])
      .then(([p, a, j, s, m]) => {
        setProjects(p.items);
        setAssets(a.items);
        setJobs(j.items);
        setStats(s);
        setModels({
          total: m.items.length,
          ready: m.items.filter((x) => ['installed', 'available'].includes(x.status)).length,
          gpuAvailable: Boolean(m.gpu?.gpu_available),
        });
      })
      .catch((err) => toast({ kind: 'error', title: 'Could not load dashboard', message: (err as Error).message }))
      .finally(() => setLoading(false));
  };

  useEffect(refresh, []);

  useJobSocket((event) => {
    if (['completed', 'failed', 'cancelled'].includes(event.status)) refresh();
    else {
      setJobs((prev) => prev.map((j) => (j.id === event.job_id ? { ...j, status: event.status as Job['status'], progress: event.progress, stage: event.stage } : j)));
    }
  });

  const active = jobs.filter((j) => j.status === 'queued' || j.status === 'processing');

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-ink">
            Welcome back{user?.name ? `, ${user.name.split(' ')[0]}` : ''}
          </h1>
          <p className="mt-0.5 text-xs text-ink-faint">
            One workspace for image, video, avatar, voice and audio production.
          </p>
        </div>
        <Link to="/create"><Button variant="primary" icon={<Sparkles className="h-4 w-4" />}>New creation</Button></Link>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard label="Assets" value={stats ? Object.values(stats.by_kind).reduce((a, b) => a + b.count, 0) : '—'}
          hint={stats ? `${bytes(Object.values(stats.by_kind).reduce((a, b) => a + b.bytes, 0))} stored` : undefined}
          icon={<Layers className="h-4 w-4" />} />
        <StatCard label="Projects" value={projects.length} hint="Most recently updated" icon={<FolderKanban className="h-4 w-4" />} />
        <StatCard label="Active jobs" value={active.length} tone={active.length ? 'brand' : 'default'}
          hint={active.length ? 'Processing now' : 'Queue is clear'} icon={<Cpu className="h-4 w-4" />} />
        <StatCard label="Models ready" value={models ? `${models.ready}/${models.total}` : '—'}
          hint={models?.gpuAvailable ? 'GPU detected' : 'CPU-only machine'}
          tone={models && models.ready < models.total ? 'warn' : 'success'} icon={<Wand2 className="h-4 w-4" />} />
      </div>

      <section>
        <SectionTitle title="Create" subtitle="Start from an idea, an image, a script or a voice" />
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
          {CREATE_CARDS.map((card) => (
            <Link key={card.label} to={card.to}
              className="panel group flex flex-col gap-2 p-3 transition hover:border-brand/50 hover:bg-surface-2/60">
              <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-brand/10 text-brand transition group-hover:bg-brand/20">
                <card.icon className="h-4 w-4" />
              </span>
              <p className="text-xs font-semibold text-ink">{card.label}</p>
              <p className="text-[10px] leading-snug text-ink-faint">{card.hint}</p>
            </Link>
          ))}
        </div>
      </section>

      <div className="grid gap-4 xl:grid-cols-[1.6fr_1fr]">
        <section>
          <SectionTitle
            title="Recent generations"
            subtitle="Every asset keeps its engine, seed and provenance"
            action={<Link to="/assets" className="text-[11px] text-brand hover:underline">View all</Link>}
          />
          {loading ? (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="aspect-[4/3]" />)}
            </div>
          ) : assets.length ? (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              {assets.map((asset) => (
                <AssetCard key={asset.id} asset={asset} onOpen={setOpenAsset} onChanged={refresh} />
              ))}
            </div>
          ) : (
            <Card className="text-center text-xs text-ink-faint">
              Nothing generated yet — open <Link to="/create" className="text-brand hover:underline">Create</Link> to make your first asset.
            </Card>
          )}
        </section>

        <div className="space-y-4">
          <section>
            <SectionTitle title="Processing jobs" subtitle="Live queue status" />
            <Card className="space-y-3 p-3">
              {active.length === 0 && (
                <p className="py-3 text-center text-[11px] text-ink-faint">No jobs running right now.</p>
              )}
              {active.map((job) => (
                <div key={job.id} className="space-y-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-[11px] font-medium text-ink">{job.mode || job.type}</span>
                    <Badge tone={job.status === 'processing' ? 'brand' : 'default'}>{job.status}</Badge>
                  </div>
                  <ProgressBar value={job.progress} />
                  <p className="text-[10px] text-ink-faint">{job.stage || 'queued'} · {job.engine || 'engine pending'}</p>
                </div>
              ))}
              {jobs.filter((j) => j.status === 'failed').slice(0, 2).map((job) => (
                <div key={job.id} className="rounded-lg border border-red-500/30 bg-red-500/5 p-2">
                  <p className="text-[11px] font-medium text-red-300">{job.error?.message ?? 'Job failed'}</p>
                  <p className="text-[10px] text-red-200/70">{job.error?.suggested_action}</p>
                </div>
              ))}
            </Card>
          </section>

          <section>
            <SectionTitle title="Recent projects" action={<Link to="/projects" className="text-[11px] text-brand hover:underline">All</Link>} />
            <Card className="divide-y divide-edge p-0">
              {projects.length === 0 && <p className="p-3 text-center text-[11px] text-ink-faint">No projects yet.</p>}
              {projects.slice(0, 5).map((project) => (
                <Link key={project.id} to={`/projects/${project.id}`}
                  className="flex items-center justify-between gap-3 p-3 transition hover:bg-surface-2/50">
                  <div className="min-w-0">
                    <p className="truncate text-xs font-medium text-ink">{project.name}</p>
                    <p className="text-[10px] text-ink-faint">
                      {project.kind} · {project.counts?.assets ?? 0} assets · {relativeTime(project.updated_at)}
                    </p>
                  </div>
                  <Badge>{project.status}</Badge>
                </Link>
              ))}
            </Card>
          </section>

          <section>
            <SectionTitle title="Storage" />
            <Card className="space-y-2 p-3">
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-ink-dim">Used</span>
                <span className="text-ink">{stats ? bytes(stats.total_mb * 1024 * 1024) : '—'}</span>
              </div>
              <ProgressBar value={user ? Math.min(100, (user.storage_used_mb / Math.max(1, user.storage_quota_mb)) * 100) : 0} />
              <p className="flex items-center gap-1.5 text-[10px] text-ink-faint">
                <HardDrive className="h-3 w-3" /> Local-first storage backend
              </p>
            </Card>
          </section>
        </div>
      </div>

      <AssetModal asset={openAsset} onClose={() => setOpenAsset(null)} onChanged={refresh} />
    </div>
  );
}
