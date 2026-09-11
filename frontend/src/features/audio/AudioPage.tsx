import { useEffect, useState } from 'react';
import { AudioLines, Play } from 'lucide-react';
import { Asset, endpoints } from '@/lib/api';
import { Badge, Button, Card, SectionTitle, Select, Slider } from '@/components/ui';
import { bytes, duration as fmtDuration } from '@/lib/format';
import { useAppStore } from '@/app/store';
import { useJobPolling } from '@/lib/hooks';

export function AudioPage() {
  const toast = useAppStore((s) => s.toast);
  const [genre, setGenre] = useState('cinematic');
  const [seconds, setSeconds] = useState(20);
  const [key, setKey] = useState('C');
  const [sfx, setSfx] = useState('whoosh');
  const [jobId, setJobId] = useState<string | null>(null);
  const [items, setItems] = useState<Asset[]>([]);

  const { job, done } = useJobPolling(jobId, 1000);

  const load = () => endpoints.assets({ kind: 'audio', limit: 40 })
    .then((r) => setItems(r.items))
    .catch(() => setItems([]));

  useEffect(() => { void load(); }, []);

  useEffect(() => {
    if (done && job?.status === 'completed') {
      toast({ kind: 'success', title: 'Audio ready' });
      void load();
    }
    if (done && job?.status === 'failed') {
      toast({ kind: 'error', title: 'Audio generation failed', message: job.error?.message,
        action: job.error?.suggested_action });
    }
  }, [done, job]);

  async function generate(mode: string, params: Record<string, unknown>, prompt: string) {
    try {
      const res = await endpoints.generate('audio', { prompt, mode, params });
      setJobId(res.job_id);
      toast({ kind: 'success', title: 'Job queued', message: `${res.engine_name}` });
    } catch (err) {
      toast({ kind: 'error', title: 'Could not queue job', message: (err as Error).message });
    }
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
      <Card className="space-y-4">
        <div>
          <h2 className="text-sm font-semibold text-ink">Audio studio</h2>
          <p className="text-[11px] text-ink-faint">Deterministic synthesiser — real DSP, real files.</p>
        </div>

        <div className="space-y-3">
          <Select label="Genre" value={genre} onChange={(e) => setGenre(e.target.value)}
            options={['cinematic', 'corporate', 'kids', 'ambient', 'documentary', 'uplifting', 'suspense']
              .map((g) => ({ value: g, label: g }))} />
          <Slider label="Duration" min={5} max={180} value={seconds} suffix="s" onChange={setSeconds} />
          <Select label="Key" value={key} onChange={(e) => setKey(e.target.value)}
            options={['C', 'D', 'E', 'F', 'G', 'A', 'B'].map((k) => ({ value: k, label: k }))} />
          <Button variant="primary" className="w-full" icon={<AudioLines className="h-4 w-4" />}
            loading={jobId !== null && !done}
            onClick={() => void generate('background-music', { genre, duration: seconds, key }, `${genre} music bed in ${key}`)}>
            Generate music
          </Button>
        </div>

        <div className="border-t border-edge pt-3">
          <Select label="Sound effect" value={sfx} onChange={(e) => setSfx(e.target.value)}
            options={['whoosh', 'impact', 'riser', 'pop', 'nature', 'water', 'rain', 'crowd', 'magic', 'footstep']
              .map((s) => ({ value: s, label: s }))} />
          <Button className="mt-3 w-full" icon={<Play className="h-4 w-4" />}
            onClick={() => void generate('sound-effects', { sfx, duration: 2 }, `${sfx} sound effect`)}>
            Generate SFX
          </Button>
        </div>

        <p className="rounded-lg border border-edge bg-surface-2/50 p-2.5 text-[10px] leading-relaxed text-ink-faint">
          Music and SFX are synthesised locally (chords, arpeggios, bass, percussion, filtered noise). Install a
          neural audio model to replace this engine without changing anything else in the platform.
        </p>
      </Card>

      <div className="space-y-3">
        <SectionTitle title="Generated audio" subtitle="Click play to preview — files stream, never fully preloaded" />
        <div className="space-y-2">
          {items.length === 0 && (
            <Card className="text-center text-xs text-ink-faint">No audio yet. Generate a track to get started.</Card>
          )}
          {items.map((asset) => (
            <Card key={asset.id} className="flex items-center gap-3 p-3">
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm text-ink">{asset.name}</p>
                <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-ink-faint">
                  <span>{fmtDuration(asset.duration_sec)}</span>
                  <span>· {bytes(asset.size_bytes)}</span>
                  {asset.meta?.genre && <Badge>{String(asset.meta.genre)}</Badge>}
                  {asset.meta?.sfx && <Badge tone="info">{String(asset.meta.sfx)}</Badge>}
                  {asset.meta?.placeholder && <Badge tone="warn">placeholder</Badge>}
                </div>
              </div>
              <audio src={asset.url} controls className="max-w-[280px] flex-1" />
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
