// Aktywny projekt: media, oś czasu, wersje, podgląd, czat, QC, loudness, eksporty.
import { create } from 'zustand';
import { projects as P, sys } from '../api/endpoints';
import { errMsg } from '../api/client';
import { ws } from '../api/ws';
import type {
  ChatMessage,
  ExportFile,
  Look,
  Loudness,
  MediaItem,
  PreviewInfo,
  ProjectSummary,
  QCReport,
  Timeline,
  TimelineVersions,
} from '../types';
import { player } from './player';
import { ui } from './ui';

interface ProjectState {
  projects: ProjectSummary[];
  projectsLoaded: boolean;
  activeId: string | null;
  media: MediaItem[];
  mediaLoaded: boolean;
  timeline: Timeline | null;
  timelineLoaded: boolean;
  versions: TimelineVersions | null;
  preview: Record<string, PreviewInfo>;
  previewLoaded: boolean;
  variant: string;
  chat: ChatMessage[];
  chatLoaded: boolean;
  qc: QCReport | null;
  loudness: Loudness | null;
  exports: ExportFile[];
  exportsLoaded: boolean;
  looks: Look[];
}

const LS_ACTIVE = 'studioedit.activeProject';

function lsGet(): string | null {
  try {
    return localStorage.getItem(LS_ACTIVE);
  } catch {
    return null;
  }
}
function lsSet(v: string | null) {
  try {
    if (v) localStorage.setItem(LS_ACTIVE, v);
    else localStorage.removeItem(LS_ACTIVE);
  } catch {
    /* ignoruj */
  }
}

const empty = {
  media: [],
  mediaLoaded: false,
  timeline: null,
  timelineLoaded: false,
  versions: null,
  preview: {},
  previewLoaded: false,
  chat: [],
  chatLoaded: false,
  qc: null,
  loudness: null,
  exports: [],
  exportsLoaded: false,
};

export const useProject = create<ProjectState>(() => ({
  projects: [],
  projectsLoaded: false,
  activeId: lsGet(),
  variant: 'youtube',
  looks: [],
  ...empty,
}));

const set = useProject.setState;
const get = useProject.getState;

// prosty debounce per klucz
const timers: Record<string, ReturnType<typeof setTimeout>> = {};
function debounce(key: string, ms: number, fn: () => void) {
  clearTimeout(timers[key]);
  timers[key] = setTimeout(fn, ms);
}

function normalizeTimeline(tl: Timeline): Timeline {
  const tracks = tl.tracks || {};
  for (const k of ['V1', 'V2', 'V3', 'A1', 'A2', 'A3', 'A4', 'A5']) if (!tracks[k]) tracks[k] = [];
  let duration = tl.duration || 0;
  if (!duration) {
    Object.values(tracks).forEach((items) =>
      (items || []).forEach((c) => {
        const e = (c as { end?: number }).end ?? c.start + ((c as { duration?: number }).duration || 0);
        duration = Math.max(duration, e);
      }),
    );
  }
  return {
    ...tl,
    tracks,
    transitions: tl.transitions || [],
    markers: tl.markers || [],
    variants: tl.variants?.length ? tl.variants : [{ name: 'youtube', aspect: '16:9', width: 1920, height: 1080, fps: 30, enabled: true }],
    duration,
  };
}

export function sortChat(list: ChatMessage[]): ChatMessage[] {
  const map = new Map<string, ChatMessage>();
  list.forEach((m) => {
    if (m && m.id) map.set(m.id, { ...map.get(m.id), ...m });
  });
  return [...map.values()].sort((a, b) => (a.ts || 0) - (b.ts || 0));
}

export const project = {
  get activeId() {
    return get().activeId;
  },

  async loadProjects() {
    try {
      const list = await P.list();
      set({ projects: Array.isArray(list) ? list : [], projectsLoaded: true });
      const active = get().activeId;
      if (active && !list.some((p) => p.id === active)) {
        // aktywny projekt został usunięty
        project.setActive(list[0]?.id ?? null, false);
      } else if (!active && list.length) {
        const flagged = list.find((p) => p.active);
        project.setActive((flagged || list[0]).id, false);
      }
    } catch (e) {
      set({ projectsLoaded: true });
      if ((e as { isNetwork?: boolean }).isNetwork) return;
      ui.toast(`Nie udało się pobrać projektów: ${errMsg(e)}`, 'error');
    }
  },

  /** ustawia aktywny projekt (lokalnie + opcjonalnie POST /activate) i ładuje jego dane */
  setActive(pid: string | null, activateOnServer = true) {
    const prev = get().activeId;
    lsSet(pid);
    if (prev !== pid) {
      set({ activeId: pid, ...empty, variant: 'youtube' });
      player.reset();
    }
    ws.setProject(pid);
    if (pid) {
      if (activateOnServer) P.activate(pid).catch((e) => ui.toast(`Nie udało się aktywować projektu: ${errMsg(e)}`, 'error'));
      project.loadAll();
    }
  },

  /** projekt aktywny według backendu (z /api/system/status) */
  adoptServerActive(pid: string | null) {
    if (!pid) return;
    const cur = get().activeId;
    if (!cur) project.setActive(pid, false);
  },

  loadAll() {
    project.loadMedia();
    project.loadTimeline();
    project.loadVersions();
    project.loadChat();
    project.loadQc();
    project.loadLoudness();
    project.loadExports();
  },

  async loadMedia() {
    const pid = get().activeId;
    if (!pid) return;
    try {
      const media = await P.media(pid);
      if (get().activeId === pid) set({ media: Array.isArray(media) ? media : [], mediaLoaded: true });
    } catch (e) {
      if (get().activeId === pid) set({ mediaLoaded: true });
      if (!(e as { isNetwork?: boolean }).isNetwork && (e as { status?: number }).status !== 404)
        ui.toast(`Media: ${errMsg(e)}`, 'error');
    }
  },

  async loadTimeline() {
    const pid = get().activeId;
    if (!pid) return;
    try {
      const tl = await P.timeline(pid);
      if (get().activeId !== pid) return;
      project.setTimeline(tl);
    } catch (e) {
      if (get().activeId === pid) set({ timelineLoaded: true });
      if (!(e as { isNetwork?: boolean }).isNetwork && (e as { status?: number }).status !== 404)
        ui.toast(`Oś czasu: ${errMsg(e)}`, 'error');
    }
  },

  setTimeline(tl: Timeline | null) {
    if (!tl || typeof tl !== 'object' || !('tracks' in tl)) {
      set({ timeline: null, timelineLoaded: true });
      return;
    }
    const n = normalizeTimeline(tl);
    const variants = n.variants.filter((v) => v.enabled !== false);
    const cur = get().variant;
    const variant = variants.some((v) => v.name === cur) ? cur : variants[0]?.name || 'youtube';
    set({ timeline: n, timelineLoaded: true, variant });
    player.setDuration(n.duration, n.fps || 30);
    project.loadPreview(variant);
  },

  async loadVersions() {
    const pid = get().activeId;
    if (!pid) return;
    try {
      const v = await P.versions(pid);
      if (get().activeId === pid) set({ versions: v });
    } catch {
      /* brak wersji */
    }
  },

  async loadPreview(variant?: string) {
    const pid = get().activeId;
    const v = variant || get().variant;
    if (!pid) return;
    try {
      const p = await P.preview(pid, v);
      if (get().activeId === pid) set({ preview: { ...get().preview, [v]: p }, previewLoaded: true });
    } catch {
      if (get().activeId === pid) set({ previewLoaded: true });
    }
  },

  setVariant(v: string) {
    set({ variant: v });
    project.loadPreview(v);
  },

  async loadChat() {
    const pid = get().activeId;
    if (!pid) return;
    try {
      const h = await P.chat(pid);
      if (get().activeId === pid) set({ chat: sortChat([...(Array.isArray(h) ? h : []), ...get().chat.filter((m) => m.pending)]), chatLoaded: true });
    } catch {
      if (get().activeId === pid) set({ chatLoaded: true });
    }
  },

  addChat(msgs: (ChatMessage | null | undefined)[]) {
    const list = msgs.filter(Boolean) as ChatMessage[];
    if (!list.length) return;
    set({ chat: sortChat([...get().chat, ...list]) });
  },

  removeChat(id: string) {
    set({ chat: get().chat.filter((m) => m.id !== id) });
  },

  async loadQc() {
    const pid = get().activeId;
    if (!pid) return;
    try {
      const r = await P.qc(pid);
      if (get().activeId === pid) set({ qc: r && typeof r === 'object' ? r : null });
    } catch {
      /* brak raportu */
    }
  },

  setQc(r: QCReport | null) {
    set({ qc: r });
  },

  async loadLoudness() {
    const pid = get().activeId;
    if (!pid) return;
    try {
      const r = await P.loudness(pid);
      if (get().activeId === pid) set({ loudness: r && typeof r === 'object' ? r : null });
    } catch {
      /* brak pomiaru */
    }
  },

  setLoudness(l: Loudness) {
    set({ loudness: { ...get().loudness, ...l } });
  },

  async loadExports() {
    const pid = get().activeId;
    if (!pid) return;
    try {
      const r = await P.exports(pid);
      if (get().activeId === pid) set({ exports: Array.isArray(r) ? r : [], exportsLoaded: true });
    } catch {
      if (get().activeId === pid) set({ exportsLoaded: true });
    }
  },

  async loadLooks() {
    if (get().looks.length) return;
    try {
      const looks = await sys.looks();
      set({ looks: Array.isArray(looks) ? looks : [] });
    } catch {
      /* brak looków */
    }
  },

  // -------------------------------------------------------------- reakcje na zdarzenia
  onProjectUpdate(pid: string | undefined) {
    debounce('projects', 700, () => project.loadProjects());
    if (pid && pid === get().activeId) debounce('media', 500, () => project.loadMedia());
  },

  onTimelineUpdate(pid: string | undefined) {
    if (!pid || pid !== get().activeId) return;
    debounce('timeline', 250, () => {
      project.loadTimeline();
      project.loadVersions();
    });
  },

  refreshPreviewSoon() {
    debounce('preview', 400, () => project.loadPreview());
  },

  refreshExportsSoon() {
    debounce('exports', 600, () => project.loadExports());
  },
};
