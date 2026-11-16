// Karty systemowe: GPU, usługi lokalne, kluczowe modele, moduły, pasek agentów.
import { motion } from 'framer-motion';
import { useEffect, useMemo, useState } from 'react';
import { errMsg } from '../api/client';
import { sys } from '../api/endpoints';
import { AGENT_ORDER, agentMeta } from '../lib/agents';
import { MODULE_NAMES, MODULE_STATE } from '../lib/constants';
import { cx, durLong, gb, pct } from '../lib/format';
import {
  AudioWaveform,
  Brain,
  CircleCheck,
  Cpu,
  Eye,
  Layers,
  Play,
  Power,
  ScanEye,
  Server,
  Sparkles,
  Thermometer,
  WandSparkles,
  Zap,
  type LucideIcon,
} from '../lib/icons';
import { live, useLive } from '../store/live';
import { ui } from '../store/ui';
import type { ModelEntry, Service } from '../types';
import { AgentAvatar } from './common';
import { SVC_LABEL, svcTone } from './layout';
import { Badge, Button, Card, CardHeader, Progress, Ring, Skeleton, StatusDot, Switch, Tip } from './ui';

// ------------------------------------------------------------------ Sparkline
export function Sparkline({ values, color = 'var(--c-accent)', height = 36, max = 1 }: { values: number[]; color?: string; height?: number; max?: number }) {
  const w = 200;
  if (values.length < 2) return <div style={{ height }} />;
  const pts = values.map((v, i) => [(i / (values.length - 1)) * w, height - (Math.min(max, Math.max(0, v)) / max) * (height - 2) - 1]);
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(' ');
  const id = `sg-${color.replace(/[^a-z0-9]/gi, '')}`;
  return (
    <svg viewBox={`0 0 ${w} ${height}`} preserveAspectRatio="none" className="w-full" style={{ height }}>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={color} stopOpacity="0.28" />
          <stop offset="1" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={`${d} L${w},${height} L0,${height} Z`} fill={`url(#${id})`} />
      <path d={d} fill="none" stroke={color} strokeWidth="1.6" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
    </svg>
  );
}

// ------------------------------------------------------------------ GPU
export function VramBar({ big }: { big?: boolean }) {
  const gpu = useLive((s) => s.gpu);
  const used = gpu?.mem_used_gb ?? 0;
  const total = gpu?.mem_total_gb ?? 0;
  const r = total ? used / total : 0;
  return (
    <div>
      <div className="flex items-baseline justify-between">
        <span className="text-[12px] font-medium text-muted">VRAM</span>
        <span className={cx('tabular font-semibold', big ? 'text-[15px]' : 'text-[12.5px]')}>
          {total ? `${used.toFixed(1)} / ${total.toFixed(1)} GB` : '—'}
        </span>
      </div>
      <Progress value={r} height={big ? 10 : 6} className="mt-1.5" color={r > 0.92 ? 'var(--c-danger)' : r > 0.8 ? 'var(--c-warn)' : undefined} />
    </div>
  );
}

export function VramOwner() {
  const vram = useLive((s) => s.vram);
  const gpu = useLive((s) => s.gpu);
  const owner = vram?.owner || gpu?.owner || null;
  const ownerAgent = vram?.owner_agent || gpu?.owner_agent || null;
  return (
    <div className="rounded-xl bg-surface-2 p-3">
      <div className="flex items-center gap-2.5">
        {ownerAgent ? <AgentAvatar agent={ownerAgent} size={26} busy /> : <div className="flex h-[26px] w-[26px] items-center justify-center rounded-lg bg-surface-3 text-subtle"><Layers size={14} /></div>}
        <div className="min-w-0 flex-1">
          <div className="text-[11px] text-muted">Właściciel VRAM</div>
          <div className="truncate text-[12.5px] font-medium">{owner ? vram?.owner_label || owner : 'Wolne — brak aktywnego modelu'}</div>
        </div>
        {vram && vram.users > 0 && <Badge tone="accent">{vram.users} użytk.</Badge>}
      </div>
      {vram?.queue?.length ? (
        <div className="mt-2.5 space-y-1 border-t border-border pt-2">
          <div className="text-[11px] font-medium text-muted">Kolejka ({vram.queue.length})</div>
          {vram.queue.map((q, i) => (
            <div key={i} className="flex items-center gap-2 text-[11.5px]">
              <AgentAvatar agent={q.agent} size={16} />
              <span className="min-w-0 flex-1 truncate">{q.label || q.owner}</span>
              <span className="tabular text-subtle">{q.wait_s.toFixed(0)} s</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function GpuCard() {
  const gpu = useLive((s) => s.gpu);
  const hist = useLive((s) => s.gpuHist);
  const loaded = useLive((s) => s.statusLoaded || !!s.gpu);
  const utilHist = hist.map((h) => h.util / 100);
  if (!loaded) return <Skeleton className="h-[300px] rounded-2xl" />;
  const util = gpu?.util ?? 0;
  return (
    <Card className="h-full">
      <CardHeader
        icon={Cpu}
        title={gpu?.name || 'GPU'}
        subtitle={gpu?.available ? `Sterownik ${gpu.driver || '—'} · telemetria co 1 s` : gpu?.error || 'Brak danych z NVML'}
        right={<Badge tone={gpu?.available ? 'success' : 'neutral'} dot>{gpu?.available ? 'Aktywne' : 'Niedostępne'}</Badge>}
      />
      <div className="space-y-4 px-5 pb-5">
        <div className="flex items-center gap-5">
          <Ring value={util / 100} size={78} stroke={8}>
            <div className="text-center">
              <div className="tabular text-[17px] font-semibold leading-none">{util}%</div>
              <div className="mt-0.5 text-[10px] text-muted">użycie</div>
            </div>
          </Ring>
          <div className="min-w-0 flex-1">
            <Sparkline values={utilHist} height={48} />
            <div className="mt-1 text-[10.5px] text-subtle">Ostatnie {hist.length} s</div>
          </div>
        </div>
        <VramBar big />
        <div className="grid grid-cols-4 gap-2">
          <MiniStat icon={Thermometer} label="Temp." value={gpu?.temp != null ? `${gpu.temp}°C` : '—'} tone={gpu?.temp && gpu.temp > 82 ? 'var(--c-danger)' : undefined} />
          <MiniStat icon={Zap} label="Moc" value={gpu?.power_w != null ? `${gpu.power_w.toFixed(0)} W` : '—'} sub={gpu?.power_limit_w ? `/ ${gpu.power_limit_w.toFixed(0)} W` : undefined} />
          <MiniStat icon={Play} label="NVENC" value={gpu?.enc_util != null ? `${gpu.enc_util}%` : '—'} />
          <MiniStat icon={Eye} label="NVDEC" value={gpu?.dec_util != null ? `${gpu.dec_util}%` : '—'} />
        </div>
        <VramOwner />
      </div>
    </Card>
  );
}

function MiniStat({ icon: Icon, label, value, sub, tone }: { icon: LucideIcon; label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="rounded-xl border border-border px-2.5 py-2">
      <div className="flex items-center gap-1 text-[10.5px] text-muted">
        <Icon size={11} /> {label}
      </div>
      <div className="tabular mt-0.5 text-[13px] font-semibold" style={tone ? { color: tone } : undefined}>
        {value}
        {sub && <span className="ml-0.5 text-[10.5px] font-normal text-subtle">{sub}</span>}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Usługi
export function ServiceCard({ svc, delay = 0 }: { svc: Service; delay?: number }) {
  const [busy, setBusy] = useState<'start' | 'stop' | null>(null);
  const running = svc.state === 'running' || svc.state === 'external';
  const transitioning = svc.state === 'starting' || svc.state === 'stopping';
  const act = async (kind: 'start' | 'stop') => {
    setBusy(kind);
    try {
      const s = kind === 'start' ? await sys.startService(svc.name) : await sys.stopService(svc.name);
      if (s && s.name) live.upsertService(s);
      ui.toast(kind === 'start' ? `${svc.label}: uruchomiono` : `${svc.label}: zatrzymano`, 'success');
    } catch (e) {
      ui.toast(`${svc.label}: ${errMsg(e)}`, 'error');
    } finally {
      setBusy(null);
    }
  };
  const patch = async (body: Partial<Service>) => {
    live.upsertService({ name: svc.name, ...body });
    try {
      const s = await sys.patchService(svc.name, body);
      if (s && s.name) live.upsertService(s);
    } catch (e) {
      ui.toast(errMsg(e), 'error');
      sys.services().then(live.setServices).catch(() => undefined);
    }
  };
  const Icon = svc.name === 'comfyui' ? WandSparkles : Brain;
  return (
    <Card className="flex h-full flex-col" delay={delay}>
      <CardHeader
        icon={Icon}
        iconColor={svc.name === 'comfyui' ? '#f59e0b' : '#7c5cff'}
        title={svc.label || svc.name}
        subtitle={<span className="font-mono text-[11.5px]">{svc.url || '—'}</span>}
        right={
          <span className="flex items-center gap-1.5 text-[12px] font-medium">
            <StatusDot tone={svcTone(svc.state)} pulse={transitioning || running} />
            {SVC_LABEL[svc.state] || svc.state}
          </span>
        }
      />
      <div className="flex flex-1 flex-col gap-3 px-5 pb-5">
        {svc.error && <div className="rounded-lg bg-danger/8 px-3 py-2 text-[12px] text-danger">{svc.error}</div>}
        <div className="grid grid-cols-3 gap-2 text-[11.5px]">
          <div>
            <div className="text-muted">Zainstalowany</div>
            <div className="font-medium">{svc.installed === false ? 'Nie' : 'Tak'}</div>
          </div>
          <div>
            <div className="text-muted">Czas pracy</div>
            <div className="tabular font-medium">{svc.uptime_s ? durLong(svc.uptime_s) : '—'}</div>
          </div>
          <div>
            <div className="text-muted">PID</div>
            <div className="tabular font-medium">{svc.pid ?? (svc.state === 'external' ? 'zewn.' : '—')}</div>
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-xl bg-surface-2 px-3 py-2 whitespace-nowrap">
          <Tip text="Automatycznie uruchamiaj usługę, gdy agent jej potrzebuje">
            <label className="flex items-center gap-2 text-[12px] font-medium">
              <Switch size="sm" checked={svc.auto_wake !== false} onChange={(v) => patch({ auto_wake: v })} label="Auto-Wake" /> Auto-Wake
            </label>
          </Tip>
          <Tip text="Usypiaj usługę po bezczynności, zwalniając VRAM">
            <label className="flex items-center gap-2 text-[12px] font-medium">
              <Switch size="sm" checked={svc.auto_sleep !== false} onChange={(v) => patch({ auto_sleep: v })} label="Auto-Sleep" /> Auto-Sleep
            </label>
          </Tip>
          <label className="flex items-center gap-2 text-[12px] font-medium">
            <Switch size="sm" checked={svc.enabled !== false} onChange={(v) => patch({ enabled: v })} label="Włączona" /> Włączona
          </label>
        </div>
        <div className="min-h-[44px]">
          <div className="mb-1 text-[11px] font-medium text-muted">
            {svc.loaded?.length ? 'Załadowane w VRAM' : svc.models?.length ? `Dostępne modele (${svc.models.length})` : 'Modele'}
          </div>
          <div className="flex flex-wrap gap-1">
            {(svc.loaded?.length ? svc.loaded.map((m) => ({ name: m.name, sub: m.vram_gb ? gb(m.vram_gb) : '' })) : (svc.models || []).slice(0, 8).map((m) => ({ name: m.name, sub: m.size_gb ? gb(m.size_gb) : '' }))).map((m) => (
              <Badge key={m.name} tone={svc.loaded?.length ? 'accent' : 'neutral'}>
                {m.name}
                {m.sub && <span className="opacity-60">· {m.sub}</span>}
              </Badge>
            ))}
            {!svc.loaded?.length && !svc.models?.length && <span className="text-[12px] text-subtle">{running ? 'Brak załadowanych modeli' : 'Usługa nie działa'}</span>}
          </div>
        </div>
        <div className="mt-auto flex gap-2">
          <Button size="sm" variant="primary" icon={Power} loading={busy === 'start'} disabled={running || transitioning || svc.enabled === false} onClick={() => act('start')}>
            Uruchom
          </Button>
          <Button size="sm" variant="secondary" loading={busy === 'stop'} disabled={!running || transitioning || svc.state === 'external'} onClick={() => act('stop')}>
            Zatrzymaj
          </Button>
        </div>
      </div>
    </Card>
  );
}

export function ServicesGrid() {
  const services = useLive((s) => s.services);
  const order = useLive((s) => s.serviceOrder);
  const loaded = useLive((s) => s.statusLoaded);
  const list = (order.length ? order : Object.keys(services)).map((k) => services[k]).filter(Boolean);
  if (!loaded && !list.length)
    return (
      <>
        <Skeleton className="h-[300px] rounded-2xl" />
        <Skeleton className="h-[300px] rounded-2xl" />
      </>
    );
  if (!list.length)
    return (
      <Card className="flex items-center justify-center p-6 text-[13px] text-muted">
        <Server size={16} className="mr-2" /> Brak informacji o usługach lokalnych.
      </Card>
    );
  return (
    <>
      {list.map((s, i) => (
        <ServiceCard key={s.name} svc={s} delay={0.05 * i} />
      ))}
    </>
  );
}

// ------------------------------------------------------------------ Modele
export function useModels() {
  const models = useLive((s) => s.models);
  const online = useLive((s) => s.apiOnline);
  const [error, setError] = useState<string | null>(null);
  const reload = async () => {
    try {
      const m = await sys.models();
      useLive.setState({ models: Array.isArray(m) ? m : [] });
      setError(null);
    } catch (e) {
      setError(errMsg(e));
    }
  };
  useEffect(() => {
    if (online) reload();
  }, [online]);
  return { models, error, reload };
}

const isVlm = (m: ModelEntry) => m.kind === 'vlm' || m.role === 'vision' || /-vl[:-]|vl:/i.test(m.name);

const KEY_GROUPS: { key: string; title: string; icon: LucideIcon; color: string; match: (m: ModelEntry) => boolean }[] = [
  { key: 'director', title: 'AI Director', icon: Brain, color: '#7c5cff', match: (m) => m.kind === 'llm' && !isVlm(m) },
  { key: 'vlm', title: 'Video-VLM', icon: ScanEye, color: '#0ea5e9', match: isVlm },
  { key: 'audio', title: 'Audio STT / TTS', icon: AudioWaveform, color: '#10b981', match: (m) => ['stt', 'tts', 'diarization', 'music', 'denoise'].includes(m.kind) },
  { key: 'vfx', title: 'VFX / Video Gen · ComfyUI', icon: WandSparkles, color: '#f59e0b', match: (m) => ['video', 'vfx', 'video_generation', 'video_gen'].includes(m.kind) || m.provider === 'comfy' },
  { key: 'sam', title: 'SAM 2.1 · AI Effects', icon: Sparkles, color: '#ec4899', match: (m) => ['detector', 'segmentation', 'detection', 'inpainting', 'face'].includes(m.kind) },
];

export function KeyModels() {
  const { models, error } = useModels();
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-5">
      {KEY_GROUPS.map((g, i) => {
        const items = (models || []).filter(g.match);
        const installed = items.filter((m) => m.installed).length;
        const Icon = g.icon;
        return (
          <Card key={g.key} delay={0.04 * i} hover className="p-4">
            <div className="flex items-center gap-2.5">
              <div className="flex h-8 w-8 items-center justify-center rounded-[10px]" style={{ background: `color-mix(in oklab, ${g.color} 14%, transparent)`, color: g.color }}>
                <Icon size={16} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] font-semibold">{g.title}</div>
                <div className="text-[11px] text-muted">{models ? `${installed}/${items.length} zainstalowanych` : error ? 'Brak danych' : 'Ładowanie…'}</div>
              </div>
              {models && items.length > 0 && <StatusDot tone={installed === items.length ? 'success' : installed ? 'warn' : 'danger'} />}
            </div>
            <div className="mt-3 space-y-1.5">
              {!models && !error && [0, 1].map((k) => <Skeleton key={k} className="h-5" />)}
              {models && items.length === 0 && <div className="text-[12px] text-subtle">Brak modeli w katalogu</div>}
              {items.slice(0, 4).map((m) => (
                <div key={m.key} className="flex items-center gap-2 text-[12px]">
                  {m.installed ? <CircleCheck size={13} className="shrink-0 text-success" /> : <span className="h-[13px] w-[13px] shrink-0 rounded-full border border-dashed border-border-strong" />}
                  <span className={cx('min-w-0 flex-1 truncate', !m.installed && 'text-muted')} title={m.description}>
                    {m.name}
                  </span>
                  <span className="tabular shrink-0 text-[11px] text-subtle">{gb(m.size_gb)}</span>
                </div>
              ))}
              {items.length > 4 && <div className="text-[11px] text-subtle">+ {items.length - 4} więcej</div>}
            </div>
          </Card>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ Moduły
const MODULE_KEYS = Object.keys(MODULE_NAMES);

export function ModuleGrid() {
  const modules = useLive((s) => s.modules);
  const loaded = useLive((s) => s.statusLoaded);
  const list = modules.length
    ? modules
    : MODULE_KEYS.map((k) => ({ key: k, name: MODULE_NAMES[k], agent: '', state: 'off' as const, detail: loaded ? 'Brak danych' : '', requires: [] }));
  return (
    <div className="grid grid-cols-2 gap-2.5 md:grid-cols-3 xl:grid-cols-5">
      {list.map((m, i) => {
        const st = MODULE_STATE[m.state] || MODULE_STATE.off;
        const meta = m.agent ? agentMeta(m.agent) : null;
        if (!loaded && !modules.length) return <Skeleton key={m.key} className="h-[86px] rounded-xl" />;
        return (
          <motion.div
            key={m.key}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.015 * i }}
            className="group relative overflow-hidden rounded-xl border border-border bg-surface p-3 shadow-soft transition-all hover:-translate-y-0.5 hover:shadow-card"
          >
            <div className="absolute inset-x-0 top-0 h-[3px]" style={{ background: st.color, opacity: m.state === 'off' ? 0.3 : 1 }} />
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <div className="truncate text-[12.5px] font-semibold">{m.name || MODULE_NAMES[m.key] || m.key}</div>
                {meta && <div className="truncate text-[11px] text-muted">{meta.short}</div>}
              </div>
              <StatusDot tone={st.tone} pulse={m.state === 'busy'} className="mt-1" />
            </div>
            <div className="mt-2 flex items-center gap-1.5">
              <Badge tone={st.tone}>{st.label}</Badge>
            </div>
            <div className="mt-1.5 line-clamp-2 min-h-[30px] text-[11px] leading-snug text-muted" title={m.detail}>
              {m.detail || (m.requires?.length ? `Wymaga: ${m.requires.join(', ')}` : '')}
            </div>
          </motion.div>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ Agenci (pasek)
export function AgentStrip({ onSelect, selected }: { onSelect?: (k: string | null) => void; selected?: string | null }) {
  const agents = useLive((s) => s.agents);
  const states = useLive((s) => s.agentStates);
  return (
    <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4 xl:grid-cols-8">
      {AGENT_ORDER.map((k, i) => {
        const st = states[k];
        const busy = (st?.busy || 0) > 0;
        const run = st?.running?.[0];
        const m = agentMeta(k, agents[k]);
        const active = selected === k;
        return (
          <motion.button
            key={k}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.03 * i }}
            onClick={() => onSelect?.(active ? null : k)}
            className={cx(
              'relative flex min-w-0 flex-col gap-2 overflow-hidden rounded-xl border bg-surface p-3 text-left shadow-soft transition-all cursor-pointer',
              active ? 'border-transparent ring-2' : 'border-border hover:border-border-strong',
            )}
            style={active ? ({ '--tw-ring-color': m.color } as React.CSSProperties) : undefined}
          >
            {busy && <div className="absolute inset-x-0 top-0 h-[2px]" style={{ background: m.color }} />}
            <div className="flex items-center gap-2">
              <AgentAvatar agent={k} size={28} busy={busy} />
              <div className="min-w-0">
                <div className="truncate text-[12.5px] font-semibold">{m.short}</div>
                <div className="truncate text-[10.5px] text-muted">{busy ? `${st?.busy} w toku${st?.queued ? ` · ${st.queued} w kolejce` : ''}` : st?.queued ? `${st.queued} w kolejce` : 'Bezczynny'}</div>
              </div>
            </div>
            <div className="min-h-[30px]">
              {run ? (
                <>
                  <div className="truncate text-[11px] text-text" title={run.title}>
                    {run.message || run.title}
                  </div>
                  <Progress value={run.progress} height={3} striped color={m.color} className="mt-1" />
                </>
              ) : (
                <div className="line-clamp-2 text-[11px] text-subtle">{m.role}</div>
              )}
            </div>
          </motion.button>
        );
      })}
    </div>
  );
}

export function StorageSummary() {
  const storage = useLive((s) => s.storage);
  const used = storage?.used_gb ?? 0;
  const limit = storage?.limit_gb ?? 500;
  const r = limit ? used / limit : 0;
  return (
    <div>
      <div className="flex items-baseline justify-between text-[12px]">
        <span className="font-medium text-muted">Dysk StudioEdit</span>
        <span className="tabular font-semibold">
          {gb(used)} / {limit} GB
        </span>
      </div>
      <Progress value={r} height={6} className="mt-1.5" color={r > 0.9 ? 'var(--c-danger)' : r > 0.75 ? 'var(--c-warn)' : undefined} />
      <div className="mt-1 text-[11px] text-subtle">
        {pct(r)} limitu · wolne na dysku: {gb(storage?.free_disk_gb)}
      </div>
    </div>
  );
}

export function useSortedModels(models: ModelEntry[] | null) {
  return useMemo(() => (models || []).slice().sort((a, b) => a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name)), [models]);
}
