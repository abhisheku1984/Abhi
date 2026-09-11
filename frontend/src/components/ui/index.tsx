import React, { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes,
  TextareaHTMLAttributes, useEffect } from 'react';
import { clsx } from '@/lib/format';
import { motion, AnimatePresence } from 'framer-motion';
import { AlertTriangle, CheckCircle2, Info, Loader2, X } from 'lucide-react';
import { useAppStore } from '@/app/store';

/* ------------------------------- Button ------------------------------- */

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'subtle';

export function Button({
  variant = 'secondary',
  size = 'md',
  loading = false,
  icon,
  children,
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: 'sm' | 'md' | 'lg';
  loading?: boolean;
  icon?: ReactNode;
}) {
  const variants: Record<ButtonVariant, string> = {
    primary: 'bg-brand text-white hover:brightness-110 border-transparent shadow-glow',
    secondary: 'bg-surface-2/80 text-ink border-edge hover:bg-surface-3',
    ghost: 'bg-transparent text-ink-dim hover:text-ink hover:bg-surface-2/70 border-transparent',
    danger: 'bg-red-500/90 text-white hover:bg-red-500 border-transparent',
    subtle: 'bg-brand/10 text-brand border-brand/30 hover:bg-brand/20',
  };
  const sizes = {
    sm: 'h-8 px-3 text-xs rounded-lg gap-1.5',
    md: 'h-10 px-4 text-sm rounded-xl gap-2',
    lg: 'h-12 px-6 text-[15px] rounded-xl gap-2',
  };
  return (
    <button
      {...props}
      disabled={props.disabled || loading}
      className={clsx(
        'inline-flex items-center justify-center border font-medium transition-all duration-200',
        'disabled:opacity-50 disabled:cursor-not-allowed active:scale-[0.98]',
        variants[variant],
        sizes[size],
        className,
      )}
    >
      {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : icon}
      {children}
    </button>
  );
}

export function IconButton({ children, label, className, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      {...props}
      title={label}
      aria-label={label}
      className={clsx(
        'inline-flex h-8 w-8 items-center justify-center rounded-lg border border-edge bg-surface-2/60',
        'text-ink-dim transition hover:bg-surface-3 hover:text-ink disabled:opacity-40',
        className,
      )}
    >
      {children}
    </button>
  );
}

/* -------------------------------- Card -------------------------------- */

export function Card({ children, className, hover = false }: { children: ReactNode; className?: string; hover?: boolean }) {
  return (
    <div className={clsx('panel p-4', hover && 'transition hover:border-brand/40 hover:bg-surface-2/50', className)}>
      {children}
    </div>
  );
}

export function SectionTitle({ title, subtitle, action, icon }: { title: string; subtitle?: string; action?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="mb-3 flex items-end justify-between gap-4">
      <div>
        <h2 className="flex items-center gap-1.5 text-sm font-semibold tracking-tight text-ink">
          {icon}{title}
        </h2>
        {subtitle && <p className="mt-0.5 text-xs text-ink-faint">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

/* ------------------------------- Inputs ------------------------------- */

export function Input({ label, hint, className, ...props }: InputHTMLAttributes<HTMLInputElement> & { label?: string; hint?: string }) {
  return (
    <label className="block">
      {label && <span className="label mb-1.5 block">{label}</span>}
      <input {...props} className={clsx('input', className)} />
      {hint && <span className="mt-1 block text-[11px] text-ink-faint">{hint}</span>}
    </label>
  );
}

export function TextArea({ label, hint, className, ...props }: TextareaHTMLAttributes<HTMLTextAreaElement> & { label?: string; hint?: string }) {
  return (
    <label className="block">
      {label && <span className="label mb-1.5 block">{label}</span>}
      <textarea {...props} className={clsx('input min-h-[84px] resize-y', className)} />
      {hint && <span className="mt-1 block text-[11px] text-ink-faint">{hint}</span>}
    </label>
  );
}

export function Select({ label, options, className, ...props }: SelectHTMLAttributes<HTMLSelectElement> & {
  label?: string;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="block">
      {label && <span className="label mb-1.5 block">{label}</span>}
      <select {...props} className={clsx('input appearance-none pr-8', className)}>
        {options.map((o) => (
          <option key={o.value} value={o.value} className="bg-surface-2">
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function Slider({ label, value, min, max, step = 1, onChange, suffix }: {
  label: string; value: number; min: number; max: number; step?: number;
  onChange: (v: number) => void; suffix?: string;
}) {
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between">
        <span className="label">{label}</span>
        <span className="text-xs font-medium text-ink-dim">
          {value}
          {suffix}
        </span>
      </div>
      <input
        type="range"
        className="w-full"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </div>
  );
}

export function Toggle({ label, checked, onChange, hint }: { label: string; checked: boolean; onChange: (v: boolean) => void; hint?: string }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className="flex w-full items-center justify-between gap-3 rounded-lg px-1 py-1.5 text-left transition hover:bg-surface-2/50"
    >
      <span>
        <span className="block text-sm text-ink">{label}</span>
        {hint && <span className="block text-[11px] text-ink-faint">{hint}</span>}
      </span>
      <span className={clsx('relative h-5 w-9 shrink-0 rounded-full transition',
        checked ? 'bg-brand' : 'bg-surface-3')}>
        <span className={clsx('absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all',
          checked ? 'left-[18px]' : 'left-0.5')} />
      </span>
    </button>
  );
}

/* -------------------------------- Misc -------------------------------- */

export function Badge({ children, tone = 'default', className }: {
  children: ReactNode; tone?: 'default' | 'brand' | 'success' | 'warn' | 'danger' | 'info'; className?: string;
}) {
  const tones = {
    default: 'border-edge bg-surface-2/70 text-ink-dim',
    brand: 'border-brand/40 bg-brand/10 text-brand',
    success: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-400',
    warn: 'border-amber-500/40 bg-amber-500/10 text-amber-400',
    danger: 'border-red-500/40 bg-red-500/10 text-red-400',
    info: 'border-sky-500/40 bg-sky-500/10 text-sky-400',
  };
  return (
    <span className={clsx('inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-medium', tones[tone], className)}>
      {children}
    </span>
  );
}

export function ProgressBar({ value, tone = 'brand', showLabel = false }: { value: number; tone?: 'brand' | 'success' | 'danger'; showLabel?: boolean }) {
  const tones = { brand: 'bg-brand', success: 'bg-emerald-500', danger: 'bg-red-500' };
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-3">
        <motion.div
          className={clsx('h-full rounded-full', tones[tone])}
          initial={{ width: 0 }}
          animate={{ width: `${Math.max(0, Math.min(100, value))}%` }}
          transition={{ duration: 0.4 }}
        />
      </div>
      {showLabel && <span className="w-9 text-right text-[11px] tabular-nums text-ink-faint">{Math.round(value)}%</span>}
    </div>
  );
}

export function Spinner({ size = 20 }: { size?: number }) {
  return <Loader2 className="animate-spin text-brand" style={{ width: size, height: size }} />;
}

export function EmptyState({ title, description, icon, action }: {
  title: string; description?: string; icon?: ReactNode; action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-edge bg-surface-1/40 px-6 py-14 text-center">
      <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-xl border border-edge bg-surface-2/60 text-ink-faint">
        {icon}
      </div>
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      {description && <p className="mt-1 max-w-sm text-xs text-ink-faint">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorCallout({ title, message, action }: { title?: string; message: string; action?: string }) {
  return (
    <div className="flex gap-3 rounded-xl border border-red-500/30 bg-red-500/5 p-3 text-sm">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />
      <div>
        <p className="font-medium text-red-300">{title ?? 'Something went wrong'}</p>
        <p className="mt-0.5 text-xs text-red-200/80">{message}</p>
        {action && <p className="mt-1 text-xs text-ink-dim">Suggested action: {action}</p>}
      </div>
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: {
  tabs: { id: T; label: string; icon?: ReactNode }[];
  value: T;
  onChange: (id: T) => void;
}) {
  return (
    <div className="flex gap-1 overflow-x-auto rounded-xl border border-edge bg-surface-1/70 p-1 scroll-thin">
      {tabs.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          className={clsx(
            'flex items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-medium transition',
            value === t.id ? 'bg-brand/15 text-brand' : 'text-ink-dim hover:bg-surface-2/70 hover:text-ink',
          )}
        >
          {t.icon}
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function Modal({ open, onClose, title, children, footer, width = 'max-w-lg' }: {
  open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode; width?: string;
}) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    if (open) window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={onClose}
        >
          <motion.div
            className={clsx('panel w-full p-5', width)}
            initial={{ opacity: 0, y: 12, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-ink">{title}</h3>
              <IconButton label="Close" onClick={onClose}><X className="h-4 w-4" /></IconButton>
            </div>
            <div className="max-h-[70vh] overflow-y-auto scroll-thin">{children}</div>
            {footer && <div className="mt-4 flex justify-end gap-2">{footer}</div>}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function StatCard({ label, value, hint, icon, tone = 'default' }: {
  label: string; value: string | number; hint?: string; icon?: ReactNode; tone?: 'default' | 'brand' | 'warn' | 'success';
}) {
  const tones = {
    default: 'text-ink',
    brand: 'text-brand',
    warn: 'text-amber-400',
    success: 'text-emerald-400',
  };
  return (
    <div className="panel p-4">
      <div className="flex items-center justify-between">
        <span className="label">{label}</span>
        {icon && <span className="text-ink-faint">{icon}</span>}
      </div>
      <p className={clsx('mt-2 text-2xl font-semibold tabular-nums tracking-tight', tones[tone])}>{value}</p>
      {hint && <p className="mt-1 text-[11px] text-ink-faint">{hint}</p>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx('animate-shimmer rounded-lg bg-surface-2', className)} />;
}

export function Toasts() {
  const toasts = useAppStore((s) => s.toasts);
  const dismiss = useAppStore((s) => s.dismiss);
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-80 flex-col gap-2">
      <AnimatePresence>
        {toasts.map((t) => (
          <motion.div
            key={t.id}
            layout
            initial={{ opacity: 0, x: 40 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: 40 }}
            className="pointer-events-auto panel flex gap-3 p-3"
          >
            <span className="mt-0.5">
              {t.kind === 'success' ? <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                : t.kind === 'error' ? <AlertTriangle className="h-4 w-4 text-red-400" />
                : <Info className="h-4 w-4 text-sky-400" />}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-semibold text-ink">{t.title}</p>
              {t.message && <p className="mt-0.5 break-words text-[11px] text-ink-dim">{t.message}</p>}
              {t.action && <p className="mt-1 text-[11px] text-ink-faint">{t.action}</p>}
            </div>
            <button onClick={() => dismiss(t.id)} className="self-start text-ink-faint hover:text-ink">
              <X className="h-3.5 w-3.5" />
            </button>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

export function KeyHint({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded border border-edge bg-surface-2 px-1.5 py-0.5 font-mono text-[10px] text-ink-faint">
      {children}
    </kbd>
  );
}
