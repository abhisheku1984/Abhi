import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { CornerDownLeft, Sparkles, X, ShieldAlert, CheckCircle2 } from 'lucide-react';
import { Button, Badge } from '@/components/ui';
import { useAppStore } from '@/app/store';
import { endpoints } from '@/lib/api';

interface Message {
  role: 'user' | 'assistant';
  text: string;
  detail?: string;
  needsConfirm?: boolean;
  jobId?: string;
}

const SUGGESTIONS = [
  'Create a cinematic image of a futuristic Hyderabad',
  'Turn this image into a 10 second video',
  'Generate Hindi narration for my script',
  'Make scene 3 more cinematic',
  'Create a talking avatar using this script',
];

/** Command-based assistant (§27): plan → confirm → execute. */
export function AssistantPanel() {
  const open = useAppStore((s) => s.assistantOpen);
  const setOpen = useAppStore((s) => s.setAssistantOpen);
  const toast = useAppStore((s) => s.toast);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [pendingConfirm, setPendingConfirm] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen(!open);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, setOpen]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  async function send(command: string, confirm = false) {
    if (!command.trim() || busy) return;
    setBusy(true);
    setMessages((m) => [...m, { role: 'user', text: command }]);
    setInput('');
    try {
      const result = await endpoints.assistant(command, {}, confirm);
      const planned = result.planned ?? {};
      if (result.needs_confirmation) {
        setPendingConfirm(command);
        setMessages((m) => [...m, {
          role: 'assistant',
          text: planned.message ?? 'This action needs confirmation.',
          detail: 'Reply “confirm” to continue, or cancel.',
          needsConfirm: true,
        }]);
      } else {
        setPendingConfirm(null);
        const jobId = result.job_id;
        const detail = result.message
          || (result.result ? JSON.stringify(result.result).slice(0, 400) : undefined);
        setMessages((m) => [...m, {
          role: 'assistant',
          text: planned.intent ? `${planned.intent.replace(/_/g, ' ')} — ${result.executed ? 'done' : 'planned'}` : 'Done',
          detail,
          jobId,
        }]);
        if (jobId) {
          toast({ kind: 'success', title: 'Job queued', message: `Job ${jobId.slice(0, 12)}… is running` });
        }
      }
    } catch (err) {
      setMessages((m) => [...m, { role: 'assistant', text: (err as Error).message }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AnimatePresence>
      {open && (
        <motion.aside
          initial={{ x: 420, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          exit={{ x: 420, opacity: 0 }}
          transition={{ type: 'spring', damping: 26, stiffness: 260 }}
          className="fixed right-0 top-0 z-50 flex h-full w-full max-w-md flex-col border-l border-edge bg-surface-1/95 backdrop-blur-2xl"
        >
          <div className="flex h-16 items-center gap-2 border-b border-edge px-4">
            <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-brand/15 text-brand">
              <Sparkles className="h-4 w-4" />
            </div>
            <div className="flex-1">
              <p className="text-sm font-semibold text-ink">AI Assistant</p>
              <p className="text-[11px] text-ink-faint">Commands run real jobs — destructive actions ask first</p>
            </div>
            <button onClick={() => setOpen(false)} className="text-ink-faint hover:text-ink">
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="flex-1 space-y-3 overflow-y-auto p-4 scroll-thin">
            {messages.length === 0 && (
              <div className="space-y-2">
                <p className="text-xs text-ink-faint">Try one of these:</p>
                {SUGGESTIONS.map((s) => (
                  <button
                    key={s}
                    onClick={() => send(s)}
                    className="block w-full rounded-xl border border-edge bg-surface-2/50 px-3 py-2 text-left text-xs text-ink-dim transition hover:border-brand/40 hover:text-ink"
                  >
                    {s}
                  </button>
                ))}
              </div>
            )}

            {messages.map((m, i) => (
              <div key={i} className={m.role === 'user' ? 'flex justify-end' : ''}>
                <div className={
                  m.role === 'user'
                    ? 'max-w-[85%] rounded-2xl rounded-br-md bg-brand/15 px-3 py-2 text-xs text-ink'
                    : 'max-w-[95%] rounded-2xl rounded-bl-md border border-edge bg-surface-2/60 px-3 py-2 text-xs text-ink-dim'
                }>
                  <p className="whitespace-pre-wrap">{m.text}</p>
                  {m.detail && <p className="mt-1.5 whitespace-pre-wrap text-[11px] text-ink-faint">{m.detail}</p>}
                  {m.needsConfirm && (
                    <div className="mt-2 flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-2 py-1.5">
                      <ShieldAlert className="h-3.5 w-3.5 text-amber-400" />
                      <span className="text-[11px] text-amber-300">Requires confirmation</span>
                    </div>
                  )}
                  {m.jobId && (
                    <a href={`/jobs`} className="mt-2 inline-flex items-center gap-1 text-[11px] text-brand hover:underline">
                      <CheckCircle2 className="h-3 w-3" /> View job {m.jobId.slice(0, 10)}…
                    </a>
                  )}
                </div>
              </div>
            ))}
            <div ref={bottomRef} />
          </div>

          <div className="border-t border-edge p-3">
            <div className="flex items-center gap-2">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    if (pendingConfirm && /^(confirm|yes|y)$/i.test(input.trim())) {
                      const cmd = pendingConfirm;
                      setPendingConfirm(null);
                      void send(cmd, true);
                    } else {
                      void send(input);
                    }
                  }
                }}
                placeholder={pendingConfirm ? 'Type confirm to proceed…' : 'Ask me to create something…'}
                className="input h-10 text-xs"
              />
              <Button size="sm" variant="primary" loading={busy} onClick={() => void send(input)}>
                <CornerDownLeft className="h-3.5 w-3.5" />
              </Button>
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              <Badge>plan</Badge>
              <Badge>execute</Badge>
              <Badge tone="warn">confirm before delete</Badge>
            </div>
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}
