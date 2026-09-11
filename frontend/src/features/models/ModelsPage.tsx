import { useEffect, useState } from 'react';
import { Cpu, Download, HardDrive, Play, Trash2 } from 'lucide-react';
import { ModelInfo, endpoints } from '@/lib/api';
import { Badge, Button, Card, Modal, SectionTitle, Skeleton, Tabs } from '@/components/ui';
import { useAppStore } from '@/app/store';
import { clsx } from '@/lib/format';

const FAMILIES = ['image', 'upscale', 'video', 'voice', 'audio', 'avatar', 'lipsync'];

export function ModelsPage() {
  const toast = useAppStore((s) => s.toast);
  const [items, setItems] = useState<ModelInfo[]>([]);
  const [family, setFamily] = useState('image');
  const [gpu, setGpu] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState<ModelInfo | null>(null);
  const [installInfo, setInstallInfo] = useState<any>(null);

  const load = () => {
    setLoading(true);
    endpoints.models()
      .then((r) => { setItems(r.items); setGpu(r.gpu); })
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  async function test(id: string) {
    try {
      const result = await endpoints.testModel(id);
      toast({
        kind: result.ok ? 'success' : 'error',
        title: result.ok ? 'Model test passed' : 'Model not ready',
        message: result.reason || undefined,
      });
    } catch (err) {
      toast({ kind: 'error', title: 'Test failed', message: (err as Error).message });
    }
  }

  async function install(id: string) {
    try {
      const result = await endpoints.installModel(id);
      setInstallInfo(result);
    } catch (err) {
      toast({ kind: 'error', title: 'Install failed', message: (err as Error).message });
    }
  }

  async function activate(id: string, active: boolean) {
    try {
      if (active) await endpoints.activateModel(id); else await endpoints.deactivateModel(id);
      toast({ kind: 'success', title: active ? 'Model activated' : 'Model deactivated' });
      load();
    } catch (err) {
      toast({ kind: 'error', title: 'Could not change model state', message: (err as Error).message });
    }
  }

  const filtered = items.filter((m) => m.family === family);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-ink">Model manager</h1>
          <p className="text-xs text-ink-faint">
            Every model is an adapter behind one interface. Swap them without touching the app.
          </p>
        </div>
        <div className="flex items-center gap-2 text-[11px] text-ink-dim">
          <Cpu className="h-4 w-4" />
          {gpu?.gpu_available ? `${gpu.device_count} GPU · ${Math.round((gpu.vram_total_mb ?? 0) / 1024)}GB VRAM` : 'CPU only'}
        </div>
      </div>

      {gpu && !gpu.gpu_available && (
        <Card className="border-amber-500/30 bg-amber-500/5 text-[11px] text-amber-300">
          No GPU detected. Neural models report “Model not installed” until you install weights
          (<code>pip install -r backend/requirements-gpu.txt</code>) or configure an API provider.
          Deterministic CPU engines stay fully functional.
        </Card>
      )}

      <Tabs tabs={FAMILIES.map((f) => ({ id: f, label: f }))} value={family} onChange={setFamily} />

      {loading ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-44" />)}
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {filtered.map((model) => {
            const ready = ['installed', 'available'].includes(model.status);
            return (
              <Card key={model.id} hover className="flex flex-col gap-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-ink">{model.display_name}</p>
                    <p className="text-[10px] text-ink-faint">v{model.version} · {model.provider}</p>
                  </div>
                  <Badge tone={ready ? 'success' : 'warn'}>{model.status.replace('_', ' ')}</Badge>
                </div>

                <div className="flex flex-wrap gap-1">
                  <Badge>{model.license}</Badge>
                  <Badge>{model.is_local ? 'local' : 'api'}</Badge>
                  {model.vram_mb > 0 && <Badge>{Math.round(model.vram_mb / 1024)}GB VRAM</Badge>}
                  {model.size_mb > 0 && <Badge>{Math.round(model.size_mb / 1024)}GB</Badge>}
                  <Badge tone="info">{model.speed}</Badge>
                </div>

                <div className="flex flex-wrap gap-1">
                  {model.modes.slice(0, 5).map((mode) => (
                    <span key={mode} className="chip">{mode}</span>
                  ))}
                  {model.modes.length > 5 && <span className="chip">+{model.modes.length - 5}</span>}
                </div>

                {!ready && model.status_reason && (
                  <p className="rounded-lg border border-edge bg-surface-2/50 p-2 text-[10px] leading-relaxed text-ink-faint">
                    {model.status_reason}
                  </p>
                )}

                <div className="mt-auto flex flex-wrap gap-1.5 pt-2">
                  <Button size="sm" variant="ghost" onClick={() => setDetail(model)}>Details</Button>
                  <Button size="sm" variant="ghost" icon={<Play className="h-3.5 w-3.5" />}
                    onClick={() => void test(model.id)}>Test</Button>
                  {!ready && (
                    <Button size="sm" variant="subtle" icon={<Download className="h-3.5 w-3.5" />}
                      onClick={() => void install(model.id)}>Install</Button>
                  )}
                  {ready && (
                    <Button size="sm" variant="ghost" onClick={() => void activate(model.id, true)}>Activate</Button>
                  )}
                </div>
              </Card>
            );
          })}
          {filtered.length === 0 && (
            <Card className="text-xs text-ink-faint">No adapters registered for “{family}”.</Card>
          )}
        </div>
      )}

      <Modal open={Boolean(detail)} onClose={() => setDetail(null)} title={detail?.display_name ?? ''} width="max-w-2xl">
        {detail && (
          <div className="space-y-3 text-xs">
            <div className="grid grid-cols-2 gap-2">
              {[
                ['ID', detail.id], ['Family', detail.family], ['Version', detail.version],
                ['Provider', detail.provider], ['License', detail.license],
                ['Size', `${detail.size_mb} MB`], ['VRAM', `${detail.vram_mb} MB`], ['Speed', detail.speed],
              ].map(([k, v]) => (
                <div key={k} className="rounded-lg border border-edge bg-surface-2/40 p-2">
                  <p className="text-[10px] uppercase tracking-wider text-ink-faint">{k}</p>
                  <p className="truncate text-ink-dim">{v}</p>
                </div>
              ))}
            </div>
            {detail.notes && (
              <p className="rounded-lg border border-edge bg-surface-2/40 p-2.5 leading-relaxed text-ink-dim">
                {detail.notes}
              </p>
            )}
            <div>
              <p className="label mb-1.5">Capabilities</p>
              <div className="flex flex-wrap gap-1">
                {Object.entries(detail.capabilities ?? {})
                  .filter(([, v]) => v === true || (typeof v === 'number' && v > 0))
                  .map(([k]) => <Badge key={k}>{k.replace(/_/g, ' ')}</Badge>)}
              </div>
            </div>
            <div>
              <p className="label mb-1.5">Modes</p>
              <div className="flex flex-wrap gap-1">
                {detail.modes.map((m) => <span key={m} className="chip">{m}</span>)}
              </div>
            </div>
          </div>
        )}
      </Modal>

      <Modal open={Boolean(installInfo)} onClose={() => setInstallInfo(null)} title="Install model">
        {installInfo && (
          <div className="space-y-3 text-xs">
            <p className="text-ink-dim">{installInfo.message ?? 'This model needs a manual install step.'}</p>
            {Array.isArray(installInfo.instructions) && (
              <ol className="space-y-1.5">
                {installInfo.instructions.filter(Boolean).map((step: string, i: number) => (
                  <li key={i} className="rounded-lg border border-edge bg-surface-2/50 p-2 font-mono text-[11px] text-ink-dim">
                    {i + 1}. {step}
                  </li>
                ))}
              </ol>
            )}
            <p className="rounded-lg border border-edge bg-surface-2/40 p-2 text-[10px] leading-relaxed text-ink-faint">
              Large weights are never downloaded automatically — installing always stays under your control.
            </p>
          </div>
        )}
      </Modal>
    </div>
  );
}
