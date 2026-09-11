import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Heart, Library, Upload } from 'lucide-react';
import { Asset, endpoints } from '@/lib/api';
import { AssetCard, AssetModal } from '@/components/assets/AssetCard';
import { Button, Card, EmptyState, Input, Skeleton, Tabs } from '@/components/ui';
import { bytes } from '@/lib/format';
import { useAppStore } from '@/app/store';

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'image', label: 'Images' },
  { id: 'video', label: 'Videos' },
  { id: 'audio', label: 'Audio' },
  { id: 'avatar', label: 'Avatars' },
  { id: 'favorite', label: 'Favourites' },
] as const;

export function AssetsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const toast = useAppStore((s) => s.toast);
  const kindParam = searchParams.get('kind') ?? 'all';
  const [filter, setFilter] = useState<string>(kindParam);
  const [query, setQuery] = useState(searchParams.get('q') ?? '');
  const [items, setItems] = useState<Asset[]>([]);
  const [stats, setStats] = useState<Record<string, { count: number; bytes: number }>>({});
  const [loading, setLoading] = useState(true);
  const [openAsset, setOpenAsset] = useState<Asset | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const load = () => {
    setLoading(true);
    const params: Record<string, unknown> = { limit: 120 };
    if (filter === 'favorite') params.favorite = true;
    else if (filter !== 'all') params.kind = filter;
    if (query) params.q = query;
    Promise.all([endpoints.assets(params), endpoints.assetStats()])
      .then(([a, s]) => {
        setItems(a.items);
        setStats(s.by_kind ?? {});
      })
      .catch((err) => toast({ kind: 'error', title: 'Could not load assets', message: (err as Error).message }))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    setFilter(kindParam);
    setQuery(searchParams.get('q') ?? '');
  }, [searchParams]);

  useEffect(load, [filter, query]);

  async function upload(event: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    for (const file of files) {
      const form = new FormData();
      form.append('file', file);
      try {
        await endpoints.uploadAsset(form);
        toast({ kind: 'success', title: `Uploaded ${file.name}` });
      } catch (err) {
        toast({ kind: 'error', title: `Upload failed: ${file.name}`, message: (err as Error).message });
      }
    }
    load();
  }

  async function bulkDelete() {
    for (const id of selected) {
      await endpoints.deleteAsset(id).catch(() => null);
    }
    setSelected(new Set());
    load();
    toast({ kind: 'info', title: `Deleted ${selected.size} asset(s)` });
  }

  const totalBytes = Object.values(stats).reduce((sum, s) => sum + s.bytes, 0);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-ink">Asset library</h1>
          <p className="text-xs text-ink-faint">
            {Object.values(stats).reduce((sum, s) => sum + s.count, 0)} assets · {bytes(totalBytes)} stored on the local backend
          </p>
        </div>
        <div className="flex gap-2">
          {selected.size > 0 && (
            <Button variant="danger" onClick={() => void bulkDelete()}>Delete {selected.size}</Button>
          )}
          <label className="cursor-pointer">
            <input type="file" multiple className="hidden" accept="image/*,video/*,audio/*"
              onChange={(e) => void upload(e)} />
            <span className="inline-flex h-10 items-center gap-2 rounded-xl border border-edge bg-surface-2/80 px-4 text-sm font-medium text-ink transition hover:bg-surface-3">
              <Upload className="h-4 w-4" /> Upload
            </span>
          </label>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Tabs tabs={FILTERS.map((f) => ({ id: f.id, label: f.label }))} value={filter}
          onChange={(id) => setSearchParams(id === 'all' ? {} : { kind: id })} />
        <div className="min-w-[200px] flex-1">
          <Input placeholder="Search by name" value={query}
            onChange={(e) => setQuery(e.target.value)} />
        </div>
        <div className="flex gap-1.5">
          {Object.entries(stats).map(([kind, value]) => (
            <span key={kind} className="chip">
              <Heart className="h-3 w-3" /> {kind}: {value.count}
            </span>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
          {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="aspect-[4/3]" />)}
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          icon={<Library className="h-5 w-5" />}
          title="No assets found"
          description="Generate something in the workspace, or upload your own media to get started."
        />
      ) : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6">
          {items.map((asset) => (
            <div key={asset.id} onClick={() => {
              const next = new Set(selected);
              if (next.has(asset.id)) next.delete(asset.id); else next.add(asset.id);
              setSelected(next);
            }}>
              <AssetCard asset={asset} onOpen={(a) => setOpenAsset(a)} onChanged={load} selected={selected.has(asset.id)} />
            </div>
          ))}
        </div>
      )}

      <AssetModal asset={openAsset} onClose={() => setOpenAsset(null)} onChanged={load} />
    </div>
  );
}
