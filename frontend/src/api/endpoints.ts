import { api, qs, uploadFiles } from './client';
import type {
  Agent,
  ChatMessage,
  Config,
  DevBackup,
  DevCheck,
  DevProposal,
  Effort,
  ExportFile,
  LogEntry,
  Look,
  Loudness,
  MediaItem,
  ModelEntry,
  PipelineStatus,
  PreviewInfo,
  ProjectSummary,
  QCReport,
  Range,
  StyleInfo,
  Service,
  StorageInfo,
  SystemStatus,
  Task,
  Timeline,
  TimelineVersions,
} from '../types';

const P = (pid: string) => `/api/projects/${encodeURIComponent(pid)}`;
type Group = { group: string };

// ------------------------------------------------------------------ URL-e mediów
export const urls = {
  thumb: (pid: string, mid: string, v?: string | number) => `${P(pid)}/media/${encodeURIComponent(mid)}/thumb${v ? `?v=${v}` : ''}`,
  proxy: (pid: string, mid: string) => `${P(pid)}/media/${encodeURIComponent(mid)}/proxy`,
  file: (pid: string, rel: string) => `${P(pid)}/file${qs({ rel })}`,
  frame: (pid: string, t: number, variant: string, mode: 'before' | 'after', width = 960, v?: number) =>
    `${P(pid)}/frame${qs({ t: t.toFixed(3), variant, mode, width, v })}`,
};

// ------------------------------------------------------------------ system
export const sys = {
  health: () => api.get<{ ok: boolean; version: string }>('/api/health', { timeoutMs: 4000, silent: true }),
  status: () => api.get<SystemStatus>('/api/system/status', { timeoutMs: 8000 }),
  services: () => api.get<Service[]>('/api/services'),
  startService: (name: string) => api.post<Service>(`/api/services/${name}/start`, {}, { timeoutMs: 120000 }),
  stopService: (name: string) => api.post<Service>(`/api/services/${name}/stop`, {}, { timeoutMs: 60000 }),
  patchService: (name: string, body: Partial<Pick<Service, 'auto_wake' | 'auto_sleep' | 'url' | 'enabled'>>) =>
    api.patch<Service>(`/api/services/${name}`, body),
  models: () => api.get<ModelEntry[]>('/api/models'),
  installModel: (key: string) => api.post<Group>(`/api/models/${encodeURIComponent(key)}/install`),
  deleteModel: (key: string) => api.del<{ ok: boolean }>(`/api/models/${encodeURIComponent(key)}`),
  config: () => api.get<Config>('/api/config'),
  patchConfig: (patch: Config) => api.patch<Config>('/api/config', patch),
  agents: () => api.get<Agent[]>('/api/agents'),
  patchAgent: (key: string, body: { effort?: Effort; workers?: number }) => api.patch<Agent>(`/api/agents/${key}`, body),
  storage: () => api.get<StorageInfo>('/api/storage'),
  clean: (target: 'cache' | 'renders' | 'proxies' | 'comfy' | 'all_cache', project_id?: string) =>
    api.post<{ freed_gb: number }>('/api/storage/clean', { target, project_id }, { timeoutMs: 300000 }),
  tasks: (params: { project_id?: string; group?: string }) => api.get<Task[]>(`/api/tasks${qs(params)}`),
  cancelTask: (id: string) => api.post<{ cancelled: boolean }>(`/api/tasks/${id}/cancel`),
  cancelTasks: (body: { group?: string; project_id?: string }) => api.post<unknown>('/api/tasks/cancel', body),
  looks: () => api.get<Look[]>('/api/looks'),
  styles: () => api.get<StyleInfo[]>('/api/styles'),
  logs: (params: { project_id?: string; agent?: string; limit?: number }) =>
    api.get<(LogEntry | { data: LogEntry; id?: number; ts?: number })[]>(`/api/logs${qs(params)}`),
};

// ------------------------------------------------------------------ projekty
export interface NewProject {
  name: string;
  scale?: 'small' | 'medium' | 'massive';
  platforms?: string[];
  style?: string;
  target_duration?: number | null;
  ingest_mode?: 'link' | 'copy';
}

export interface MontageRequest {
  brief: string;
  target_duration?: number | null;
  platforms?: string[];
  style?: string;
  look?: string | null;
  music_mood?: string;
  subtitles?: boolean;
}

/** Ręczna edycja z osi czasu (V1 układa się magnetycznie, bez luk). */
export type TimelineEdit =
  | { op: 'reorder'; clip_id: string; index: number }
  | { op: 'move'; clip_id: string; start: number }
  | { op: 'trim'; clip_id: string; edge: 'in' | 'out'; delta: number }
  | { op: 'delete'; clip_id: string };

export interface ExportRequest {
  variants: string[];
  codec?: 'h264' | 'hevc' | 'av1' | 'prores' | 'dnxhr';
  resolution?: '1080p' | '1440p' | '2160p' | 'source';
  fps?: number;
  container?: 'mp4' | 'mov' | 'mkv';
  audio_codec?: 'aac' | 'wav' | 'pcm';
  quality?: 'auto' | 'high' | 'max' | number;
}

export const projects = {
  list: () => api.get<ProjectSummary[]>('/api/projects'),
  create: (body: NewProject) => api.post<ProjectSummary>('/api/projects', body),
  get: (pid: string) => api.get<ProjectSummary>(P(pid)),
  patch: (pid: string, body: Partial<ProjectSummary>) => api.patch<ProjectSummary>(P(pid), body),
  remove: (pid: string) => api.del<unknown>(P(pid)),
  activate: (pid: string) => api.post<unknown>(`${P(pid)}/activate`),

  media: (pid: string) => api.get<MediaItem[]>(`${P(pid)}/media`),
  mediaItem: (pid: string, mid: string) => api.get<MediaItem>(`${P(pid)}/media/${mid}`),
  importMedia: (pid: string, body: { paths: string[]; copy?: boolean; analyze?: boolean }) =>
    api.post<{ added: MediaItem[]; group?: string }>(`${P(pid)}/media/import`, body, { timeoutMs: 600000 }),
  upload: (pid: string, files: File[], onProgress?: (p: number) => void) =>
    uploadFiles<{ added: MediaItem[]; group?: string }>(`${P(pid)}/media/upload`, files, onProgress),
  patchMedia: (pid: string, mid: string, body: { bin?: string; rating?: number; name?: string }) =>
    api.patch<MediaItem>(`${P(pid)}/media/${mid}`, body),
  rejectMedia: (pid: string, mid: string, reason: string) => api.post<MediaItem>(`${P(pid)}/media/${mid}/reject`, { reason }),
  deleteMedia: (pid: string, mid: string) => api.del<unknown>(`${P(pid)}/media/${mid}`),
  analyze: (pid: string, media_ids?: string[]) => api.post<Group>(`${P(pid)}/analyze`, media_ids?.length ? { media_ids } : {}),

  montage: (pid: string, body: MontageRequest) => api.post<Group>(`${P(pid)}/montage`, body),
  pipeline: (pid: string, group: string) => api.get<PipelineStatus>(`${P(pid)}/pipeline/${encodeURIComponent(group)}`),

  timeline: (pid: string) => api.get<Timeline>(`${P(pid)}/timeline`),
  versions: (pid: string) => api.get<TimelineVersions>(`${P(pid)}/timeline/versions`),
  undo: (pid: string) => api.post<Timeline>(`${P(pid)}/timeline/undo`),
  redo: (pid: string) => api.post<Timeline>(`${P(pid)}/timeline/redo`),
  checkout: (pid: string, version: number) => api.post<Timeline>(`${P(pid)}/timeline/checkout/${version}`),
  patchClip: (pid: string, clipId: string, body: Record<string, unknown>) =>
    api.patch<Timeline>(`${P(pid)}/timeline/clips/${clipId}`, body),
  editTimeline: (pid: string, body: TimelineEdit) => api.post<Timeline>(`${P(pid)}/timeline/edit`, body),
  trackState: (pid: string, track: string, body: { hidden?: boolean; muted?: boolean }) =>
    api.patch<Timeline>(`${P(pid)}/timeline/tracks/${track}`, body),

  renderPreview: (pid: string, body: { variant?: string; t0?: number; t1?: number }) =>
    api.post<Group>(`${P(pid)}/preview/render`, body),
  preview: (pid: string, variant?: string) => api.get<PreviewInfo>(`${P(pid)}/preview${qs({ variant })}`),

  chat: (pid: string) => api.get<ChatMessage[]>(`${P(pid)}/chat`),
  sendChat: (pid: string, text: string, range?: Range | null) =>
    api.post<{ message: ChatMessage; reply?: ChatMessage | null; group?: string | null }>(
      `${P(pid)}/chat`,
      range ? { text, range } : { text },
      { timeoutMs: 300000 },
    ),

  export: (pid: string, body: ExportRequest) => api.post<Group>(`${P(pid)}/export`, body),
  exports: (pid: string) => api.get<ExportFile[]>(`${P(pid)}/exports`),
  adobe: (pid: string, targets: ('premiere' | 'ae_jsx' | 'ae_run')[]) => api.post<Group>(`${P(pid)}/export/adobe`, { targets }),

  runQc: (pid: string) => api.post<Group>(`${P(pid)}/qc`),
  qc: (pid: string) => api.get<QCReport | null>(`${P(pid)}/qc`),
  loudness: (pid: string) => api.get<Loudness | null>(`${P(pid)}/loudness`),
};

// ------------------------------------------------------------------ AI Developer
export const dev = {
  diagnostics: () => api.get<{ checks: DevCheck[] }>('/api/dev/diagnostics', { timeoutMs: 60000 }),
  propose: (request: string, files?: string[]) =>
    api.post<DevProposal>('/api/dev/propose', files?.length ? { request, files } : { request }, { timeoutMs: 600000 }),
  apply: (id: string) => api.post<{ ok: boolean; backup: string }>(`/api/dev/apply/${id}`),
  backups: () => api.get<DevBackup[]>('/api/dev/backups'),
  restore: (id: string) => api.post<{ ok: boolean }>(`/api/dev/restore/${id}`),
  tests: () => api.post<Group>('/api/dev/tests'),
};
