// Kreator „Zmontuj film”: brief → POST /montage → postęp pipeline (Analiza → Montaż → Kolor → Dźwięk → Render → QC)
import { motion } from 'framer-motion';
import { useEffect, useMemo, useState } from 'react';
import { errMsg } from '../../api/client';
import { projects as P, type MontageRequest } from '../../api/endpoints';
import { MUSIC_MOODS, PLATFORMS, STYLES } from '../../lib/constants';
import { cx, parseDuration, pct } from '../../lib/format';
import { useInterval } from '../../lib/hooks';
import {
  Archive,
  AudioWaveform,
  Captions,
  CircleCheck,
  CircleX,
  Clapperboard,
  Film,
  LoaderCircle,
  Palette,
  ShieldCheck,
  Sparkles,
  type LucideIcon,
} from '../../lib/icons';
import { project, useProject } from '../../store/project';
import { ui, useUI } from '../../store/ui';
import type { PipelineStatus, Task } from '../../types';
import { groupProgress, isTerminal, TaskRow, useGroupTasks } from '../common';
import { Button, Chip, Dialog, Field, Input, Progress, Select, Switch, Textarea } from '../ui';

const STAGES: { key: string; label: string; icon: LucideIcon; color: string }[] = [
  { key: 'analysis', label: 'Analiza', icon: Archive, color: '#0ea5e9' },
  { key: 'edit', label: 'Montaż', icon: Clapperboard, color: '#f97316' },
  { key: 'color', label: 'Kolor', icon: Palette, color: '#ec4899' },
  { key: 'audio', label: 'Dźwięk', icon: AudioWaveform, color: '#10b981' },
  { key: 'render', label: 'Render', icon: Film, color: '#6366f1' },
  { key: 'qc', label: 'QC', icon: ShieldCheck, color: '#ef4444' },
];

export function stageOf(t: Task): string {
  const txt = `${t.path} ${t.title}`.toLowerCase();
  if (/analiz|transkr|diaryz|proxy|ujęć|ujęci|shot|probe|miniatur|import|whisper|vlm|wizj/.test(txt) && t.agent !== 'render' && t.agent !== 'qc') return 'analysis';
  switch (t.agent) {
    case 'archivist':
      return 'analysis';
    case 'director':
    case 'vfx':
    case 'producer':
      return 'edit';
    case 'colorist':
      return 'color';
    case 'sound':
      return 'audio';
    case 'render':
      return 'render';
    case 'qc':
      return 'qc';
    default:
      return 'edit';
  }
}

function PipelineProgress({ group, onClose, onNew }: { group: string; onClose: () => void; onNew?: () => void }) {
  const pid = useProject((s) => s.activeId);
  const tasks = useGroupTasks(group);
  const [pipe, setPipe] = useState<PipelineStatus | null>(null);
  const poll = async () => {
    if (!pid) return;
    try {
      setPipe(await P.pipeline(pid, group));
    } catch {
      /* ignoruj */
    }
  };
  useEffect(() => {
    poll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [group, pid]);
  const g = groupProgress(tasks);
  const finished = (pipe && ['done', 'failed', 'partial'].includes(pipe.status)) || (g.total > 0 && g.finished);
  useInterval(poll, finished ? null : 2000);
  const overall = Math.max(pipe?.progress ?? 0, g.progress);
  const stages = useMemo(
    () =>
      STAGES.map((s) => {
        const items = tasks.filter((t) => stageOf(t) === s.key);
        const sp = groupProgress(items);
        const status = !items.length ? 'pending' : sp.finished ? (sp.failed ? 'failed' : 'done') : sp.running || items.some((t) => t.progress > 0) ? 'running' : 'queued';
        return { ...s, items, progress: sp.progress, status };
      }),
    [tasks],
  );
  const running = tasks.filter((t) => !isTerminal(t.status)).slice(0, 6);
  const failed = tasks.filter((t) => t.status === 'failed');
  const status = pipe?.status || (finished ? (g.failed ? 'partial' : 'done') : 'running');

  return (
    <div className="space-y-5 px-5 py-5">
      <div>
        <div className="flex items-end justify-between">
          <div>
            <div className="text-[13px] font-semibold">
              {status === 'done' ? 'Film gotowy!' : status === 'failed' ? 'Montaż nie powiódł się' : status === 'partial' ? 'Zakończono z błędami' : 'Agenci pracują nad filmem…'}
            </div>
            <div className="text-[12px] text-muted">
              {pipe?.total ? `${pipe.done}/${pipe.total} zadań` : g.total ? `${g.done}/${g.total} zadań` : 'Przygotowuję graf zadań…'}
            </div>
          </div>
          <div className="tabular text-[24px] font-semibold tracking-tight">{pct(overall)}</div>
        </div>
        <Progress value={overall} height={8} className="mt-2" striped={!finished} tone={finished ? (failed.length ? 'warn' : 'success') : 'accent'} indeterminate={!g.total && !pipe?.total} />
      </div>

      <div className="grid grid-cols-6 gap-2">
        {stages.map((s, i) => {
          const Icon = s.icon;
          return (
            <div key={s.key} className="relative flex flex-col items-center gap-1.5 text-center">
              {i < stages.length - 1 && <div className="absolute top-5 left-[calc(50%+22px)] h-[2px] w-[calc(100%-44px)] rounded bg-surface-3" />}
              {i < stages.length - 1 && (
                <motion.div
                  className="absolute top-5 left-[calc(50%+22px)] h-[2px] rounded"
                  style={{ background: s.color }}
                  initial={false}
                  animate={{ width: s.status === 'done' ? 'calc(100% - 44px)' : '0%' }}
                />
              )}
              <motion.div
                className={cx('relative flex h-10 w-10 items-center justify-center rounded-xl border-2', s.status === 'pending' ? 'border-border bg-surface-2 text-subtle' : 'text-white')}
                style={s.status !== 'pending' ? { background: s.status === 'failed' ? 'var(--c-danger)' : s.color, borderColor: 'transparent' } : undefined}
                animate={s.status === 'running' ? { scale: [1, 1.07, 1], boxShadow: [`0 0 0 0 ${s.color}55`, `0 0 0 8px ${s.color}00`, `0 0 0 0 ${s.color}00`] } : { scale: 1 }}
                transition={s.status === 'running' ? { duration: 1.6, repeat: Infinity } : undefined}
              >
                {s.status === 'done' ? <CircleCheck size={18} /> : s.status === 'failed' ? <CircleX size={18} /> : s.status === 'running' ? <LoaderCircle size={18} className="animate-spin" /> : <Icon size={17} />}
              </motion.div>
              <div className="text-[12px] font-semibold">{s.label}</div>
              <div className="tabular text-[10.5px] text-muted">
                {s.items.length ? (s.status === 'done' ? 'gotowe' : s.status === 'failed' ? 'błąd' : pct(s.progress)) : '—'}
              </div>
            </div>
          );
        })}
      </div>

      {running.length > 0 && (
        <div className="rounded-xl border border-border bg-surface-2/50 p-1">
          {running.map((t) => (
            <TaskRow key={t.id} task={t} compact />
          ))}
        </div>
      )}
      {failed.length > 0 && (
        <div className="rounded-xl border border-danger/25 bg-danger/5 p-3">
          <div className="mb-1 text-[12px] font-semibold text-danger">Błędy ({failed.length})</div>
          {failed.slice(0, 4).map((t) => (
            <div key={t.id} className="truncate text-[11.5px] text-muted" title={t.error || ''}>
              <b className="text-text">{t.title}:</b> {t.error || t.message}
            </div>
          ))}
        </div>
      )}
      <div className="flex justify-end gap-2">
        {onNew && (
          <Button variant="ghost" className="mr-auto" icon={Sparkles} onClick={onNew}>
            Nowy montaż
          </Button>
        )}
        <Button variant="ghost" onClick={() => ui.focusGroup(group, 'stream')}>
          Pokaż strumień pracy
        </Button>
        <Button
          variant={finished ? 'primary' : 'secondary'}
          onClick={() => {
            if (finished) {
              ui.setPipeline(null);
              project.loadTimeline();
              project.loadPreview();
            }
            onClose();
          }}
        >
          {finished ? 'Zamknij i oglądaj' : 'Pracuj w tle'}
        </Button>
      </div>
    </div>
  );
}

export function MontageWizard() {
  const open = useUI((s) => s.wizardOpen);
  const mode = useUI((s) => s.wizardMode);
  const pid = useProject((s) => s.activeId);
  const group = useUI((s) => (s.pipelineProject === pid ? s.pipelineGroup : null));
  const proj = useProject((s) => s.projects.find((p) => p.id === s.activeId));
  const looks = useProject((s) => s.looks);
  const styleList = useProject((s) => s.styles);
  const mediaCount = useProject((s) => s.media.length);
  const [phase, setPhase] = useState<'form' | 'progress'>('form');
  const [brief, setBrief] = useState('');
  const [platforms, setPlatforms] = useState<string[]>(['youtube']);
  const [target, setTarget] = useState('');
  const [style, setStyle] = useState('vlog');
  const [look, setLook] = useState('');
  const [mood, setMood] = useState('');
  const [subs, setSubs] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    project.loadLooks();
    project.loadStyles();
    if (mode === 'progress' && group) {
      setPhase('progress');
      return;
    }
    setPhase('form');
    if (proj) {
      if (proj.platforms?.length) setPlatforms(proj.platforms);
      if (proj.style) setStyle(proj.style);
      if (proj.target_duration) {
        const s = Math.round(proj.target_duration);
        setTarget(`${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`);
      }
      if (proj.look) setLook(proj.look);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const close = () => ui.openWizard(false);
  const styleOptions = styleList.length ? styleList : STYLES.map((s) => ({ ...s, description: '', rules: [] as string[], look: '', music_mood: '', pace: '' }));
  const styleInfo = styleList.find((s) => s.key === style);
  const styleLook = styleInfo ? looks.find((l) => l.id === styleInfo.look) : undefined;

  const submit = async () => {
    if (!pid) return;
    if (!brief.trim()) {
      ui.toast('Opisz, jaki film chcesz uzyskać', 'warn');
      return;
    }
    const body: MontageRequest = { brief: brief.trim(), platforms, style, subtitles: subs };
    const td = parseDuration(target);
    if (td) body.target_duration = td;
    if (look) body.look = look;
    if (mood) body.music_mood = mood;
    setBusy(true);
    try {
      const r = await P.montage(pid, body);
      ui.setPipeline(r.group, pid);
      ui.focusGroup(r.group, 'stream');
      setPhase('progress');
      ui.toast('Ekipa rozpoczęła montaż', 'success');
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={close}
      icon={phase === 'form' ? Sparkles : Clapperboard}
      title={phase === 'form' ? 'Zmontuj film' : 'Montaż w toku'}
      subtitle={phase === 'form' ? 'Pełny automatyczny pipeline: analiza → montaż → kolor → dźwięk → render podglądu → kontrola jakości.' : 'Możesz zamknąć to okno — praca trwa w tle.'}
      width={720}
      footer={
        phase === 'form' ? (
          <>
            {group && (
              <Button variant="ghost" className="mr-auto" onClick={() => setPhase('progress')}>
                Pokaż bieżący montaż
              </Button>
            )}
            <Button variant="ghost" onClick={close}>
              Anuluj
            </Button>
            <Button variant="primary" icon={Sparkles} loading={busy} onClick={submit} disabled={!mediaCount}>
              Zmontuj film
            </Button>
          </>
        ) : undefined
      }
    >
      {phase === 'progress' && group ? (
        <PipelineProgress group={group} onClose={close} onNew={() => setPhase('form')} />
      ) : (
        <div className="space-y-5 px-5 py-5">
          {!mediaCount && <div className="rounded-xl bg-warn/10 px-3 py-2 text-[12.5px] text-warn">Projekt nie ma jeszcze materiału — najpierw zaimportuj pliki w panelu Media.</div>}
          <Field label="Brief — opisz film" hint="Np. odbiorcę, emocję, kluczowe momenty, czego unikać. Reżyser potraktuje to jak scenariusz montażu.">
            <Textarea
              autoFocus
              rows={4}
              value={brief}
              onChange={(e) => setBrief(e.target.value)}
              placeholder="Dynamiczny vlog z podróży po Islandii: mocny hook z wodospadem w pierwszych 3 s, potem chronologicznie, ciepłe kolory, energetyczna muzyka, finał o zachodzie słońca."
            />
          </Field>
          <div>
            <div className="mb-1.5 text-[12px] font-medium text-muted">Platformy (każda dostaje własny wariant kadrowania)</div>
            <div className="flex flex-wrap gap-1.5">
              {PLATFORMS.map((p) => (
                <Chip key={p.key} active={platforms.includes(p.key)} onClick={() => setPlatforms((c) => (c.includes(p.key) ? c.filter((x) => x !== p.key) : [...c, p.key]))}>
                  {p.label} <span className="opacity-60">{p.aspect}</span>
                </Chip>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-[1.4fr_1fr_1fr] gap-3">
            <Field label="Styl montażu">
              <Select value={style} onChange={setStyle} options={styleOptions.map((s) => ({ value: s.key, label: s.label }))} />
            </Field>
            <Field label="Docelowa długość" hint="np. 4:00 lub 60 s">
              <Input value={target} onChange={(e) => setTarget(e.target.value)} placeholder="auto" />
            </Field>
            <Field label="Nastrój muzyki">
              <Select value={mood} onChange={setMood} options={MUSIC_MOODS.map((m) => ({ value: m.key, label: m.label }))} />
            </Field>
          </div>
          {styleInfo && (
            <div className="rounded-xl border border-border bg-surface-2/60 px-3 py-2.5">
              <div className="text-[12.5px] leading-snug">{styleInfo.description}</div>
              {styleInfo.rules?.length > 0 && (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {styleInfo.rules.map((r) => (
                    <span key={r} className="rounded-md bg-surface-3 px-1.5 py-0.5 text-[10.5px] text-muted">
                      {r}
                    </span>
                  ))}
                  {styleLook && <span className="rounded-md bg-accent/10 px-1.5 py-0.5 text-[10.5px] text-accent">look: {styleLook.name}</span>}
                </div>
              )}
            </div>
          )}
          <div className="grid grid-cols-[1fr_auto] items-end gap-3">
            <Field label="Look kolorystyczny" hint={look ? looks.find((l) => l.id === look)?.description : styleLook ? `Automatycznie: ${styleLook.name} — ${styleLook.description}` : undefined}>
              <Select
                value={look}
                onChange={setLook}
                options={[
                  { value: '', label: styleLook ? `Automatycznie (styl: ${styleLook.name})` : 'Automatycznie (Kolorysta dobierze)' },
                  ...looks.map((l) => ({ value: l.id, label: `${l.name}${l.builtin ? '' : ' (LUT)'}` })),
                ]}
              />
            </Field>
            <label className="flex h-9 items-center gap-2 rounded-[10px] border border-border px-3 text-[12.5px] font-medium">
              <Captions size={15} className="text-muted" /> Napisy
              <Switch checked={subs} onChange={setSubs} size="sm" label="Napisy" />
            </label>
          </div>
        </div>
      )}
    </Dialog>
  );
}
