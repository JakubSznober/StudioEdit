import { motion } from 'framer-motion';
import { useMemo, useRef, useState } from 'react';
import { errMsg } from '../api/client';
import { sys } from '../api/endpoints';
import { AgentAvatar, isTerminal, TaskRow } from '../components/common';
import { Badge, Card, EmptyState, PageHeader, Skeleton } from '../components/ui';
import { AGENT_ORDER, agentMeta, EFFORTS } from '../lib/agents';
import { MODULE_NAMES } from '../lib/constants';
import { cx } from '../lib/format';
import { Bot, Cpu } from '../lib/icons';
import { live, useLive } from '../store/live';
import { ui } from '../store/ui';
import type { Agent, Effort } from '../types';

function EffortPicker({ value, onChange, color }: { value: Effort; onChange: (e: Effort) => void; color: string }) {
  const cur = EFFORTS.find((e) => e.key === value) || EFFORTS[2];
  const idx = EFFORTS.findIndex((e) => e.key === value);
  return (
    <div>
      <div className="grid grid-cols-4 gap-1 rounded-xl bg-surface-3 p-1">
        {EFFORTS.map((e, i) => {
          const active = e.key === value;
          return (
            <button
              key={e.key}
              onClick={() => onChange(e.key)}
              className={cx('relative h-8 rounded-lg text-[12px] font-medium transition-colors cursor-pointer', active ? 'text-white' : i <= idx ? 'text-text' : 'text-muted hover:text-text')}
            >
              {active && <motion.span layoutId={`effort-${color}`} className="absolute inset-0 rounded-lg shadow-soft" style={{ background: color }} transition={{ type: 'spring', stiffness: 500, damping: 36 }} />}
              <span className="relative">{e.label}</span>
            </button>
          );
        })}
      </div>
      <div className="mt-2 flex items-start gap-2 text-[11.5px] leading-snug text-muted">
        <div className="mt-0.5 flex shrink-0 gap-0.5">
          {[0, 1, 2, 3].map((i) => (
            <span key={i} className="h-3 w-1.5 rounded-sm" style={{ background: i <= idx ? color : 'var(--c-surface-3)' }} />
          ))}
        </div>
        <span>
          <b className="text-text">Jakość vs czas:</b> {cur.desc}
        </span>
      </div>
    </div>
  );
}

function Stepper({ value, onChange, min = 1, max = 16 }: { value: number; onChange: (v: number) => void; min?: number; max?: number }) {
  return (
    <div className="inline-flex h-8 items-center overflow-hidden rounded-lg border border-border">
      <button onClick={() => onChange(Math.max(min, value - 1))} disabled={value <= min} className="h-full w-8 text-[15px] text-muted hover:bg-surface-2 disabled:opacity-40 cursor-pointer">
        −
      </button>
      <span className="tabular w-8 text-center text-[13px] font-semibold">{value}</span>
      <button onClick={() => onChange(Math.min(max, value + 1))} disabled={value >= max} className="h-full w-8 text-[15px] text-muted hover:bg-surface-2 disabled:opacity-40 cursor-pointer">
        +
      </button>
    </div>
  );
}

function AgentCard({ a, i }: { a: Agent; i: number }) {
  const st = useLive((s) => s.agentStates[a.key]);
  const allTasks = useLive((s) => s.tasks);
  const m = agentMeta(a.key, a);
  const busy = (st?.busy || 0) > 0;
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [saving, setSaving] = useState(false);
  const tasks = useMemo(
    () =>
      Object.values(allTasks)
        .filter((t) => t.agent === a.key && !isTerminal(t.status))
        .sort((x, y) => (x.status === 'running' ? -1 : 0) - (y.status === 'running' ? -1 : 0))
        .slice(0, 5),
    [allTasks, a.key],
  );
  const patch = async (body: { effort?: Effort; workers?: number }) => {
    const prev = a;
    live.upsertAgent({ ...a, ...body });
    setSaving(true);
    try {
      const r = await sys.patchAgent(a.key, body);
      if (r && r.key) live.upsertAgent(r);
      ui.toast(`${m.short}: zapisano ustawienia`, 'success', 2000);
    } catch (e) {
      live.upsertAgent(prev);
      ui.toast(errMsg(e), 'error');
    } finally {
      setSaving(false);
    }
  };
  const setWorkers = (v: number) => {
    live.upsertAgent({ ...a, workers: v });
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => patch({ workers: v }), 600);
  };
  return (
    <Card delay={0.04 * i} className="relative flex flex-col overflow-hidden">
      <div className="absolute inset-x-0 top-0 h-1" style={{ background: `linear-gradient(90deg, ${m.color}, color-mix(in oklab, ${m.color} 40%, transparent))` }} />
      <div className="flex items-start gap-3 p-5 pb-3">
        <AgentAvatar agent={a.key} size={44} busy={busy} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <div className="truncate text-[15px] font-semibold">{m.name}</div>
            {saving && <span className="text-[11px] text-subtle">zapisywanie…</span>}
          </div>
          <div className="text-[12.5px] font-medium" style={{ color: m.color }}>
            {a.role || m.role}
          </div>
        </div>
        <Badge tone={busy ? 'accent' : st?.queued ? 'warn' : 'neutral'} dot>
          {busy ? `Pracuje (${st?.busy})` : st?.queued ? `${st.queued} w kolejce` : 'Bezczynny'}
        </Badge>
      </div>
      <div className="flex flex-1 flex-col gap-4 px-5 pb-5">
        {a.description && <p className="text-[12.5px] leading-relaxed text-muted">{a.description}</p>}
        <div className="flex flex-wrap gap-1">
          {(a.modules || []).map((k) => (
            <Badge key={k} color={m.color}>
              {MODULE_NAMES[k] || k}
            </Badge>
          ))}
        </div>
        {a.paths?.length ? (
          <div>
            <div className="mb-1 text-[11px] font-semibold text-muted">Ścieżki pracy</div>
            <div className="flex flex-wrap gap-1">
              {a.paths.map((p) => (
                <span key={p} className="rounded-md border border-border px-1.5 py-0.5 text-[11px] text-muted">
                  {p}
                </span>
              ))}
            </div>
          </div>
        ) : null}
        <div>
          <div className="mb-1.5 text-[11px] font-semibold text-muted">Wysiłek</div>
          <EffortPicker value={a.effort} onChange={(e) => patch({ effort: e })} color={m.color} />
        </div>
        <div className="flex items-center justify-between gap-3 rounded-xl bg-surface-2 px-3 py-2">
          <div>
            <div className="flex items-center gap-1.5 text-[12px] font-medium">
              <Cpu size={13} className="text-muted" /> Wątki (workers)
            </div>
            <div className="text-[11px] text-muted">Ile zadań agent wykonuje równolegle. Więcej = szybciej, ale większe obciążenie CPU/GPU.</div>
          </div>
          <Stepper value={a.workers || 1} onChange={setWorkers} />
        </div>
        <div className="mt-auto">
          <div className="mb-1 text-[11px] font-semibold text-muted">Bieżące zadania</div>
          {tasks.length ? (
            <div className="-mx-2">
              {tasks.map((t) => (
                <TaskRow key={t.id} task={t} compact />
              ))}
            </div>
          ) : (
            <div className="text-[12px] text-subtle">Brak zadań w toku.</div>
          )}
        </div>
      </div>
    </Card>
  );
}

export default function AgentsPage() {
  const agents = useLive((s) => s.agents);
  const online = useLive((s) => s.apiOnline);
  const loaded = useLive((s) => s.statusLoaded);
  const list = AGENT_ORDER.map((k) => agents[k]).filter(Boolean).concat(Object.values(agents).filter((a) => !(AGENT_ORDER as readonly string[]).includes(a.key)));
  return (
    <div>
      <PageHeader icon={Bot} title="Agenci" subtitle="Ośmioosobowa ekipa postprodukcji. Ustaw wysiłek (jakość vs czas) i liczbę wątków dla każdego agenta." />
      {!list.length ? (
        !loaded && online !== false ? (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 2xl:grid-cols-3">
            {[0, 1, 2, 3, 4, 5].map((k) => (
              <Skeleton key={k} className="h-[420px] rounded-2xl" />
            ))}
          </div>
        ) : (
          <Card>
            <EmptyState icon={Bot} title="Brak danych o agentach">
              {online === false ? 'Serwer StudioEdit nie odpowiada — agenci pojawią się po połączeniu.' : 'Backend nie zwrócił listy agentów.'}
            </EmptyState>
          </Card>
        )
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 2xl:grid-cols-3">
          {list.map((a, i) => (
            <AgentCard key={a.key} a={a} i={i} />
          ))}
        </div>
      )}
    </div>
  );
}
