// Dolny panel Studio: strumień pracy agentów, zadania, mierniki, wersje, QC.
import { AnimatePresence, motion } from 'framer-motion';
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { errMsg } from '../../api/client';
import { projects as P, sys } from '../../api/endpoints';
import { AGENT_ORDER, agentMeta } from '../../lib/agents';
import { clock, cx, dur, tc, when } from '../../lib/format';
import {
  Activity,
  ChevronDown,
  ChevronUp,
  CircleCheck,
  Gauge,
  Info,
  ListChecks,
  RotateCcw,
  RotateCcwClock,
  ShieldCheck,
  TriangleExclamationPoint,
  CircleX,
  X,
} from '../../lib/icons';
import { useLive } from '../../store/live';
import { player } from '../../store/player';
import { project, useProject } from '../../store/project';
import { ui, useUI, type BottomTab } from '../../store/ui';
import type { Level, QCIssue } from '../../types';
import { AgentAvatar, groupProgress, isTerminal, TaskRow } from '../common';
import { Badge, Button, confirmDialog, EmptyState, IconButton, Progress, Ring, Tabs } from '../ui';
import { MetersTab } from './Meters';

const LEVELS: { key: Level; label: string }[] = [
  { key: 'debug', label: 'Debug' },
  { key: 'info', label: 'Info' },
  { key: 'warn', label: 'Ostrzeżenia' },
  { key: 'error', label: 'Błędy' },
];

const LEVEL_CLS: Record<Level, string> = {
  debug: 'text-subtle',
  info: 'text-text',
  warn: 'text-warn',
  error: 'text-danger',
};

// ------------------------------------------------------------------ strumień pracy
function AgentLane() {
  const states = useLive((s) => s.agentStates);
  const agents = useLive((s) => s.agents);
  const sel = useUI((s) => s.logAgent);
  return (
    <div className="scroll-thin flex w-[250px] shrink-0 flex-col gap-1 overflow-y-auto border-r border-border p-2">
      {AGENT_ORDER.map((k) => {
        const st = states[k];
        const busy = (st?.busy || 0) > 0;
        const m = agentMeta(k, agents[k]);
        const run = st?.running || [];
        const active = sel === k;
        return (
          <button
            key={k}
            onClick={() => ui.setLogAgent(active ? null : k)}
            className={cx('flex w-full items-start gap-2.5 rounded-xl px-2 py-1.5 text-left transition-colors cursor-pointer', active ? 'bg-surface-3' : 'hover:bg-surface-2')}
          >
            <AgentAvatar agent={k} size={26} busy={busy} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className="truncate text-[12px] font-semibold">{m.short}</span>
                {busy ? (
                  <span className="text-[10px] font-medium" style={{ color: m.color }}>
                    pracuje {st!.busy}/{st?.workers ?? agents[k]?.workers ?? '?'}
                  </span>
                ) : st?.queued ? (
                  <span className="text-[10px] text-muted">{st.queued} w kolejce</span>
                ) : (
                  <span className="text-[10px] text-subtle">bezczynny</span>
                )}
              </div>
              {run.slice(0, 2).map((r) => (
                <div key={r.id} className="mt-0.5">
                  <div className="truncate text-[10.5px] text-muted" title={`${r.path} — ${r.title}`}>
                    <span className="text-subtle">{r.path ? `${r.path} · ` : ''}</span>
                    {r.message || r.title}
                  </div>
                  <Progress value={r.progress} height={3} striped color={m.color} className="mt-0.5" />
                </div>
              ))}
            </div>
          </button>
        );
      })}
    </div>
  );
}

function LogStream() {
  const logs = useLive((s) => s.logs);
  const pid = useProject((s) => s.activeId);
  const agentSel = useUI((s) => s.logAgent);
  const [levels, setLevels] = useState<Record<Level, boolean>>({ debug: false, info: true, warn: true, error: true });
  const [q, setQ] = useState('');
  const ref = useRef<HTMLDivElement | null>(null);
  const stick = useRef(true);
  const filtered = useMemo(() => {
    const out = logs.filter(
      (l) =>
        (!l.project_id || !pid || l.project_id === pid) &&
        levels[l.level] !== false &&
        (!agentSel || l.agent === agentSel) &&
        (!q || l.text.toLowerCase().includes(q.toLowerCase()) || l.path.toLowerCase().includes(q.toLowerCase())),
    );
    return out.slice(-600);
  }, [logs, pid, levels, agentSel, q]);
  useLayoutEffect(() => {
    if (stick.current && ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [filtered]);
  return (
    <div className="flex min-w-0 flex-1 flex-col">
      <div className="flex h-9 shrink-0 items-center gap-1.5 border-b border-border px-3">
        <span className="text-[11.5px] font-semibold text-muted">Log decyzyjny</span>
        {agentSel && (
          <Badge color={agentMeta(agentSel).color}>
            {agentMeta(agentSel).short}
            <button onClick={() => ui.setLogAgent(null)} className="cursor-pointer" aria-label="Usuń filtr agenta">
              <X size={10} />
            </button>
          </Badge>
        )}
        <div className="flex-1" />
        {LEVELS.map((l) => (
          <button
            key={l.key}
            onClick={() => setLevels((s) => ({ ...s, [l.key]: !s[l.key] }))}
            className={cx(
              'h-6 rounded-md px-2 text-[11px] font-medium transition-colors cursor-pointer',
              levels[l.key] ? 'bg-surface-3 text-text' : 'text-subtle hover:text-muted',
            )}
          >
            {l.label}
          </button>
        ))}
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Filtruj…"
          className="ml-1 h-6 w-32 rounded-md border border-border bg-surface px-2 text-[11.5px] outline-none focus:border-accent/50"
        />
      </div>
      <div
        ref={ref}
        onScroll={(e) => {
          const el = e.currentTarget;
          stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
        className="scroll-thin min-h-0 flex-1 overflow-y-auto px-2 py-1.5 font-mono text-[11.5px] leading-[1.55]"
      >
        {filtered.length === 0 && (
          <div className="flex h-full items-center justify-center font-sans text-[12px] text-subtle">Agenci jeszcze nic nie zapisali w tym widoku. Logi pojawią się tu na żywo.</div>
        )}
        {filtered.map((l) => {
          const m = agentMeta(l.agent);
          return (
            <div key={l.id} className="flex gap-2 rounded px-1 hover:bg-surface-2">
              <span className="shrink-0 text-subtle">{clock(l.ts)}</span>
              <span className="w-[74px] shrink-0 truncate font-sans font-semibold" style={{ color: m.color }}>
                {m.short}
              </span>
              {l.path && <span className="max-w-[160px] shrink-0 truncate text-muted">[{l.path}]</span>}
              <span className={cx('min-w-0 font-sans break-words', LEVEL_CLS[l.level] || 'text-text')}>
                {l.level === 'warn' && <TriangleExclamationPoint size={11} className="mr-1 inline -translate-y-px" />}
                {l.level === 'error' && <CircleX size={11} className="mr-1 inline -translate-y-px" />}
                {l.text}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ zadania
function TasksTab() {
  const tasks = useLive((s) => s.tasks);
  const pid = useProject((s) => s.activeId);
  const focus = useUI((s) => s.focusGroup);
  const [onlyActive, setOnlyActive] = useState(false);
  const groups = useMemo(() => {
    const g: Record<string, { group: string; created: number; items: typeof list }> = {};
    const list = Object.values(tasks).filter((t) => !pid || t.project_id === pid || !t.project_id);
    list.forEach((t) => {
      const k = t.group || t.id;
      if (!g[k]) g[k] = { group: k, created: t.created || 0, items: [] };
      g[k].items.push(t);
      g[k].created = Math.min(g[k].created || Infinity, t.created || Infinity);
    });
    return Object.values(g)
      .map((x) => ({ ...x, items: x.items.sort((a, b) => (a.created || 0) - (b.created || 0)) }))
      .filter((x) => !onlyActive || x.items.some((t) => !isTerminal(t.status)))
      .sort((a, b) => (a.group === focus ? -1 : b.group === focus ? 1 : (b.created || 0) - (a.created || 0)));
  }, [tasks, pid, onlyActive, focus]);

  const cancelGroup = async (group: string) => {
    const ok = await confirmDialog({ title: 'Anulować grupę zadań?', text: 'Wszystkie oczekujące i trwające zadania w tej grupie zostaną przerwane.', confirm: 'Anuluj zadania', danger: true });
    if (!ok) return;
    try {
      await sys.cancelTasks({ group });
      ui.toast('Anulowano zadania', 'success');
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    }
  };

  if (!groups.length)
    return (
      <EmptyState icon={ListChecks} title="Brak zadań" compact className="h-full">
        Zadania agentów (analiza, montaż, kolor, render…) pojawią się tutaj z paskami postępu.
      </EmptyState>
    );

  return (
    <div className="scroll-thin h-full overflow-y-auto p-3">
      <div className="mb-2 flex items-center justify-end">
        <label className="flex items-center gap-1.5 text-[11.5px] text-muted">
          <input type="checkbox" checked={onlyActive} onChange={(e) => setOnlyActive(e.target.checked)} className="accent-[var(--c-accent)]" /> Tylko aktywne
        </label>
      </div>
      <div className="space-y-3">
        {groups.slice(0, 30).map((g) => {
          const gp = groupProgress(g.items);
          const isFocus = g.group === focus;
          return (
            <div key={g.group} className={cx('rounded-xl border bg-surface', isFocus ? 'border-accent/40 ring-3 ring-accent/10' : 'border-border')}>
              <div className="flex items-center gap-3 border-b border-border px-3 py-2">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 text-[12px] font-semibold">
                    {g.items[0]?.title && g.items.length === 1 ? g.items[0].title : `Grupa ${g.group.slice(0, 10)}`}
                    <span className="font-normal text-subtle">· {when(g.created)}</span>
                  </div>
                  <Progress value={gp.progress} height={4} className="mt-1" striped={!gp.finished} tone={gp.finished ? (gp.failed ? 'warn' : 'success') : 'accent'} />
                </div>
                <span className="tabular text-[11.5px] text-muted">
                  {gp.done}/{gp.total}
                  {gp.failed ? <span className="text-danger"> · {gp.failed} błędów</span> : null}
                </span>
                {!gp.finished && (
                  <Button size="xs" variant="ghost" onClick={() => cancelGroup(g.group)}>
                    Anuluj
                  </Button>
                )}
              </div>
              <div className="p-1">
                {g.items.map((t) => (
                  <TaskRow key={t.id} task={t} compact />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ wersje
function VersionsTab() {
  const pid = useProject((s) => s.activeId);
  const versions = useProject((s) => s.versions);
  const tl = useProject((s) => s.timeline);
  const [busy, setBusy] = useState<number | null>(null);
  useEffect(() => {
    project.loadVersions();
  }, [tl?.version]);
  if (!versions || !versions.versions?.length)
    return (
      <EmptyState icon={RotateCcwClock} title="Brak wersji montażu" compact className="h-full">
        Każda zmiana osi czasu (przez agenta lub Ciebie) zapisuje nową wersję edl_vN — tu możesz wrócić do dowolnej z nich.
      </EmptyState>
    );
  const restore = async (v: number) => {
    if (!pid) return;
    setBusy(v);
    try {
      const t = await P.checkout(pid, v);
      project.setTimeline(t);
      project.loadVersions();
      ui.toast(`Przywrócono wersję v${v}`, 'success');
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    } finally {
      setBusy(null);
    }
  };
  const list = [...versions.versions].sort((a, b) => b.version - a.version);
  const redo = new Set((versions.redo || []).map((r) => (typeof r === 'number' ? r : r.version)));
  return (
    <div className="scroll-thin h-full overflow-y-auto p-3">
      <div className="relative ml-3 border-l border-border pl-5">
        {list.map((v) => {
          const current = v.version === versions.current;
          const isRedo = redo.has(v.version);
          return (
            <motion.div key={v.version} initial={{ opacity: 0, x: -4 }} animate={{ opacity: 1, x: 0 }} className={cx('relative mb-2 rounded-xl border px-3 py-2', current ? 'border-accent/40 bg-accent/5' : 'border-border bg-surface', isRedo && 'opacity-60')}>
              <span className={cx('absolute top-3.5 -left-[27px] h-3 w-3 rounded-full border-2 border-surface', current ? 'bg-accent' : 'bg-border-strong')} />
              <div className="flex items-center gap-3">
                <Badge tone={current ? 'accent' : 'neutral'}>edl_v{v.version}</Badge>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-[12.5px] font-medium">{v.note || 'Zmiana osi czasu'}</div>
                  <div className="text-[11px] text-muted">
                    {v.author ? `${agentMeta(v.author).short} · ` : ''}
                    {when(v.ts)} · {dur(v.duration)}
                    {isRedo ? ' · cofnięta (dostępna do ponowienia)' : ''}
                  </div>
                </div>
                {current ? (
                  <Badge tone="success">
                    <CircleCheck size={10} /> bieżąca
                  </Badge>
                ) : (
                  <Button size="xs" variant="secondary" icon={RotateCcw} loading={busy === v.version} onClick={() => restore(v.version)}>
                    Przywróć
                  </Button>
                )}
              </div>
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ QC
const SEV: Record<string, { tone: 'info' | 'warn' | 'danger'; label: string; icon: typeof Info }> = {
  info: { tone: 'info', label: 'Info', icon: Info },
  warn: { tone: 'warn', label: 'Ostrzeżenie', icon: TriangleExclamationPoint },
  error: { tone: 'danger', label: 'Błąd', icon: CircleX },
};

export function QCPanel() {
  const pid = useProject((s) => s.activeId);
  const qc = useProject((s) => s.qc);
  const fps = useProject((s) => s.timeline?.fps || 30);
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState<'all' | 'warn' | 'error'>('all');
  const run = async () => {
    if (!pid) return;
    setBusy(true);
    try {
      const r = await P.runQc(pid);
      ui.toast('Nadzorca Jakości sprawdza film…', 'info');
      if (r?.group) ui.focusGroup(r.group, 'qc');
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    } finally {
      setBusy(false);
    }
  };
  const fix = async (i: QCIssue) => {
    if (!pid) return;
    try {
      const res = await P.sendChat(pid, `Popraw problem QC: ${i.message}`, { in: i.t0, out: Math.max(i.t1, i.t0 + 0.1) });
      project.addChat([res?.message, res?.reply]);
      ui.toast('Przekazano poprawkę do agentów', 'success');
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    }
  };
  if (!qc)
    return (
      <EmptyState
        icon={ShieldCheck}
        title="Brak raportu QC"
        compact
        className="h-full"
        action={
          <Button size="sm" variant="primary" icon={ShieldCheck} loading={busy} onClick={run}>
            Sprawdź jakość
          </Button>
        }
      >
        Nadzorca Jakości sprawdzi czarne klatki, przesterowania, głośność, ostrość, napisy i ciągłość montażu.
      </EmptyState>
    );
  const issues = [...(qc.issues || [])].filter((i) => filter === 'all' || i.severity === filter || (filter === 'warn' && i.severity === 'error')).sort((a, b) => a.t0 - b.t0);
  const score = qc.score ?? 0;
  const color = score >= 85 ? 'var(--c-success)' : score >= 65 ? 'var(--c-warn)' : 'var(--c-danger)';
  return (
    <div className="flex h-full min-h-0">
      <div className="flex w-[200px] shrink-0 flex-col items-center justify-center gap-2 border-r border-border p-3">
        <Ring value={score / 100} size={86} stroke={8} color={color}>
          <div className="text-center">
            <div className="tabular text-[22px] font-semibold leading-none" style={{ color }}>
              {Math.round(score)}
            </div>
            <div className="text-[10px] text-muted">/ 100</div>
          </div>
        </Ring>
        <Badge tone={qc.passed ? 'success' : 'danger'}>{qc.passed ? 'Zaliczony' : 'Wymaga poprawek'}</Badge>
        <Button size="xs" variant="secondary" icon={ShieldCheck} loading={busy} onClick={run}>
          Sprawdź ponownie
        </Button>
      </div>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border px-3">
          <span className="text-[11.5px] font-semibold text-muted">Problemy ({qc.issues?.length || 0})</span>
          <div className="flex-1" />
          {(['all', 'warn', 'error'] as const).map((f) => (
            <button key={f} onClick={() => setFilter(f)} className={cx('h-6 rounded-md px-2 text-[11px] font-medium cursor-pointer', filter === f ? 'bg-surface-3 text-text' : 'text-muted hover:text-text')}>
              {f === 'all' ? 'Wszystkie' : f === 'warn' ? 'Ostrzeżenia+' : 'Błędy'}
            </button>
          ))}
        </div>
        <div className="scroll-thin min-h-0 flex-1 overflow-y-auto p-1.5">
          {issues.length === 0 && <div className="flex h-full items-center justify-center text-[12px] text-success">Brak problemów w tym widoku.</div>}
          {issues.map((i, idx) => {
            const sev = SEV[i.severity] || SEV.info;
            const Icon = sev.icon;
            return (
              <div
                key={i.id || idx}
                role="button"
                tabIndex={0}
                onClick={() => {
                  player.seek(i.t0);
                  if (i.t1 > i.t0) player.setRange({ in: i.t0, out: i.t1 });
                }}
                className="group flex w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left hover:bg-surface-2 cursor-pointer"
              >
                <Icon size={14} className={cx('shrink-0', sev.tone === 'danger' ? 'text-danger' : sev.tone === 'warn' ? 'text-warn' : 'text-info')} />
                <span className="tabular w-[132px] shrink-0 text-[11.5px] font-medium text-accent">
                  {tc(i.t0, fps)}
                  {i.t1 > i.t0 ? ` – ${tc(i.t1, fps)}` : ''}
                </span>
                <span className="min-w-0 flex-1 truncate text-[12px]" title={i.message}>
                  {i.message}
                </span>
                {i.kind && <Badge>{i.kind}</Badge>}
                {i.fix && (
                  <Button
                    size="xs"
                    variant="soft"
                    className="opacity-0 group-hover:opacity-100"
                    onClick={(e) => {
                      e.stopPropagation();
                      fix(i);
                    }}
                  >
                    Popraw ({agentMeta(i.fix.agent).short})
                  </Button>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ panel
export function BottomPanel() {
  const open = useUI((s) => s.bottomOpen);
  const tab = useUI((s) => s.bottomTab);
  const states = useLive((s) => s.agentStates);
  const tasks = useLive((s) => s.tasks);
  const pid = useProject((s) => s.activeId);
  const qc = useProject((s) => s.qc);
  const busy = AGENT_ORDER.filter((k) => (states[k]?.busy || 0) > 0).length;
  const activeTasks = Object.values(tasks).filter((t) => (!pid || t.project_id === pid) && !isTerminal(t.status)).length;
  const qcErrors = qc?.issues?.filter((i) => i.severity === 'error').length || 0;
  const setTab = (t: BottomTab) => ui.setBottom(true, t);
  return (
    <div className="flex min-h-0 flex-col border-t border-border bg-surface">
      <div className="flex h-10 shrink-0 items-center gap-2 px-2">
        <Tabs
          value={open ? tab : ('' as BottomTab)}
          onChange={setTab}
          tabs={[
            {
              value: 'stream',
              label: 'Strumień pracy',
              icon: Activity,
              badge: busy ? (
                <span className="flex items-center gap-1 rounded-full bg-accent/12 px-1.5 text-[10px] text-accent">
                  <motion.span className="h-1.5 w-1.5 rounded-full bg-accent" animate={{ opacity: [1, 0.3, 1] }} transition={{ duration: 1.2, repeat: Infinity }} />
                  {busy}
                </span>
              ) : null,
            },
            { value: 'tasks', label: 'Zadania', icon: ListChecks, badge: activeTasks ? <span className="tabular rounded-full bg-surface-3 px-1.5 text-[10px] text-muted">{activeTasks}</span> : null },
            { value: 'meters', label: 'Mierniki', icon: Gauge },
            { value: 'versions', label: 'Wersje', icon: RotateCcwClock },
            { value: 'qc', label: 'QC', icon: ShieldCheck, badge: qcErrors ? <span className="rounded-full bg-danger/12 px-1.5 text-[10px] text-danger">{qcErrors}</span> : null },
          ]}
        />
        <div className="flex-1" />
        <IconButton icon={open ? ChevronDown : ChevronUp} label={open ? 'Zwiń panel' : 'Rozwiń panel'} size="sm" onClick={() => ui.setBottom(!open)} />
      </div>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            key="body"
            initial={{ height: 0 }}
            animate={{ height: 236 }}
            exit={{ height: 0 }}
            transition={{ type: 'spring', stiffness: 380, damping: 40 }}
            className="overflow-hidden border-t border-border"
          >
            <div className="h-[236px]">
              {tab === 'stream' && (
                <div className="flex h-full">
                  <AgentLane />
                  <LogStream />
                </div>
              )}
              {tab === 'tasks' && <TasksTab />}
              {tab === 'meters' && <MetersTab />}
              {tab === 'versions' && <VersionsTab />}
              {tab === 'qc' && <QCPanel />}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
