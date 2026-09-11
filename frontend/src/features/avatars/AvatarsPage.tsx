import { useEffect, useState } from 'react';
import { Plus, UserSquare2 } from 'lucide-react';
import { endpoints } from '@/lib/api';
import { Badge, Button, Card, EmptyState, Input, Modal, Select, TextArea } from '@/components/ui';
import { useAppStore } from '@/app/store';
import { useJobPolling } from '@/lib/hooks';

const TYPES = ['corporate-presenter', 'news-presenter', 'teacher', 'influencer', 'cartoon', '3d-character',
  'photorealistic-human', 'virtual-assistant', 'historical-character', 'fantasy-character'];

export function AvatarsPage() {
  const toast = useAppStore((s) => s.toast);
  const [items, setItems] = useState<any[]>([]);
  const [voices, setVoices] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    name: '', avatar_type: 'corporate-presenter', voice_id: '',
    profile: { expression: 'neutral', gesture: 'talking', style: 'photorealistic' } as Record<string, string>,
  });
  const [script, setScript] = useState('Hello! Welcome to AI Creative Studio. Let me walk you through today’s update.');
  const [jobId, setJobId] = useState<string | null>(null);
  const { job, done } = useJobPolling(jobId, 1200);

  const load = () => {
    endpoints.avatars().then((r) => setItems(r.items)).catch(() => setItems([]));
    endpoints.voices().then((r) => setVoices(r.items)).catch(() => setVoices([]));
  };
  useEffect(() => { void load(); }, []);

  useEffect(() => {
    if (done && job) {
      if (job.status === 'completed') toast({ kind: 'success', title: 'Avatar video ready' });
      else toast({ kind: 'error', title: 'Avatar failed', message: job.error?.message, action: job.error?.suggested_action });
    }
  }, [done, job]);

  async function create() {
    try {
      await endpoints.createAvatar(form);
      toast({ kind: 'success', title: 'Avatar created' });
      setOpen(false);
      void load();
    } catch (err) {
      toast({ kind: 'error', title: 'Could not create avatar', message: (err as Error).message });
    }
  }

  async function talk(avatar?: any) {
    try {
      const res = await endpoints.generate('avatar', {
        prompt: script,
        mode: 'avatar-from-script',
        voice_id: avatar?.voice_id || undefined,
        params: {
          script,
          avatar_type: avatar?.avatar_type ?? 'corporate-presenter',
          profile: avatar?.profile ?? undefined,
          aspect: '9:16',
          resolution: '512',
          fps: 24,
        },
      });
      setJobId(res.job_id);
      toast({ kind: 'success', title: 'Talking avatar queued', message: res.engine_name });
    } catch (err) {
      toast({ kind: 'error', title: 'Could not queue avatar', message: (err as Error).message });
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-ink">Avatar studio</h1>
          <p className="text-xs text-ink-faint">
            Talking avatars with audio-driven mouth animation, expressions and gestures.
          </p>
        </div>
        <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setOpen(true)}>New avatar</Button>
      </div>

      <Card className="space-y-3">
        <TextArea label="Script" rows={3} value={script} onChange={(e) => setScript(e.target.value)} />
        <Button variant="primary" icon={<UserSquare2 className="h-4 w-4" />} loading={jobId !== null && !done}
          onClick={() => void talk()}>Generate talking avatar</Button>
        {job && <p className="text-[11px] text-ink-faint">{job.status} · {job.stage} · {Math.round(job.progress)}%</p>}
      </Card>

      {items.length === 0 ? (
        <EmptyState icon={<UserSquare2 className="h-5 w-5" />} title="No avatars yet"
          description="Create an avatar profile, or generate a talking avatar directly from a script."
          action={<Button variant="primary" onClick={() => setOpen(true)}>Create avatar</Button>} />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {items.map((avatar) => (
            <Card key={avatar.id} hover>
              <div className="mb-2 flex h-32 items-center justify-center rounded-xl border border-edge bg-surface-2">
                {avatar.thumbnail_asset_id ? (
                  <img src={`/api/v1/assets/${avatar.thumbnail_asset_id}`} alt="" className="h-full w-full object-cover" />
                ) : (
                  <UserSquare2 className="h-7 w-7 text-ink-faint" />
                )}
              </div>
              <p className="truncate text-sm font-semibold text-ink">{avatar.name}</p>
              <div className="mt-1 flex flex-wrap gap-1">
                <Badge tone="brand">{avatar.avatar_type}</Badge>
                {avatar.profile?.style && <Badge>{avatar.profile.style}</Badge>}
              </div>
              <Button size="sm" className="mt-3 w-full" onClick={() => void talk(avatar)}>Speak script</Button>
            </Card>
          ))}
        </div>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title="New avatar"
        footer={<>
          <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="primary" onClick={() => void create()}>Create</Button>
        </>}>
        <div className="space-y-3">
          <Input label="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <Select label="Avatar type" value={form.avatar_type}
            onChange={(e) => setForm({ ...form, avatar_type: e.target.value })}
            options={TYPES.map((t) => ({ value: t, label: t.replace(/-/g, ' ') }))} />
          <Select label="Voice" value={form.voice_id} onChange={(e) => setForm({ ...form, voice_id: e.target.value })}
            options={[{ value: '', label: 'Default' }, ...voices.map((v) => ({ value: v.id, label: v.name }))]} />
          <Select label="Expression" value={form.profile.expression}
            onChange={(e) => setForm({ ...form, profile: { ...form.profile, expression: e.target.value } })}
            options={['neutral', 'happy', 'serious', 'excited', 'calm', 'surprised', 'sad'].map((v) => ({ value: v, label: v }))} />
          <Select label="Gesture" value={form.profile.gesture}
            onChange={(e) => setForm({ ...form, profile: { ...form.profile, gesture: e.target.value } })}
            options={['none', 'talking', 'presenting', 'open-palm'].map((v) => ({ value: v, label: v }))} />
        </div>
      </Modal>
    </div>
  );
}
