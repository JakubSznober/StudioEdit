import { useCallback, useEffect, useRef, useState } from 'react';
import { errMsg } from '../api/client';
import { ui } from '../store/ui';

/** Wywołanie asynchroniczne ze stanem ładowania i toastem błędu */
export function useAction<A extends unknown[], R>(fn: (...args: A) => Promise<R>, opts: { success?: string | ((r: R) => string) } = {}) {
  const [busy, setBusy] = useState(false);
  const ref = useRef(fn);
  ref.current = fn;
  const run = useCallback(
    async (...args: A): Promise<R | undefined> => {
      setBusy(true);
      try {
        const r = await ref.current(...args);
        if (opts.success) ui.toast(typeof opts.success === 'function' ? opts.success(r) : opts.success, 'success');
        return r;
      } catch (e) {
        ui.toast(errMsg(e), 'error');
        return undefined;
      } finally {
        setBusy(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  return [run, busy] as const;
}

/** Pobieranie danych z ponawianiem przy zmianie zależności */
export function useFetch<T>(fn: () => Promise<T>, deps: unknown[], opts: { interval?: number; enabled?: boolean } = {}) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const ref = useRef(fn);
  ref.current = fn;
  const enabled = opts.enabled !== false;

  const reload = useCallback(async () => {
    try {
      const r = await ref.current();
      setData(r);
      setError(null);
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    setLoading(true);
    reload();
    if (opts.interval) {
      const t = setInterval(reload, opts.interval);
      return () => clearInterval(t);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled]);

  return { data, error, loading, reload, setData };
}

export function useInterval(fn: () => void, ms: number | null) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (ms == null) return;
    const t = setInterval(() => ref.current(), ms);
    return () => clearInterval(t);
  }, [ms]);
}

export function useNow(ms = 1000) {
  const [now, setNow] = useState(Date.now());
  useInterval(() => setNow(Date.now()), ms);
  return now;
}

export function useElementWidth<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(el);
    setW(el.getBoundingClientRect().width);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

export function isTypingTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}

export function useWindowWidth() {
  const [w, setW] = useState(typeof window === 'undefined' ? 1920 : window.innerWidth);
  useEffect(() => {
    const h = () => setW(window.innerWidth);
    window.addEventListener('resize', h);
    return () => window.removeEventListener('resize', h);
  }, []);
  return w;
}
