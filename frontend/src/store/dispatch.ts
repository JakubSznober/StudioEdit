// Dystrybucja zdarzeń WebSocket do store'ów.
import type { LogEntry, Task, WsEvent } from '../types';
import { live, useLive } from './live';
import { project, useProject } from './project';
import { ui } from './ui';

let logSeq = 1_000_000_000;
let pendingLogs: LogEntry[] = [];
let pendingTasks: Task[] = [];
let flushTimer: ReturnType<typeof setTimeout> | null = null;

function scheduleFlush() {
  if (flushTimer) return;
  flushTimer = setTimeout(() => {
    flushTimer = null;
    const logs = pendingLogs;
    const tasks = pendingTasks;
    pendingLogs = [];
    pendingTasks = [];
    if (tasks.length) live.upsertTasks(tasks);
    if (logs.length) live.pushLogs(logs);
  }, 60);
}

export function dispatch(evt: WsEvent, replay: boolean) {
  const d = evt.data || {};
  const active = useProject.getState().activeId;
  switch (evt.type) {
    case 'gpu.stats':
      live.setGpu(d);
      break;
    case 'vram.update':
      useLive.setState({ vram: d });
      break;
    case 'services.update':
      if (d.name) live.upsertService(d);
      break;
    case 'task.update':
      if (d.id) {
        pendingTasks.push(d as Task);
        scheduleFlush();
      }
      break;
    case 'agent.state':
      if (d.agent) live.setAgentState(d);
      break;
    case 'agent.log':
      pendingLogs.push({
        id: evt.id ?? logSeq++,
        ts: evt.ts ?? Date.now() / 1000,
        agent: d.agent || 'system',
        path: d.path || '',
        level: d.level || 'info',
        text: String(d.text ?? ''),
        project_id: d.project_id ?? null,
      });
      scheduleFlush();
      break;
    case 'project.update':
      project.onProjectUpdate(d.project_id);
      break;
    case 'timeline.update':
      if (!replay) project.onTimelineUpdate(d.project_id);
      break;
    case 'render.progress':
      live.setRender(d);
      if (!replay && d.project_id === active && (d.progress >= 1 || d.path)) {
        project.refreshPreviewSoon();
        project.refreshExportsSoon();
      }
      break;
    case 'chat.message':
      if (d.project_id === active && d.id) project.addChat([d]);
      break;
    case 'qc.report':
      if (d.project_id === active && d.report) project.setQc(d.report);
      break;
    case 'loudness':
      if (d.project_id === active) project.setLoudness(d);
      break;
    case 'toast':
      if (!replay && d.text) ui.toast(String(d.text), d.kind || 'info');
      break;
    default:
      break;
  }
}
