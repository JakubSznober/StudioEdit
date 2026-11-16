// Panel mediów: biny, miniatury, flagi, stan analizy, import ścieżki, upload drag&drop, podgląd klipu.
import { AnimatePresence, motion } from 'framer-motion';
import { useEffect, useMemo, useRef, useState } from 'react';
import { errMsg } from '../../api/client';
import { projects as P, urls } from '../../api/endpoints';
import { BINS, flagMeta } from '../../lib/constants';
import { bytes, cx, dur, durLong, tc } from '../../lib/format';
import {
  AudioWaveform,
  CircleCheck,
  FolderOpen,
  Image as ImageIcon,
  Plus,
  ScanEye,
  Search,
  Star,
  Trash,
  TriangleExclamationPoint,
  Upload,
  X,
} from '../../lib/icons';
import { useLive } from '../../store/live';
import { project, useProject } from '../../store/project';
import { ui } from '../../store/ui';
import type { MediaItem } from '../../types';
import { Badge, Button, confirmDialog, Dialog, IconButton, Input, Progress, promptDialog, Select, Skeleton, Spinner } from '../ui';

function analysisState(m: MediaItem, running: boolean): { label: string; tone: 'neutral' | 'success' | 'warn' | 'accent'; } {
  if (running) return { label: 'Analiza…', tone: 'accent' };
  const st = Object.values(m.analysis?.stage || {});
  if (!st.length) return { label: 'Nie analizowano', tone: 'neutral' };
  if (st.some((s) => s === 'failed')) return { label: 'Analiza częściowa', tone: 'warn' };
  return { label: 'Przeanalizowano', tone: 'success' };
}

function Thumb({ pid, m, className }: { pid: string; m: MediaItem; className?: string }) {
  const [err, setErr] = useState(false);
  const Icon = m.kind === 'audio' ? AudioWaveform : ImageIcon;
  return (
    <div className={cx('relative overflow-hidden rounded-lg bg-surface-3', className)}>
      {!err && m.kind !== 'audio' ? (
        <img src={urls.thumb(pid, m.id, m.thumb ? 1 : undefined)} alt="" loading="lazy" className="h-full w-full object-cover" onError={() => setErr(true)} draggable={false} />
      ) : (
        <div className="flex h-full w-full items-center justify-center text-subtle">
          <Icon size={18} />
        </div>
      )}
      {m.info?.duration ? (
        <span className="tabular absolute right-1 bottom-1 rounded bg-black/65 px-1 text-[9.5px] font-medium text-white">{dur(m.info.duration)}</span>
      ) : null}
    </div>
  );
}

function MediaCard({ pid, m, running, onOpen }: { pid: string; m: MediaItem; running: boolean; onOpen: () => void }) {
  const st = analysisState(m, running);
  const info = m.info || {};
  return (
    <motion.button
      layout="position"
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      onClick={onOpen}
      className={cx(
        'group flex w-full gap-2.5 rounded-xl border border-transparent p-1.5 text-left transition-all hover:border-border hover:bg-surface-2 cursor-pointer',
        m.bin === 'Odrzucone_Duble' && 'opacity-60',
      )}
    >
      <Thumb pid={pid} m={m} className="h-[52px] w-[86px] shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[12.5px] font-medium" title={m.name}>
          {m.name}
        </div>
        <div className="tabular truncate text-[10.5px] text-muted">
          {info.width ? `${info.width}×${info.height}` : m.kind === 'audio' ? 'audio' : '—'}
          {info.fps ? ` · ${Math.round(Number(info.fps) * 100) / 100} fps` : ''}
          {m.analysis?.log_profile ? ` · ${m.analysis.log_profile}` : ''}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-1">
          <Badge tone={st.tone} className="!px-1 !text-[10px]">
            {st.tone === 'accent' && <Spinner size={9} className="text-accent" />}
            {st.label}
          </Badge>
          {m.flags.slice(0, 3).map((f) => {
            const fm = flagMeta(f);
            return (
              <Badge key={f} tone={fm.tone} className="!px-1 !text-[10px]" title={fm.desc}>
                {fm.label}
              </Badge>
            );
          })}
          {m.flags.length > 3 && <span className="text-[10px] text-subtle">+{m.flags.length - 3}</span>}
        </div>
      </div>
    </motion.button>
  );
}

function MediaPreview({ pid, m, onClose }: { pid: string; m: MediaItem | null; onClose: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [full, setFull] = useState<MediaItem | null>(null);
  const item = full && m && full.id === m.id ? full : m;
  useEffect(() => {
    if (!m) return;
    let cancelled = false;
    P.mediaItem(pid, m.id)
      .then((r) => !cancelled && r && setFull(r))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [m?.id, pid]);
  const run = async (key: string, fn: () => Promise<unknown>, ok?: string) => {
    setBusy(key);
    try {
      await fn();
      if (ok) ui.toast(ok, 'success');
      project.loadMedia();
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    } finally {
      setBusy(null);
    }
  };
  const info = item?.info || {};
  const a = item?.analysis;
  const ts = item?.transcript_summary;
  const transcriptText =
    typeof ts === 'string'
      ? ts
      : ts && typeof ts === 'object'
        ? String((ts as Record<string, unknown>).text || (ts as Record<string, unknown>).summary || JSON.stringify(ts))
        : item?.transcript?.sentences?.slice(0, 6).map((s) => s.text).join(' ');
  return (
    <Dialog open={!!m} onClose={onClose} title={item?.name} subtitle={item?.path} width={860}>
      {item && (
        <div className="grid grid-cols-1 gap-5 p-5 lg:grid-cols-[1.4fr_1fr]">
          <div>
            <div className="overflow-hidden rounded-xl bg-black">
              {item.kind === 'video' ? (
                <video src={urls.proxy(pid, item.id)} poster={urls.thumb(pid, item.id)} controls className="aspect-video w-full bg-black" preload="metadata" />
              ) : item.kind === 'audio' ? (
                <div className="flex aspect-video flex-col items-center justify-center gap-4 p-6">
                  <AudioWaveform size={40} className="text-white/50" />
                  <audio src={urls.proxy(pid, item.id)} controls className="w-full" />
                </div>
              ) : (
                <img src={urls.thumb(pid, item.id)} alt="" className="aspect-video w-full object-contain" />
              )}
            </div>
            {a?.summary && (
              <div className="mt-3 rounded-xl bg-surface-2 p-3 text-[12.5px] leading-relaxed">
                <div className="mb-1 text-[11px] font-semibold text-muted">Opis archiwisty</div>
                {a.summary}
              </div>
            )}
            {transcriptText && (
              <div className="mt-3 rounded-xl bg-surface-2 p-3 text-[12.5px] leading-relaxed">
                <div className="mb-1 text-[11px] font-semibold text-muted">Transkrypcja (skrót)</div>
                <div className="line-clamp-6">{transcriptText}</div>
              </div>
            )}
          </div>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-[12px]">
              {[
                ['Czas trwania', durLong(info.duration)],
                ['Rozdzielczość', info.width ? `${info.width}×${info.height}` : '—'],
                ['Klatkaż', info.fps ? `${info.fps} fps` : '—'],
                ['Kodek', [info.codec, info.bit_depth ? `${info.bit_depth}-bit` : ''].filter(Boolean).join(' · ') || '—'],
                ['Rozmiar', bytes(info.size)],
                ['Profil', a?.log_profile || 'Rec.709'],
                ['Ujęcia', a?.shots?.length ? String(a.shots.length) : '—'],
                ['Głośność', a?.loudness_lufs != null ? `${a.loudness_lufs.toFixed(1)} LUFS` : '—'],
                ['Mowa', a?.speech_ratio ? `${Math.round(a.speech_ratio * 100)}%` : '—'],
                ['Tempo', a?.bpm ? `${Math.round(a.bpm)} BPM` : '—'],
              ].map(([k, v]) => (
                <div key={k}>
                  <div className="text-muted">{k}</div>
                  <div className="tabular truncate font-medium">{v}</div>
                </div>
              ))}
            </div>
            {item.flags.length > 0 && (
              <div>
                <div className="mb-1.5 text-[11px] font-semibold text-muted">Flagi</div>
                <div className="space-y-1">
                  {item.flags.map((f) => {
                    const fm = flagMeta(f);
                    const d = item.flag_details?.[f];
                    return (
                      <div key={f} className="flex items-start gap-2 text-[12px]">
                        <Badge tone={fm.tone}>{fm.label}</Badge>
                        <span className="text-muted">
                          {fm.desc}
                          {d != null && typeof d !== 'object' ? ` (${String(d)})` : ''}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
            {Object.keys(a?.stage || {}).length > 0 && (
              <div>
                <div className="mb-1.5 text-[11px] font-semibold text-muted">Etapy analizy</div>
                <div className="flex flex-wrap gap-1">
                  {Object.entries(a!.stage).map(([k, v]) => (
                    <Badge key={k} tone={v === 'done' ? 'success' : v === 'failed' ? 'danger' : 'neutral'}>
                      {v === 'done' ? <CircleCheck size={10} /> : v === 'failed' ? <TriangleExclamationPoint size={10} /> : null} {k}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
            <div className="grid grid-cols-2 gap-3 border-t border-border pt-4">
              <div>
                <div className="mb-1 text-[11px] font-medium text-muted">Bin</div>
                <Select
                  value={item.bin}
                  onChange={(bin) => run('bin', () => P.patchMedia(pid, item.id, { bin }).then((r) => r && setFull(r)), `Przeniesiono do „${bin}”`)}
                  options={BINS.map((b) => ({ value: b.key, label: b.label }))}
                />
              </div>
              <div>
                <div className="mb-1 text-[11px] font-medium text-muted">Ocena</div>
                <div className="flex h-9 items-center gap-0.5">
                  {[1, 2, 3, 4, 5].map((n) => (
                    <button
                      key={n}
                      onClick={() => run('rating', () => P.patchMedia(pid, item.id, { rating: n === item.rating ? 0 : n }).then((r) => r && setFull(r)))}
                      className="cursor-pointer p-0.5"
                      aria-label={`Ocena ${n}`}
                    >
                      <Star size={18} className={n <= item.rating ? 'fill-warn text-warn' : 'text-border-strong'} />
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" variant="soft" icon={ScanEye} loading={busy === 'analyze'} onClick={() => run('analyze', () => P.analyze(pid, [item.id]), 'Zlecono analizę klipu')}>
                Analizuj ponownie
              </Button>
              {item.bin !== 'Odrzucone_Duble' && (
                <Button
                  size="sm"
                  variant="secondary"
                  loading={busy === 'reject'}
                  onClick={async () => {
                    const reason = await promptDialog({ title: 'Odrzuć klip', text: 'Klip trafi do binu „Odrzucone duble”. Podaj powód (opcjonalnie):', confirm: 'Odrzuć', input: { placeholder: 'np. nieostry, zły dubel' } });
                    if (reason === null) return;
                    run('reject', () => P.rejectMedia(pid, item.id, reason || 'Odrzucone ręcznie'), 'Klip odrzucony');
                  }}
                >
                  Odrzuć
                </Button>
              )}
              <Button
                size="sm"
                variant="danger"
                icon={Trash}
                loading={busy === 'delete'}
                onClick={async () => {
                  const ok = await confirmDialog({ title: 'Usunąć klip z projektu?', text: 'Wpis zostanie usunięty z projektu. Oryginalny plik na dysku pozostaje nietknięty.', confirm: 'Usuń', danger: true });
                  if (!ok) return;
                  await run('delete', () => P.deleteMedia(pid, item.id), 'Usunięto klip z projektu');
                  onClose();
                }}
              >
                Usuń
              </Button>
            </div>
            {a?.shots?.length ? (
              <div>
                <div className="mb-1.5 text-[11px] font-semibold text-muted">Ujęcia</div>
                <div className="scroll-thin max-h-40 space-y-1 overflow-y-auto pr-1">
                  {a.shots.slice(0, 60).map((s) => (
                    <div key={s.id} className="flex gap-2 text-[11.5px]">
                      <span className="tabular shrink-0 text-subtle">{tc(s.s, Number(info.fps) || 30)}</span>
                      <span className="truncate text-muted" title={s.description}>
                        {[s.shot_size, s.camera_move, s.description].filter(Boolean).join(' · ') || '—'}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        </div>
      )}
    </Dialog>
  );
}

export function MediaPanel() {
  const pid = useProject((s) => s.activeId);
  const media = useProject((s) => s.media);
  const loaded = useProject((s) => s.mediaLoaded);
  const ingest = useProject((s) => s.projects.find((p) => p.id === s.activeId)?.ingest_mode);
  const tasks = useLive((s) => s.tasks);
  const [bin, setBin] = useState<string>('all');
  const [q, setQ] = useState('');
  const [showImport, setShowImport] = useState(false);
  const [path, setPath] = useState('');
  const [copy, setCopy] = useState<boolean | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [upload, setUpload] = useState<number | null>(null);
  const [drag, setDrag] = useState(false);
  const [open, setOpen] = useState<MediaItem | null>(null);
  const dragDepth = useRef(0);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const runningNames = useMemo(() => {
    const out: string[] = [];
    Object.values(tasks).forEach((t) => {
      if (t.project_id === pid && (t.status === 'running' || t.status === 'queued' || t.status === 'waiting') && (t.agent === 'archivist' || t.agent === 'sound'))
        out.push(`${t.title} ${t.path}`.toLowerCase());
    });
    return out;
  }, [tasks, pid]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    media.forEach((m) => (c[m.bin] = (c[m.bin] || 0) + 1));
    return c;
  }, [media]);

  const filtered = useMemo(
    () =>
      media
        .filter((m) => (bin === 'all' ? m.bin !== 'Odrzucone_Duble' : m.bin === bin))
        .filter((m) => !q || m.name.toLowerCase().includes(q.toLowerCase()) || m.flags.some((f) => f.toLowerCase().includes(q.toLowerCase())))
        .sort((a, b) => (a.added_at || 0) - (b.added_at || 0)),
    [media, bin, q],
  );

  if (!pid) return null;

  const doImport = async () => {
    const paths = path
      .split(/\r?\n|;/)
      .map((s) => s.trim().replace(/^"|"$/g, ''))
      .filter(Boolean);
    if (!paths.length) return ui.toast('Podaj ścieżkę pliku lub folderu', 'warn');
    setBusy('import');
    try {
      const r = await P.importMedia(pid, { paths, copy: copy ?? ingest === 'copy', analyze: true });
      ui.toast(`Zaimportowano ${r?.added?.length ?? 0} plików${r?.group ? ' — analiza w toku' : ''}`, 'success');
      if (r?.group) ui.focusGroup(r.group, 'stream');
      setPath('');
      setShowImport(false);
      project.loadMedia();
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    } finally {
      setBusy(null);
    }
  };

  const doUpload = async (files: File[]) => {
    if (!files.length) return;
    setUpload(0);
    try {
      const r = await P.upload(pid, files, setUpload);
      ui.toast(`Przesłano ${r?.added?.length ?? files.length} plików`, 'success');
      if (r?.group) ui.focusGroup(r.group, 'stream');
      project.loadMedia();
    } catch (e) {
      ui.toast(`Upload nie powiódł się: ${errMsg(e)}`, 'error');
    } finally {
      setUpload(null);
    }
  };

  const analyzeAll = async () => {
    setBusy('analyze');
    try {
      const ids = filtered.map((m) => m.id);
      const r = await P.analyze(pid, bin === 'all' ? undefined : ids);
      ui.toast('Zlecono analizę materiału', 'success');
      if (r?.group) ui.focusGroup(r.group, 'stream');
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    } finally {
      setBusy(null);
    }
  };

  const binList = [{ key: 'all', label: 'Wszystkie' }, ...BINS].filter((b) => b.key === 'all' || b.key === 'A-Roll' || b.key === 'B-Roll' || counts[b.key]);

  return (
    <div
      className="relative flex h-full min-h-0 flex-col bg-surface"
      onDragEnter={(e) => {
        if (!e.dataTransfer.types.includes('Files')) return;
        e.preventDefault();
        dragDepth.current++;
        setDrag(true);
      }}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes('Files')) e.preventDefault();
      }}
      onDragLeave={() => {
        dragDepth.current = Math.max(0, dragDepth.current - 1);
        if (!dragDepth.current) setDrag(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        dragDepth.current = 0;
        setDrag(false);
        doUpload(Array.from(e.dataTransfer.files || []));
      }}
    >
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-3">
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-semibold leading-tight">Media</div>
          <div className="text-[10.5px] text-muted">{media.length} plików</div>
        </div>
        <IconButton icon={FolderOpen} label="Importuj z dysku (ścieżka)" size="sm" active={showImport} onClick={() => setShowImport((v) => !v)} />
        <IconButton icon={Upload} label="Prześlij pliki" size="sm" onClick={() => fileRef.current?.click()} />
        <Button size="xs" variant="soft" icon={ScanEye} loading={busy === 'analyze'} onClick={analyzeAll} disabled={!media.length}>
          Analizuj
        </Button>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept="video/*,audio/*,image/*,.mxf,.mts,.braw,.r3d"
          className="hidden"
          onChange={(e) => {
            doUpload(Array.from(e.target.files || []));
            e.target.value = '';
          }}
        />
      </div>

      <AnimatePresence initial={false}>
        {showImport && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="shrink-0 overflow-hidden border-b border-border">
            <div className="space-y-2 p-3">
              <div className="text-[11.5px] text-muted">Ścieżka pliku lub folderu na dysku (kilka — w osobnych liniach):</div>
              <textarea
                autoFocus
                value={path}
                onChange={(e) => setPath(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    doImport();
                  }
                }}
                rows={2}
                placeholder={'D:\\Nagrania\\Islandia'}
                className="w-full resize-none rounded-[10px] border border-border bg-surface px-2.5 py-2 font-mono text-[12px] outline-none focus:border-accent/60 focus:ring-3 focus:ring-accent/15"
              />
              <div className="flex items-center justify-between gap-2">
                <label className="flex items-center gap-1.5 text-[11.5px] text-muted">
                  <input type="checkbox" checked={copy ?? ingest === 'copy'} onChange={(e) => setCopy(e.target.checked)} className="accent-[var(--c-accent)]" />
                  Kopiuj do projektu
                </label>
                <Button size="xs" variant="primary" icon={Plus} loading={busy === 'import'} onClick={doImport}>
                  Importuj
                </Button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {upload != null && (
        <div className="shrink-0 border-b border-border px-3 py-2">
          <div className="flex justify-between text-[11px] text-muted">
            <span>Przesyłanie plików…</span>
            <span className="tabular">{Math.round(upload * 100)}%</span>
          </div>
          <Progress value={upload} height={4} striped className="mt-1" />
        </div>
      )}

      <div className="shrink-0 space-y-2 px-3 pt-2.5 pb-1.5">
        <div className="relative">
          <Search size={13} className="absolute top-1/2 left-2.5 -translate-y-1/2 text-subtle" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Szukaj nazwy lub flagi…" className="!h-8 !pl-7 !text-[12px]" />
          {q && (
            <button onClick={() => setQ('')} className="absolute top-1/2 right-2 -translate-y-1/2 text-subtle hover:text-text cursor-pointer" aria-label="Wyczyść">
              <X size={13} />
            </button>
          )}
        </div>
        <div className="flex flex-wrap gap-1">
          {binList.map((b) => {
            const n = b.key === 'all' ? media.filter((m) => m.bin !== 'Odrzucone_Duble').length : counts[b.key] || 0;
            const active = bin === b.key;
            return (
              <button
                key={b.key}
                onClick={() => setBin(b.key)}
                className={cx(
                  'flex h-6 items-center gap-1 rounded-md px-2 text-[11px] font-medium transition-colors cursor-pointer',
                  active ? 'bg-accent/12 text-accent' : 'bg-surface-2 text-muted hover:text-text',
                )}
              >
                {b.label}
                <span className="tabular opacity-60">{n}</span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-1.5 pb-3">
        {!loaded &&
          [0, 1, 2, 3, 4].map((k) => (
            <div key={k} className="flex gap-2.5 p-1.5">
              <Skeleton className="h-[52px] w-[86px]" />
              <div className="flex-1 space-y-1.5 py-1">
                <Skeleton className="h-3 w-3/4" />
                <Skeleton className="h-2.5 w-1/2" />
              </div>
            </div>
          ))}
        {loaded && media.length === 0 && (
          <div className="m-1.5 rounded-xl border-2 border-dashed border-border p-5 text-center">
            <Upload size={22} className="mx-auto text-accent" />
            <div className="mt-2 text-[13px] font-semibold">Dodaj materiał</div>
            <div className="mt-1 text-[11.5px] leading-snug text-muted">
              Przeciągnij tu pliki wideo, audio lub grafiki — albo kliknij <FolderOpen size={11} className="inline" /> i wpisz ścieżkę folderu na dysku.
            </div>
            <div className="mt-3 flex justify-center gap-2">
              <Button size="xs" variant="primary" icon={FolderOpen} onClick={() => setShowImport(true)}>
                Ścieżka z dysku
              </Button>
              <Button size="xs" variant="secondary" icon={Upload} onClick={() => fileRef.current?.click()}>
                Wybierz pliki
              </Button>
            </div>
          </div>
        )}
        {loaded && media.length > 0 && filtered.length === 0 && <div className="p-4 text-center text-[12px] text-muted">Brak plików w tym widoku.</div>}
        {filtered.map((m) => (
          <MediaCard key={m.id} pid={pid} m={m} running={runningNames.some((n) => n.includes(m.name.toLowerCase()))} onOpen={() => setOpen(m)} />
        ))}
      </div>

      <AnimatePresence>
        {drag && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="pointer-events-none absolute inset-2 z-30 flex flex-col items-center justify-center rounded-2xl border-2 border-dashed border-accent bg-accent/10 backdrop-blur-sm"
          >
            <motion.div animate={{ y: [0, -6, 0] }} transition={{ duration: 1.2, repeat: Infinity }}>
              <Upload size={28} className="text-accent" />
            </motion.div>
            <div className="mt-2 text-[13px] font-semibold text-accent">Upuść pliki, aby przesłać</div>
            <span className="text-[11px] text-muted">Analiza rozpocznie się automatycznie</span>
          </motion.div>
        )}
      </AnimatePresence>

      <MediaPreview pid={pid} m={open} onClose={() => setOpen(null)} />
    </div>
  );
}
