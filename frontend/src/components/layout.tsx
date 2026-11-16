// Szkielet aplikacji: lewy sidebar, górny pasek (projekt, GPU/VRAM, usługi, motyw).
import { AnimatePresence, motion } from 'framer-motion';
import { useEffect, useMemo, useRef, useState } from 'react';
import { AGENT_ORDER, agentMeta } from '../lib/agents';
import { cx, gb } from '../lib/format';
import { useWindowWidth } from '../lib/hooks';
import {
  Bot,
  Check,
  ChevronDown,
  Clapperboard,
  CodeXml,
  Cpu,
  Download,
  LayoutDashboard,
  Monitor,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Server,
  Settings2,
  Sun,
  type LucideIcon,
} from '../lib/icons';
import { navigate, useRoute, type RouteKey } from '../lib/router';
import { useLive } from '../store/live';
import { project, useProject } from '../store/project';
import { ui, useUI, type ThemeMode } from '../store/ui';
import type { ServiceState } from '../types';
import { AgentAvatar } from './common';
import { StatusDot, Tip, type Tone } from './ui';

const NAV: { key: RouteKey; label: string; icon: LucideIcon; hint: string }[] = [
  { key: 'dashboard', label: 'Pulpit', icon: LayoutDashboard, hint: 'Status modeli, usług i modułów' },
  { key: 'studio', label: 'Studio Montażowe', icon: Clapperboard, hint: 'Media, oś czasu, czat reżyserski' },
  { key: 'agents', label: 'Agenci', icon: Bot, hint: 'Wysiłek i wątki agentów' },
  { key: 'export', label: 'Eksport', icon: Download, hint: 'Final Master i Adobe' },
  { key: 'models', label: 'Modele i usługi', icon: Server, hint: 'Modele AI, dysk, usługi lokalne' },
  { key: 'developer', label: 'AI Developer', icon: CodeXml, hint: 'Diagnostyka i zmiany kodu (tryb bezpieczny)' },
  { key: 'settings', label: 'Ustawienia', icon: Settings2, hint: 'Audio, render, edycja, motyw' },
];

export function Sidebar() {
  const route = useRoute();
  const userCollapsed = useUI((s) => s.sidebarCollapsed);
  const winW = useWindowWidth();
  const collapsed = userCollapsed || (route === 'studio' && winW < 1600);
  const states = useLive((s) => s.agentStates);
  const busyAgents = AGENT_ORDER.filter((k) => (states[k]?.busy || 0) > 0);
  const version = useLive((s) => s.version);
  return (
    <motion.aside
      initial={false}
      animate={{ width: collapsed ? 68 : 232 }}
      transition={{ type: 'spring', stiffness: 400, damping: 40 }}
      className="relative z-20 flex h-full shrink-0 flex-col border-r border-border bg-surface/80 backdrop-blur-xl"
    >
      <div className={cx('flex h-14 items-center gap-2.5 px-4', collapsed && 'justify-center px-0')}>
        <div className="accent-gradient flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] text-white shadow-card">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor">
            <path d="M8 5.5v13l10.5-6.5z" />
          </svg>
        </div>
        {!collapsed && (
          <div className="min-w-0">
            <div className="text-[14px] font-semibold tracking-[-0.02em]">StudioEdit</div>
            <div className="-mt-0.5 text-[11px] text-muted">Lokalne AI Film Studio</div>
          </div>
        )}
      </div>

      <nav className="mt-2 flex flex-1 flex-col gap-0.5 px-2.5">
        {NAV.map((n) => {
          const active = route === n.key;
          const Icon = n.icon;
          const link = (
            <a
              key={n.key}
              href={`#/${n.key}`}
              onClick={(e) => {
                e.preventDefault();
                navigate(n.key);
              }}
              className={cx(
                'relative flex h-9 items-center gap-3 rounded-[10px] px-2.5 text-[13px] font-medium transition-colors',
                collapsed && 'justify-center px-0',
                active ? 'text-text' : 'text-muted hover:bg-surface-2 hover:text-text',
              )}
            >
              {active && (
                <motion.span
                  layoutId="nav-active"
                  className="absolute inset-0 rounded-[10px] border border-border bg-surface-2 shadow-soft"
                  transition={{ type: 'spring', stiffness: 500, damping: 38 }}
                />
              )}
              <Icon size={17} className={cx('relative shrink-0', active && 'text-accent')} />
              {!collapsed && <span className="relative truncate">{n.label}</span>}
            </a>
          );
          return collapsed ? (
            <Tip key={n.key} text={n.label} side="right">
              {link}
            </Tip>
          ) : (
            link
          );
        })}
      </nav>

      <div className="border-t border-border px-3 py-3">
        {!collapsed && (
          <div className="mb-2 flex items-center justify-between text-[11px] text-muted">
            <span>Agenci</span>
            <span className="tabular">{busyAgents.length ? `${busyAgents.length} pracuje` : 'bezczynni'}</span>
          </div>
        )}
        <div className={cx('flex flex-wrap gap-1.5', collapsed && 'flex-col items-center')}>
          {AGENT_ORDER.map((k) => (
            <Tip key={k} text={`${agentMeta(k).name}${(states[k]?.busy || 0) > 0 ? ' — pracuje' : ''}`} side="right">
              <span>
                <AgentAvatar agent={k} size={collapsed ? 26 : 22} busy={(states[k]?.busy || 0) > 0} />
              </span>
            </Tip>
          ))}
        </div>
        <button
          onClick={ui.toggleSidebar}
          className={cx('mt-3 flex h-8 w-full items-center gap-2 rounded-lg px-2 text-[12px] text-muted hover:bg-surface-2 hover:text-text cursor-pointer', collapsed && 'justify-center px-0')}
        >
          {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
          {!collapsed && <span>Zwiń panel</span>}
          {!collapsed && version && <span className="ml-auto text-[10.5px] text-subtle">v{version}</span>}
        </button>
      </div>
    </motion.aside>
  );
}

// ------------------------------------------------------------------ Topbar
const SVC_TONE: Record<ServiceState, Tone> = {
  running: 'success',
  external: 'success',
  starting: 'warn',
  stopping: 'warn',
  stopped: 'neutral',
  disabled: 'neutral',
  error: 'danger',
};

export const SVC_LABEL: Record<ServiceState, string> = {
  running: 'Działa',
  external: 'Działa (zewnętrzny)',
  starting: 'Uruchamianie…',
  stopping: 'Zatrzymywanie…',
  stopped: 'Zatrzymany',
  disabled: 'Wyłączony',
  error: 'Błąd',
};

export const svcTone = (s: ServiceState | undefined): Tone => (s ? SVC_TONE[s] || 'neutral' : 'neutral');

function ProjectSwitcher() {
  const projects = useProject((s) => s.projects);
  const activeId = useProject((s) => s.activeId);
  const active = projects.find((p) => p.id === activeId);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener('mousedown', h);
    return () => window.removeEventListener('mousedown', h);
  }, [open]);
  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex h-9 max-w-[320px] items-center gap-2.5 rounded-[10px] border border-border bg-surface px-2.5 shadow-soft transition-colors hover:border-border-strong cursor-pointer"
      >
        <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-accent/12 text-[11px] font-bold text-accent">
          {(active?.name || '?').slice(0, 1).toUpperCase()}
        </div>
        <div className="min-w-0 text-left">
          <div className="text-[10.5px] leading-none text-subtle">Aktywny projekt</div>
          <div className="truncate text-[13px] leading-tight font-medium">{active?.name || (activeId ? '…' : 'Brak projektu')}</div>
        </div>
        <ChevronDown size={14} className="shrink-0 text-muted" />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -4, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -4, scale: 0.98 }}
            transition={{ duration: 0.14 }}
            className="absolute top-11 left-0 z-50 w-[300px] overflow-hidden rounded-xl border border-border bg-elevated p-1 shadow-pop"
          >
            <div className="scroll-thin max-h-[320px] overflow-y-auto">
              {projects.length === 0 && <div className="px-3 py-4 text-center text-[12.5px] text-muted">Nie ma jeszcze projektów.</div>}
              {projects.map((p) => (
                <button
                  key={p.id}
                  onClick={() => {
                    project.setActive(p.id);
                    setOpen(false);
                  }}
                  className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left hover:bg-surface-2 cursor-pointer"
                >
                  <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-surface-3 text-[11px] font-bold text-muted">
                    {p.name.slice(0, 1).toUpperCase()}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px] font-medium">{p.name}</div>
                    <div className="text-[11px] text-muted">
                      {p.media_count ?? 0} mediów · {p.scale}
                    </div>
                  </div>
                  {p.id === activeId && <Check size={15} className="text-accent" />}
                </button>
              ))}
            </div>
            <div className="mt-1 border-t border-border pt-1">
              <button
                onClick={() => {
                  setOpen(false);
                  navigate('dashboard');
                  setTimeout(() => window.dispatchEvent(new CustomEvent('studioedit:new-project')), 50);
                }}
                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-[13px] font-medium text-accent hover:bg-accent/8 cursor-pointer"
              >
                <Plus size={15} /> Nowy projekt
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function GpuMini() {
  const gpu = useLive((s) => s.gpu);
  const vram = useLive((s) => s.vram);
  const used = gpu?.mem_used_gb ?? 0;
  const total = gpu?.mem_total_gb ?? 0;
  const ratio = total ? used / total : 0;
  const owner = vram?.owner_label || vram?.owner || gpu?.owner;
  return (
    <Tip
      side="bottom"
      text={
        gpu?.available
          ? `${gpu.name} · VRAM ${gb(used)} / ${gb(total)}${owner ? ` · właściciel: ${owner}` : ''}`
          : 'GPU niedostępne lub brak danych'
      }
    >
      <button onClick={() => navigate('dashboard')} className="flex h-9 items-center gap-3 rounded-[10px] border border-border bg-surface px-3 shadow-soft cursor-pointer">
        <Cpu size={15} className="text-accent" />
        <div className="w-[120px]">
          <div className="flex justify-between text-[10.5px] leading-none text-muted">
            <span>GPU {gpu?.util != null ? `${gpu.util}%` : '—'}</span>
            <span className="tabular">{total ? `${used.toFixed(1)}/${total.toFixed(0)} GB` : 'VRAM —'}</span>
          </div>
          <div className="mt-1.5 flex gap-1">
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-3">
              <motion.div
                className="h-full rounded-full"
                style={{ background: 'linear-gradient(90deg, var(--c-accent), var(--c-accent-2))' }}
                animate={{ width: `${ratio * 100}%` }}
                transition={{ type: 'spring', stiffness: 120, damping: 20 }}
              />
            </div>
          </div>
        </div>
      </button>
    </Tip>
  );
}

function ServicesMini() {
  const services = useLive((s) => s.services);
  const order = useLive((s) => s.serviceOrder);
  const apiOnline = useLive((s) => s.apiOnline);
  const ws = useLive((s) => s.wsStatus);
  const list = (order.length ? order : Object.keys(services)).map((k) => services[k]).filter(Boolean);
  return (
    <div className="flex h-9 items-center gap-3 rounded-[10px] border border-border bg-surface px-3 shadow-soft">
      <Tip side="bottom" text={apiOnline ? (ws === 'open' ? 'Serwer połączony, zdarzenia na żywo' : 'Serwer OK, WebSocket łączy się…') : 'Brak połączenia z serwerem'}>
        <span className="flex items-center gap-1.5 text-[12px] font-medium text-muted">
          <StatusDot tone={apiOnline ? (ws === 'open' ? 'success' : 'warn') : 'danger'} pulse={apiOnline === true && ws === 'open'} />
          Serwer
        </span>
      </Tip>
      {list.map((s) => (
        <Tip key={s.name} side="bottom" text={`${s.label || s.name}: ${SVC_LABEL[s.state] || s.state}${s.error ? ` — ${s.error}` : ''}`}>
          <button onClick={() => navigate('dashboard')} className="flex items-center gap-1.5 text-[12px] font-medium text-muted hover:text-text cursor-pointer">
            <StatusDot tone={svcTone(s.state)} pulse={s.state === 'starting'} />
            {s.label || s.name}
          </button>
        </Tip>
      ))}
    </div>
  );
}

function ThemeToggle() {
  const theme = useUI((s) => s.theme);
  const opts: { v: ThemeMode; icon: LucideIcon; label: string }[] = [
    { v: 'light', icon: Sun, label: 'Jasny' },
    { v: 'dark', icon: Moon, label: 'Ciemny' },
    { v: 'system', icon: Monitor, label: 'Systemowy' },
  ];
  return (
    <div className="flex h-9 items-center rounded-[10px] border border-border bg-surface p-0.5 shadow-soft">
      {opts.map((o) => {
        const Icon = o.icon;
        const active = theme === o.v;
        return (
          <Tip key={o.v} side="bottom" text={`Motyw: ${o.label}`}>
            <button
              onClick={() => ui.setTheme(o.v)}
              className={cx('relative flex h-7.5 w-8 items-center justify-center rounded-lg transition-colors cursor-pointer', active ? 'text-accent' : 'text-muted hover:text-text')}
            >
              {active && <motion.span layoutId="theme-active" className="absolute inset-0 rounded-lg bg-accent/10" />}
              <Icon size={15} className="relative" />
            </button>
          </Tip>
        );
      })}
    </div>
  );
}

export function Topbar() {
  const route = useRoute();
  const title = useMemo(() => NAV.find((n) => n.key === route), [route]);
  return (
    <header className="relative z-10 flex h-14 shrink-0 items-center gap-3 border-b border-border bg-surface/70 px-4 backdrop-blur-xl">
      <ProjectSwitcher />
      <div className="hidden min-w-0 flex-1 items-center gap-2 text-[12.5px] text-muted xl:flex">
        <span className="text-subtle">/</span>
        <span className="font-medium text-text">{title?.label}</span>
        <span className="truncate text-subtle">· {title?.hint}</span>
      </div>
      <div className="flex-1 xl:hidden" />
      <GpuMini />
      <div className="hidden lg:block">
        <ServicesMini />
      </div>
      <ThemeToggle />
    </header>
  );
}
