import { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  AudioLines, Camera, Clapperboard, Download, Film, ImageIcon, Mic2, Sparkles, Upload,
  UserSquare2, Wand2, X, Layers, RefreshCw, Heart,
} from 'lucide-react';
import { Asset, ModelInfo, endpoints, fileUrl } from '@/lib/api';
import { Badge, Button, Card, ErrorCallout, Input, ProgressBar, Select, Slider, Tabs, TextArea } from '@/components/ui';
import { AssetThumb } from '@/components/assets/AssetCard';
import { bytes, clsx, duration as fmtDuration } from '@/lib/format';
import { useAppStore } from '@/app/store';
import { useJobPolling, useJobSocket } from '@/lib/hooks';

type Kind = 'image' | 'video' | 'avatar' | 'voice' | 'audio';

const KINDS: { id: Kind; label: string; icon: typeof ImageIcon }[] = [
  { id: 'image', label: 'Image', icon: ImageIcon },
  { id: 'video', label: 'Video', icon: Film },
  { id: 'avatar', label: 'Avatar', icon: UserSquare2 },
  { id: 'voice', label: 'Voice', icon: Mic2 },
  { id: 'audio', label: 'Audio', icon: AudioLines },
];

const ASPECTS = ['1:1', '16:9', '9:16', '4:3', '3:2', '4:5', '21:9'];
const RESOLUTIONS = ['512', '768', '1024', '1536', '2048'];
const LIGHTING = ['cinematic', 'golden-hour', 'blue-hour', 'daylight', 'studio', 'neon', 'low-key', 'high-key', 'night'];
const CAMERAS = ['static', 'pan-left', 'pan-right', 'tilt-up', 'tilt-down', 'zoom-in', 'zoom-out', 'dolly',
  'tracking', 'orbit', 'crane', 'handheld', 'drone', 'pov', 'macro', 'wide-shot', 'close-up'];
const LENSES = ['18mm', '24mm', '35mm', '50mm', '85mm', '135mm'];
const MOTIONS = ['slow', 'normal', 'fast', 'cinematic', 'dynamic', 'realistic', 'smooth', 'handheld'];
const IMAGE_MODES = [
  { value: 'text-to-image', label: 'Text → Image' },
  { value: 'image-to-image', label: 'Image → Image' },
  { value: 'image-edit', label: 'Image edit' },
  { value: 'inpaint', label: 'Inpaint' },
  { value: 'outpaint', label: 'Outpaint' },
  { value: 'object-removal', label: 'Object removal' },
  { value: 'object-replacement', label: 'Object replacement' },
  { value: 'background-remove', label: 'Background remove' },
  { value: 'background-replace', label: 'Background replace' },
  { value: 'upscale', label: 'Upscale' },
  { value: 'face-restore', label: 'Face restore' },
  { value: 'colorize', label: 'Colorize' },
  { value: 'sketch-to-image', label: 'Sketch → Image' },
  { value: 'depth-to-image', label: 'Depth → Image' },
  { value: 'edge-to-image', label: 'Edge → Image' },
  { value: 'pose-to-image', label: 'Pose → Image' },
  { value: 'reference-to-image', label: 'Reference → Image' },
  { value: 'multi-reference-image', label: 'Multi-reference' },
  { value: 'style-transfer', label: 'Style transfer' },
  { value: 'variations', label: 'Variations' },
];
const VIDEO_MODES = [
  { value: 'text-to-video', label: 'Text → Video' },
  { value: 'image-to-video', label: 'Image → Video' },
  { value: 'first-frame-to-video', label: 'First frame → Video' },
  { value: 'first-last-frame-video', label: 'First + last frame' },
  { value: 'video-to-video', label: 'Video → Video' },
  { value: 'video-restyle', label: 'Restyle' },
  { value: 'video-upscale', label: 'Upscale video' },
  { value: 'extend-video', label: 'Extend video' },
  { value: 'interpolate', label: 'Interpolate' },
  { value: 'video-edit', label: 'Edit (trim/speed/crop)' },
];
const AVATAR_MODES = [
  { value: 'talking-avatar', label: 'Talking avatar' },
  { value: 'avatar-from-script', label: 'From script' },
  { value: 'avatar-from-audio', label: 'From audio' },
  { value: 'create-avatar', label: 'Create still avatar' },
  { value: 'photo-to-avatar', label: 'Photo → avatar' },
  { value: 'avatar-lip-sync', label: 'Lip sync' },
];
const VOICE_MODES = [{ value: 'text-to-speech', label: 'Text → Speech' }, { value: 'narration', label: 'Narration' }];
const AUDIO_MODES = [
  { value: 'background-music', label: 'Background music' },
  { value: 'cinematic-music', label: 'Cinematic music' },
  { value: 'kids-music', label: 'Kids music' },
  { value: 'corporate-music', label: 'Corporate music' },
  { value: 'ambient', label: 'Ambient' },
  { value: 'nature-sounds', label: 'Nature sounds' },
  { value: 'sound-effects', label: 'Sound effects' },
  { value: 'transitions', label: 'Transitions' },
  { value: 'voice-narration', label: 'Narration' },
];

interface HistoryEntry { jobId: string; kind: Kind; mode: string; status: string; assets: Asset[]; error?: string }

export function CreatePage() {
  const [params, setSearchParams] = useSearchParams();
  const toast = useAppStore((s) => s.toast);
  const kind = ((params.get('kind') as Kind) || 'image') as Kind;

  const [catalog, setCatalog] = useState<Record<string, ModelInfo[]>>({});
  const [modelId, setModelId] = useState<string>('');
  const [mode, setMode] = useState('text-to-image');
  const [prompt, setPrompt] = useState('cinematic wide shot of a businessman walking through Hyderabad at sunset');
  const [negative, setNegative] = useState('');
  const [refs, setRefs] = useState<{ asset: Asset; role: string }[]>([]);
  const [library, setLibrary] = useState<Asset[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [characters, setCharacters] = useState<any[]>([]);
  const [voices, setVoices] = useState<any[]>([]);
  const [projects, setProjects] = useState<any[]>([]);
  const [projectId, setProjectId] = useState<string>('');
  const [characterId, setCharacterId] = useState('');
  const [voiceId, setVoiceId] = useState('');
  const [settings, setSettings] = useState<Record<string, any>>({
    aspect: '16:9', resolution: '768', lighting: 'cinematic', camera: 'dolly', lens: '35mm',
    motion: 'cinematic', duration: 5, fps: 24, seed: '', steps: 30, guidance: 7.5, strength: 0.65,
    count: 1, upscale_factor: 2, avatar_type: 'corporate-presenter', style: 'photorealistic',
    expression: 'neutral', gesture: 'talking', language: 'en', voice_name: 'narrator',
    speed: 1, pitch: 1, genre: 'cinematic', sfx: 'whoosh', key: 'C', script: '',
  });
  const [jobId, setJobId] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<{ message: string; action?: string; errors?: string[] } | null>(null);

  const { job, done } = useJobPolling(jobId, 1200);

  const models = useMemo(() => catalog[kind] ?? [], [catalog, kind]);
  const selectedModel = models.find((m) => m.id === modelId);

  useEffect(() => {
    endpoints.modes().then((data) => setCatalog(data.families)).catch(() => setCatalog({}));
    endpoints.assets({ limit: 40 }).then((a) => setLibrary(a.items)).catch(() => setLibrary([]));
    endpoints.characters().then((c) => setCharacters(c.items)).catch(() => setCharacters([]));
    endpoints.voices().then((v) => setVoices(v.items)).catch(() => setVoices([]));
    endpoints.projects({ limit: 50 }).then((p) => setProjects(p.items)).catch(() => setProjects([]));
  }, []);

  // Pick a working model automatically when the tab changes.
  useEffect(() => {
    const list = catalog[kind] ?? [];
    if (!list.length) return;
    const ready = list.find((m) => ['installed', 'available'].includes(m.status) && m.modes.includes(mode))
      || list.find((m) => ['installed', 'available'].includes(m.status))
      || list[0];
    setModelId(ready.id);
  }, [catalog, kind, mode]);

  useEffect(() => {
    setMode(kind === 'image' ? 'text-to-image'
      : kind === 'video' ? 'text-to-video'
      : kind === 'avatar' ? 'talking-avatar'
      : kind === 'voice' ? 'text-to-speech' : 'background-music');
  }, [kind]);

  const loadResults = useCallback(async (id: string, entry: Omit<HistoryEntry, 'assets'>) => {
    const jobData = await endpoints.job(id);
    const ids = (jobData.result?.asset_ids as string[]) ?? [];
    const assets = await Promise.all(ids.map((aid) => endpoints.asset(aid).catch(() => null)));
    setHistory((h) => [{ ...entry, status: jobData.status, assets: assets.filter(Boolean) as Asset[],
      error: jobData.error?.message }, ...h.filter((x) => x.jobId !== id)]);
  }, []);

  useEffect(() => {
    if (done && job) {
      const entry = { jobId: job.id, kind, mode, status: job.status } as Omit<HistoryEntry, 'assets'>;
      if (job.status === 'completed') void loadResults(job.id, entry);
      else setHistory((h) => [{ ...entry, assets: [], error: job.error?.message }, ...h.filter((x) => x.jobId !== job.id)]);
      setSubmitting(false);
    }
  }, [done, job, kind, mode, loadResults]);

  useJobSocket((event) => {
    if (event.job_id === jobId) setSubmitting(!['completed', 'failed', 'cancelled'].includes(event.status));
  });

  function setKind(next: Kind) {
    setSearchParams({ kind: next });
    setJobId(null);
    setError(null);
  }

  async function generate() {
    setSubmitting(true);
    setError(null);
    try {
      const payload: Record<string, unknown> = {
        prompt,
        negative_prompt: negative,
        mode,
        model_id: modelId || undefined,
        project_id: projectId || undefined,
        character_id: characterId || undefined,
        voice_id: voiceId || undefined,
        references: refs.map((r) => ({ asset_id: r.asset.id, kind: r.asset.kind, role: r.role, weight: 1 })),
        params: { ...settings, script: settings.script || prompt, text: prompt },
      };
      const res = await endpoints.generate(kind, payload);
      setJobId(res.job_id);
      toast({ kind: 'success', title: 'Job queued', message: `${res.engine_name} · est. ${Math.round(res.estimate_seconds ?? 0)}s` });
    } catch (err) {
      const apiErr = err as { message: string; suggestedAction?: string; meta?: { errors?: string[] } };
      setError({ message: apiErr.message, action: apiErr.suggestedAction, errors: apiErr.meta?.errors });
      setSubmitting(false);
    }
  }

  async function enhance() {
    try {
      const result = await endpoints.transformPrompt(prompt, 'enhance', 'cinematic');
      setPrompt(result.enhanced);
      toast({ kind: 'info', title: 'Prompt enhanced' });
    } catch (err) {
      toast({ kind: 'error', title: 'Could not enhance prompt', message: (err as Error).message });
    }
  }

  async function upload(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    if (projectId) form.append('project_id', projectId);
    try {
      const asset = await endpoints.uploadAsset(form);
      setRefs((r) => [...r, { asset, role: 'reference' }]);
      setLibrary((l) => [asset, ...l]);
      toast({ kind: 'success', title: 'Reference uploaded' });
    } catch (err) {
      toast({ kind: 'error', title: 'Upload failed', message: (err as Error).message });
    }
  }

  const latest = history[0]?.assets ?? [];
  const needsSource = ['upscale', 'image-to-image', 'image-edit', 'inpaint', 'outpaint', 'object-removal',
    'object-replacement', 'background-remove', 'background-replace', 'face-restore', 'colorize',
    'sketch-to-image', 'depth-to-image', 'edge-to-image', 'pose-to-image', 'style-transfer', 'variations',
    'image-to-video', 'first-frame-to-video', 'video-to-video', 'video-restyle', 'video-upscale',
    'extend-video', 'interpolate', 'photo-to-avatar', 'avatar-from-audio'].includes(mode);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-ink">Creation workspace</h1>
          <p className="text-xs text-ink-faint">One canvas for every generation mode.</p>
        </div>
        <Tabs tabs={KINDS.map((k) => ({ id: k.id, label: k.label, icon: <k.icon className="h-3.5 w-3.5" /> }))}
          value={kind} onChange={setKind} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[320px_minmax(0,1fr)_320px]">
        {/* ---------------- Left: prompt & references ---------------- */}
        <div className="space-y-3">
          <Card className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="label">Prompt</span>
              <button onClick={() => void enhance()} className="text-[11px] text-brand hover:underline">
                Enhance
              </button>
            </div>
            <TextArea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={6}
              placeholder="Describe what you want to create…" />
            <TextArea label="Negative prompt" value={negative} onChange={(e) => setNegative(e.target.value)} rows={2}
              placeholder="What to avoid" />
          </Card>

          <Card className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="label">References</span>
              <div className="flex gap-1">
                <label className="cursor-pointer text-[11px] text-brand hover:underline">
                  Upload
                  <input type="file" className="hidden" accept="image/*,video/*,audio/*" onChange={(e) => void upload(e)} />
                </label>
                <button onClick={() => setPickerOpen(true)} className="text-[11px] text-brand hover:underline">
                  Library
                </button>
              </div>
            </div>
            {refs.length === 0 && (
              <p className="text-[11px] text-ink-faint">
                {needsSource ? 'This mode needs a source image or video — attach one below.'
                  : 'Optional: attach references to guide the generation.'}
              </p>
            )}
            <div className="grid grid-cols-3 gap-2">
              {refs.map((ref, i) => (
                <div key={i} className="group relative overflow-hidden rounded-lg border border-edge">
                  <div className="aspect-square"><AssetThumb asset={ref.asset} /></div>
                  <button
                    onClick={() => setRefs((r) => r.filter((_, idx) => idx !== i))}
                    className="absolute right-1 top-1 rounded bg-black/60 p-1 text-white opacity-0 transition group-hover:opacity-100"
                  >
                    <X className="h-3 w-3" />
                  </button>
                  <select
                    value={ref.role}
                    onChange={(e) => setRefs((r) => r.map((x, idx) => idx === i ? { ...x, role: e.target.value } : x))}
                    className="w-full border-t border-edge bg-surface-2 px-1 py-0.5 text-[10px] text-ink-dim outline-none"
                  >
                    <option value="reference">reference</option>
                    <option value="style">style</option>
                    <option value="mask">mask</option>
                    <option value="first_frame">first frame</option>
                    <option value="last_frame">last frame</option>
                  </select>
                </div>
              ))}
            </div>
          </Card>

          {pickerOpen && (
            <Card className="max-h-72 space-y-2 overflow-y-auto scroll-thin">
              <div className="flex items-center justify-between">
                <span className="label">Asset library</span>
                <button onClick={() => setPickerOpen(false)} className="text-ink-faint hover:text-ink">
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
              <div className="grid grid-cols-3 gap-2">
                {library.map((asset) => (
                  <button key={asset.id}
                    onClick={() => { setRefs((r) => [...r, { asset, role: 'reference' }]); setPickerOpen(false); }}
                    className="overflow-hidden rounded-lg border border-edge transition hover:border-brand">
                    <div className="aspect-square"><AssetThumb asset={asset} /></div>
                  </button>
                ))}
              </div>
            </Card>
          )}

          <Card className="space-y-3">
            <span className="label">Project & entities</span>
            <Select label="Project" value={projectId} onChange={(e) => setProjectId(e.target.value)}
              options={[{ value: '', label: 'No project' }, ...projects.map((p) => ({ value: p.id, label: p.name }))]} />
            <Select label="Character (consistency)" value={characterId} onChange={(e) => setCharacterId(e.target.value)}
              options={[{ value: '', label: 'None' }, ...characters.map((c) => ({ value: c.id, label: c.name }))]} />
            <Select label="Voice" value={voiceId} onChange={(e) => setVoiceId(e.target.value)}
              options={[{ value: '', label: 'Default' }, ...voices.map((v) => ({ value: v.id, label: v.name }))]} />
          </Card>
        </div>

        {/* ---------------- Center: canvas ---------------- */}
        <div className="space-y-3">
          <Card className="flex min-h-[460px] flex-col p-3">
            <div className="mb-2 flex items-center justify-between">
              <span className="label">Canvas</span>
              {job && (
                <Badge tone={job.status === 'failed' ? 'danger' : job.status === 'completed' ? 'success' : 'brand'}>
                  {job.status} · {Math.round(job.progress)}%
                </Badge>
              )}
            </div>

            <div className="grid-fade relative flex flex-1 items-center justify-center overflow-hidden rounded-xl border border-edge bg-black/30">
              {submitting || (job && !done) ? (
                <div className="w-full max-w-sm space-y-3 p-6 text-center">
                  <p className="text-xs text-ink-dim">{job?.stage || 'queued'}…</p>
                  <ProgressBar value={job?.progress ?? 5} />
                  <p className="text-[11px] text-ink-faint">
                    {selectedModel?.display_name ?? 'engine'} · real background job {jobId?.slice(0, 10)}…
                  </p>
                </div>
              ) : latest.length ? (
                <div className="grid w-full gap-2 p-2 md:grid-cols-2">
                  {latest.map((asset) => (
                    <div key={asset.id} className="overflow-hidden rounded-lg border border-edge bg-black/40">
                      {asset.kind === 'video' ? (
                        <video src={asset.preview_url || asset.url} poster={asset.thumbnail_url || undefined}
                          controls className="max-h-[420px] w-full" />
                      ) : asset.kind === 'audio' ? (
                        <div className="p-6"><audio src={asset.url} controls className="w-full" /></div>
                      ) : (
                        <img src={asset.url} alt={asset.name} className="max-h-[420px] w-full object-contain" />
                      )}
                      <div className="flex items-center justify-between gap-2 border-t border-edge bg-surface-1/70 px-2 py-1.5">
                        <span className="truncate text-[10px] text-ink-faint">
                          {asset.kind} · {asset.width}×{asset.height} · {bytes(asset.size_bytes)}
                          {asset.duration_sec ? ` · ${fmtDuration(asset.duration_sec)}` : ''}
                        </span>
                        <div className="flex gap-1">
                          <a href={asset.url} download><Button size="sm" variant="ghost"><Download className="h-3.5 w-3.5" /></Button></a>
                          <Button size="sm" variant="ghost"
                            onClick={() => void endpoints.updateAsset(asset.id, { is_favorite: true })
                              .then(() => toast({ kind: 'success', title: 'Added to favourites' }))}>
                            <Heart className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="p-10 text-center">
                  <Sparkles className="mx-auto mb-3 h-8 w-8 text-ink-faint" />
                  <p className="text-sm text-ink-dim">Your generation will appear here</p>
                  <p className="mt-1 text-[11px] text-ink-faint">
                    Every result is a real file with metadata and provenance.
                  </p>
                </div>
              )}
            </div>

            {error && (
              <div className="mt-3">
                <ErrorCallout message={error.message} action={error.action} />
                {error.errors && error.errors.length > 1 && (
                  <ul className="mt-2 space-y-1 text-[11px] text-ink-faint">
                    {error.errors.map((e) => <li key={e}>· {e}</li>)}
                  </ul>
                )}
              </div>
            )}

            <div className="mt-3 flex flex-wrap items-center gap-2">
              <Button variant="primary" size="lg" icon={<Sparkles className="h-4 w-4" />} loading={submitting}
                onClick={() => void generate()}>
                Generate
              </Button>
              {jobId && !done && (
                <Button variant="ghost" icon={<X className="h-4 w-4" />}
                  onClick={() => void endpoints.cancelJob(jobId).then(() => toast({ kind: 'info', title: 'Cancel requested' }))}>
                  Cancel
                </Button>
              )}
              <span className="ml-auto text-[11px] text-ink-faint">
                {selectedModel ? `${selectedModel.display_name} · v${selectedModel.version}` : 'No model selected'}
              </span>
            </div>
          </Card>

          <Card className="max-h-56 overflow-y-auto scroll-thin p-3">
            <div className="mb-2 flex items-center justify-between">
              <span className="label">Session history</span>
              {history.length > 0 && (
                <button onClick={() => setHistory([])} className="text-[11px] text-ink-faint hover:text-ink">Clear</button>
              )}
            </div>
            {history.length === 0 ? (
              <p className="py-3 text-center text-[11px] text-ink-faint">No generations in this session yet.</p>
            ) : (
              <div className="space-y-2">
                {history.map((entry) => (
                  <div key={entry.jobId} className="panel-2 flex items-center gap-3 p-2">
                    <div className="h-10 w-14 shrink-0 overflow-hidden rounded border border-edge bg-black/40">
                      {entry.assets[0] && <AssetThumb asset={entry.assets[0]} />}
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[11px] text-ink">{entry.mode}</p>
                      <p className="text-[10px] text-ink-faint">
                        {entry.status} · {entry.assets.length} asset(s) · {entry.jobId.slice(0, 10)}…
                      </p>
                      {entry.error && <p className="text-[10px] text-red-400">{entry.error}</p>}
                    </div>
                    {entry.assets[0] && (
                      <a href={entry.assets[0].url} download>
                        <Button size="sm" variant="ghost"><Download className="h-3.5 w-3.5" /></Button>
                      </a>
                    )}
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>

        {/* ---------------- Right: settings ---------------- */}
        <div className="space-y-3">
          <Card className="space-y-3">
            <Select
              label="Mode"
              value={mode}
              onChange={(e) => setMode(e.target.value)}
              options={
                kind === 'image' ? IMAGE_MODES
                  : kind === 'video' ? VIDEO_MODES
                  : kind === 'avatar' ? AVATAR_MODES
                  : kind === 'voice' ? VOICE_MODES : AUDIO_MODES
              }
            />
            <Select label="Model" value={modelId} onChange={(e) => setModelId(e.target.value)}
              options={models.length
                ? models.map((m) => ({ value: m.id, label: `${m.display_name}${['installed', 'available'].includes(m.status) ? '' : ' — not installed'}` }))
                : [{ value: '', label: 'No models registered' }]} />
            {selectedModel && !['installed', 'available'].includes(selectedModel.status) && (
              <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-2 text-[11px] text-amber-300">
                Model not installed. {selectedModel.status_reason}
              </div>
            )}
            {selectedModel && (
              <div className="flex flex-wrap gap-1">
                <Badge>{selectedModel.family}</Badge>
                <Badge tone={selectedModel.is_local ? 'default' : 'info'}>{selectedModel.is_local ? 'local' : 'api'}</Badge>
                <Badge>{selectedModel.license}</Badge>
                {selectedModel.vram_mb ? <Badge>{Math.round(selectedModel.vram_mb / 1024)}GB VRAM</Badge> : <Badge>CPU</Badge>}
              </div>
            )}
          </Card>

          <Card className="space-y-3">
            <span className="label">Output</span>
            {(kind === 'image' || kind === 'video' || kind === 'avatar') && (
              <>
                <Select label="Aspect ratio" value={settings.aspect} onChange={(e) => setSettings({ ...settings, aspect: e.target.value })}
                  options={ASPECTS.map((a) => ({ value: a, label: a }))} />
                <Select label="Resolution" value={settings.resolution} onChange={(e) => setSettings({ ...settings, resolution: e.target.value })}
                  options={RESOLUTIONS.map((r) => ({ value: r, label: `${r}p` }))} />
              </>
            )}
            {kind === 'image' && (
              <>
                <Select label="Lighting" value={settings.lighting} onChange={(e) => setSettings({ ...settings, lighting: e.target.value })}
                  options={LIGHTING.map((l) => ({ value: l, label: l }))} />
                <Slider label="Images" min={1} max={4} value={settings.count} onChange={(v) => setSettings({ ...settings, count: v })} />
                <Slider label="Steps" min={4} max={60} value={settings.steps} onChange={(v) => setSettings({ ...settings, steps: v })} />
                <Slider label="Guidance" min={1} max={20} step={0.5} value={settings.guidance}
                  onChange={(v) => setSettings({ ...settings, guidance: v })} />
                {mode !== 'text-to-image' && (
                  <Slider label="Strength" min={0} max={1} step={0.05} value={settings.strength}
                    onChange={(v) => setSettings({ ...settings, strength: v })} />
                )}
                {mode === 'upscale' && (
                  <Select label="Upscale factor" value={String(settings.upscale_factor)}
                    onChange={(e) => setSettings({ ...settings, upscale_factor: Number(e.target.value) })}
                    options={['2', '3', '4'].map((f) => ({ value: f, label: `${f}×` }))} />
                )}
              </>
            )}
            {kind === 'video' && (
              <>
                <Slider label="Duration" min={1} max={30} value={settings.duration} suffix="s"
                  onChange={(v) => setSettings({ ...settings, duration: v })} />
                <Select label="FPS" value={String(settings.fps)} onChange={(e) => setSettings({ ...settings, fps: Number(e.target.value) })}
                  options={['12', '16', '24', '30'].map((f) => ({ value: f, label: `${f} fps` }))} />
                <Select label="Camera" value={settings.camera} onChange={(e) => setSettings({ ...settings, camera: e.target.value })}
                  options={CAMERAS.map((c) => ({ value: c, label: c }))} />
                <Select label="Lens" value={settings.lens} onChange={(e) => setSettings({ ...settings, lens: e.target.value })}
                  options={LENSES.map((l) => ({ value: l, label: l }))} />
                <Select label="Motion" value={settings.motion} onChange={(e) => setSettings({ ...settings, motion: e.target.value })}
                  options={MOTIONS.map((m) => ({ value: m, label: m }))} />
                <Select label="Lighting" value={settings.lighting} onChange={(e) => setSettings({ ...settings, lighting: e.target.value })}
                  options={LIGHTING.map((l) => ({ value: l, label: l }))} />
                <Slider label="Strength (image input)" min={0} max={1} step={0.05} value={settings.strength}
                  onChange={(v) => setSettings({ ...settings, strength: v })} />
              </>
            )}
            {kind === 'avatar' && (
              <>
                <TextArea label="Script" rows={4} value={settings.script}
                  onChange={(e) => setSettings({ ...settings, script: e.target.value })}
                  placeholder="What the avatar should say" />
                <Select label="Avatar type" value={settings.avatar_type}
                  onChange={(e) => setSettings({ ...settings, avatar_type: e.target.value })}
                  options={['corporate-presenter', 'news-presenter', 'teacher', 'influencer', 'cartoon',
                    '3d-character', 'photorealistic-human', 'virtual-assistant', 'historical-character',
                    'fantasy-character'].map((t) => ({ value: t, label: t.replace(/-/g, ' ') }))} />
                <Select label="Expression" value={settings.expression}
                  onChange={(e) => setSettings({ ...settings, expression: e.target.value })}
                  options={['neutral', 'happy', 'serious', 'excited', 'calm', 'surprised', 'sad'].map((t) => ({ value: t, label: t }))} />
                <Select label="Gesture" value={settings.gesture}
                  onChange={(e) => setSettings({ ...settings, gesture: e.target.value })}
                  options={['none', 'talking', 'presenting', 'open-palm'].map((t) => ({ value: t, label: t }))} />
                <Select label="Aspect" value={settings.aspect} onChange={(e) => setSettings({ ...settings, aspect: e.target.value })}
                  options={['9:16', '16:9', '1:1'].map((a) => ({ value: a, label: a }))} />
              </>
            )}
            {kind === 'voice' && (
              <>
                <Select label="Language" value={settings.language}
                  onChange={(e) => setSettings({ ...settings, language: e.target.value })}
                  options={[['en', 'English'], ['hi', 'Hindi'], ['te', 'Telugu'], ['ta', 'Tamil'], ['kn', 'Kannada'],
                    ['ml', 'Malayalam'], ['mr', 'Marathi'], ['bn', 'Bengali'], ['gu', 'Gujarati'], ['pa', 'Punjabi'],
                    ['or', 'Odia'], ['as', 'Assamese'], ['ur', 'Urdu']].map(([v, l]) => ({ value: v, label: l }))} />
                <Select label="Voice profile" value={settings.voice_name}
                  onChange={(e) => setSettings({ ...settings, voice_name: e.target.value })}
                  options={['narrator', 'female', 'child', 'deep'].map((v) => ({ value: v, label: v }))} />
                <Slider label="Speed" min={0.5} max={2} step={0.1} value={settings.speed}
                  onChange={(v) => setSettings({ ...settings, speed: v })} />
                <Slider label="Pitch" min={0.5} max={2} step={0.1} value={settings.pitch}
                  onChange={(v) => setSettings({ ...settings, pitch: v })} />
                <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-2 text-[10px] leading-relaxed text-amber-300">
                  No TTS engine is installed on this machine, so narration uses a labelled placeholder track
                  (correct timing, not intelligible speech). Install eSpeak NG or configure a provider for real speech.
                </p>
              </>
            )}
            {kind === 'audio' && (
              <>
                <Select label="Genre" value={settings.genre} onChange={(e) => setSettings({ ...settings, genre: e.target.value })}
                  options={['cinematic', 'corporate', 'kids', 'ambient', 'documentary', 'uplifting', 'suspense']
                    .map((g) => ({ value: g, label: g }))} />
                <Slider label="Duration" min={5} max={120} value={settings.duration} suffix="s"
                  onChange={(v) => setSettings({ ...settings, duration: v })} />
                <Select label="Key" value={settings.key} onChange={(e) => setSettings({ ...settings, key: e.target.value })}
                  options={['C', 'D', 'E', 'F', 'G', 'A', 'B'].map((k) => ({ value: k, label: k }))} />
              </>
            )}
            <Input label="Seed (blank = random)" value={settings.seed}
              onChange={(e) => setSettings({ ...settings, seed: e.target.value })}
              placeholder="deterministic output" />
          </Card>
        </div>
      </div>
    </div>
  );
}
