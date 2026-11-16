import { create } from 'zustand';
import type { ToastKind } from '../types';

export type ThemeMode = 'light' | 'dark' | 'system';

export interface Toast {
  id: number;
  text: string;
  kind: ToastKind;
}

export type BottomTab = 'stream' | 'tasks' | 'meters' | 'versions' | 'qc';

interface UIState {
  theme: ThemeMode;
  toasts: Toast[];
  sidebarCollapsed: boolean;
  bottomOpen: boolean;
  bottomTab: BottomTab;
  focusGroup: string | null;
  wizardOpen: boolean;
  pipelineGroup: string | null;
  logAgent: string | null;
}

const LS = 'studioedit.';
function read<T>(k: string, d: T): T {
  try {
    const v = localStorage.getItem(LS + k);
    return v == null ? d : (JSON.parse(v) as T);
  } catch {
    return d;
  }
}
function write(k: string, v: unknown) {
  try {
    localStorage.setItem(LS + k, JSON.stringify(v));
  } catch {
    /* brak localStorage */
  }
}

function readTheme(): ThemeMode {
  try {
    const t = localStorage.getItem(LS + 'theme');
    return t === 'light' || t === 'dark' ? t : 'system';
  } catch {
    return 'system';
  }
}

export const useUI = create<UIState>(() => ({
  theme: readTheme(),
  toasts: [],
  sidebarCollapsed: read('sidebar', false),
  bottomOpen: read('bottomOpen', typeof window === 'undefined' || window.innerHeight >= 1000),
  bottomTab: read<BottomTab>('bottomTab', 'stream'),
  focusGroup: null,
  wizardOpen: false,
  pipelineGroup: read<string | null>('pipelineGroup', null),
  logAgent: null,
}));

let toastSeq = 1;

export const ui = {
  toast(text: string, kind: ToastKind = 'info', ttl = 5000) {
    const id = toastSeq++;
    const list = useUI.getState().toasts;
    // nie duplikuj identycznych powiadomień
    if (list.some((t) => t.text === text && t.kind === kind)) return;
    useUI.setState({ toasts: [...list.slice(-4), { id, text, kind }] });
    setTimeout(() => ui.dismiss(id), kind === 'error' ? ttl * 1.6 : ttl);
  },
  dismiss(id: number) {
    useUI.setState({ toasts: useUI.getState().toasts.filter((t) => t.id !== id) });
  },
  setTheme(theme: ThemeMode) {
    try {
      localStorage.setItem(LS + 'theme', theme);
    } catch {
      /* ignoruj */
    }
    useUI.setState({ theme });
    applyTheme();
  },
  toggleSidebar() {
    const v = !useUI.getState().sidebarCollapsed;
    write('sidebar', v);
    useUI.setState({ sidebarCollapsed: v });
  },
  setBottom(open: boolean, tab?: BottomTab) {
    write('bottomOpen', open);
    if (tab) write('bottomTab', tab);
    useUI.setState(tab ? { bottomOpen: open, bottomTab: tab } : { bottomOpen: open });
  },
  focusGroup(group: string | null, tab: BottomTab = 'tasks') {
    useUI.setState({ focusGroup: group });
    if (group) ui.setBottom(true, tab);
  },
  openWizard(open = true) {
    useUI.setState({ wizardOpen: open });
  },
  setPipeline(group: string | null) {
    write('pipelineGroup', group);
    useUI.setState({ pipelineGroup: group });
  },
  setLogAgent(a: string | null) {
    useUI.setState({ logAgent: a });
  },
};

const mql = typeof window !== 'undefined' ? window.matchMedia('(prefers-color-scheme: dark)') : null;

export function applyTheme() {
  const t = useUI.getState().theme;
  const dark = t === 'dark' || (t === 'system' && !!mql?.matches);
  document.documentElement.classList.toggle('dark', dark);
}

mql?.addEventListener('change', applyTheme);
