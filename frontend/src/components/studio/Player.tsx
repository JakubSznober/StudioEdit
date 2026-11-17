// Odtwarzacz podglądu: wideo z /preview, warianty, porównanie Przed/Po, kontrolki transportu.
import { AnimatePresence, motion } from 'framer-motion';
import { useEffect, useMemo, useRef, useState } from 'react';
import { errMsg } from '../../api/client';
import { projects as P, urls } from '../../api/endpoints';
import { platformLabel } from '../../lib/constants';
import { clamp, cx, pct, tc } from '../../lib/format';
import {
  Clapperboard,
  Columns2,
  Film,
  Pause,
  Play,
  RefreshCw,
  Repeat,
  SkipBack,
  SkipForward,
  Smartphone,
  Tv,
  Upload,
  Volume2,
  VolumeX,
} from '../../lib/icons';
import { useLive } from '../../store/live';
import { player, usePlayer } from '../../store/player';
import { project, useProject } from '../../store/project';
import { ui } from '../../store/ui';
import { Badge, Button, EmptyState, IconButton, Progress, Spinner, Tip } from '../ui';

function parseAspect(a: string | undefined, w?: number, h?: number): number {
  if (w && h) return w / h;
  const m = (a || '16:9').split(':').map(Number);
  return m.length === 2 && m[0] > 0 && m[1] > 0 ? m[0] / m[1] : 16 / 9;
}

function useFitBox(aspect: number) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const calc = () => {
      const W = el.clientWidth - 24;
      const H = el.clientHeight - 24;
      if (W <= 0 || H <= 0) return setBox({ w: 0, h: 0 });
      if (W / H > aspect) setBox({ w: Math.round(H * aspect), h: Math.round(H) });
      else setBox({ w: Math.round(W), h: Math.round(W / aspect) });
    };
    calc();
    const ro = new ResizeObserver(calc);
    ro.observe(el);
    return () => ro.disconnect();
  }, [aspect]);
  return [ref, box] as const;
}

function loadImg(src: string): Promise<void> {
  return new Promise((res, rej) => {
    const i = new Image();
    i.onload = () => res();
    i.onerror = () => rej(new Error('Nie udało się wczytać klatki'));
    i.src = src;
  });
}

// ------------------------------------------------------------------ Przed / Po
function BeforeAfter({ pid, variant, version, width }: { pid: string; variant: string; version: number; width: number }) {
  const time = usePlayer((s) => s.time);
  const playing = usePlayer((s) => s.playing);
  const seekSeq = usePlayer((s) => s.seekSeq);
  const pos = usePlayer((s) => s.comparePos);
  const [frameT, setFrameT] = useState<number | null>(null);
  const [srcs, setSrcs] = useState<{ before: string; after: string } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const dragging = useRef(false);
  const reqW = clamp(Math.ceil((width * (window.devicePixelRatio || 1)) / 320) * 320, 320, 1920);

  useEffect(() => {
    if (playing) return;
    const t = setTimeout(() => setFrameT(usePlayer.getState().time), 160);
    return () => clearTimeout(t);
  }, [time, playing, seekSeq, version]);

  useEffect(() => {
    if (frameT == null) return;
    let cancelled = false;
    const b = urls.frame(pid, frameT, variant, 'before', reqW, version);
    const a = urls.frame(pid, frameT, variant, 'after', reqW, version);
    setLoading(true);
    setError(false);
    Promise.all([loadImg(b), loadImg(a)])
      .then(() => {
        if (!cancelled) {
          setSrcs({ before: b, after: a });
          setLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setLoading(false);
          setError(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [frameT, pid, variant, version, reqW]);

  const setFromEvent = (clientX: number) => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    player.setComparePos((clientX - r.left) / r.width);
  };

  return (
    <div
      ref={ref}
      className={cx('absolute inset-0 z-10 cursor-ew-resize select-none transition-opacity duration-200', playing ? 'pointer-events-none opacity-0' : 'opacity-100')}
      onPointerDown={(e) => {
        dragging.current = true;
        (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
        setFromEvent(e.clientX);
      }}
      onPointerMove={(e) => dragging.current && setFromEvent(e.clientX)}
      onPointerUp={() => (dragging.current = false)}
    >
      {srcs ? (
        <>
          <img src={srcs.after} alt="Po" className="absolute inset-0 h-full w-full object-contain" draggable={false} />
          <img
            src={srcs.before}
            alt="Przed"
            className="absolute inset-0 h-full w-full object-contain"
            style={{ clipPath: `inset(0 ${(1 - pos) * 100}% 0 0)` }}
            draggable={false}
          />
        </>
      ) : (
        <div className="skeleton absolute inset-0" />
      )}
      <div className="absolute top-0 bottom-0" style={{ left: `${pos * 100}%` }}>
        <div className="absolute top-0 bottom-0 -left-px w-[2px] bg-white shadow-[0_0_8px_rgba(0,0,0,0.5)]" />
        <div className="absolute top-1/2 -left-[15px] flex h-[30px] w-[30px] -translate-y-1/2 items-center justify-center rounded-full bg-white text-[#222] shadow-lg">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2.2">
            <path d="M9 6l-6 6 6 6M15 6l6 6-6 6" />
          </svg>
        </div>
      </div>
      <span className="absolute top-3 left-3 rounded-md bg-black/55 px-2 py-0.5 text-[10.5px] font-semibold tracking-wider text-white backdrop-blur">PRZED</span>
      <span className="absolute top-3 right-3 rounded-md bg-black/55 px-2 py-0.5 text-[10.5px] font-semibold tracking-wider text-white backdrop-blur">PO</span>
      {loading && (
        <div className="absolute top-3 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-md bg-black/55 px-2 py-0.5 text-[10.5px] text-white backdrop-blur">
          <Spinner size={11} className="text-white" /> Renderuję klatki…
        </div>
      )}
      {error && !loading && (
        <div className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-md bg-black/65 px-2.5 py-1 text-[11px] text-white">Nie udało się wyrenderować klatki porównania</div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ Player
const AUTO_PREVIEW_DELAY_MS = 2000;

export function Player() {
  const pid = useProject((s) => s.activeId);
  const tl = useProject((s) => s.timeline);
  const tlLoaded = useProject((s) => s.timelineLoaded);
  const mediaCount = useProject((s) => s.media.length);
  const variant = useProject((s) => s.variant);
  const preview = useProject((s) => s.preview[s.variant]);
  const previewLoaded = useProject((s) => s.previewLoaded);
  const renders = useLive((s) => s.renders);
  const playing = usePlayer((s) => s.playing);
  const time = usePlayer((s) => s.time);
  const duration = usePlayer((s) => s.duration);
  const compare = usePlayer((s) => s.compare);
  const muted = usePlayer((s) => s.muted);
  const range = usePlayer((s) => s.range);
  const loop = usePlayer((s) => s.loop);
  const fps = tl?.fps || 30;
  const variants = (tl?.variants || []).filter((v) => v.enabled !== false);
  const v = variants.find((x) => x.name === variant);
  const aspect = parseAspect(v?.aspect, v?.width, v?.height);
  const [boxRef, box] = useFitBox(aspect);
  const [rendering, setRendering] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);

  const src = useMemo(() => {
    if (!preview?.url) return null;
    const sep = preview.url.includes('?') ? '&' : '?';
    return `${preview.url}${sep}v=${preview.version ?? 0}-${Math.round(preview.rendered_at || 0)}`;
  }, [preview?.url, preview?.version, preview?.rendered_at]);

  useEffect(() => {
    player.register(videoRef.current);
    return () => player.register(null);
  }, [src]);

  const activeRender = useMemo(
    () =>
      Object.values(renders)
        .filter((r) => r.project_id === pid && r.variant === variant && r.quality !== 'final' && r.progress < 1)
        .sort((a, b) => (b.ts || 0) - (a.ts || 0))[0],
    [renders, pid, variant],
  );

  const hasClips = !!tl && Object.values(tl.tracks || {}).some((items) => (items || []).length > 0);

  // ---------------------------------------------------------------- auto-podgląd
  // Po każdej zmianie osi (ręcznej, z czatu, cofnięcia) podgląd sam się przelicza: gdy jest nieaktualny,
  // w projekcie nic nie pracuje (montaż sam renderuje na końcu) i nie trwa render tego wariantu.
  const tasks = useLive((s) => s.tasks);
  const projectBusy = useMemo(
    () => Object.values(tasks).some((t) => t.project_id === pid && ['queued', 'waiting', 'running'].includes(t.status)),
    [tasks, pid],
  );
  const [auto, setAuto] = useState(() => {
    try {
      return localStorage.getItem('studioedit.autoPreview') !== '0';
    } catch {
      return true;
    }
  });
  const toggleAuto = () =>
    setAuto((a) => {
      try {
        localStorage.setItem('studioedit.autoPreview', a ? '0' : '1');
      } catch {
        /* brak localStorage */
      }
      return !a;
    });
  const autoDone = useRef('');
  const needsPreview = hasClips && previewLoaded && (!preview?.url || !!preview?.stale);
  useEffect(() => {
    if (!auto || !pid || !tl || !needsPreview || activeRender || rendering || projectBusy) return;
    // klucz: wersja osi + stan ostatniego podglądu. Numer wersji powtarza się po cofnięciu, więc sam nie wystarczy;
    // gdy render się nie uda, rendered_at się nie zmienia - nie ponawiamy w pętli
    const key = `${pid}:${variant}:${tl.version}:${preview?.rendered_at ?? 0}`;
    if (autoDone.current === key) return;
    const h = window.setTimeout(() => {
      autoDone.current = key;
      P.renderPreview(pid, { variant }).catch(() => undefined);
    }, AUTO_PREVIEW_DELAY_MS);
    return () => window.clearTimeout(h);
  }, [auto, pid, variant, tl, needsPreview, activeRender, rendering, projectBusy, preview?.rendered_at]);

  const renderPreview = async () => {
    if (!pid) return;
    setRendering(true);
    try {
      const r = await P.renderPreview(pid, { variant });
      ui.toast('Zlecono render podglądu', 'info');
      if (r?.group) ui.focusGroup(r.group);
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    } finally {
      setRendering(false);
    }
  };

  let empty: React.ReactNode = null;
  if (!src) {
    if (!tlLoaded || !previewLoaded) empty = <div className="skeleton absolute inset-0 rounded-xl" />;
    else if (!mediaCount)
      empty = (
        <EmptyState icon={Upload} title="Zacznij od materiału" compact>
          Przeciągnij pliki wideo na panel Media po lewej lub wpisz ścieżkę folderu na dysku. Agenci przeanalizują je automatycznie.
        </EmptyState>
      );
    else if (!hasClips)
      empty = (
        <EmptyState
          icon={Clapperboard}
          title="Gotowy do montażu"
          compact
          action={
            <Button variant="primary" icon={Clapperboard} onClick={() => ui.openWizard(true)}>
              Zmontuj film
            </Button>
          }
        >
          Opisz, jaki film chcesz uzyskać — Reżyser ułoży oś czasu, a pozostali agenci zajmą się kolorem, dźwiękiem i renderem.
        </EmptyState>
      );
    else
      empty = (
        <EmptyState
          icon={Film}
          title="Brak podglądu tej wersji"
          compact
          action={
            <Button variant="primary" icon={RefreshCw} loading={rendering || !!activeRender} onClick={renderPreview}>
              Renderuj podgląd
            </Button>
          }
        >
          Oś czasu jest gotowa (v{tl?.version}). Wyrenderuj szybki podgląd {platformLabel(variant)} ({v?.aspect || '16:9'}), aby go obejrzeć.
        </EmptyState>
      );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div ref={boxRef} className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden bg-[#0c0c12] dark:bg-black/40">
        <motion.div
          layout
          transition={{ type: 'spring', stiffness: 260, damping: 30 }}
          className={cx('relative overflow-hidden rounded-xl', src ? 'bg-black shadow-pop' : 'bg-surface')}
          style={{ width: box.w || '100%', height: box.h || '100%' }}
        >
          {src ? (
            <video
              key={src}
              ref={videoRef}
              src={src}
              className="h-full w-full bg-black object-contain"
              preload="auto"
              playsInline
              onLoadedMetadata={(e) => {
                const el = e.currentTarget;
                player.register(el);
                if (!tl?.duration && isFinite(el.duration)) player.setDuration(el.duration);
              }}
              onPlay={() => player.syncFromVideo(true)}
              onPause={() => player.syncFromVideo(false)}
              onEnded={() => player.pause()}
              onClick={() => player.toggle()}
            />
          ) : (
            <div className="absolute inset-0 flex items-center justify-center overflow-auto">{empty}</div>
          )}
          {src && compare && pid && <BeforeAfter pid={pid} variant={variant} version={tl?.version || 0} width={box.w} />}
          {/* status podglądu */}
          <div className="pointer-events-none absolute top-2.5 left-2.5 z-20 flex gap-1.5">
            {src && preview?.stale && (
              <span className="pointer-events-auto">
                <Tip text="Oś czasu zmieniła się od ostatniego renderu podglądu">
                  <button onClick={renderPreview} className="flex items-center gap-1 rounded-md bg-warn/90 px-2 py-0.5 text-[10.5px] font-semibold text-black cursor-pointer">
                    <RefreshCw size={11} className={rendering || activeRender ? 'animate-spin' : ''} /> Podgląd nieaktualny — odśwież
                  </button>
                </Tip>
              </span>
            )}
          </div>
          <AnimatePresence>
            {activeRender && (
              <motion.div
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: 8 }}
                className="absolute right-3 bottom-3 left-3 z-20 rounded-lg bg-black/70 px-3 py-2 text-white backdrop-blur"
              >
                <div className="flex justify-between text-[11px]">
                  <span>Render podglądu · {activeRender.stage || 'przetwarzanie'}</span>
                  <span className="tabular">{pct(activeRender.progress)}</span>
                </div>
                <Progress value={activeRender.progress} height={4} striped className="mt-1 bg-white/15" />
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>
      </div>

      {/* transport */}
      <div className="flex h-11 shrink-0 items-center gap-1 border-t border-border bg-surface px-2.5">
        <IconButton icon={SkipBack} label="Klatka wstecz (←)" size="sm" onClick={() => player.step(-1)} />
        <Tip text={playing ? 'Pauza (Spacja)' : 'Odtwórz (Spacja)'}>
          <button
            onClick={() => player.toggle()}
            className="accent-gradient flex h-8 w-8 items-center justify-center rounded-full text-white shadow-card transition-transform hover:scale-105 active:scale-95 cursor-pointer"
            aria-label={playing ? 'Pauza' : 'Odtwórz'}
          >
            {playing ? <Pause size={15} fill="currentColor" /> : <Play size={15} fill="currentColor" className="ml-0.5" />}
          </button>
        </Tip>
        <IconButton icon={SkipForward} label="Klatka naprzód (→)" size="sm" onClick={() => player.step(1)} />
        <div className="tabular ml-1.5 text-[12.5px] font-semibold">
          {tc(time, fps)}
          <span className="font-normal text-subtle"> / {tc(duration || tl?.duration || 0, fps)}</span>
        </div>
        <div className="mx-2 h-5 w-px bg-border" />
        <Tip text="Ustaw IN (I)">
          <button onClick={() => player.setIn()} className="h-7 rounded-md px-2 text-[11.5px] font-bold text-muted hover:bg-surface-2 hover:text-text cursor-pointer">
            IN
          </button>
        </Tip>
        <Tip text="Ustaw OUT (O)">
          <button onClick={() => player.setOut()} className="h-7 rounded-md px-2 text-[11.5px] font-bold text-muted hover:bg-surface-2 hover:text-text cursor-pointer">
            OUT
          </button>
        </Tip>
        <IconButton icon={Repeat} label="Pętla zakresu (L)" size="sm" active={loop} disabled={!range} onClick={player.toggleLoop} />
        {range && (
          <span className="tabular hidden rounded-md bg-accent/10 px-1.5 py-0.5 text-[11px] font-medium text-accent xl:inline">
            {tc(range.in, fps)} – {tc(range.out, fps)}
          </span>
        )}
        <div className="flex-1" />
        <Tip text="Porównanie Przed / Po (przeciągnij linię na obrazie)">
          <button
            onClick={player.toggleCompare}
            disabled={!src}
            className={cx(
              'flex h-7 items-center gap-1.5 rounded-lg px-2 text-[11.5px] font-semibold transition-colors cursor-pointer disabled:opacity-40',
              compare ? 'bg-accent/12 text-accent' : 'text-muted hover:bg-surface-2 hover:text-text',
            )}
          >
            <Columns2 size={14} /> Przed / Po
          </button>
        </Tip>
        <IconButton icon={muted ? VolumeX : Volume2} label={muted ? 'Włącz dźwięk' : 'Wycisz'} size="sm" onClick={player.toggleMute} />
        {variants.length > 0 && (
          <div className="ml-1 flex items-center rounded-lg bg-surface-3 p-0.5">
            {variants.map((x) => {
              const vertical = parseAspect(x.aspect, x.width, x.height) < 1;
              const Icon = vertical ? Smartphone : Tv;
              const active = x.name === variant;
              return (
                <Tip key={x.name} text={`${platformLabel(x.name)} · ${x.aspect} · ${x.width}×${x.height}`}>
                  <button
                    onClick={() => project.setVariant(x.name)}
                    className={cx(
                      'relative flex h-6 items-center gap-1 rounded-md px-2 text-[11px] font-medium transition-colors cursor-pointer',
                      active ? 'text-text' : 'text-muted hover:text-text',
                    )}
                  >
                    {active && <motion.span layoutId="variant-active" className="absolute inset-0 rounded-md bg-surface shadow-soft" />}
                    <Icon size={12} className="relative" />
                    <span className="relative">{platformLabel(x.name)}</span>
                  </button>
                </Tip>
              );
            })}
          </div>
        )}
        {src && preview && (
          <Badge className="ml-1 hidden 2xl:inline-flex" tone={preview.stale ? 'warn' : 'success'}>
            podgląd v{preview.version ?? '?'}
          </Badge>
        )}
        <Tip text={auto ? 'Auto-podgląd włączony: po każdej zmianie osi podgląd przelicza się sam' : 'Auto-podgląd wyłączony: odświeżasz ręcznie'}>
          <button
            onClick={toggleAuto}
            aria-pressed={auto}
            className={cx(
              'ml-1 flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10.5px] font-semibold cursor-pointer',
              auto ? 'bg-success/12 text-success' : 'bg-surface-3 text-muted hover:text-text',
            )}
          >
            <RefreshCw size={11} className={auto && (activeRender || rendering) ? 'animate-spin' : ''} /> Auto
          </button>
        </Tip>
      </div>
    </div>
  );
}
