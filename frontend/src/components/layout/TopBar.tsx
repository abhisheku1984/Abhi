import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Cpu, LogOut, Search, Sparkles, Wifi, WifiOff } from 'lucide-react';
import { Button, IconButton } from '@/components/ui';
import { useAppStore } from '@/app/store';
import { endpoints } from '@/lib/api';
import { useJobSocket } from '@/lib/hooks';

export function TopBar() {
  const navigate = useNavigate();
  const location = useLocation();
  const user = useAppStore((s) => s.user);
  const logout = useAppStore((s) => s.logout);
  const toast = useAppStore((s) => s.toast);
  const setAssistantOpen = useAppStore((s) => s.setAssistantOpen);
  const [gpu, setGpu] = useState<{ gpu_available: boolean; backend: string; device_count: number } | null>(null);
  const [live, setLive] = useState(false);
  const [query, setQuery] = useState('');

  useEffect(() => {
    endpoints.gpu().then((data) => setGpu(data as never)).catch(() => setGpu(null));
  }, []);

  useJobSocket((event) => {
    setLive(true);
    if (event.status === 'completed') {
      toast({ kind: 'success', title: 'Generation complete', message: `${event.stage || 'Job'} finished` });
    } else if (event.status === 'failed') {
      toast({ kind: 'error', title: 'Generation failed', message: 'Retry / Change model / Check GPU' });
    }
  });

  const title = location.pathname.split('/').filter(Boolean).slice(-1)[0] || 'dashboard';

  return (
    <header className="flex h-16 shrink-0 items-center gap-3 border-b border-edge bg-surface-1/50 px-4 backdrop-blur-xl">
      <div className="hidden min-w-0 flex-1 items-center gap-3 md:flex">
        <div className="relative w-full max-w-md">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-faint" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && query.trim()) {
                navigate(`/assets?q=${encodeURIComponent(query.trim())}`);
              }
            }}
            placeholder="Search assets, or press ⌘K for the assistant"
            className="input h-9 pl-9 text-xs"
          />
        </div>
      </div>

      <div className="ml-auto flex items-center gap-2">
        <div className="hidden items-center gap-1.5 rounded-lg border border-edge bg-surface-2/60 px-2.5 py-1.5 text-[11px] text-ink-dim sm:flex">
          <Cpu className="h-3.5 w-3.5" />
          {gpu ? (
            <span>{gpu.gpu_available ? `${gpu.device_count}× GPU · ${gpu.backend}` : 'CPU only'}</span>
          ) : (
            <span>…</span>
          )}
        </div>

        <div className="hidden items-center gap-1.5 rounded-lg border border-edge bg-surface-2/60 px-2.5 py-1.5 text-[11px] sm:flex">
          {live ? <Wifi className="h-3.5 w-3.5 text-emerald-400" /> : <WifiOff className="h-3.5 w-3.5 text-ink-faint" />}
          <span className="text-ink-dim">{live ? 'Live' : 'Idle'}</span>
        </div>

        <Button size="sm" variant="subtle" icon={<Sparkles className="h-3.5 w-3.5" />}
          onClick={() => setAssistantOpen(true)}>
          Assistant
        </Button>

        <div className="flex items-center gap-2 rounded-xl border border-edge bg-surface-2/50 py-1 pl-1 pr-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand/20 text-[11px] font-semibold text-brand">
            {(user?.name || user?.email || 'U').slice(0, 2).toUpperCase()}
          </div>
          <div className="hidden leading-tight sm:block">
            <p className="text-[11px] font-medium text-ink">{user?.name || user?.email}</p>
            <p className="text-[10px] uppercase tracking-wide text-ink-faint">{user?.role}</p>
          </div>
          <IconButton label="Sign out" onClick={() => { logout(); navigate('/login'); }}>
            <LogOut className="h-3.5 w-3.5" />
          </IconButton>
        </div>
      </div>

      <span className="sr-only">{title}</span>
    </header>
  );
}
