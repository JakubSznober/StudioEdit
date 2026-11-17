import { motion } from 'framer-motion';
import { useEffect, useState } from 'react';
import { errMsg } from '../api/client';
import { projects as P, urls, type NewProject } from '../api/endpoints';
import { AgentStrip, GpuCard, KeyModels, ModuleGrid, ServicesGrid, StorageSummary } from '../components/system';
import {
  Badge,
  Button,
  Card,
  Chip,
  confirmDialog,
  Dialog,
  EmptyState,
  Field,
  Input,
  PageHeader,
  SectionTitle,
  Segmented,
  Select,
  Skeleton,
} from '../components/ui';
import { PLATFORMS, SCALES, STYLES, platformLabel } from '../lib/constants';
import { cx, dur, gb, parseDuration, when } from '../lib/format';
import { Clapperboard, FolderOpen, LayoutDashboard, Plus, Trash } from '../lib/icons';
import { navigate } from '../lib/router';
import { useLive } from '../store/live';
import { project, useProject } from '../store/project';
import { ui } from '../store/ui';
import type { ProjectSummary } from '../types';

const STATUS_LABEL: Record<string, string> = {
  new: 'Nowy',
  ingesting: 'Import',
  analyzed: 'Przeanalizowany',
  edited: 'Zmontowany',
  rendered: 'Wyrenderowany',
};

function NewProjectDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [name, setName] = useState('');
  const [scale, setScale] = useState<'small' | 'medium' | 'massive'>('medium');
  const [platforms, setPlatforms] = useState<string[]>(['youtube']);
  const [style, setStyle] = useState('vlog');
  const [target, setTarget] = useState('');
  const [ingest, setIngest] = useState<'link' | 'copy'>('link');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (open) {
      setName('');
      setTarget('');
    }
  }, [open]);
  const submit = async () => {
    if (!name.trim()) {
      ui.toast('Podaj nazwę projektu', 'warn');
      return;
    }
    const body: NewProject = { name: name.trim(), scale, platforms, style, ingest_mode: ingest };
    const td = parseDuration(target);
    if (td) body.target_duration = td;
    setBusy(true);
    try {
      const p = await P.create(body);
      ui.toast(`Utworzono projekt „${p.name}”`, 'success');
      await project.loadProjects();
      project.setActive(p.id);
      onClose();
      navigate('studio');
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open={open}
      onClose={onClose}
      icon={Plus}
      title="Nowy projekt"
      subtitle="Ustawienia możesz zmienić później — agenci dopasują analizę do skali materiału."
      width={620}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Anuluj
          </Button>
          <Button variant="primary" loading={busy} onClick={submit} icon={Plus}>
            Utwórz projekt
          </Button>
        </>
      }
    >
      <div className="space-y-5 px-5 py-5">
        <Field label="Nazwa projektu">
          <Input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="np. Vlog z Islandii" onKeyDown={(e) => e.key === 'Enter' && submit()} />
        </Field>
        <div>
          <div className="mb-1.5 text-[12px] font-medium text-muted">Skala materiału</div>
          <div className="grid grid-cols-3 gap-2">
            {SCALES.map((s) => (
              <button
                key={s.key}
                onClick={() => setScale(s.key)}
                className={cx(
                  'rounded-xl border p-3 text-left transition-all cursor-pointer',
                  scale === s.key ? 'border-accent/50 bg-accent/6 ring-3 ring-accent/12' : 'border-border hover:border-border-strong',
                )}
              >
                <div className="text-[13px] font-semibold">{s.label}</div>
                <div className="mt-1 text-[11.5px] leading-snug text-muted">{s.desc}</div>
              </button>
            ))}
          </div>
        </div>
        <div>
          <div className="mb-1.5 text-[12px] font-medium text-muted">Platformy docelowe</div>
          <div className="flex flex-wrap gap-1.5">
            {PLATFORMS.map((p) => (
              <Chip
                key={p.key}
                active={platforms.includes(p.key)}
                onClick={() => setPlatforms((cur) => (cur.includes(p.key) ? cur.filter((x) => x !== p.key) : [...cur, p.key]))}
              >
                {p.label} <span className="opacity-60">{p.aspect}</span>
              </Chip>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Styl">
            <Select value={style} onChange={setStyle} options={STYLES.map((s) => ({ value: s.key, label: s.label }))} />
          </Field>
          <Field label="Docelowa długość" hint="np. 4:00, 90 s, 3 min — puste = automatycznie">
            <Input value={target} onChange={(e) => setTarget(e.target.value)} placeholder="4:00" />
          </Field>
        </div>
        <Field label="Tryb importu" hint={ingest === 'link' ? 'Oryginały zostają na miejscu (tylko do odczytu) — oszczędza dysk.' : 'Pliki są kopiowane do folderu projektu — bezpieczne przy dyskach zewnętrznych.'}>
          <Segmented
            value={ingest}
            onChange={setIngest}
            options={[
              { value: 'link', label: 'Linkuj oryginały' },
              { value: 'copy', label: 'Kopiuj do projektu' },
            ]}
          />
        </Field>
      </div>
    </Dialog>
  );
}

function ProjectCard({ p, active, i }: { p: ProjectSummary; active: boolean; i: number }) {
  const remove = async () => {
    const ok = await confirmDialog({
      title: `Usunąć projekt „${p.name}”?`,
      text: 'Projekt zostanie przeniesiony do folderu Backups (oryginalne pliki wideo nie są usuwane).',
      confirm: 'Usuń projekt',
      danger: true,
    });
    if (!ok) return;
    try {
      await P.remove(p.id);
      ui.toast(`Usunięto projekt „${p.name}”`, 'success');
      project.loadProjects();
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    }
  };
  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.97 }}
      transition={{ delay: 0.03 * i }}
      className={cx(
        'group relative flex flex-col overflow-hidden rounded-2xl border bg-surface shadow-soft transition-all hover:shadow-card',
        active ? 'border-accent/45 ring-3 ring-accent/10' : 'border-border hover:border-border-strong',
      )}
    >
      <div className="relative h-24 overflow-hidden bg-black">
        {p.thumb ? (
          <img src={p.thumb.startsWith('/api') || p.thumb.startsWith('http') ? p.thumb : urls.file(p.id, p.thumb)} alt="" className="h-full w-full object-contain" onError={(e) => ((e.target as HTMLImageElement).style.display = 'none')} />
        ) : (
          <div className="accent-gradient flex h-full w-full items-center justify-center opacity-80">
            <span className="text-[30px] font-bold text-white/90">{p.name.slice(0, 1).toUpperCase()}</span>
          </div>
        )}
        {active && (
          <span className="absolute top-2 left-2">
            <Badge tone="accent" className="bg-surface/90 backdrop-blur">
              Aktywny
            </Badge>
          </span>
        )}
        <button
          onClick={remove}
          className="absolute top-2 right-2 flex h-7 w-7 items-center justify-center rounded-lg bg-surface/90 text-muted opacity-0 backdrop-blur transition-opacity group-hover:opacity-100 hover:text-danger cursor-pointer"
          aria-label="Usuń projekt"
        >
          <Trash size={14} />
        </button>
      </div>
      <div className="flex flex-1 flex-col gap-2 p-3.5">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="truncate text-[14px] font-semibold">{p.name}</div>
            <div className="text-[11.5px] text-muted">Zmieniono {when(p.updated_at)}</div>
          </div>
          <Badge>{STATUS_LABEL[p.status] || p.status}</Badge>
        </div>
        <div className="flex flex-wrap gap-1">
          {(p.platforms || []).map((x) => (
            <Badge key={x} tone="info">
              {platformLabel(x)}
            </Badge>
          ))}
          {p.style && <Badge>{STYLES.find((s) => s.key === p.style)?.label || p.style}</Badge>}
        </div>
        <div className="grid grid-cols-3 gap-2 border-t border-border pt-2 text-[11.5px]">
          <div>
            <div className="text-subtle">Media</div>
            <div className="tabular font-medium">{p.media_count ?? 0}</div>
          </div>
          <div>
            <div className="text-subtle">Materiał</div>
            <div className="tabular font-medium">{dur(p.media_duration)}</div>
          </div>
          <div>
            <div className="text-subtle">Montaż</div>
            <div className="tabular font-medium">{p.timeline_duration ? dur(p.timeline_duration) : '—'}</div>
          </div>
        </div>
        <div className="mt-auto flex gap-2 pt-1">
          <Button
            size="sm"
            variant={active ? 'primary' : 'secondary'}
            icon={Clapperboard}
            className="flex-1"
            onClick={() => {
              if (!active) project.setActive(p.id);
              navigate('studio');
            }}
          >
            Otwórz w Studio
          </Button>
          {!active && (
            <Button size="sm" variant="ghost" onClick={() => project.setActive(p.id)}>
              Aktywuj
            </Button>
          )}
        </div>
        <div className="text-[10.5px] text-subtle">
          Skala: {SCALES.find((s) => s.key === p.scale)?.label || p.scale} · {p.ingest_mode === 'copy' ? 'kopia' : 'linki'} · {gb(p.size_gb)}
        </div>
      </div>
    </motion.div>
  );
}

function ProjectManager() {
  const list = useProject((s) => s.projects);
  const loaded = useProject((s) => s.projectsLoaded);
  const activeId = useProject((s) => s.activeId);
  const online = useLive((s) => s.apiOnline);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const h = () => setOpen(true);
    window.addEventListener('studioedit:new-project', h);
    return () => window.removeEventListener('studioedit:new-project', h);
  }, []);
  return (
    <section>
      <SectionTitle
        right={
          <Button size="sm" variant="primary" icon={Plus} onClick={() => setOpen(true)} disabled={online === false}>
            Nowy projekt
          </Button>
        }
      >
        Projekty
      </SectionTitle>
      {!loaded ? (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
          {[0, 1, 2, 3].map((k) => (
            <Skeleton key={k} className="h-[250px] rounded-2xl" />
          ))}
        </div>
      ) : list.length === 0 ? (
        <Card className="py-4">
          <EmptyState
            icon={FolderOpen}
            title="Zacznij od pierwszego projektu"
            action={
              <Button variant="primary" icon={Plus} onClick={() => setOpen(true)} disabled={online === false}>
                Utwórz projekt
              </Button>
            }
          >
            Projekt grupuje materiał, oś czasu i eksporty. Po utworzeniu zaimportuj nagrania w Studio Montażowym, a agenci przeanalizują je automatycznie.
          </EmptyState>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
          {list.map((p, i) => (
            <ProjectCard key={p.id} p={p} active={p.id === activeId} i={i} />
          ))}
        </div>
      )}
      <NewProjectDialog open={open} onClose={() => setOpen(false)} />
    </section>
  );
}

export default function DashboardPage() {
  return (
    <div className="space-y-8">
      <PageHeader icon={LayoutDashboard} title="Status modeli, usług i modułów" subtitle="Wszystko działa lokalnie, offline — na Twoim GPU." right={<div className="w-[260px]"><StorageSummary /></div>} />

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <GpuCard />
        <ServicesGrid />
      </section>

      <section>
        <SectionTitle>Agenci</SectionTitle>
        <AgentStrip onSelect={() => navigate('agents')} />
      </section>

      <section>
        <SectionTitle right={<Button size="xs" variant="ghost" onClick={() => navigate('models')}>Wszystkie modele →</Button>}>Kluczowe modele</SectionTitle>
        <KeyModels />
      </section>

      <section>
        <SectionTitle>Moduły</SectionTitle>
        <ModuleGrid />
      </section>

      <ProjectManager />
    </div>
  );
}
