import { useState } from 'react';
import { Download, Film, Heart, Music4, Sparkles, Trash2, Copy, ImageIcon, Loader2, Wand2, Layers } from 'lucide-react';
import { Asset, endpoints } from '@/lib/api';
import { Badge, IconButton, Modal, Button } from '@/components/ui';
import { bytes, clsx, duration as fmtDuration, relativeTime } from '@/lib/format';
import { useAppStore } from '@/app/store';

export function AssetThumb({ asset, className }: { asset: Asset; className?: string }) {
  const src = (asset.kind === 'video'
    ? asset.thumbnail_url || asset.preview_url
    : asset.thumbnail_url || asset.url) ?? undefined;
  if (asset.kind === 'image' || asset.kind === 'avatar' || asset.kind === 'character') {
    return <img src={src} alt={asset.name} loading="lazy" className={clsx('h-full w-full object-cover', className)} />;
  }
  if (asset.kind === 'video') {
    return src ? (
      <img src={src} alt={asset.name} loading="lazy" className={clsx('h-full w-full object-cover', className)} />
    ) : (
      <div className="flex h-full w-full items-center justify-center bg-surface-3 text-ink-faint"><Film /></div>
    );
  }
  if (asset.kind === 'audio') {
    return (
      <div className="flex h-full w-full items-center justify-center bg-gradient-to-br from-surface-3 to-surface-2 text-ink-faint">
        <Music4 className="h-6 w-6" />
      </div>
    );
  }
  return <div className="flex h-full w-full items-center justify-center text-ink-faint"><Layers /></div>;
}

export function AssetCard({ asset, onChanged, onOpen, selected }: {
  asset: Asset;
  onChanged?: () => void;
  onOpen?: (asset: Asset) => void;
  selected?: boolean;
}) {
  const toast = useAppStore((s) => s.toast);
  const [busy, setBusy] = useState(false);

  async function favorite() {
    await endpoints.updateAsset(asset.id, { is_favorite: !asset.is_favorite });
    onChanged?.();
  }

  async function duplicate() {
    setBusy(true);
    try {
      await endpoints.duplicateAsset(asset.id);
      toast({ kind: 'success', title: 'Asset duplicated' });
      onChanged?.();
    } catch (err) {
      toast({ kind: 'error', title: 'Could not duplicate', message: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      await endpoints.deleteAsset(asset.id);
      toast({ kind: 'info', title: 'Asset deleted' });
      onChanged?.();
    } finally {
      setBusy(false);
    }
  }

  const placeholder = asset.meta?.placeholder || asset.meta?.intelligible === false;

  return (
    <div
      onClick={() => onOpen?.(asset)}
      className={clsx(
        'group relative cursor-pointer overflow-hidden rounded-2xl border bg-surface-1/60 transition',
        selected ? 'border-brand ring-2 ring-brand/40' : 'border-edge hover:border-brand/40',
      )}
    >
      <div className="aspect-[4/3] overflow-hidden bg-surface-2">
        <AssetThumb asset={asset} className="transition duration-500 group-hover:scale-[1.03]" />
      </div>

      <div className="absolute right-2 top-2 flex gap-1 opacity-0 transition group-hover:opacity-100">
        <a href={asset.url} download onClick={(e) => e.stopPropagation()}>
          <IconButton label="Download"><Download className="h-3.5 w-3.5" /></IconButton>
        </a>
        <IconButton label="Duplicate" onClick={(e) => { e.stopPropagation(); void duplicate(); }}>
          <Copy className="h-3.5 w-3.5" />
        </IconButton>
        <IconButton label="Delete" onClick={(e) => { e.stopPropagation(); void remove(); }}>
          <Trash2 className="h-3.5 w-3.5" />
        </IconButton>
      </div>

      <button
        onClick={(e) => { e.stopPropagation(); void favorite(); }}
        className={clsx(
          'absolute left-2 top-2 rounded-lg border border-edge bg-surface-1/80 p-1.5 backdrop-blur transition',
          asset.is_favorite ? 'text-brand' : 'text-ink-faint opacity-0 group-hover:opacity-100',
        )}
        aria-label="Favourite"
      >
        <Heart className={clsx('h-3.5 w-3.5', asset.is_favorite && 'fill-current')} />
      </button>

      {busy && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/50">
          <Loader2 className="h-5 w-5 animate-spin text-brand" />
        </div>
      )}

      <div className="p-2.5">
        <p className="truncate text-xs font-medium text-ink">{asset.name}</p>
        <div className="mt-1 flex items-center gap-1.5 text-[10px] text-ink-faint">
          <span className="uppercase">{asset.kind}</span>
          {asset.width && <span>· {asset.width}×{asset.height}</span>}
          {asset.duration_sec ? <span>· {fmtDuration(asset.duration_sec)}</span> : null}
          <span>· {bytes(asset.size_bytes)}</span>
        </div>
        <div className="mt-1.5 flex flex-wrap gap-1">
          {placeholder && <Badge tone="warn">placeholder audio</Badge>}
          {asset.meta?.diffusion === false && <Badge>deterministic</Badge>}
          {asset.meta?.lip_sync && <Badge tone="info">lip sync</Badge>}
        </div>
      </div>
    </div>
  );
}

export function AssetModal({ asset, onClose, onChanged }: {
  asset: Asset | null;
  onClose: () => void;
  onChanged?: () => void;
}) {
  const toast = useAppStore((s) => s.toast);
  const [busy, setBusy] = useState<string | null>(null);

  if (!asset) return null;

  async function run(action: 'upscale' | 'animate' | 'background-remove' | 'variations') {
    setBusy(action);
    try {
      const payload =
        action === 'upscale'
          ? { mode: 'upscale', references: [{ asset_id: asset!.id, kind: 'image' }], params: { upscale_factor: 2 } }
          : action === 'animate'
          ? { mode: 'image-to-video', prompt: asset!.meta?.prompt || '',
              references: [{ asset_id: asset!.id, kind: 'image' }], params: { duration: 5, camera: 'dolly' } }
          : action === 'background-remove'
          ? { mode: 'background-remove', references: [{ asset_id: asset!.id, kind: 'image' }], params: {} }
          : { mode: 'variations', prompt: asset!.meta?.prompt || '',
              references: [{ asset_id: asset!.id, kind: 'image' }], params: { count: 4 } };
      const res = await endpoints.generate(action === 'animate' ? 'video' : 'image', payload);
      toast({ kind: 'success', title: 'Job queued', message: `${res.engine_name} · ${res.job_id.slice(0, 12)}…` });
    } catch (err) {
      toast({
        kind: 'error', title: 'Could not start the job',
        message: (err as Error).message,
        action: (err as unknown as { suggestedAction?: string }).suggestedAction,
      });
    } finally {
      setBusy(null);
    }
  }

  const meta = asset.meta ?? {};

  return (
    <Modal open onClose={onClose} title={asset.name} width="max-w-4xl"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Close</Button>
          <a href={asset.url} download>
            <Button variant="primary" icon={<Download className="h-4 w-4" />}>Download</Button>
          </a>
        </>
      }>
      <div className="grid gap-4 lg:grid-cols-[1.4fr_1fr]">
        <div className="overflow-hidden rounded-xl border border-edge bg-black/40">
          {asset.kind === 'video' ? (
            <video src={asset.preview_url || asset.url} poster={asset.thumbnail_url || undefined}
              controls className="max-h-[52vh] w-full" />
          ) : asset.kind === 'audio' ? (
            <div className="p-6"><audio src={asset.url} controls className="w-full" /></div>
          ) : (
            <img src={asset.url} alt={asset.name} className="max-h-[52vh] w-full object-contain" />
          )}
        </div>

        <div className="space-y-3">
          <div className="panel-2 p-3">
            <p className="label mb-2">Details</p>
            <dl className="space-y-1 text-[11px]">
              {[
                ['Type', asset.kind],
                ['Size', bytes(asset.size_bytes)],
                ['Dimensions', asset.width ? `${asset.width}×${asset.height}` : '—'],
                ['Duration', fmtDuration(asset.duration_sec)],
                ['Created', relativeTime(asset.created_at)],
                ['Checksum', asset.meta?.checksum ? String(asset.meta.checksum).slice(0, 16) + '…' : '—'],
              ].map(([k, v]) => (
                <div key={k} className="flex justify-between gap-3">
                  <dt className="text-ink-faint">{k}</dt>
                  <dd className="truncate text-ink-dim">{v as string}</dd>
                </div>
              ))}
            </dl>
          </div>

          {meta.engine && (
            <div className="panel-2 p-3">
              <p className="label mb-2">Provenance</p>
              <div className="flex flex-wrap gap-1.5">
                <Badge tone="brand">{String(meta.engine)}</Badge>
                {meta.mode && <Badge>{String(meta.mode)}</Badge>}
                {meta.deterministic && <Badge>deterministic</Badge>}
                {meta.diffusion === false && <Badge tone="info">not a diffusion model</Badge>}
                {meta.diffusion === true && <Badge tone="success">diffusion</Badge>}
                {meta.seed !== undefined && <Badge>seed {String(meta.seed)}</Badge>}
              </div>
              {meta.prompt ? (
                <p className="mt-2 text-[11px] leading-relaxed text-ink-faint">{String(meta.prompt)}</p>
              ) : null}
              {meta.notice && (
                <p className="mt-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-2 text-[11px] text-amber-300">
                  {String(meta.notice)}
                </p>
              )}
            </div>
          )}

          <div className="panel-2 p-3">
            <p className="label mb-2">Actions</p>
            <div className="grid grid-cols-2 gap-2">
              <Button size="sm" icon={<Sparkles className="h-3.5 w-3.5" />} loading={busy === 'upscale'}
                onClick={() => void run('upscale')}>Upscale</Button>
              <Button size="sm" icon={<Film className="h-3.5 w-3.5" />} loading={busy === 'animate'}
                onClick={() => void run('animate')}>Animate</Button>
              <Button size="sm" icon={<Wand2 className="h-3.5 w-3.5" />} loading={busy === 'background-remove'}
                onClick={() => void run('background-remove')}>Cut out</Button>
              <Button size="sm" icon={<ImageIcon className="h-3.5 w-3.5" />} loading={busy === 'variations'}
                onClick={() => void run('variations')}>Variations</Button>
            </div>
          </div>
        </div>
      </div>
    </Modal>
  );
}
