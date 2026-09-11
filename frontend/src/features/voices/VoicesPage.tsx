import { useEffect, useState } from 'react';
import { Mic2, Plus, ShieldCheck } from 'lucide-react';
import { endpoints } from '@/lib/api';
import { Badge, Button, Card, EmptyState, Input, Modal, Select, Slider, Toggle } from '@/components/ui';
import { useAppStore } from '@/app/store';
import { useJobPolling } from '@/lib/hooks';

const LANGUAGES: [string, string][] = [
  ['en', 'English'], ['hi', 'Hindi'], ['te', 'Telugu'], ['ta', 'Tamil'], ['kn', 'Kannada'],
  ['ml', 'Malayalam'], ['mr', 'Marathi'], ['bn', 'Bengali'], ['gu', 'Gujarati'], ['pa', 'Punjabi'],
  ['or', 'Odia'], ['as', 'Assamese'], ['ur', 'Urdu'],
];

const CONSENT_FIELDS: [string, string][] = [
  ['owner_attestation', 'I own or am authorised to use this voice'],
  ['rights_holder', 'Rights holder name'],
  ['authorised_by', 'Authorised by'],
  ['purpose', 'Intended purpose'],
];

export function VoicesPage() {
  const toast = useAppStore((s) => s.toast);
  const [items, setItems] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    name: '', engine: '', language: 'en', accent: '', gender: 'neutral', style: 'neutral',
    emotion: 'neutral', speed: 1, pitch: 1, is_cloned: false,
    consent: {} as Record<string, string | boolean>,
  });
  const [testText, setTestText] = useState('Welcome to AI Creative Studio.');
  const [jobId, setJobId] = useState<string | null>(null);
  const { job, done } = useJobPolling(jobId, 1000);

  const load = () => endpoints.voices().then((r) => setItems(r.items)).catch(() => setItems([]));
  useEffect(() => { void load(); }, []);

  useEffect(() => {
    if (done && job) {
      if (job.status === 'completed') toast({ kind: 'success', title: 'Narration ready', message: 'Available in the asset library' });
      else toast({ kind: 'error', title: 'Narration failed', message: job.error?.message, action: job.error?.suggested_action });
    }
  }, [done, job]);

  async function create() {
    try {
      await endpoints.createVoice(form);
      toast({ kind: 'success', title: 'Voice created' });
      setOpen(false);
      void load();
    } catch (err) {
      toast({ kind: 'error', title: 'Could not create voice', message: (err as Error).message });
    }
  }

  async function speak(voiceId?: string) {
    try {
      const res = await endpoints.generate('voice', {
        prompt: testText,
        mode: 'text-to-speech',
        voice_id: voiceId,
        params: { text: testText, language: form.language, speed: form.speed, pitch: form.pitch },
      });
      setJobId(res.job_id);
      toast({ kind: 'success', title: 'Narration queued', message: res.engine_name });
    } catch (err) {
      toast({ kind: 'error', title: 'Could not queue narration', message: (err as Error).message });
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-ink">Voices</h1>
          <p className="text-xs text-ink-faint">Multilingual narration profiles — Indian languages first.</p>
        </div>
        <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setOpen(true)}>New voice</Button>
      </div>

      <Card className="space-y-3">
        <p className="label">Quick narration</p>
        <Input value={testText} onChange={(e) => setTestText(e.target.value)} placeholder="Text to speak" />
        <div className="flex flex-wrap items-center gap-2">
          <Select value={form.language} onChange={(e) => setForm({ ...form, language: e.target.value })}
            options={LANGUAGES.map(([v, l]) => ({ value: v, label: l }))} className="w-40" />
          <Button variant="primary" icon={<Mic2 className="h-4 w-4" />} loading={jobId !== null && !done}
            onClick={() => void speak()}>Generate</Button>
        </div>
        <p className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-2 text-[10px] leading-relaxed text-amber-300">
          No TTS engine is installed here, so narration uses a labelled placeholder track with correct timing
          (flagged <code>intelligible: false</code> in the asset metadata). Install eSpeak NG or configure a provider
          for intelligible speech — no code changes required.
        </p>
      </Card>

      {items.length === 0 ? (
        <EmptyState icon={<Mic2 className="h-5 w-5" />} title="No voice profiles yet"
          description="Create profiles for narrators, presenters and characters."
          action={<Button variant="primary" onClick={() => setOpen(true)}>Create voice</Button>} />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {items.map((voice) => (
            <Card key={voice.id} hover>
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold text-ink">{voice.name}</p>
                  <p className="text-[11px] text-ink-faint">
                    {LANGUAGES.find(([v]) => v === voice.language)?.[1] ?? voice.language}
                    {voice.accent ? ` · ${voice.accent}` : ''} · {voice.style}
                  </p>
                </div>
                {voice.is_cloned && <Badge tone={voice.consent_complete ? 'success' : 'danger'}>
                  {voice.consent_complete ? 'consent ok' : 'consent missing'}
                </Badge>}
              </div>
              <div className="mt-3 flex gap-2">
                <Button size="sm" onClick={() => void speak(voice.id)} icon={<Mic2 className="h-3.5 w-3.5" />}>Speak</Button>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title="New voice profile"
        footer={<>
          <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="primary" onClick={() => void create()}>Create</Button>
        </>}>
        <div className="space-y-3">
          <Input label="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <div className="grid gap-2 md:grid-cols-2">
            <Select label="Language" value={form.language} onChange={(e) => setForm({ ...form, language: e.target.value })}
              options={LANGUAGES.map(([v, l]) => ({ value: v, label: l }))} />
            <Input label="Accent" value={form.accent} onChange={(e) => setForm({ ...form, accent: e.target.value })} />
            <Select label="Gender" value={form.gender} onChange={(e) => setForm({ ...form, gender: e.target.value })}
              options={['neutral', 'feminine', 'masculine'].map((g) => ({ value: g, label: g }))} />
            <Select label="Style" value={form.style} onChange={(e) => setForm({ ...form, style: e.target.value })}
              options={['neutral', 'narration', 'conversational', 'advertising', 'educational'].map((s) => ({ value: s, label: s }))} />
          </div>
          <Slider label="Speed" min={0.5} max={2} step={0.1} value={form.speed}
            onChange={(v) => setForm({ ...form, speed: v })} />
          <Slider label="Pitch" min={0.5} max={2} step={0.1} value={form.pitch}
            onChange={(v) => setForm({ ...form, pitch: v })} />

          <div className="border-t border-edge pt-3">
            <Toggle label="This is a cloned voice" hint="Requires a recorded rights attestation"
              checked={form.is_cloned}
              onChange={(v) => setForm({ ...form, is_cloned: v })} />
            {form.is_cloned && (
              <div className="mt-2 space-y-2 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3">
                <p className="flex items-center gap-1.5 text-[11px] text-amber-300">
                  <ShieldCheck className="h-3.5 w-3.5" /> Cloning a real person without authorisation is blocked.
                </p>
                {CONSENT_FIELDS.map(([field, label]) => (
                  field === 'owner_attestation' ? (
                    <Toggle key={field} label={label} checked={Boolean(form.consent[field])}
                      onChange={(v) => setForm({ ...form, consent: { ...form.consent, [field]: v } })} />
                  ) : (
                    <Input key={field} label={label} value={String(form.consent[field] ?? '')}
                      onChange={(e) => setForm({ ...form, consent: { ...form.consent, [field]: e.target.value } })} />
                  )
                ))}
              </div>
            )}
          </div>
        </div>
      </Modal>
    </div>
  );
}
