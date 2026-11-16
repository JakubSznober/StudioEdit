// Start aplikacji: połączenie z API i WebSocketem, ponawianie z backoffem, okresowe odświeżanie.
import { onNetworkState } from '../api/client';
import { sys } from '../api/endpoints';
import { ws } from '../api/ws';
import { dispatch } from './dispatch';
import { live, useLive } from './live';
import { project, useProject } from './project';
import { applyTheme } from './ui';

let retryTimer: ReturnType<typeof setTimeout> | null = null;
let attempt = 0;
let booted = false;
let connecting = false;

async function loadProjectTasks() {
  const pid = useProject.getState().activeId;
  if (!pid) return;
  try {
    const tasks = await sys.tasks({ project_id: pid });
    if (Array.isArray(tasks)) live.upsertTasks(tasks);
  } catch {
    /* ignoruj */
  }
}

export async function refreshStatus(): Promise<boolean> {
  try {
    const s = await sys.status();
    live.applyStatus(s || {});
    const ap = s?.active_project;
    const apId = typeof ap === 'string' ? ap : ap && typeof ap === 'object' ? ap.id : null;
    project.adoptServerActive(apId);
    return true;
  } catch {
    return false;
  }
}

async function onOnline() {
  useLive.setState({ apiOnline: true, apiRetryAt: null });
  attempt = 0;
  sys
    .health()
    .then((h) => useLive.setState({ version: h?.version ?? null }))
    .catch(() => undefined);
  sys
    .agents()
    .then((a) => Array.isArray(a) && live.setAgents(a))
    .catch(() => undefined);
  await project.loadProjects();
  project.loadLooks();
  if (useProject.getState().activeId) {
    project.loadAll();
    loadProjectTasks();
  }
  if (ws.status !== 'open') ws.reconnectNow();
}

export async function connect() {
  if (connecting) return;
  connecting = true;
  if (retryTimer) clearTimeout(retryTimer);
  retryTimer = null;
  const ok = await refreshStatus();
  connecting = false;
  if (ok) {
    const wasOnline = useLive.getState().apiOnline === true;
    if (!wasOnline) await onOnline();
    return;
  }
  const delay = Math.min(10000, 1000 * 2 ** Math.min(attempt, 4));
  attempt += 1;
  useLive.setState({ apiOnline: false, apiRetryAt: Date.now() + delay });
  retryTimer = setTimeout(connect, delay);
}

export function retryNow() {
  attempt = 0;
  connect();
  ws.reconnectNow();
}

export function boot() {
  if (booted) return;
  booted = true;
  applyTheme();

  let wasClosed = false;
  ws.onStatus((s, retryIn) => {
    useLive.setState({ wsStatus: s, wsRetryAt: retryIn != null ? Date.now() + retryIn : null });
    if (s === 'closed') wasClosed = true;
    if (s === 'open' && wasClosed) {
      wasClosed = false;
      // po ponownym połączeniu odśwież dane (mogły nas ominąć zdarzenia)
      if (useLive.getState().apiOnline) {
        refreshStatus();
        if (useProject.getState().activeId) {
          project.loadAll();
          loadProjectTasks();
        }
      } else {
        connect();
      }
    }
  });
  ws.on(dispatch);

  onNetworkState((online) => {
    const cur = useLive.getState().apiOnline;
    if (!online && cur !== false) {
      useLive.setState({ apiOnline: false });
      if (!retryTimer && !connecting) {
        attempt = 0;
        retryTimer = setTimeout(connect, 1000);
      }
    } else if (online && cur === false && !connecting) {
      connect();
    }
  });

  ws.start(useProject.getState().activeId);
  connect();

  setInterval(() => {
    if (useLive.getState().apiOnline) refreshStatus();
  }, 15000);

  // przy zmianie projektu pobierz jego zadania
  let lastPid = useProject.getState().activeId;
  useProject.subscribe((s) => {
    if (s.activeId !== lastPid) {
      lastPid = s.activeId;
      loadProjectTasks();
    }
  });
}

