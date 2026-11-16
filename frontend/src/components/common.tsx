// Wspólne komponenty domenowe: avatar agenta, wiersz zadania, postęp grupy, toasty, banner offline, ErrorBoundary.
import { AnimatePresence, motion } from 'framer-motion';
import React, { useEffect, useMemo } from 'react';
import { errMsg } from '../api/client';
import { sys } from '../api/endpoints';
import { agentMeta } from '../lib/agents';
import { cx, elapsed, pct } from '../lib/format';
import { useNow } from '../lib/hooks';
import {
  CircleCheck,
  CircleX,
  FolderOpen,
  Info,
  LayoutDashboard,
  RefreshCw,
  TriangleExclamationPoint,
  WifiOff,
  X,
} from '../lib/icons';
import { navigate } from '../lib/router';
import { useLive } from '../store/live';
import { retryNow } from '../store/boot';
import { ui, useUI } from '../store/ui';
import type { Task, TaskStatus } from '../types';
import { Badge, Button, EmptyState, IconButton, Progress, type Tone } from './ui';

// ------------------------------------------------------------------ Avatar agenta
export function AgentAvatar({
  agent,
  size = 32,
  busy,
  className,
  ring,
}: {
  agent: string;
  size?: number;
  busy?: boolean;
  className?: string;
  ring?: boolean;
}) {
  const a = useLive((s) => s.agents[agent]);
  const m = agentMeta(agent, a);
  const Icon = m.icon;
  return (
    <div className={cx('relative shrink-0', className)} style={{ width: size, height: size }}>
      {busy && (
        <motion.span
          className="absolute inset-0 rounded-[30%]"
          style={{ background: m.color }}
          animate={{ scale: [1, 1.35, 1], opacity: [0.35, 0, 0.35] }}
          transition={{ duration: 1.8, repeat: Infinity, ease: 'easeOut' }}
        />
      )}
      <motion.div
        className={cx('relative flex h-full w-full items-center justify-center rounded-[30%]', ring && 'ring-2 ring-surface')}
        style={{
          background: `linear-gradient(135deg, ${m.color}, color-mix(in oklab, ${m.color} 65%, #000))`,
          color: '#fff',
          boxShadow: busy ? `0 0 0 2px color-mix(in oklab, ${m.color} 35%, transparent), 0 6px 16px -6px ${m.color}` : undefined,
        }}
        animate={busy ? { scale: [1, 1.04, 1] } : { scale: 1 }}
        transition={busy ? { duration: 1.8, repeat: Infinity, ease: 'easeInOut' } : undefined}
      >
        <Icon size={Math.round(size * 0.5)} />
      </motion.div>
    </div>
  );
}

// ------------------------------------------------------------------ Status zadań
export const TASK_STATUS: Record<TaskStatus, { label: string; tone: Tone }> = {
  queued: { label: 'W kolejce', tone: 'neutral' },
  waiting: { label: 'Czeka', tone: 'neutral' },
  running: { label: 'Pracuje', tone: 'accent' },
  done: { label: 'Gotowe', tone: 'success' },
  failed: { label: 'Błąd', tone: 'danger' },
  cancelled: { label: 'Anulowano', tone: 'warn' },
  skipped: { label: 'Pominięto', tone: 'neutral' },
};

export const isTerminal = (s: TaskStatus) => s === 'done' || s === 'failed' || s === 'cancelled' || s === 'skipped';

export function TaskRow({ task, compact }: { task: Task; compact?: boolean }) {
  const st = TASK_STATUS[task.status] || { label: task.status, tone: 'neutral' as Tone };
  const running = task.status === 'running';
  const m = agentMeta(task.agent);
  const cancel = async () => {
    try {
      await sys.cancelTask(task.id);
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    }
  };
  return (
    <motion.div
      layout="position"
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      className={cx('group flex items-center gap-3 rounded-xl px-2.5', compact ? 'py-1.5' : 'py-2', running && 'bg-accent/[0.04]')}
    >
      <AgentAvatar agent={task.agent} size={compact ? 22 : 26} busy={running} />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-[12.5px] font-medium text-text">{task.title || task.path}</span>
          {task.path && !compact && (
            <span className="truncate text-[11px] text-subtle" title={task.path}>
              {task.path}
            </span>
          )}
        </div>
        <div className="mt-1 flex items-center gap-2">
          <Progress
            value={task.status === 'done' ? 1 : task.progress}
            height={4}
            striped={running}
            color={task.status === 'failed' ? 'var(--c-danger)' : task.status === 'done' ? 'var(--c-success)' : m.color}
            className="max-w-[260px]"
          />
          <span className="tabular w-9 shrink-0 text-right text-[11px] text-muted">{pct(task.status === 'done' ? 1 : task.progress)}</span>
        </div>
        {(task.error || task.message) && (
          <div className={cx('mt-0.5 truncate text-[11.5px]', task.error ? 'text-danger' : 'text-muted')} title={task.error || task.message}>
            {task.error || task.message}
          </div>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        {task.elapsed != null && <span className="tabular text-[11px] text-subtle">{elapsed(task.elapsed)}</span>}
        <Badge tone={st.tone}>{st.label}</Badge>
        {!isTerminal(task.status) && (
          <IconButton icon={X} label="Anuluj zadanie" size="sm" className="opacity-0 group-hover:opacity-100" onClick={cancel} />
        )}
      </div>
    </motion.div>
  );
}

export function useGroupTasks(group: string | null | undefined): Task[] {
  const tasks = useLive((s) => s.tasks);
  return useMemo(() => {
    if (!group) return [];
    return Object.values(tasks)
      .filter((t) => t.group === group)
      .sort((a, b) => (a.created || 0) - (b.created || 0));
  }, [tasks, group]);
}

export function groupProgress(tasks: Task[]) {
  if (!tasks.length) return { progress: 0, done: 0, total: 0, failed: 0, running: 0, finished: false };
  const done = tasks.filter((t) => isTerminal(t.status)).length;
  const failed = tasks.filter((t) => t.status === 'failed').length;
  const running = tasks.filter((t) => t.status === 'running').length;
  const progress = tasks.reduce((s, t) => s + (isTerminal(t.status) ? 1 : t.progress || 0), 0) / tasks.length;
  return { progress, done, total: tasks.length, failed, running, finished: done === tasks.length };
}

/** Karta postępu grupy zadań (np. w czacie). Pobiera zadania, jeśli nie ma ich w store. */
export function GroupProgress({ group, label, onOpen }: { group: string; label?: string; onOpen?: () => void }) {
  const tasks = useGroupTasks(group);
  useEffect(() => {
    if (tasks.length) return;
    sys
      .tasks({ group })
      .then((list) => Array.isArray(list) && list.length && useLive.setState((s) => {
        const next = { ...s.tasks };
        list.forEach((t) => (next[t.id] = t));
        return { tasks: next };
      }))
      .catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group]);
  const g = groupProgress(tasks);
  const current = tasks.find((t) => t.status === 'running');
  return (
    <button
      onClick={onOpen || (() => ui.focusGroup(group))}
      className="mt-2 w-full rounded-xl border border-border bg-surface-2/70 px-3 py-2 text-left transition-colors hover:border-accent/40 cursor-pointer"
    >
      <div className="flex items-center justify-between gap-2 text-[11.5px]">
        <span className="font-medium text-text">{label || 'Zadania agentów'}</span>
        <span className="tabular text-muted">
          {g.total ? `${g.done}/${g.total}` : 'oczekiwanie…'} {g.failed ? <span className="text-danger">· {g.failed} błędów</span> : null}
        </span>
      </div>
      <Progress value={g.progress} height={4} striped={!g.finished && g.total > 0} indeterminate={!g.total} className="mt-1.5" tone={g.finished ? (g.failed ? 'warn' : 'success') : 'accent'} />
      {current && <div className="mt-1 truncate text-[11px] text-muted">{agentMeta(current.agent).short}: {current.message || current.title}</div>}
    </button>
  );
}

// ------------------------------------------------------------------ Toasty
const TOAST_ICON = { info: Info, success: CircleCheck, warn: TriangleExclamationPoint, error: CircleX };
const TOAST_COLOR = { info: 'var(--c-info)', success: 'var(--c-success)', warn: 'var(--c-warn)', error: 'var(--c-danger)' };

export function Toaster() {
  const toasts = useUI((s) => s.toasts);
  return (
    <div className="pointer-events-none fixed right-4 bottom-4 z-[300] flex w-[360px] max-w-[calc(100vw-32px)] flex-col gap-2">
      <AnimatePresence initial={false}>
        {toasts.map((t) => {
          const Icon = TOAST_ICON[t.kind] || Info;
          return (
            <motion.div
              key={t.id}
              layout
              initial={{ opacity: 0, x: 40, scale: 0.96 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 40, scale: 0.96 }}
              transition={{ type: 'spring', stiffness: 420, damping: 34 }}
              className="pointer-events-auto flex items-start gap-3 rounded-xl border border-border bg-elevated px-3.5 py-3 shadow-pop"
            >
              <Icon size={17} style={{ color: TOAST_COLOR[t.kind] }} className="mt-0.5 shrink-0" />
              <div className="min-w-0 flex-1 text-[13px] leading-snug text-text">{t.text}</div>
              <button className="shrink-0 text-subtle hover:text-text cursor-pointer" onClick={() => ui.dismiss(t.id)} aria-label="Zamknij">
                <X size={14} />
              </button>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}

// ------------------------------------------------------------------ Banner offline
export function OfflineBanner() {
  const apiOnline = useLive((s) => s.apiOnline);
  const ws = useLive((s) => s.wsStatus);
  const apiRetryAt = useLive((s) => s.apiRetryAt);
  const wsRetryAt = useLive((s) => s.wsRetryAt);
  const now = useNow(500);
  const offline = apiOnline === false || (apiOnline === true && ws !== 'open');
  const retryAt = apiOnline === false ? apiRetryAt : wsRetryAt;
  const secs = retryAt ? Math.max(0, Math.ceil((retryAt - now) / 1000)) : null;
  const [delayed, setDelayed] = React.useState(false);
  // nie migaj bannerem przy krótkich przerwach (< 1,5 s)
  useEffect(() => {
    if (!offline) {
      setDelayed(false);
      return;
    }
    const t = setTimeout(() => setDelayed(true), 1500);
    return () => clearTimeout(t);
  }, [offline]);
  return (
    <AnimatePresence>
      {offline && delayed && (
        <motion.div
          initial={{ height: 0, opacity: 0 }}
          animate={{ height: 'auto', opacity: 1 }}
          exit={{ height: 0, opacity: 0 }}
          className="overflow-hidden"
        >
          <div className="flex items-center gap-3 border-b border-warn/25 bg-warn/10 px-4 py-2 text-[12.5px]">
            <motion.div animate={{ opacity: [1, 0.4, 1] }} transition={{ duration: 1.6, repeat: Infinity }}>
              <WifiOff size={15} className="text-warn" />
            </motion.div>
            <div className="min-w-0 flex-1">
              <span className="font-semibold text-text">Łączenie z serwerem…</span>{' '}
              <span className="text-muted">
                {apiOnline === false
                  ? 'Backend StudioEdit (http://127.0.0.1:8765) nie odpowiada. Uruchom serwer — aplikacja połączy się automatycznie.'
                  : 'Kanał zdarzeń na żywo (WebSocket) jest rozłączony.'}
                {secs != null && ` Ponowna próba za ${secs} s.`}
              </span>
            </div>
            <Button size="xs" variant="secondary" icon={RefreshCw} onClick={retryNow}>
              Spróbuj teraz
            </Button>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

// ------------------------------------------------------------------ ErrorBoundary
export class ErrorBoundary extends React.Component<{ children: React.ReactNode; name?: string }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('[ui] błąd komponentu', this.props.name, error, info);
  }
  render() {
    if (this.state.error) {
      return (
        <div className="m-6 rounded-2xl border border-danger/25 bg-danger/5 p-5">
          <div className="flex items-center gap-2 text-[14px] font-semibold text-danger">
            <TriangleExclamationPoint size={16} /> Ten widok napotkał błąd
          </div>
          <div className="mt-1 font-mono text-[12px] text-muted">{errMsg(this.state.error)}</div>
          <Button size="sm" className="mt-3" icon={RefreshCw} onClick={() => this.setState({ error: null })}>
            Spróbuj ponownie
          </Button>
        </div>
      );
    }
    return this.props.children;
  }
}

/** Pusty stan, gdy nie wybrano projektu */
export function NoProject({ what = 'tę sekcję' }: { what?: string }) {
  const online = useLive((s) => s.apiOnline);
  return (
    <div className="flex h-full items-center justify-center p-8">
      <EmptyState
        icon={FolderOpen}
        title={online === false ? 'Czekam na serwer StudioEdit' : 'Brak aktywnego projektu'}
        action={
          <Button variant="primary" icon={LayoutDashboard} onClick={() => navigate('dashboard')}>
            Przejdź do projektów
          </Button>
        }
      >
        {online === false
          ? 'Gdy backend wystartuje, projekty załadują się automatycznie.'
          : `Aby otworzyć ${what}, utwórz nowy projekt lub aktywuj istniejący na Pulpicie.`}
      </EmptyState>
    </div>
  );
}
