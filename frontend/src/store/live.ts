// Stan „na żywo” z WebSocketu i /api/system/status: GPU, VRAM, usługi, moduły, agenci, zadania, logi.
import { create } from 'zustand';
import type {
  Agent,
  AgentState,
  GpuStats,
  LogEntry,
  ModelEntry,
  Module,
  RenderProgress,
  Service,
  StorageInfo,
  SystemStatus,
  Task,
  VramState,
} from '../types';
import type { WsStatus } from '../api/ws';

export const LOG_LIMIT = 2000;
const GPU_HISTORY = 90;
const TASK_LIMIT = 1500;

export interface LiveState {
  apiOnline: boolean | null;
  apiRetryAt: number | null;
  wsStatus: WsStatus;
  wsRetryAt: number | null;
  statusLoaded: boolean;
  version: string | null;

  gpu: GpuStats | null;
  gpuHist: { util: number; mem: number }[];
  vram: VramState | null;
  services: Record<string, Service>;
  serviceOrder: string[];
  modules: Module[];
  agents: Record<string, Agent>;
  agentStates: Record<string, AgentState>;
  tasks: Record<string, Task>;
  logs: LogEntry[];
  renders: Record<string, RenderProgress>;
  storage: StorageInfo | null;
  models: ModelEntry[] | null;
}

export const useLive = create<LiveState>(() => ({
  apiOnline: null,
  apiRetryAt: null,
  wsStatus: 'connecting',
  wsRetryAt: null,
  statusLoaded: false,
  version: null,
  gpu: null,
  gpuHist: [],
  vram: null,
  services: {},
  serviceOrder: [],
  modules: [],
  agents: {},
  agentStates: {},
  tasks: {},
  logs: [],
  renders: {},
  storage: null,
  models: null,
}));

const set = useLive.setState;
const get = useLive.getState;

export const live = {
  applyStatus(s: SystemStatus) {
    const patch: Partial<LiveState> = { statusLoaded: true };
    if (s.gpu) {
      patch.gpu = { ...get().gpu, ...s.gpu };
    }
    if (s.vram) patch.vram = s.vram;
    if (Array.isArray(s.services)) live.setServices(s.services);
    if (Array.isArray(s.modules)) patch.modules = s.modules;
    if (Array.isArray(s.agents)) live.setAgents(s.agents);
    if (s.storage) patch.storage = s.storage;
    set(patch);
  },

  setServices(list: Service[]) {
    const services: Record<string, Service> = { ...get().services };
    list.forEach((s) => (services[s.name] = { ...services[s.name], ...s }));
    set({ services, serviceOrder: list.map((s) => s.name) });
  },

  upsertService(s: Partial<Service> & { name: string }) {
    const cur = get().services[s.name];
    const defaults: Service = { name: s.name, label: s.name, url: '', state: 'stopped' };
    const merged = Object.assign(defaults, cur, s) as Service;
    const order = get().serviceOrder.includes(s.name) ? get().serviceOrder : [...get().serviceOrder, s.name];
    set({ services: { ...get().services, [s.name]: merged }, serviceOrder: order });
  },

  setAgents(list: Agent[]) {
    const agents: Record<string, Agent> = {};
    const states = { ...get().agentStates };
    list.forEach((a) => {
      agents[a.key] = a;
      if (a.state) states[a.key] = { ...a.state, agent: a.key, workers: a.workers };
    });
    set({ agents, agentStates: states });
  },

  upsertAgent(a: Agent) {
    set({ agents: { ...get().agents, [a.key]: { ...get().agents[a.key], ...a } } });
  },

  setAgentState(s: AgentState & { agent: string }) {
    set({ agentStates: { ...get().agentStates, [s.agent]: s } });
  },

  setGpu(g: GpuStats) {
    const hist = get().gpuHist;
    const total = g.mem_total_gb || 0;
    const next = [...hist, { util: g.util ?? 0, mem: total ? (g.mem_used_gb ?? 0) / total : 0 }];
    if (next.length > GPU_HISTORY) next.splice(0, next.length - GPU_HISTORY);
    set({ gpu: g, gpuHist: next });
  },

  upsertTasks(list: Task[]) {
    if (!list.length) return;
    const tasks = { ...get().tasks };
    list.forEach((t) => {
      const cur = tasks[t.id];
      tasks[t.id] = cur ? { ...cur, ...t } : t;
    });
    const ids = Object.keys(tasks);
    if (ids.length > TASK_LIMIT) {
      // usuń najstarsze zakończone
      const terminal = ids
        .map((id) => tasks[id])
        .filter((t) => ['done', 'failed', 'cancelled', 'skipped'].includes(t.status))
        .sort((a, b) => (a.finished || a.created || 0) - (b.finished || b.created || 0));
      for (let i = 0; i < ids.length - TASK_LIMIT && i < terminal.length; i++) delete tasks[terminal[i].id];
    }
    set({ tasks });
  },

  pushLogs(entries: LogEntry[]) {
    if (!entries.length) return;
    const cur = get().logs;
    const seen = new Set(cur.slice(-400).map((l) => l.id));
    const fresh = entries.filter((e) => !seen.has(e.id));
    if (!fresh.length) return;
    let next = cur.concat(fresh);
    if (next.length > LOG_LIMIT) next = next.slice(next.length - LOG_LIMIT);
    set({ logs: next });
  },

  setRender(r: RenderProgress) {
    const key = `${r.group}:${r.variant}:${r.quality}`;
    set({ renders: { ...get().renders, [key]: { ...r, ts: Date.now() / 1000 } } });
  },
};
