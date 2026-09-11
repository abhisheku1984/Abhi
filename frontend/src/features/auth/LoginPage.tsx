import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Sparkles } from 'lucide-react';
import { Button, Card, ErrorCallout, Input } from '@/components/ui';
import { endpoints, setAccessToken } from '@/lib/api';
import { useAppStore } from '@/app/store';

export function LoginPage() {
  const navigate = useNavigate();
  const branding = useAppStore((s) => s.branding);
  const setUser = useAppStore((s) => s.setUser);
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [email, setEmail] = useState('admin@studio.ai');
  const [password, setPassword] = useState('Admin@12345');
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = mode === 'login'
        ? await endpoints.login(email, password)
        : await endpoints.register(email, password, name || email.split('@')[0]);
      setAccessToken(result.access_token);
      setUser(result.user);
      navigate('/dashboard');
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-full lg:grid-cols-2">
      <div className="relative hidden flex-col justify-between overflow-hidden border-r border-edge bg-surface-1/60 p-10 lg:flex">
        <div className="grid-fade absolute inset-0 opacity-40" />
        <div className="relative flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-2xl bg-brand/15 text-brand">
            <Sparkles className="h-5 w-5" />
          </div>
          <div>
            <p className="text-sm font-semibold text-ink">{branding.product_name}</p>
            <p className="text-[11px] uppercase tracking-wider text-ink-faint">{branding.company_name}</p>
          </div>
        </div>

        <div className="relative max-w-md">
          <h1 className="text-3xl font-semibold leading-tight tracking-tight text-ink">
            {branding.login_headline}
          </h1>
          <p className="mt-3 text-sm text-ink-dim">{branding.login_subheadline}</p>
          <div className="mt-8 space-y-2.5">
            {[
              ['Image', 'Text to image, editing, inpainting, upscaling, background control'],
              ['Video', 'Text/image to video, camera moves, first & last frame, extension'],
              ['Avatar & Voice', 'Talking avatars, lip sync, multilingual narration'],
              ['Story', 'Idea → script → storyboard → scenes → finished video'],
            ].map(([title, desc]) => (
              <div key={title} className="rounded-xl border border-edge bg-surface-2/40 px-3 py-2.5">
                <p className="text-xs font-semibold text-ink">{title}</p>
                <p className="text-[11px] text-ink-faint">{desc}</p>
              </div>
            ))}
          </div>
        </div>

        <p className="relative text-[11px] text-ink-faint">{branding.footer}</p>
      </div>

      <div className="flex items-center justify-center p-6">
        <Card className="w-full max-w-sm p-6">
          <h2 className="text-lg font-semibold tracking-tight text-ink">
            {mode === 'login' ? 'Sign in' : 'Create account'}
          </h2>
          <p className="mt-1 text-xs text-ink-faint">
            {mode === 'login'
              ? 'Use the bootstrap admin account created on first start.'
              : 'New accounts are created with the editor role.'}
          </p>

          <form onSubmit={submit} className="mt-5 space-y-3">
            {mode === 'register' && (
              <Input label="Name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" />
            )}
            <Input label="Email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            <Input label="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              required minLength={8} />
            {error && <ErrorCallout title="Authentication failed" message={error} />}
            <Button type="submit" variant="primary" size="lg" className="w-full" loading={busy}>
              {mode === 'login' ? 'Sign in' : 'Create account'}
            </Button>
          </form>

          <button
            onClick={() => setMode(mode === 'login' ? 'register' : 'login')}
            className="mt-4 w-full text-center text-[11px] text-ink-faint hover:text-brand"
          >
            {mode === 'login' ? 'Need an account? Create one' : 'Already have an account? Sign in'}
          </button>

          <p className="mt-6 rounded-lg border border-edge bg-surface-2/40 p-2.5 text-[10px] leading-relaxed text-ink-faint">
            Local-first: your assets, projects and models stay on this machine. Nothing is uploaded unless you
            configure an external provider in Settings.
          </p>
        </Card>
      </div>
    </div>
  );
}
