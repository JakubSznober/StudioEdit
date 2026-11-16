// Jedno połączenie WebSocket z auto-reconnectem (backoff z jitterem) i dystrybucją zdarzeń.
import type { WsEvent } from '../types';

export type WsHandler = (evt: WsEvent, replay: boolean) => void;
export type WsStatus = 'connecting' | 'open' | 'closed';
type StatusHandler = (s: WsStatus, retryInMs: number | null) => void;

class WsClient {
  private ws: WebSocket | null = null;
  private projectId: string | null = null;
  private handlers = new Set<WsHandler>();
  private statusHandlers = new Set<StatusHandler>();
  private attempt = 0;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private lastMsg = 0;
  private started = false;
  status: WsStatus = 'closed';
  nextRetryAt: number | null = null;

  start(projectId: string | null) {
    this.started = true;
    if (this.ws && this.projectId === projectId && this.status !== 'closed') return;
    this.projectId = projectId;
    this.reconnectNow();
  }

  setProject(projectId: string | null) {
    if (projectId === this.projectId) return;
    this.projectId = projectId;
    if (this.started) this.reconnectNow();
  }

  on(fn: WsHandler): () => void {
    this.handlers.add(fn);
    return () => this.handlers.delete(fn);
  }

  onStatus(fn: StatusHandler): () => void {
    this.statusHandlers.add(fn);
    fn(this.status, this.nextRetryAt ? this.nextRetryAt - Date.now() : null);
    return () => this.statusHandlers.delete(fn);
  }

  send(obj: unknown) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj));
  }

  reconnectNow() {
    this.clearTimers();
    this.attempt = 0;
    this.teardown();
    this.connect();
  }

  private setStatus(s: WsStatus, retryIn: number | null = null) {
    this.status = s;
    this.nextRetryAt = retryIn != null ? Date.now() + retryIn : null;
    this.statusHandlers.forEach((fn) => fn(s, retryIn));
  }

  private url(): string {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const q = this.projectId ? `?project_id=${encodeURIComponent(this.projectId)}` : '';
    return `${proto}://${location.host}/ws${q}`;
  }

  private teardown() {
    if (this.ws) {
      const old = this.ws;
      this.ws = null;
      old.onopen = old.onclose = old.onerror = old.onmessage = null;
      try {
        old.close();
      } catch {
        /* ignoruj */
      }
    }
  }

  private clearTimers() {
    if (this.retryTimer) clearTimeout(this.retryTimer);
    if (this.pingTimer) clearInterval(this.pingTimer);
    this.retryTimer = null;
    this.pingTimer = null;
  }

  private connect() {
    this.setStatus('connecting');
    let ws: WebSocket;
    try {
      ws = new WebSocket(this.url());
    } catch {
      this.scheduleRetry();
      return;
    }
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      this.lastMsg = Date.now();
      this.setStatus('open');
      this.pingTimer = setInterval(() => {
        this.send({ type: 'ping' });
        // brak jakiejkolwiek wiadomości przez 45 s (gpu.stats co 1 s) = martwe połączenie
        if (Date.now() - this.lastMsg > 45000) {
          this.teardown();
          this.clearTimers();
          this.scheduleRetry();
        }
      }, 15000);
    };
    ws.onmessage = (m) => {
      this.lastMsg = Date.now();
      let evt: WsEvent;
      try {
        evt = JSON.parse(typeof m.data === 'string' ? m.data : '');
      } catch {
        return;
      }
      if (!evt || typeof evt.type !== 'string') return;
      if (evt.type === 'pong') return;
      if (evt.type === 'snapshot') {
        const events: WsEvent[] = (evt.data && Array.isArray(evt.data.events) ? evt.data.events : []) as WsEvent[];
        events.forEach((e) => this.emit(e, true));
        return;
      }
      this.emit(evt, false);
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      this.clearTimers();
      this.scheduleRetry();
    };
    ws.onerror = () => {
      /* onclose obsłuży ponowienie */
    };
  }

  private emit(evt: WsEvent, replay: boolean) {
    if (!evt.data) evt.data = {};
    this.handlers.forEach((fn) => {
      try {
        fn(evt, replay);
      } catch (e) {
        console.error('[ws] błąd obsługi zdarzenia', evt.type, e);
      }
    });
  }

  private scheduleRetry() {
    const base = Math.min(15000, 500 * 2 ** Math.min(this.attempt, 5));
    const delay = Math.round(base * (0.75 + Math.random() * 0.5));
    this.attempt += 1;
    this.setStatus('closed', delay);
    this.retryTimer = setTimeout(() => this.connect(), delay);
  }
}

export const ws = new WsClient();
