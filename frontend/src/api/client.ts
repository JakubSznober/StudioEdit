// Wrapper fetch z obsługą błędów `{"detail": "..."}` z backendu FastAPI.

export class ApiError extends Error {
  status: number;
  detail: string;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
  }
  get isNetwork(): boolean {
    return this.status === 0;
  }
}

type NetListener = (online: boolean) => void;
const netListeners = new Set<NetListener>();

/** Powiadamia (np. store połączenia) o sukcesie / błędzie sieci. */
export function onNetworkState(fn: NetListener): () => void {
  netListeners.add(fn);
  return () => netListeners.delete(fn);
}

function emitNet(online: boolean) {
  netListeners.forEach((fn) => fn(online));
}

function extractDetail(body: unknown, status: number): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) {
      return d
        .map((x) => (x && typeof x === 'object' && 'msg' in x ? String((x as { msg: unknown }).msg) : String(x)))
        .join('; ');
    }
    if (d != null) return JSON.stringify(d);
  }
  if (typeof body === 'string' && body.trim()) return body.slice(0, 300);
  return `Błąd serwera (HTTP ${status})`;
}

export interface RequestOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
  /** nie zgłaszaj stanu sieci (np. dla zapytań pomocniczych) */
  silent?: boolean;
}

export async function request<T>(method: string, path: string, body?: unknown, opts: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  const ctrl = new AbortController();
  const timeout = opts.timeoutMs ?? 30000;
  const timer = timeout > 0 ? setTimeout(() => ctrl.abort(), timeout) : undefined;
  if (opts.signal) {
    if (opts.signal.aborted) ctrl.abort();
    else opts.signal.addEventListener('abort', () => ctrl.abort(), { once: true });
  }
  let res: Response;
  try {
    res = await fetch(path, { method, headers, body: payload, signal: ctrl.signal });
  } catch (e) {
    if (timer) clearTimeout(timer);
    if (opts.signal?.aborted) throw new ApiError(-1, 'Anulowano');
    if (!opts.silent) emitNet(false);
    throw new ApiError(0, 'Brak połączenia z serwerem StudioEdit');
  }
  if (timer) clearTimeout(timer);
  if (!opts.silent) {
    // 502/503/504 z proxy Vite = backend nie działa
    emitNet(!(res.status === 502 || res.status === 503 || res.status === 504));
  }
  const ct = res.headers.get('content-type') || '';
  let data: unknown = null;
  if (res.status !== 204) {
    try {
      data = ct.includes('application/json') ? await res.json() : await res.text();
    } catch {
      data = null;
    }
  }
  if (!res.ok) {
    if (res.status === 502 || res.status === 503 || res.status === 504) {
      throw new ApiError(0, 'Serwer StudioEdit jest niedostępny');
    }
    throw new ApiError(res.status, extractDetail(data, res.status));
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, opts?: RequestOptions) => request<T>('GET', path, undefined, opts),
  post: <T>(path: string, body?: unknown, opts?: RequestOptions) => request<T>('POST', path, body ?? {}, opts),
  patch: <T>(path: string, body?: unknown, opts?: RequestOptions) => request<T>('PATCH', path, body ?? {}, opts),
  del: <T>(path: string, opts?: RequestOptions) => request<T>('DELETE', path, undefined, opts),
};

/** Upload multipart z postępem (XMLHttpRequest - fetch nie raportuje postępu wysyłania). */
export function uploadFiles<T>(path: string, files: File[], onProgress?: (p: number) => void, field = 'files'): Promise<T> {
  return new Promise((resolve, reject) => {
    const fd = new FormData();
    files.forEach((f) => fd.append(field, f, f.name));
    const xhr = new XMLHttpRequest();
    xhr.open('POST', path);
    xhr.responseType = 'text';
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
    };
    xhr.onerror = () => {
      emitNet(false);
      reject(new ApiError(0, 'Brak połączenia z serwerem StudioEdit'));
    };
    xhr.onload = () => {
      let data: unknown = xhr.responseText;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* tekst */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as T);
      else reject(new ApiError(xhr.status, extractDetail(data, xhr.status)));
    };
    xhr.send(fd);
  });
}

export function errMsg(e: unknown): string {
  if (e instanceof ApiError) return e.detail;
  if (e instanceof Error) return e.message;
  return String(e);
}

export const qs = (params: Record<string, string | number | boolean | null | undefined>) => {
  const p = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') p.set(k, String(v));
  });
  const s = p.toString();
  return s ? `?${s}` : '';
};
