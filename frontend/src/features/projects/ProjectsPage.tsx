import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { FolderKanban, Plus, Search, Star } from 'lucide-react';
import { Project, endpoints } from '@/lib/api';
import { Badge, Button, Card, EmptyState, Input, Modal, Select, Skeleton, StatCard, TextArea } from '@/components/ui';
import { relativeTime } from '@/lib/format';
import { useAppStore } from '@/app/store';

export function ProjectsPage() {
  const toast = useAppStore((s) => s.toast);
  const [items, setItems] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState('');
  const [favorite, setFavorite] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ name: '', description: '', kind: 'general' });

  const load = () => {
    setLoading(true);
    endpoints.projects({ q: query || undefined, kind: kind || undefined, favorite: favorite || undefined, limit: 100 })
      .then((r) => setItems(r.items))
      .catch((err) => toast({ kind: 'error', title: 'Could not load projects', message: (err as Error).message }))
      .finally(() => setLoading(false));
  };

  useEffect(load, [query, kind, favorite]);

  async function create() {
    try {
      const project = await endpoints.createProject(form);
      toast({ kind: 'success', title: 'Project created' });
      setCreating(false);
      setForm({ name: '', description: '', kind: 'general' });
      load();
      return project;
    } catch (err) {
      toast({ kind: 'error', title: 'Could not create project', message: (err as Error).message });
    }
  }

  async function toggleFavorite(project: Project) {
    await endpoints.updateProject(project.id, { is_favorite: !project.is_favorite });
    load();
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-ink">Projects</h1>
          <p className="text-xs text-ink-faint">Every generation belongs to a project with version history.</p>
        </div>
        <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setCreating(true)}>New project</Button>
      </div>

      <div className="grid gap-3 md:grid-cols-4">
        <StatCard label="Total projects" value={items.length} icon={<FolderKanban className="h-4 w-4" />} />
        <StatCard label="Story projects" value={items.filter((p) => p.kind === 'story').length} />
        <StatCard label="Video projects" value={items.filter((p) => p.kind === 'video').length} />
        <StatCard label="Favourites" value={items.filter((p) => p.is_favorite).length} />
      </div>

      <Card className="flex flex-wrap items-center gap-3">
        <div className="min-w-[220px] flex-1">
          <Input placeholder="Search projects" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <Select value={kind} onChange={(e) => setKind(e.target.value)} className="w-40"
          options={[{ value: '', label: 'All kinds' }, { value: 'general', label: 'general' },
            { value: 'image', label: 'image' }, { value: 'video', label: 'video' },
            { value: 'avatar', label: 'avatar' }, { value: 'story', label: 'story' }]} />
        <Button variant={favorite ? 'subtle' : 'secondary'} icon={<Star className="h-4 w-4" />}
          onClick={() => setFavorite(!favorite)}>Favourites</Button>
      </Card>

      {loading ? (
        <div className="grid gap-3 md:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-32" />)}
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={<FolderKanban className="h-5 w-5" />}
          title="No projects yet"
          description="Projects hold your characters, scenes, shots, assets and version history."
          action={<Button variant="primary" onClick={() => setCreating(true)}>Create your first project</Button>}
        />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {items.map((project) => (
            <Card key={project.id} hover className="relative">
              <Link to={`/projects/${project.id}`} className="block">
                <div className="mb-3 flex h-28 items-center justify-center overflow-hidden rounded-xl border border-edge bg-surface-2">
                  {project.thumbnail_url ? (
                    <img src={project.thumbnail_url} alt="" className="h-full w-full object-cover" />
                  ) : (
                    <FolderKanban className="h-7 w-7 text-ink-faint" />
                  )}
                </div>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-ink">{project.name}</p>
                    <p className="truncate text-[11px] text-ink-faint">{project.description || 'No description'}</p>
                  </div>
                  <Badge tone={project.kind === 'story' ? 'brand' : 'default'}>{project.kind}</Badge>
                </div>
                <p className="mt-2 text-[10px] text-ink-faint">
                  {project.counts?.assets ?? 0} assets · {project.counts?.scenes ?? 0} scenes · updated {relativeTime(project.updated_at)}
                </p>
              </Link>
              <button onClick={() => void toggleFavorite(project)}
                className="absolute right-3 top-3 rounded-lg border border-edge bg-surface-1/80 p-1.5 backdrop-blur">
                <Star className={project.is_favorite ? 'h-3.5 w-3.5 fill-current text-brand' : 'h-3.5 w-3.5 text-ink-faint'} />
              </button>
            </Card>
          ))}
        </div>
      )}

      <Modal open={creating} onClose={() => setCreating(false)} title="New project"
        footer={<>
          <Button variant="ghost" onClick={() => setCreating(false)}>Cancel</Button>
          <Button variant="primary" onClick={() => void create()}>Create</Button>
        </>}>
        <div className="space-y-3">
          <Input label="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="Product launch film" />
          <TextArea label="Description" value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })} />
          <Select label="Kind" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}
            options={['general', 'image', 'video', 'avatar', 'story'].map((k) => ({ value: k, label: k }))} />
        </div>
      </Modal>
    </div>
  );
}
