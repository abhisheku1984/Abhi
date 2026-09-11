import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Archive, History, Layers, Trash2 } from 'lucide-react';
import { Asset, Project, authHeaders, endpoints } from '@/lib/api';
import { AssetCard, AssetModal } from '@/components/assets/AssetCard';
import { Badge, Button, Card, EmptyState, SectionTitle, Tabs } from '@/components/ui';
import { relativeTime } from '@/lib/format';
import { useAppStore } from '@/app/store';

type Tab = 'assets' | 'scenes' | 'versions';

export function ProjectDetailPage() {
  const { id = '' } = useParams();
  const navigate = useNavigate();
  const toast = useAppStore((s) => s.toast);
  const [project, setProject] = useState<Project | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [scenes, setScenes] = useState<any[]>([]);
  const [versions, setVersions] = useState<any[]>([]);
  const [tab, setTab] = useState<Tab>('assets');
  const [openAsset, setOpenAsset] = useState<Asset | null>(null);

  const load = () => {
    if (!id) return;
    endpoints.project(id).then(setProject).catch(() => setProject(null));
    endpoints.projectAssets(id).then((r) => setAssets(r.items)).catch(() => setAssets([]));
    endpoints.story(id).then((r) => setScenes(r.scenes)).catch(() => setScenes([]));
    endpoints.projects().catch(() => null);
  };

  useEffect(load, [id]);

  useEffect(() => {
    if (tab === 'versions' && id) {
      fetch(`/api/v1/projects/${id}/versions`, {
        headers: authHeaders(),
      })
        .then((r) => r.json())
        .then((d) => setVersions(d.items ?? []))
        .catch(() => setVersions([]));
    }
  }, [tab, id]);

  if (!project) {
    return <Card className="text-center text-xs text-ink-faint">Loading project…</Card>;
  }

  async function snapshot() {
    try {
      await fetch(`/api/v1/projects/${id}/versions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ label: 'Manual snapshot' }),
      });
      toast({ kind: 'success', title: 'Version saved' });
      setTab('versions');
      load();
    } catch (err) {
      toast({ kind: 'error', title: 'Could not save version', message: (err as Error).message });
    }
  }

  async function restore(versionId: string) {
    try {
      await fetch(`/api/v1/projects/${id}/versions/${versionId}/restore`, {
        method: 'POST',
        headers: authHeaders(),
      });
      toast({ kind: 'success', title: 'Version restored' });
      load();
    } catch (err) {
      toast({ kind: 'error', title: 'Restore failed', message: (err as Error).message });
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <button onClick={() => navigate('/projects')} className="mt-1 text-ink-faint hover:text-ink">
            <ArrowLeft className="h-4 w-4" />
          </button>
          <div>
            <h1 className="text-lg font-semibold tracking-tight text-ink">{project.name}</h1>
            <p className="text-xs text-ink-faint">
              {project.description || 'No description'} · updated {relativeTime(project.updated_at)}
            </p>
          </div>
        </div>
        <div className="flex gap-2">
          <Button icon={<History className="h-4 w-4" />} onClick={() => void snapshot()}>Save version</Button>
          <Link to={`/story/${project.id}`}><Button variant="primary" icon={<Layers className="h-4 w-4" />}>
            Open story
          </Button></Link>
        </div>
      </div>

      <Tabs
        tabs={[
          { id: 'assets', label: `Assets (${assets.length})` },
          { id: 'scenes', label: `Scenes (${scenes.length})` },
          { id: 'versions', label: 'Version history' },
        ]}
        value={tab}
        onChange={(value) => setTab(value as Tab)}
      />

      {tab === 'assets' && (
        assets.length === 0 ? (
          <EmptyState icon={<Layers className="h-5 w-5" />} title="No assets in this project yet"
            description="Generate something with this project selected and it will appear here."
            action={<Link to="/create"><Button variant="primary">Open workspace</Button></Link>} />
        ) : (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
            {assets.map((asset) => (
              <AssetCard key={asset.id} asset={asset} onOpen={setOpenAsset} onChanged={load} />
            ))}
          </div>
        )
      )}

      {tab === 'scenes' && (
        scenes.length === 0 ? (
          <EmptyState icon={<Layers className="h-5 w-5" />} title="No scenes yet"
            description="Use the story engine to turn an idea into scenes, shots and video."
            action={<Link to={`/story/${project.id}`}><Button variant="primary">Create story</Button></Link>} />
        ) : (
          <div className="space-y-2">
            {scenes.map((scene) => (
              <Card key={scene.id} className="flex gap-3 p-3">
                <div className="h-20 w-32 shrink-0 overflow-hidden rounded-lg border border-edge bg-surface-2">
                  {scene.image_url && <img src={scene.image_url} alt="" className="h-full w-full object-cover" />}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="truncate text-sm font-medium text-ink">{scene.title}</p>
                    <Badge tone={scene.status === 'ready' ? 'success' : 'default'}>{scene.status}</Badge>
                  </div>
                  <p className="mt-1 line-clamp-2 text-[11px] text-ink-faint">{scene.narration}</p>
                </div>
              </Card>
            ))}
          </div>
        )
      )}

      {tab === 'versions' && (
        versions.length === 0 ? (
          <EmptyState icon={<Archive className="h-5 w-5" />} title="No versions saved"
            description="Snapshots capture scenes, shots and characters so you can roll back safely." />
        ) : (
          <div className="space-y-2">
            {versions.map((version) => (
              <Card key={version.id} className="flex items-center justify-between gap-3 p-3">
                <div>
                  <p className="text-sm text-ink">v{version.version} · {version.label}</p>
                  <p className="text-[11px] text-ink-faint">
                    {version.entity} · {relativeTime(version.created_at)} · {version.created_by || 'system'}
                  </p>
                </div>
                <Button size="sm" icon={<History className="h-3.5 w-3.5" />} onClick={() => void restore(version.id)}>
                  Restore
                </Button>
              </Card>
            ))}
          </div>
        )
      )}

      <AssetModal asset={openAsset} onClose={() => setOpenAsset(null)} onChanged={load} />
    </div>
  );
}
