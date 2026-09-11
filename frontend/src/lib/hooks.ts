import { useCallback, useEffect, useRef, useState } from 'react';
import { Job, endpoints, getAccessToken } from '@/lib/api';

/** Poll a job until it reaches a terminal state (WebSocket fallback / non-WS pages). */
export function useJobPolling(jobId?: string | null, intervalMs = 1200) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!jobId) {
      setJob(null);
      return;
    }
    let cancelled = false;
    const tick = async () => {
      try {
        const data = await endpoints.job(jobId);
        if (cancelled) return;
        setJob(data);
        if (['completed', 'failed', 'cancelled'].includes(data.status)) return;
      } catch (err) {
        if (!cancelled) setError((err as Error).message);
        return;
      }
    };
    void tick();
    const timer = window.setInterval(tick, intervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [jobId, intervalMs]);

  return { job, error, done: job ? ['completed', 'failed', 'cancelled'].includes(job.status) : false };
}

/** Live job events over WebSocket (falls back silently when WS is unavailable). */
export function useJobSocket(onEvent: (event: { job_id: string; status: string; progress: number;
  stage: string; error?: unknown; result?: unknown }) => void) {
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  useEffect(() => {
    const token = getAccessToken();
    if (!token) return;
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const url = `${proto}://${window.location.host}/ws/jobs?token=${encodeURIComponent(token)}`;
    let socket: WebSocket | null = null;
    let retry = 0;
    let closed = false;

    const connect = () => {
      if (closed) return;
      try {
        socket = new WebSocket(url);
      } catch {
        return;
      }
      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'job') handlerRef.current(data);
        } catch {
          /* ignore malformed frames */
        }
      };
      socket.onclose = () => {
        if (closed) return;
        retry += 1;
        if (retry <= 5) window.setTimeout(connect, Math.min(8000, 500 * 2 ** retry));
      };
    };
    connect();
    return () => {
      closed = true;
      socket?.close();
    };
  }, []);
}

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);
  const [tick, setTick] = useState(0);

  const refetch = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fn()
      .then((result) => !cancelled && setData(result))
      .catch((err) => !cancelled && setError(err as Error))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  return { data, loading, error, refetch };
}

export function useDebounced<T>(value: T, delay = 400) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}
