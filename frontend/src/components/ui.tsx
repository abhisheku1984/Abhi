import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { Asset, Job, Mode } from "../api";

/* ------------------------------------------------------------------ *
 * Capability honesty helpers — the contract this UI must uphold.
 * ------------------------------------------------------------------ */
export function ModeBadge({ mode, title }: { mode: Mode | string; title?: string }) {
  const text: Record<string, string> = {
    real: "REAL",
    demo: "DEMO",
    not_configured: "NOT CONFIGURED",
    unavailable: "UNAVAILABLE",
  };
  return (
    <span className={`badge ${mode}`} title={title}>
      {text[mode] ?? mode}
    </span>
  );
}

export function modeExplanation(mode: string): string {
  switch (mode) {
    case "real":
      return "Working end-to-end right now.";
    case "demo":
      return "A real algorithm standing in for a neural model — labelled DEMO everywhere, never presented as AI.";
    case "not_configured":
      return "The adapter is implemented and waiting for an API key or a GPU host.";
    default:
      return "Unavailable in this environment.";
  }
}

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge ${status}`}>{status}</span>;
}

/* ------------------------------------------------------------------ *
 * Toasts
 * ------------------------------------------------------------------ */
type Toast = { id: number; kind: "info" | "error" | "ok"; text: string };
const ToastContext = createContext<{ push: (kind: Toast["kind"], text: string) => void }>({
  push: () => {},
});

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = Date.now() + Math.random();
    setToasts((current) => [...current, { id, kind, text }]);
    setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), kind === "error" ? 9000 : 5000);
  }, []);
  const value = useMemo(() => ({ push }), [push]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div style={{ position: "fixed", right: 18, bottom: 18, zIndex: 200, display: "flex", flexDirection: "column", gap: 8, maxWidth: 420 }}>
        {toasts.map((toast) => (
          <div key={toast.id} className={`banner ${toast.kind === "error" ? "error" : toast.kind === "ok" ? "ok" : "info"}`}>
            <span>{toast.text}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext);
}

/* ------------------------------------------------------------------ *
 * Small building blocks
 * ------------------------------------------------------------------ */
export function Card({
  title,
  actions,
  children,
  tight,
}: {
  title?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  tight?: boolean;
}) {
  return (
    <div className={`card${tight ? " tight" : ""}`}>
      {(title || actions) && (
        <div className="card-head">
          {title ? <h2 style={{ margin: 0 }}>{title}</h2> : <span />}
          {actions}
        </div>
      )}
      {children}
    </div>
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="row" style={{ gap: 8 }}>
      <span className="spinner" />
      {label && <span className="muted">{label}</span>}
    </span>
  );
}

export function ProgressBar({ value }: { value: number }) {
  return (
    <div className="progress">
      <div style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }} />
    </div>
  );
}

export function Modal({ children, onClose, title }: { children: React.ReactNode; onClose: () => void; title?: React.ReactNode }) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="card-head">
          <h2 style={{ margin: 0 }}>{title}</h2>
          <button className="ghost" onClick={onClose}>
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Asset tile: renders image / video / audio previews from the API.
 * ------------------------------------------------------------------ */
export function AssetTile({
  asset,
  selected,
  onClick,
  showMeta = true,
}: {
  asset: Asset;
  selected?: boolean;
  onClick?: () => void;
  showMeta?: boolean;
}) {
  const src = asset.thumb_url || asset.url;
  return (
    <div className={`asset-tile${selected ? " selected" : ""}`} onClick={onClick}>
      {asset.kind === "image" && <img className="asset-thumb" src={src} alt={asset.filename} loading="lazy" />}
      {asset.kind === "video" && (
        <video className="asset-thumb" src={asset.url} muted loop preload="metadata" onMouseOver={(e) => (e.target as HTMLVideoElement).play().catch(() => {})} onMouseOut={(e) => { const v = e.target as HTMLVideoElement; v.pause(); v.currentTime = 0; }} />
      )}
      {asset.kind === "audio" && (
        <div className="asset-thumb center" style={{ flexDirection: "column", gap: 8 }}>
          <span style={{ fontSize: 26 }}>♪</span>
          <span className="faint">{asset.duration_s ? `${asset.duration_s.toFixed(1)}s` : "audio"}</span>
        </div>
      )}
      {asset.kind === "file" && (
        <div className="asset-thumb center">
          <span className="faint">file</span>
        </div>
      )}
      {showMeta && (
        <div className="asset-meta">
          <span className="asset-title truncate" title={asset.filename}>
            {asset.filename}
          </span>
          <span className="chip-row">
            <span className="faint">{asset.kind}</span>
            {asset.meta?.quality === "demo" && <ModeBadge mode="demo" />}
            {asset.width && asset.height && (
              <span className="faint">
                {asset.width}×{asset.height}
              </span>
            )}
            {asset.duration_s ? <span className="faint">{asset.duration_s.toFixed(1)}s</span> : null}
          </span>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Job log with live polling.
 * ------------------------------------------------------------------ */
export function JobLog({ job }: { job: Job | null }) {
  if (!job) return null;
  return (
    <div className="log">
      {(job.logs ?? []).map((line, index) => (
        <div key={index} className={`log-line${line.message.startsWith("[error]") ? " err" : ""}`}>
          {line.message}
        </div>
      ))}
      {job.error && <div className="log-line err">{job.error}</div>}
      {!job.logs?.length && !job.error && <div className="log-line">no output yet…</div>}
    </div>
  );
}

export function usePolling(callback: () => void, intervalMs = 4000, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    const timer = setInterval(callback, intervalMs);
    return () => clearInterval(timer);
  }, [callback, intervalMs, enabled]);
}
