import { useEffect, useState } from 'react';
import { Palette, Server, ShieldCheck, UserRound } from 'lucide-react';
import { Branding, endpoints } from '@/lib/api';
import { Badge, Button, Card, Input, SectionTitle, Select, Tabs, TextArea, Toggle } from '@/components/ui';
import { useAppStore } from '@/app/store';
import { hexToRgbTriplet } from '@/lib/format';

type Tab = 'branding' | 'providers' | 'moderation' | 'account';

function ErrorText({ value }: { value: string | null }) {
  if (!value) return null;
  return <p className="rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">{value}</p>;
}

export function SettingsPage() {
  const store = useAppStore();
  const user = useAppStore((s) => s.user);
  const toast = useAppStore((s) => s.toast);
  const [tab, setTab] = useState<Tab>('branding');
  const [branding, setBranding] = useState<Branding>(store.branding);
  const [providers, setProviders] = useState<any[]>([]);
  const [moderation, setModeration] = useState<any>({});
  const [saving, setSaving] = useState(false);
  const [profile, setProfile] = useState({ name: user?.name ?? '' });
  const [passwords, setPasswords] = useState({ current: '', next: '', confirm: '' });
  const [passwordError, setPasswordError] = useState<string | null>(null);

  useEffect(() => {
    endpoints.branding().then(setBranding).catch(() => null);
    endpoints.providers().then((r) => setProviders(r.providers ?? [])).catch(() => setProviders([]));
    endpoints.moderation().then(setModeration).catch(() => setModeration({}));
  }, []);

  const isAdmin = user?.role === 'admin' || user?.role === 'owner';

  async function saveBranding() {
    setSaving(true);
    try {
      const updated = await endpoints.updateBranding(branding);
      setBranding(updated);
      store.setBranding(updated);
      toast({ kind: 'success', title: 'Branding updated' });
    } catch (err) {
      toast({ kind: 'error', title: 'Could not save branding', message: (err as Error).message });
    } finally {
      setSaving(false);
    }
  }

  async function saveProfile() {
    setSaving(true);
    try {
      const updated = await endpoints.updateMe({ name: profile.name.trim() });
      store.setUser(updated);
      toast({ kind: 'success', title: 'Profile updated' });
    } catch (err) {
      toast({ kind: 'error', title: 'Could not save profile', message: (err as Error).message });
    } finally {
      setSaving(false);
    }
  }

  async function changePassword() {
    setPasswordError(null);
    if (passwords.next.length < 8) {
      setPasswordError('The new password must be at least 8 characters long.');
      return;
    }
    if (passwords.next !== passwords.confirm) {
      setPasswordError('The two new passwords do not match.');
      return;
    }
    setSaving(true);
    try {
      await endpoints.changePassword(passwords.current, passwords.next);
      setPasswords({ current: '', next: '', confirm: '' });
      toast({ kind: 'success', title: 'Password changed' });
    } catch (err) {
      setPasswordError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  async function saveModeration() {
    try {
      const updated = await endpoints.updateModeration(moderation);
      setModeration(updated);
      toast({ kind: 'success', title: 'Moderation settings updated' });
    } catch (err) {
      toast({ kind: 'error', title: 'Could not save settings', message: (err as Error).message });
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-ink">Settings</h1>
        <p className="text-xs text-ink-faint">Branding, providers and safety — all stored server-side.</p>
      </div>

      <Tabs
        tabs={[
          { id: 'branding', label: 'Branding', icon: <Palette className="h-3.5 w-3.5" /> },
          { id: 'providers', label: 'AI providers', icon: <Server className="h-3.5 w-3.5" /> },
          { id: 'moderation', label: 'Safety & rights', icon: <ShieldCheck className="h-3.5 w-3.5" /> },
          { id: 'account', label: 'Account', icon: <UserRound className="h-3.5 w-3.5" /> },
        ]}
        value={tab}
        onChange={(id) => setTab(id as Tab)}
      />

      {tab === 'branding' && (
        <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]">
          <Card className="space-y-3">
            <div className="grid gap-3 md:grid-cols-2">
              <Input label="Product name" value={branding.product_name}
                onChange={(e) => setBranding({ ...branding, product_name: e.target.value })} />
              <Input label="Company name" value={branding.company_name}
                onChange={(e) => setBranding({ ...branding, company_name: e.target.value })} />
              <Input label="Logo URL" value={branding.logo_url}
                onChange={(e) => setBranding({ ...branding, logo_url: e.target.value })} />
              <Input label="Favicon URL" value={branding.favicon_url}
                onChange={(e) => setBranding({ ...branding, favicon_url: e.target.value })} />
              <Input label="Login headline" value={branding.login_headline}
                onChange={(e) => setBranding({ ...branding, login_headline: e.target.value })} />
              <Input label="Login sub-headline" value={branding.login_subheadline}
                onChange={(e) => setBranding({ ...branding, login_subheadline: e.target.value })} />
            </div>
            <TextArea label="Footer" value={branding.footer}
              onChange={(e) => setBranding({ ...branding, footer: e.target.value })} />
            <div className="grid gap-3 md:grid-cols-3">
              <div>
                <span className="label mb-1.5 block">Primary colour</span>
                <div className="flex items-center gap-2">
                  <input type="color" value={branding.primary_color}
                    onChange={(e) => setBranding({ ...branding, primary_color: e.target.value })}
                    className="h-10 w-14 cursor-pointer rounded-lg border border-edge bg-transparent" />
                  <Input value={branding.primary_color}
                    onChange={(e) => setBranding({ ...branding, primary_color: e.target.value })} />
                </div>
              </div>
              <div>
                <span className="label mb-1.5 block">Secondary colour</span>
                <div className="flex items-center gap-2">
                  <input type="color" value={branding.secondary_color}
                    onChange={(e) => setBranding({ ...branding, secondary_color: e.target.value })}
                    className="h-10 w-14 cursor-pointer rounded-lg border border-edge bg-transparent" />
                  <Input value={branding.secondary_color}
                    onChange={(e) => setBranding({ ...branding, secondary_color: e.target.value })} />
                </div>
              </div>
              <Select label="Theme" value={branding.theme}
                onChange={(e) => setBranding({ ...branding, theme: e.target.value })}
                options={[{ value: 'dark', label: 'Dark' }, { value: 'light', label: 'Light' }]} />
            </div>
            <div className="flex items-center gap-2">
              <Button variant="primary" loading={saving} onClick={() => void saveBranding()}>Save branding</Button>
              <Button variant="ghost" onClick={() => store.setBranding(branding)}>Preview</Button>
              {!isAdmin && <Badge tone="warn">Admin role required to save</Badge>}
            </div>
          </Card>

          <Card className="space-y-3">
            <SectionTitle title="Live preview" />
            <div className="space-y-2 rounded-xl border border-edge p-3"
              style={{
                background: `linear-gradient(135deg, rgb(${hexToRgbTriplet(branding.primary_color)} / 0.2), transparent)`,
              }}>
              <div className="flex items-center gap-2">
                <span className="flex h-8 w-8 items-center justify-center rounded-xl"
                  style={{ background: branding.primary_color }}>
                  {(branding.product_name || 'A').slice(0, 1)}
                </span>
                <div>
                  <p className="text-sm font-semibold text-ink">{branding.product_name}</p>
                  <p className="text-[10px] text-ink-faint">{branding.company_name}</p>
                </div>
              </div>
              <p className="text-[11px] text-ink-dim">{branding.login_headline}</p>
              <div className="flex gap-2">
                <span className="rounded-lg px-2.5 py-1 text-[10px] font-medium text-white"
                  style={{ background: branding.primary_color }}>Primary</span>
                <span className="rounded-lg px-2.5 py-1 text-[10px] font-medium text-ink"
                  style={{ background: branding.secondary_color }}>Secondary</span>
              </div>
              <p className="text-[10px] text-ink-faint">{branding.footer}</p>
            </div>
          </Card>
        </div>
      )}

      {tab === 'providers' && (
        <Card className="space-y-3">
          <p className="text-[11px] text-ink-faint">
            Provider credentials live in <code>backend/.env</code> and are never exposed to the browser.
            Configure one to unlock neural models through the same adapter interface.
          </p>
          <div className="divide-y divide-edge">
            {providers.map((provider) => (
              <div key={provider.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5">
                <div>
                  <p className="text-xs text-ink">{provider.id}</p>
                  <p className="font-mono text-[10px] text-ink-faint">{provider.env.join(', ')}</p>
                </div>
                <Badge tone={provider.configured ? 'success' : 'default'}>
                  {provider.configured ? 'configured' : 'not configured'}
                </Badge>
              </div>
            ))}
          </div>
        </Card>
      )}

      {tab === 'moderation' && (
        <Card className="space-y-3">
          <Toggle label="Content moderation" hint="Block prompts that match the safety policy"
            checked={Boolean(moderation.enabled)} onChange={(v) => setModeration({ ...moderation, enabled: v })} />
          <Toggle label="Visible watermark" hint="Stamp generated assets with provenance text"
            checked={Boolean(moderation.watermark)} onChange={(v) => setModeration({ ...moderation, watermark: v })} />
          <Input label="Watermark text" value={moderation.watermark_text ?? ''}
            onChange={(e) => setModeration({ ...moderation, watermark_text: e.target.value })} />
          <Toggle label="Require voice-cloning consent" hint="Blocks cloning until a rights attestation is recorded"
            checked={Boolean(moderation.require_voice_consent)}
            onChange={(v) => setModeration({ ...moderation, require_voice_consent: v })} />
          <TextArea label="Blocklist (comma separated)" value={moderation.blocklist ?? ''}
            onChange={(e) => setModeration({ ...moderation, blocklist: e.target.value })} />
          <Button variant="primary" onClick={() => void saveModeration()}>Save safety settings</Button>
        </Card>
      )}

      {tab === 'account' && (
        <div className="grid gap-4 xl:grid-cols-2">
          <Card className="space-y-3">
            <SectionTitle title="Profile" subtitle="How your name appears on assets and audit entries" />
            <Input label="Name" value={profile.name}
              onChange={(e) => setProfile({ name: e.target.value })} />
            <Input label="Email" value={user?.email ?? ''} disabled
              hint="Contact your administrator to change the account email." />
            <div className="flex flex-wrap gap-2 text-[11px] text-ink-faint">
              <Badge tone="default">{user?.role ?? 'viewer'}</Badge>
              <Badge tone="default">{(user?.storage_used_mb ?? 0).toFixed(1)} MB used</Badge>
            </div>
            <Button variant="primary" disabled={saving || !profile.name.trim()} onClick={() => void saveProfile()}>
              Save profile
            </Button>
          </Card>

          <Card className="space-y-3">
            <SectionTitle title="Password" subtitle="Eight characters or more. You stay signed in on this device." />
            <Input label="Current password" type="password" value={passwords.current}
              onChange={(e) => setPasswords({ ...passwords, current: e.target.value })} />
            <Input label="New password" type="password" value={passwords.next}
              onChange={(e) => setPasswords({ ...passwords, next: e.target.value })} />
            <Input label="Confirm new password" type="password" value={passwords.confirm}
              onChange={(e) => setPasswords({ ...passwords, confirm: e.target.value })} />
            <ErrorText value={passwordError} />
            <Button variant="primary" disabled={saving || !passwords.current || !passwords.next}
              onClick={() => void changePassword()}>
              Change password
            </Button>
          </Card>
        </div>
      )}
    </div>
  );
}
