import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Lock, Plus, Users, Wand2 } from 'lucide-react';
import { endpoints } from '@/lib/api';
import { Badge, Button, Card, EmptyState, Input, Modal, Select, TextArea, Toggle } from '@/components/ui';
import { useAppStore } from '@/app/store';

const PROFILE_FIELDS: [string, string][] = [
  ['face', 'Face'], ['hair', 'Hair'], ['age_appearance', 'Age appearance'], ['body_type', 'Body type'],
  ['clothing', 'Clothing'], ['accessories', 'Accessories'], ['personality', 'Personality'], ['style', 'Style'],
];

const LOCK_FIELDS = [
  ['character', 'Lock character'], ['face', 'Lock face'], ['costume', 'Lock costume'],
  ['style', 'Lock style'], ['environment', 'Lock environment'], ['voice', 'Lock voice'],
];

export function CharactersPage() {
  const toast = useAppStore((s) => s.toast);
  const [items, setItems] = useState<any[]>([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ name: '', description: '', profile: {} as Record<string, string>,
    locks: { character: true, face: true, costume: true, style: true, environment: false, voice: false } as Record<string, boolean> });
  const [promptResult, setPromptResult] = useState<{ character_id?: string; locked_prompt: string; enhanced: string } | null>(null);

  const load = () => endpoints.characters().then((r) => setItems(r.items)).catch(() => setItems([]));
  useEffect(() => { void load(); }, []);

  async function create() {
    try {
      await endpoints.createCharacter({
        name: form.name || 'New character',
        description: form.description,
        profile: form.profile,
        locks: form.locks,
      });
      toast({ kind: 'success', title: 'Character created' });
      setOpen(false);
      setForm({ name: '', description: '', profile: {}, locks: form.locks });
      void load();
    } catch (err) {
      toast({ kind: 'error', title: 'Could not create character', message: (err as Error).message });
    }
  }

  async function buildPrompt(characterId: string) {
    try {
      const result = await endpoints.characterPrompt(characterId, { action: 'standing in a sunlit street', environment: 'modern city' });
      setPromptResult(result);
    } catch (err) {
      toast({ kind: 'error', title: 'Could not build prompt', message: (err as Error).message });
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-ink">Characters</h1>
          <p className="text-xs text-ink-faint">
            Reusable identities with lockable consistency — face, costume, style, voice.
          </p>
        </div>
        <Button variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setOpen(true)}>New character</Button>
      </div>

      {items.length === 0 ? (
        <EmptyState icon={<Users className="h-5 w-5" />} title="No characters yet"
          description="Create a character once, then reuse it across scenes, shots and videos."
          action={<Button variant="primary" onClick={() => setOpen(true)}>Create character</Button>} />
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {items.map((character) => {
            const locked = Object.entries(character.locks ?? {}).filter(([, v]) => v).map(([k]) => k);
            return (
              <Card key={character.id} hover>
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-ink">{character.name}</p>
                    <p className="line-clamp-2 text-[11px] text-ink-faint">{character.description || 'No description'}</p>
                  </div>
                  <Lock className="h-4 w-4 text-brand" />
                </div>

                <div className="mt-3 space-y-1 text-[11px] text-ink-dim">
                  {PROFILE_FIELDS.filter(([f]) => character.profile?.[f]).map(([field, label]) => (
                    <p key={field}><span className="text-ink-faint">{label}:</span> {character.profile[field]}</p>
                  ))}
                </div>

                <div className="mt-3 flex flex-wrap gap-1">
                  {locked.map((l) => <Badge key={l} tone="brand">lock: {l}</Badge>)}
                </div>

                <div className="mt-3 flex gap-2">
                  <Button size="sm" icon={<Wand2 className="h-3.5 w-3.5" />} onClick={() => void buildPrompt(character.id)}>
                    Consistency prompt
                  </Button>
                  <Link to="/create?kind=image">
                    <Button size="sm" variant="subtle">Use in workspace</Button>
                  </Link>
                </div>
              </Card>
            );
          })}
        </div>
      )}

      {promptResult && (
        <Card className="space-y-2">
          <p className="label">Locked prompt</p>
          <p className="rounded-lg border border-edge bg-surface-2/50 p-2.5 text-xs text-ink-dim">
            {promptResult.locked_prompt}
          </p>
          <p className="label">Enhanced</p>
          <p className="rounded-lg border border-brand/30 bg-brand/5 p-2.5 text-xs text-ink-dim">
            {promptResult.enhanced}
          </p>
        </Card>
      )}

      <Modal open={open} onClose={() => setOpen(false)} title="New character" width="max-w-2xl"
        footer={<>
          <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
          <Button variant="primary" onClick={() => void create()}>Create</Button>
        </>}>
        <div className="space-y-3">
          <Input label="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <TextArea label="Description" value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })} />
          <div className="grid gap-2 md:grid-cols-2">
            {PROFILE_FIELDS.map(([field, label]) => (
              <Input key={field} label={label} value={form.profile[field] ?? ''}
                onChange={(e) => setForm({ ...form, profile: { ...form.profile, [field]: e.target.value } })} />
            ))}
          </div>
          <div className="border-t border-edge pt-3">
            <p className="label mb-2">Consistency locks</p>
            {LOCK_FIELDS.map(([field, label]) => (
              <Toggle key={field} label={label} checked={Boolean(form.locks[field])}
                onChange={(v) => setForm({ ...form, locks: { ...form.locks, [field]: v } })} />
            ))}
          </div>
        </div>
      </Modal>
    </div>
  );
}
