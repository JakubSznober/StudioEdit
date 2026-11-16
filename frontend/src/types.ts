// Typy zgodne z docs/API.md i app/studio/project/model.py

export type Effort = 'low' | 'medium' | 'high' | 'max';
export type Level = 'debug' | 'info' | 'warn' | 'error';
export type ToastKind = 'info' | 'success' | 'warn' | 'error';

// ------------------------------------------------------------------ system
export interface GpuStats {
  available: boolean;
  name?: string;
  util?: number;
  mem_util?: number;
  mem_used_gb?: number;
  mem_total_gb?: number;
  mem_free_gb?: number;
  temp?: number;
  power_w?: number;
  power_limit_w?: number;
  enc_util?: number;
  dec_util?: number;
  driver?: string;
  owner?: string | null;
  owner_agent?: string | null;
  error?: string;
  processes?: { pid: number; mem_gb: number }[];
}

export interface VramQueueItem {
  owner: string;
  agent: string;
  label: string;
  wait_s: number;
}

export interface VramState {
  owner: string | null;
  owner_agent: string | null;
  owner_label: string | null;
  users: number;
  queue: VramQueueItem[];
}

export type ServiceState = 'stopped' | 'starting' | 'running' | 'stopping' | 'error' | 'external' | 'disabled';

export interface ServiceModel {
  name: string;
  size_gb?: number;
  params?: string;
  quant?: string;
}

export interface Service {
  name: string;
  label: string;
  url: string;
  state: ServiceState;
  error?: string | null;
  installed?: boolean;
  managed?: boolean;
  auto_wake?: boolean;
  auto_sleep?: boolean;
  enabled?: boolean;
  pid?: number | null;
  uptime_s?: number | null;
  models?: ServiceModel[];
  loaded?: { name: string; vram_gb?: number }[];
}

export type ModuleState = 'ok' | 'warn' | 'error' | 'off' | 'busy';

export interface Module {
  key: string;
  name: string;
  agent: string;
  state: ModuleState;
  detail: string;
  requires: string[];
}

export interface RunningTask {
  id: string;
  path: string;
  title: string;
  progress: number;
  message: string;
  project_id?: string | null;
}

export interface AgentState {
  agent?: string;
  workers?: number;
  busy: number;
  queued: number;
  running: RunningTask[];
}

export interface Agent {
  key: string;
  name: string;
  role: string;
  color: string;
  icon: string;
  modules: string[];
  effort: Effort;
  workers: number;
  paths: string[];
  description: string;
  state: AgentState;
}

export type ModelKind = 'llm' | 'vlm' | 'stt' | 'tts' | 'music' | 'vfx' | 'video' | 'detector' | 'diarization';

export interface ModelEntry {
  key: string;
  name: string;
  kind: ModelKind | string;
  provider: 'ollama' | 'hf' | 'file' | 'comfy' | string;
  installed: boolean;
  size_gb: number;
  used_by: string[];
  description: string;
  role?: string;
}

export interface StorageFolder {
  key: string;
  label: string;
  path: string;
  size_gb: number;
}

export interface StorageInfo {
  limit_gb: number;
  used_gb: number;
  free_disk_gb: number;
  folders: StorageFolder[];
}

export type TaskStatus = 'queued' | 'waiting' | 'running' | 'done' | 'failed' | 'cancelled' | 'skipped';

export interface Task {
  id: string;
  agent: string;
  path: string;
  title: string;
  project_id?: string | null;
  deps: string[];
  group?: string | null;
  status: TaskStatus;
  progress: number;
  message?: string;
  error?: string | null;
  created?: number;
  started?: number | null;
  finished?: number | null;
  elapsed?: number | null;
}

export interface LogEntry {
  id: number;
  ts: number;
  agent: string;
  path: string;
  level: Level;
  text: string;
  project_id?: string | null;
}

export interface SystemStatus {
  gpu?: GpuStats;
  vram?: VramState;
  services?: Service[];
  modules?: Module[];
  agents?: Agent[];
  storage?: StorageInfo;
  active_project?: string | ProjectSummary | null;
}

export interface Look {
  id: string;
  name: string;
  description: string;
  builtin: boolean;
}

export interface PipelineStatus {
  group: string;
  status: 'running' | 'done' | 'failed' | 'partial' | 'unknown' | string;
  total?: number;
  done?: number;
  progress: number;
  failed?: Task[];
}

// ------------------------------------------------------------------ projekt
export type Scale = 'small' | 'medium' | 'massive';

export interface ProjectMeta {
  id: string;
  name: string;
  created_at: number;
  updated_at: number;
  scale: Scale;
  platforms: string[];
  style: string;
  target_duration: number | null;
  language: string;
  ingest_mode: 'copy' | 'link';
  status: string;
  thumb: string | null;
  notes: string;
  look: string | null;
  stats: Record<string, unknown>;
}

export interface ProjectSummary extends ProjectMeta {
  media_count?: number;
  media_duration?: number;
  timeline_version?: number;
  timeline_duration?: number;
  size_gb?: number;
  active?: boolean;
}

export interface Word {
  w: string;
  s: number;
  e: number;
  p?: number;
  spk?: string | null;
  filler?: boolean;
}

export interface Sentence {
  id: string;
  text: string;
  s: number;
  e: number;
  spk?: string | null;
  words: Word[];
  fluency: number;
  take_group?: string | null;
  hero: boolean;
}

export interface Transcript {
  language: string;
  engine: string;
  sentences: Sentence[];
  speakers: string[];
}

export interface Shot {
  id: string;
  s: number;
  e: number;
  shot_size: string;
  camera_move: string;
  description: string;
  people: number;
  emotion: string;
  action: string;
  colors: string[];
  tags: string[];
  score: number;
  sharpness: number;
  luma_mean: number;
  clipped_pct: number;
  crushed_pct: number;
  motion: number;
  shake: number;
  face_box?: number[] | null;
  keyframes: string[];
}

export interface Analysis {
  shots: Shot[];
  motion_curve?: number[];
  motion_fps?: number;
  audio_rms?: number[];
  audio_rms_hop?: number;
  loudness_lufs?: number | null;
  peak_db?: number | null;
  speech_ratio?: number;
  bpm?: number | null;
  beats?: number[];
  downbeats?: number[];
  energy_sections?: Record<string, unknown>[];
  room_tone?: { s: number; e: number; db: number } | null;
  summary: string;
  log_profile: string;
  stage: Record<string, string>;
}

export type MediaKind = 'video' | 'audio' | 'image' | 'unknown';

export interface MediaInfo {
  duration?: number;
  size?: number;
  format?: string;
  width?: number;
  height?: number;
  fps?: number;
  codec?: string;
  pix_fmt?: string;
  bit_depth?: number;
  [k: string]: unknown;
}

export interface MediaItem {
  id: string;
  name: string;
  path: string;
  kind: MediaKind;
  bin: string;
  info: MediaInfo;
  proxy?: string | null;
  thumb?: string | null;
  waveform?: string | null;
  flags: string[];
  flag_details: Record<string, unknown>;
  analysis: Analysis;
  transcript?: Transcript | null;
  transcript_summary?: string | Record<string, unknown> | null;
  rating: number;
  added_at: number;
  generated: boolean;
  derived_from?: string | null;
}

// ------------------------------------------------------------------ oś czasu
export interface Keyframe {
  t: number;
  scale: number;
  x: number;
  y: number;
  rot: number;
  ease: 'linear' | 'ease' | 'hold';
}

export interface SpeedPoint {
  t: number;
  speed: number;
}

export interface Grade {
  lut?: string | null;
  input_profile: string;
  exposure: number;
  temperature: number;
  tint: number;
  contrast: number;
  saturation: number;
  highlight_recovery: number;
  shadow_lift: number;
  sky_darken: number;
  look?: string | null;
  look_strength: number;
  skin_protect: number;
  match_to?: string | null;
  match?: Record<string, unknown>;
  relight?: Record<string, unknown> | null;
}

export interface Effect {
  type: string;
  params: Record<string, unknown>;
  enabled: boolean;
  status: string;
  output?: string | null;
}

export type VRole = 'a_roll' | 'b_roll' | 'generated' | 'vfx' | 'image';
export type ARole = 'dialog' | 'roomtone' | 'music' | 'sfx' | 'vo';

export interface VClip {
  id: string;
  media_id: string;
  src_in: number;
  src_out: number;
  start: number;
  speed: number;
  speed_ramp?: SpeedPoint[];
  interpolate?: 'none' | 'blend' | 'optical';
  role: VRole;
  transform?: Keyframe[];
  reframe?: Record<string, { t: number; cx: number; cy: number }[]>;
  grade?: Grade;
  effects?: Effect[];
  opacity?: number;
  label: string;
  reason: string;
  locked?: boolean;
  duration?: number;
  end?: number;
}

export interface AClip {
  id: string;
  media_id?: string | null;
  path?: string | null;
  src_in: number;
  src_out: number;
  start: number;
  role: ARole;
  gain_db: number;
  fade_in: number;
  fade_out: number;
  link?: string | null;
  duck: boolean;
  loop?: boolean;
  processing?: Record<string, unknown>;
  label: string;
  reason: string;
  duration?: number;
  end?: number;
}

export interface TitleClip {
  id: string;
  text: string;
  start: number;
  duration: number;
  style: string;
  position: string;
  params?: Record<string, unknown>;
  end?: number;
}

export type AnyClip = VClip | AClip | TitleClip;

export type TransitionKind = 'cut' | 'dissolve' | 'dip_black' | 'dip_white' | 'whip' | 'light_leak' | 'zoom' | 'slide';

export interface Transition {
  id: string;
  at: number;
  kind: TransitionKind;
  duration: number;
  reason: string;
}

export interface SubtitleSettings {
  enabled: boolean;
  burn_in: boolean;
  mode: 'static' | 'karaoke' | 'word_pop' | 'none';
  style: string;
  font: string;
  size: number;
  primary: string;
  highlight: string;
  outline: string;
  box: boolean;
  position: 'bottom' | 'middle' | 'top';
  max_chars: number;
  max_lines: number;
  uppercase: boolean;
}

export interface AudioMix {
  target_lufs: number;
  true_peak: number;
  music_gain_db: number;
  duck_db: number;
  duck_attack: number;
  duck_release: number;
  room_tone: boolean;
  room_tone_db: number;
  crossfade_ms: number;
  voice_chain: Record<string, unknown>;
}

export interface Variant {
  name: string;
  aspect: string;
  width: number;
  height: number;
  fps: number;
  max_duration?: number | null;
  enabled: boolean;
}

export interface Marker {
  t: number;
  label: string;
  kind: string;
  color: string;
}

export type TrackKey = 'V1' | 'V2' | 'V3' | 'A1' | 'A2' | 'A3' | 'A4' | 'A5';

export interface Timeline {
  version: number;
  fps: number;
  width: number;
  height: number;
  sample_rate: number;
  tracks: Partial<Record<TrackKey, AnyClip[]>> & Record<string, AnyClip[] | undefined>;
  transitions: Transition[];
  markers: Marker[];
  subtitles: SubtitleSettings;
  mix: AudioMix;
  look?: string | null;
  look_strength?: number;
  variants: Variant[];
  brief: string;
  plan: Record<string, unknown>;
  note: string;
  created_at: number;
  created_by: string;
  duration: number;
}

export interface TimelineVersion {
  version: number;
  note: string;
  author: string;
  ts: number;
  duration: number;
  parent?: number | null;
}

export interface TimelineVersions {
  current: number;
  versions: TimelineVersion[];
  redo: (TimelineVersion | number)[];
}

export interface PreviewInfo {
  url: string | null;
  version: number | null;
  variant: string;
  rendered_at: number | null;
  stale: boolean;
}

// ------------------------------------------------------------------ czat / qc / audio / eksport
export interface Range {
  in: number;
  out: number;
}

export type ChatAction = string | Record<string, unknown>;

export interface ChatMessage {
  id: string;
  ts: number;
  role: 'user' | 'assistant' | 'system';
  text: string;
  range?: Range | null;
  actions?: ChatAction[] | null;
  group?: string | null;
  project_id?: string;
  pending?: boolean;
}

export interface QCIssue {
  id: string;
  kind: string;
  severity: 'info' | 'warn' | 'error';
  t0: number;
  t1: number;
  message: string;
  fix?: { agent: string; action: string; params: Record<string, unknown> } | null;
}

export interface QCReport {
  passed: boolean;
  score: number;
  issues: QCIssue[];
  metrics?: Record<string, unknown>;
  ts?: number;
}

export interface Loudness {
  lufs: number | null;
  true_peak: number | null;
  lra: number | null;
  momentary?: number[];
}

export interface RenderProgress {
  project_id: string;
  group: string;
  variant: string;
  quality: string;
  progress: number;
  stage: string;
  path?: string;
  ts?: number;
}

export interface ExportFile {
  name: string;
  path: string;
  size_mb: number;
  created: number;
  variant: string;
  url: string;
}

// ------------------------------------------------------------------ AI Developer
export interface DevCheck {
  name: string;
  ok: boolean;
  detail: string;
}

export interface DevProposal {
  id: string;
  summary: string;
  diff: string;
  files: string[];
}

export interface DevBackup {
  id: string;
  ts: number;
  files: string[];
}

// ------------------------------------------------------------------ WebSocket
export interface WsEvent<T = any> {
  id?: number;
  type: string;
  ts?: number;
  data: T;
}

export type Config = Record<string, any>;
