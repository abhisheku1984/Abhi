import { useEffect, useState } from 'react';
import { Film, ImageIcon, Music4, Plus, Trash2, Type } from 'lucide-react';
import { Asset, endpoints } from '@/lib/api';
import { Badge, Button, Card, EmptyState, Input, ProgressBar, SectionTitle, Select, Slider } from '@/components/ui';
import { clsx } from '@/lib/format';
import { useAppStore } from '@/app/store';
import { useJobPolling } from '@/lib/hooks';

interface Clip {
  id: string;
  track: 'video' | 'image' | 'audio' | 'text';
  asset_id?: string;
  name: string;
  start: number;
  duration: number;
  volume?: number;
  text?: string;
  transition?: string;
}

const TRACKS: { id: Clip['track']; label: string; icon: typeof Film }[] = [
  { id: 'video', label: 'Video', icon: Film },
  { id: 'image', label: 'Image', icon: ImageIcon },
  { id: 'audio', label: 'Audio', icon: Music4 },
  { id: 'text', label: 'Text', icon: Type },
];

export function EditorPage() {
  const toast = useAppStore((s) => s.toast);
  const [videos, setVideos] = useState<Asset[]>([]);
  const [images, setImages] = useState<Asset[]>([]);
  const [audios, setAudios] = useState<Asset[]>([]);
  const [clips, setClips] = useState<Clip[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [preset, setPreset] = useState('youtube');
  const [captions, setCaptions] = useState(true);
  const [jobId, setJobId] = useState<string | null>(null);
  const { job, done } = useJobPolling(jobId, 1500);
  const [result, setResult] = useState<Asset | null>(null);

  useEffect(() => {
    endpoints.assets({ kind: 'video', limit: 40 }).then((r) => setVideos(r.items)).catch(() => setVideos([]));
    endpoints.assets({ kind: 'image', limit: 40 }).then((r) => setImages(r.items)).catch(() => setImages([]));
    endpoints.assets({ kind: 'audio', limit: 40 }).then((r) => setAudios(r.items)).catch(() => setAudios([]));
  }, []);

  useEffect(() => {
    if (done && job?.status === 'completed') {
      const ids = (job.result?.asset_ids as string[]) ?? [];
      if (ids.length) endpoints.asset(ids[0]).then(setResult).catch(() => null);
      toast({ kind: 'success', title: 'Export complete' });
    }
    if (done && job?.status === 'failed') {
      toast({ kind: 'error', title: 'Export failed', message: job.error?.message, action: job.error?.suggested_action });
    }
  }, [done, job]);

  function addClip(asset: Asset, track: Clip['track']) {
    setClips((c) => [...c, {
      id: `${track}-${asset.id}-${Date.now()}`,
      track,
      asset_id: asset.id,
      name: asset.name,
      start: 0,
      duration: Math.min(asset.duration_sec ?? 5, 15),
      volume: 1,
      transition: 'cut',
    }]);
  }

  function addTextClip() {
    setClips((c) => [...c, {
      id: `text-${Date.now()}`, track: 'text', name: 'Text overlay',
      start: 0, duration: 3, text: 'Your caption here',
    }]);
  }

  function update(id: string, patch: Partial<Clip>) {
    setClips((c) => c.map((clip) => (clip.id === id ? { ...clip, ...patch } : clip)));
  }

  async function render() {
    if (!clips.length) {
      toast({ kind: 'error', title: 'Timeline is empty', message: 'Add at least one video or image clip.' });
      return;
    }
    try {
      const res = await endpoints.renderTimeline({
        preset,
        timeline: {
          clips: clips.map((c) => ({
            track: c.track,
            asset_id: c.asset_id,
            start: c.start,
            duration: c.duration,
            volume: c.volume,
            text: c.text,
            transition: c.transition,
          })),
          captions: captions
            ? clips.filter((c) => c.text).map((c, i) => ({ start: c.start, end: c.start + c.duration, text: c.text ?? '' }))
            : [],
          keep_original_audio: true,
        },
      });
      setJobId(res.job_id);
      toast({ kind: 'success', title: 'Render queued' });
    } catch (err) {
      toast({ kind: 'error', title: 'Could not queue render', message: (err as Error).message });
    }
  }

  const selectedClip = clips.find((c) => c.id === selected);

  return (
    <div className="grid gap-4 xl:grid-cols-[300px_minmax(0,1fr)]">
      <div className="space-y-3">
        <Card className="space-y-2">
          <p className="label">Media</p>
          {[
            { label: 'Videos', items: videos, track: 'video' as const },
            { label: 'Images', items: images, track: 'image' as const },
            { label: 'Audio', items: audios, track: 'audio' as const },
          ].map((group) => (
            <div key={group.label}>
              <p className="mb-1 text-[10px] uppercase tracking-wider text-ink-faint">{group.label}</p>
              <div className="max-h-40 space-y-1 overflow-y-auto scroll-thin">
                {group.items.length === 0 && <p className="text-[10px] text-ink-faint">None yet</p>}
                {group.items.map((asset) => (
                  <button key={asset.id} onClick={() => addClip(asset, group.track)}
                    className="flex w-full items-center gap-2 rounded-lg border border-edge bg-surface-2/40 p-1.5 text-left transition hover:border-brand/50">
                    <div className="h-8 w-12 shrink-0 overflow-hidden rounded border border-edge bg-black/40">
                      {group.track !== 'audio' && <img src={asset.thumbnail_url || asset.url} alt="" className="h-full w-full object-cover" />}
                    </div>
                    <span className="min-w-0 flex-1 truncate text-[11px] text-ink-dim">{asset.name}</span>
                    <Plus className="h-3 w-3 text-ink-faint" />
                  </button>
                ))}
              </div>
            </div>
          ))}
          <Button size="sm" className="w-full" icon={<Type className="h-3.5 w-3.5" />} onClick={addTextClip}>
            Add text overlay
          </Button>
        </Card>

        <Card className="space-y-3">
          <p className="label">Export</p>
          <Select value={preset} onChange={(e) => setPreset(e.target.value)}
            options={[
              { value: 'youtube', label: 'YouTube 16:9' },
              { value: 'youtube-shorts', label: 'YouTube Shorts 9:16' },
              { value: 'instagram-reels', label: 'Instagram Reels 9:16' },
              { value: 'instagram-post', label: 'Instagram 1:1' },
              { value: 'instagram-story', label: 'Instagram Story 9:16' },
              { value: 'tiktok', label: 'TikTok 9:16' },
              { value: 'facebook', label: 'Facebook 16:9' },
              { value: 'linkedin', label: 'LinkedIn 16:9' },
              { value: 'square', label: 'Square 1:1' },
              { value: 'portrait', label: 'Portrait 4:5' },
              { value: 'webm', label: 'WebM 16:9' },
            ]} />
          <label className="flex items-center gap-2 text-[11px] text-ink-dim">
            <input type="checkbox" checked={captions} onChange={(e) => setCaptions(e.target.checked)} />
            Burn in captions from text clips
          </label>
          <Button variant="primary" className="w-full" loading={jobId !== null && !done} onClick={() => void render()}>
            Render & export
          </Button>
          {job && !done && <ProgressBar value={job.progress} />}
        </Card>

        {selectedClip && (
          <Card className="space-y-3">
            <p className="label">Inspector</p>
            <p className="truncate text-xs text-ink">{selectedClip.name}</p>
            <Input label="Start (s)" type="number" step="0.1" value={selectedClip.start}
              onChange={(e) => update(selectedClip.id, { start: Number(e.target.value) })} />
            <Input label="Duration (s)" type="number" step="0.1" value={selectedClip.duration}
              onChange={(e) => update(selectedClip.id, { duration: Number(e.target.value) })} />
            {selectedClip.track === 'audio' && (
              <Slider label="Volume" min={0} max={2} step={0.05} value={selectedClip.volume ?? 1}
                onChange={(v) => update(selectedClip.id, { volume: v })} />
            )}
            {selectedClip.track === 'text' && (
              <Input label="Text" value={selectedClip.text ?? ''}
                onChange={(e) => update(selectedClip.id, { text: e.target.value })} />
            )}
            {selectedClip.track === 'video' && (
              <Select label="Transition" value={selectedClip.transition ?? 'cut'}
                onChange={(e) => update(selectedClip.id, { transition: e.target.value })}
                options={['cut', 'fade', 'dissolve', 'wipeleft', 'wiperight', 'slideup', 'slide down'.replace(' ', ''), 'radial']
                  .map((t) => ({ value: t, label: t }))} />
            )}
          </Card>
        )}
      </div>

      <div className="space-y-3">
        <SectionTitle title="Timeline" subtitle="Tracks are rendered with real FFmpeg — trim, transitions, captions, audio mix" />

        <Card className="space-y-2">
          {TRACKS.map((track) => {
            const trackClips = clips.filter((c) => c.track === track.id);
            return (
              <div key={track.id} className="flex items-center gap-2">
                <div className="flex w-20 items-center gap-1.5 text-[10px] uppercase tracking-wider text-ink-faint">
                  <track.icon className="h-3 w-3" /> {track.label}
                </div>
                <div className="relative h-12 flex-1 overflow-hidden rounded-lg border border-edge bg-surface-2/40">
                  {trackClips.length === 0 && (
                    <p className="flex h-full items-center px-3 text-[10px] text-ink-faint">Empty</p>
                  )}
                  <div className="flex h-full items-center gap-1 p-1">
                    {trackClips.map((clip) => (
                      <div
                        key={clip.id}
                        onClick={() => setSelected(clip.id)}
                        style={{ width: `${Math.max(60, clip.duration * 28)}px` }}
                        className={clsx(
                          'flex h-full cursor-pointer flex-col justify-center rounded-md border px-2 transition',
                          selected === clip.id
                            ? 'border-brand bg-brand/20'
                            : 'border-edge bg-surface-3/70 hover:border-brand/50',
                        )}
                      >
                        <p className="truncate text-[10px] font-medium text-ink">{clip.name}</p>
                        <p className="text-[9px] text-ink-faint">{clip.duration}s @ {clip.start}s</p>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            );
          })}
          <div className="flex items-center justify-between pt-1">
            <p className="text-[10px] text-ink-faint">{clips.length} clip(s) on the timeline</p>
            {clips.length > 0 && (
              <Button size="sm" variant="ghost" icon={<Trash2 className="h-3.5 w-3.5" />}
                onClick={() => { setClips([]); setSelected(null); }}>Clear</Button>
            )}
          </div>
        </Card>

        <Card className="min-h-[240px]">
          <p className="label mb-2">Preview</p>
          {result ? (
            <div className="space-y-2">
              <video src={result.url} controls className="max-h-[420px] w-full rounded-lg" />
              <div className="flex gap-1.5">
                <Badge tone="success">exported</Badge>
                <Badge>{preset}</Badge>
                {result.duration_sec && <Badge>{result.duration_sec.toFixed(1)}s</Badge>}
              </div>
            </div>
          ) : (
            <EmptyState icon={<Film className="h-5 w-5" />} title="No export yet"
              description="Add clips, choose a preset and render. The export runs as a background job." />
          )}
        </Card>
      </div>
    </div>
  );
}
