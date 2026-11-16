// Oś czasu: linijka z podziałką, ścieżki V1-V3 / A1-A5, przejścia, markery, playhead, zoom, zakres IN/OUT.
import { AnimatePresence, motion } from 'framer-motion';
import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { agentMeta } from '../../lib/agents';
import {
  MARKER_COLOR,
  MARKER_LABEL,
  ROLE_COLOR,
  ROLE_LABEL,
  TRACK_LABEL,
  TRACKS,
  TRANSITION_LABEL,
} from '../../lib/constants';
import { clamp, cx, tc } from '../../lib/format';
import { Clapperboard, Maximize2, Redo2, Repeat, Scissors, Undo2, WandSparkles, X } from '../../lib/icons';
import { player, usePlayer } from '../../store/player';
import { useProject } from '../../store/project';
import { ui } from '../../store/ui';
import type { AClip, AnyClip, MediaItem, TitleClip, Transition, VClip } from '../../types';
import { Badge, Button, IconButton, Tip } from '../ui';

const PAD = 10;
const RULER_H = 30;
const LABEL_W = 96;
const TRACK_H: Record<string, number> = { V1: 36, V2: 30, V3: 28, A1: 28, A2: 22, A3: 26, A4: 22, A5: 24 };
const MAX_PPS = 800;

const isTitle = (c: AnyClip): c is TitleClip => typeof (c as TitleClip).text === 'string';
const isV = (k: string) => k.startsWith('V');

export function clipEnd(c: AnyClip): number {
  if (typeof c.end === 'number') return c.end;
  if (isTitle(c)) return c.start + (c.duration || 0);
  const v = c as VClip;
  if (typeof v.duration === 'number') return c.start + v.duration;
  const src = Math.max(0, (v.src_out || 0) - (v.src_in || 0));
  return c.start + src / Math.max(0.05, (v as VClip).speed || 1);
}

function clipRole(track: string, c: AnyClip): string {
  if (isTitle(c)) return 'title';
  return (c as VClip | AClip).role || (isV(track) ? 'a_roll' : 'dialog');
}

function clipLabel(c: AnyClip, media: Record<string, MediaItem>): string {
  if (isTitle(c)) return c.text;
  const lbl = (c as VClip).label;
  if (lbl) return lbl;
  const mid = (c as VClip).media_id;
  if (mid && media[mid]) return media[mid].name;
  const p = (c as AClip).path;
  if (p) return p.split(/[\\/]/).pop() || p;
  return ROLE_LABEL[clipRole('', c)] || 'Klip';
}

const STEPS_BASE = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200];

function tickSteps(zoom: number, fps: number) {
  const steps = [1 / fps, 2 / fps, 5 / fps, 10 / fps, ...STEPS_BASE].filter((s, i, a) => a.indexOf(s) === i).sort((a, b) => a - b);
  const major = steps.find((s) => s * zoom >= 90) ?? 1200;
  const minor = [...steps].reverse().find((s) => s < major && s * zoom >= 10 && Math.abs(major / s - Math.round(major / s)) < 1e-6) ?? major;
  return { major, minor };
}

interface Hover {
  track: string;
  clip?: AnyClip;
  transition?: Transition;
  x: number;
  y: number;
}

type DragMode = null | { kind: 'pending'; t0: number; x0: number } | { kind: 'range'; t0: number } | { kind: 'in' } | { kind: 'out' } | { kind: 'scrub' };

export function TimelinePanel({ onUndo, onRedo }: { onUndo: () => void; onRedo: () => void }) {
  const tl = useProject((s) => s.timeline);
  const loaded = useProject((s) => s.timelineLoaded);
  const mediaList = useProject((s) => s.media);
  const versions = useProject((s) => s.versions);
  const media = useMemo(() => Object.fromEntries(mediaList.map((m) => [m.id, m])), [mediaList]);
  const fps = tl?.fps || 30;
  const time = usePlayer((s) => s.time);
  const playing = usePlayer((s) => s.playing);
  const range = usePlayer((s) => s.range);
  const loop = usePlayer((s) => s.loop);
  const pDuration = usePlayer((s) => s.duration);
  const duration = Math.max(tl?.duration || 0, pDuration || 0, 10);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [viewW, setViewW] = useState(800);
  const [scrollLeft, setScrollLeft] = useState(0);
  const [pps, setPps] = useState<number | null>(null);
  const fitPps = Math.max(0.5, (viewW - PAD * 2 - 8) / duration);
  const zoom = pps ?? fitPps;
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  const pendingScroll = useRef<number | null>(null);
  const contentW = Math.max(viewW, duration * zoom + PAD * 2 + 40);
  const [hover, setHover] = useState<Hover | null>(null);
  const drag = useRef<DragMode>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setViewW(e.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useLayoutEffect(() => {
    if (pendingScroll.current != null && scrollRef.current) {
      scrollRef.current.scrollLeft = pendingScroll.current;
      pendingScroll.current = null;
    }
  });

  // Ctrl + kółko = zoom wokół kursora; kółko = przewijanie poziome
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      const rect = el.getBoundingClientRect();
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        const mx = e.clientX - rect.left;
        const z = zoomRef.current;
        const tAt = (mx + el.scrollLeft - PAD) / z;
        const nz = clamp(z * Math.exp(-e.deltaY * 0.0018), Math.min(fitPps, 1), MAX_PPS);
        pendingScroll.current = Math.max(0, tAt * nz + PAD - mx);
        setPps(nz <= fitPps * 1.001 ? null : nz);
      } else if (Math.abs(e.deltaY) > Math.abs(e.deltaX) && el.scrollWidth > el.clientWidth) {
        e.preventDefault();
        el.scrollLeft += e.deltaY;
      }
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [fitPps]);

  // playhead podąża za odtwarzaniem
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !playing) return;
    const x = PAD + time * zoom;
    if (x < el.scrollLeft + 20 || x > el.scrollLeft + el.clientWidth - 40) {
      el.scrollLeft = Math.max(0, x - el.clientWidth * 0.15);
    }
  }, [time, playing, zoom]);

  const tAt = useCallback(
    (clientX: number) => {
      const el = scrollRef.current;
      if (!el) return 0;
      const rect = el.getBoundingClientRect();
      return clamp((clientX - rect.left + el.scrollLeft - PAD) / zoomRef.current, 0, duration);
    },
    [duration],
  );

  // ---------------------------------------------------------------- interakcje myszy
  const onRulerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { kind: 'pending', t0: tAt(e.clientX), x0: e.clientX };
  };
  const onTracksDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    const t = tAt(e.clientX);
    if (e.shiftKey) {
      drag.current = { kind: 'range', t0: range ? (Math.abs(t - range.in) < Math.abs(t - range.out) ? range.out : range.in) : t };
      player.setRange({ in: (drag.current as { t0: number }).t0, out: t });
    } else {
      drag.current = { kind: 'scrub' };
      player.seek(t);
    }
  };
  const onHandleDown = (which: 'in' | 'out') => (e: React.PointerEvent) => {
    e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { kind: which };
  };
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const t = tAt(e.clientX);
    if (d.kind === 'pending') {
      if (Math.abs(e.clientX - d.x0) > 3) {
        drag.current = { kind: 'range', t0: d.t0 };
        player.setRange({ in: d.t0, out: t });
      }
    } else if (d.kind === 'range') {
      player.setRange({ in: d.t0, out: t });
    } else if (d.kind === 'in' && range) {
      player.setRange({ in: Math.min(t, range.out - 1 / fps), out: range.out });
    } else if (d.kind === 'out' && range) {
      player.setRange({ in: range.in, out: Math.max(t, range.in + 1 / fps) });
    } else if (d.kind === 'scrub') {
      player.seek(t);
    }
  };
  const onUp = (e: React.PointerEvent) => {
    const d = drag.current;
    drag.current = null;
    if (d?.kind === 'pending') player.seek(d.t0);
    try {
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
    } catch {
      /* ignoruj */
    }
  };

  // ---------------------------------------------------------------- podziałka
  const { major, minor } = tickSteps(zoom, fps);
  const visT0 = Math.max(0, (scrollLeft - PAD) / zoom);
  const visT1 = (scrollLeft + viewW) / zoom;
  const ticks = useMemo(() => {
    const out: { t: number; major: boolean }[] = [];
    const start = Math.floor(visT0 / minor) * minor;
    for (let t = start, n = 0; t <= Math.min(visT1, duration + major) && n < 800; t += minor, n++) {
      const tt = Math.round(t * 1000) / 1000;
      out.push({ t: tt, major: Math.abs(tt / major - Math.round(tt / major)) < 1e-4 });
    }
    return out;
  }, [visT0, visT1, minor, major, duration]);

  const tracks = tl?.tracks || {};
  const v1 = (tracks.V1 || []) as VClip[];
  const cuts = useMemo(() => v1.map((c) => c.start).filter((t) => t > 0.01), [v1]);
  const isEmpty = !tl || TRACKS.every((k) => !(tracks[k] || []).length);
  const tracksH = TRACKS.reduce((s, k) => s + TRACK_H[k], 0);
  const x = (t: number) => PAD + t * zoom;
  const inView = (s: number, e: number) => x(e) >= scrollLeft - 200 && x(s) <= scrollLeft + viewW + 200;

  const zoomSlider = Math.round((Math.log(zoom / fitPps) / Math.log(Math.max(1.0001, MAX_PPS / fitPps))) * 100);
  const setZoomSlider = (v: number) => {
    const el = scrollRef.current;
    const nz = fitPps * Math.pow(MAX_PPS / fitPps, v / 100);
    if (el) {
      // zoom wokół playheada
      const px = x(time) - el.scrollLeft;
      pendingScroll.current = Math.max(0, PAD + time * nz - px);
    }
    setPps(v <= 0 ? null : nz);
  };

  const canUndo = (versions?.versions?.length || 0) > 1 || (tl?.version || 0) > 1;
  const canRedo = (versions?.redo?.length || 0) > 0;

  return (
    <div className="flex h-full min-h-0 flex-col border-t border-border bg-surface">
      {/* pasek narzędzi osi */}
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border px-3">
        <div className="flex items-center gap-2 text-[12.5px] font-semibold">
          <Scissors size={14} className="text-accent" /> Oś czasu
        </div>
        {tl && <Badge tone="accent">v{tl.version}</Badge>}
        <span className="tabular text-[11.5px] text-muted">{tc(tl?.duration || 0, fps)}</span>
        {tl?.note && (
          <span className="hidden max-w-[260px] truncate text-[11.5px] text-subtle 2xl:inline" title={tl.note}>
            · {tl.note}
          </span>
        )}
        <div className="mx-1 h-4 w-px bg-border" />
        <IconButton icon={Undo2} label="Cofnij (Ctrl+Z)" size="sm" onClick={onUndo} disabled={!canUndo} />
        <IconButton icon={Redo2} label="Ponów (Ctrl+Shift+Z)" size="sm" onClick={onRedo} disabled={!canRedo} />
        <div className="flex-1" />
        <AnimatePresence>
          {range && (
            <motion.div initial={{ opacity: 0, x: 6 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }} className="flex items-center gap-1">
              <span className="tabular rounded-md bg-accent/10 px-2 py-0.5 text-[11.5px] font-medium text-accent">
                [{tc(range.in, fps)} – {tc(range.out, fps)}]
              </span>
              <IconButton icon={X} label="Usuń zaznaczenie (Esc)" size="sm" onClick={() => player.setRange(null)} />
            </motion.div>
          )}
        </AnimatePresence>
        <IconButton icon={Repeat} label="Pętla zakresu (L)" size="sm" active={loop} onClick={player.toggleLoop} disabled={!range} />
        <div className="mx-1 h-4 w-px bg-border" />
        <Tip text="Zoom (Ctrl + kółko myszy)">
          <input
            type="range"
            min={0}
            max={100}
            value={clamp(isFinite(zoomSlider) ? zoomSlider : 0, 0, 100)}
            onChange={(e) => setZoomSlider(Number(e.target.value))}
            className="range w-28"
            aria-label="Zoom osi czasu"
          />
        </Tip>
        <IconButton icon={Maximize2} label="Dopasuj do okna" size="sm" onClick={() => setPps(null)} />
      </div>

      <div className="relative flex min-h-0 flex-1">
        {/* etykiety ścieżek */}
        <div className="shrink-0 border-r border-border bg-surface" style={{ width: LABEL_W }}>
          <div style={{ height: RULER_H }} className="flex items-center border-b border-border px-2.5">
            <span className="tabular text-[11px] font-semibold text-accent">{tc(time, fps)}</span>
          </div>
          {TRACKS.map((k) => (
            <div key={k} style={{ height: TRACK_H[k] }} className={cx('flex items-center gap-1.5 border-b border-border/70 px-2.5', isV(k) ? 'bg-track-alt' : 'bg-track')}>
              <span className={cx('w-6 text-[11px] font-bold', isV(k) ? 'text-accent' : 'text-success')}>{k}</span>
              <span className="truncate text-[10.5px] text-muted">{TRACK_LABEL[k]}</span>
            </div>
          ))}
        </div>

        {/* obszar przewijany */}
        <div
          ref={scrollRef}
          className="scroll-thin relative min-w-0 flex-1 overflow-x-auto overflow-y-hidden"
          onScroll={(e) => setScrollLeft((e.target as HTMLDivElement).scrollLeft)}
        >
          <div className="relative" style={{ width: contentW, height: RULER_H + tracksH }}>
            {/* linijka */}
            <div
              className="sticky top-0 z-20 cursor-text border-b border-border bg-surface select-none"
              style={{ height: RULER_H }}
              onPointerDown={onRulerDown}
              onPointerMove={onMove}
              onPointerUp={onUp}
            >
              {ticks.map((tk) => (
                <div key={tk.t} className="absolute bottom-0" style={{ left: x(tk.t) }}>
                  <div className={cx('w-px', tk.major ? 'h-2.5 bg-border-strong' : 'h-1.5 bg-border')} />
                  {tk.major && (
                    <div className="tabular absolute bottom-3 -translate-x-1/2 text-[10px] whitespace-nowrap text-subtle">
                      {major >= 1 ? tc(tk.t, fps).replace(/\.\d+$/, '') : tc(tk.t, fps)}
                    </div>
                  )}
                </div>
              ))}
              {/* znaczniki cięć */}
              {cuts.map((t, i) => (
                <div key={i} className="absolute bottom-0 h-1.5 w-[2px] -translate-x-1/2 rounded-t bg-accent/70" style={{ left: x(t) }} />
              ))}
              {/* markery */}
              {(tl?.markers || []).map((m, i) => {
                const color = m.color || MARKER_COLOR[m.kind] || '#7c5cff';
                return (
                  <Tip key={i} text={`${MARKER_LABEL[m.kind] || m.kind}: ${m.label} · ${tc(m.t, fps)}`}>
                    <button
                      className="absolute top-0.5 flex -translate-x-[1px] items-center gap-1 rounded-r-md rounded-bl-none py-px pr-1.5 pl-1 text-[9.5px] font-semibold text-white cursor-pointer"
                      style={{ left: x(m.t), background: color }}
                      onPointerDown={(e) => e.stopPropagation()}
                      onClick={() => player.seek(m.t)}
                    >
                      {zoom * 4 > 30 ? m.label || MARKER_LABEL[m.kind] : (MARKER_LABEL[m.kind] || m.kind).slice(0, 1)}
                    </button>
                  </Tip>
                );
              })}
              {/* zakres na linijce */}
              {range && (
                <div className="absolute top-0 bottom-0 bg-accent/20" style={{ left: x(range.in), width: Math.max(2, (range.out - range.in) * zoom) }}>
                  <div
                    className="absolute top-0 -left-[5px] h-full w-[10px] cursor-ew-resize"
                    onPointerDown={onHandleDown('in')}
                    onPointerMove={onMove}
                    onPointerUp={onUp}
                  >
                    <div className="mx-auto h-full w-[3px] rounded bg-accent" />
                  </div>
                  <div
                    className="absolute top-0 -right-[5px] h-full w-[10px] cursor-ew-resize"
                    onPointerDown={onHandleDown('out')}
                    onPointerMove={onMove}
                    onPointerUp={onUp}
                  >
                    <div className="mx-auto h-full w-[3px] rounded bg-accent" />
                  </div>
                </div>
              )}
            </div>

            {/* ścieżki */}
            <div
              className="relative select-none"
              style={{ height: tracksH }}
              onPointerDown={onTracksDown}
              onPointerMove={onMove}
              onPointerUp={onUp}
              onMouseLeave={() => setHover(null)}
            >
              {TRACKS.map((k) => {
                const items = (tracks[k] || []) as AnyClip[];
                const h = TRACK_H[k];
                return (
                  <div key={k} className={cx('relative border-b border-border/70', isV(k) ? 'bg-track-alt' : 'bg-track')} style={{ height: h }}>
                    {items.map((c) => {
                      const s = c.start;
                      const e = clipEnd(c);
                      if (!inView(s, e)) return null;
                      const role = clipRole(k, c);
                      const color = ROLE_COLOR[role] || '#7c5cff';
                      const w = Math.max(2, (e - s) * zoom - 1);
                      const vc = c as VClip;
                      const speed = !isTitle(c) && isV(k) && vc.speed && Math.abs(vc.speed - 1) > 0.01 ? vc.speed : null;
                      const fx = !isTitle(c) && isV(k) && (vc.effects || []).filter((f) => f.enabled !== false).length;
                      return (
                        <div
                          key={c.id}
                          className="absolute top-[3px] bottom-[3px] overflow-hidden rounded-[6px] border transition-[filter] hover:brightness-110"
                          style={{
                            left: x(s),
                            width: w,
                            background: isV(k)
                              ? `linear-gradient(180deg, color-mix(in oklab, ${color} 92%, #fff), ${color})`
                              : `repeating-linear-gradient(90deg, color-mix(in oklab, ${color} 70%, transparent) 0 2px, color-mix(in oklab, ${color} 45%, transparent) 2px 4px)`,
                            borderColor: `color-mix(in oklab, ${color} 70%, #000)`,
                          }}
                          onMouseMove={(ev) => setHover({ track: k, clip: c, x: ev.clientX, y: ev.clientY })}
                          onMouseLeave={() => setHover(null)}
                          onDoubleClick={(ev) => {
                            ev.stopPropagation();
                            player.setRange({ in: s, out: e });
                          }}
                        >
                          {w > 26 && (
                            <div className={cx('flex h-full items-center gap-1 px-1.5 text-[10.5px] font-medium whitespace-nowrap', isV(k) ? 'text-white' : 'text-text')}>
                              {fx ? <WandSparkles size={10} className="shrink-0" /> : null}
                              <span className="truncate">{clipLabel(c, media)}</span>
                              {speed && <span className="shrink-0 rounded bg-black/25 px-1 text-[9px]">{Math.round(speed * 100)}%</span>}
                            </div>
                          )}
                        </div>
                      );
                    })}
                    {/* przejścia na V1 */}
                    {k === 'V1' &&
                      (tl?.transitions || [])
                        .filter((tr) => tr.kind !== 'cut')
                        .map((tr) => {
                          const w = Math.max(10, tr.duration * zoom);
                          if (!inView(tr.at - tr.duration, tr.at + tr.duration)) return null;
                          return (
                            <div
                              key={tr.id}
                              className="absolute top-[2px] bottom-[2px] z-10 flex items-center justify-center rounded-md border border-white/60 bg-white/35 backdrop-blur-[1px]"
                              style={{ left: x(tr.at) - w / 2, width: w }}
                              onMouseMove={(ev) => {
                                ev.stopPropagation();
                                setHover({ track: k, transition: tr, x: ev.clientX, y: ev.clientY });
                              }}
                              onMouseLeave={() => setHover(null)}
                            >
                              <svg viewBox="0 0 10 10" className="h-2.5 w-2.5 text-white drop-shadow">
                                <path d="M0 0 L10 10 M10 0 L0 10" stroke="currentColor" strokeWidth="1.6" />
                              </svg>
                            </div>
                          );
                        })}
                  </div>
                );
              })}

              {/* linie markerów */}
              {(tl?.markers || []).map((m, i) => (
                <div key={i} className="pointer-events-none absolute top-0 bottom-0 w-px opacity-40" style={{ left: x(m.t), background: m.color || MARKER_COLOR[m.kind] || '#7c5cff' }} />
              ))}

              {/* zakres */}
              {range && (
                <div
                  className="pointer-events-none absolute top-0 bottom-0 border-x-2 border-accent bg-accent/10"
                  style={{ left: x(range.in), width: Math.max(2, (range.out - range.in) * zoom) }}
                />
              )}

              {isEmpty && loaded && (
                <div className="pointer-events-none absolute inset-0 flex items-center justify-center" style={{ left: scrollLeft, width: viewW }}>
                  <div className="pointer-events-auto flex items-center gap-3 rounded-xl border border-border bg-surface/95 px-4 py-2.5 shadow-card backdrop-blur">
                    <Clapperboard size={16} className="text-accent" />
                    <span className="text-[12.5px] text-muted">Oś czasu jest pusta — zleć montaż lub poproś reżysera w czacie.</span>
                    <Button size="xs" variant="primary" onPointerDown={(e) => e.stopPropagation()} onClick={() => ui.openWizard(true)}>
                      Zmontuj film
                    </Button>
                  </div>
                </div>
              )}
            </div>

            {/* playhead */}
            <div className="pointer-events-none absolute top-0 bottom-0 z-30" style={{ left: x(time) }}>
              <div className="absolute top-0 -left-[6px] h-0 w-0 border-x-[6px] border-t-[8px] border-x-transparent border-t-danger" />
              <div className="absolute top-0 bottom-0 -left-px w-[2px] bg-danger shadow-[0_0_6px_rgba(239,68,68,0.5)]" />
            </div>
          </div>
        </div>
      </div>

      {hover && <ClipTooltip hover={hover} media={media} fps={fps} />}
    </div>
  );
}

function ClipTooltip({ hover, media, fps }: { hover: Hover; media: Record<string, MediaItem>; fps: number }) {
  const W = 300;
  const left = Math.min(window.innerWidth - W - 12, hover.x + 14);
  const top = Math.max(8, hover.y - 14);
  let body: React.ReactNode = null;
  if (hover.transition) {
    const tr = hover.transition;
    body = (
      <>
        <div className="text-[12.5px] font-semibold">Przejście: {TRANSITION_LABEL[tr.kind] || tr.kind}</div>
        <div className="tabular text-[11px] text-white/60">
          {tc(tr.at, fps)} · {tr.duration.toFixed(2)} s
        </div>
        {tr.reason && <Reason text={tr.reason} />}
      </>
    );
  } else if (hover.clip) {
    const c = hover.clip;
    const role = clipRole(hover.track, c);
    const e = clipEnd(c);
    const vc = c as VClip;
    const ac = c as AClip;
    const m = !isTitle(c) && vc.media_id ? media[vc.media_id] : null;
    body = (
      <>
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: ROLE_COLOR[role] }} />
          <span className="truncate text-[12.5px] font-semibold">{clipLabel(c, media)}</span>
        </div>
        <div className="mt-0.5 text-[11px] text-white/60">
          {hover.track} · {ROLE_LABEL[role] || role}
          {m && m.name !== clipLabel(c, media) ? ` · ${m.name}` : ''}
        </div>
        <div className="tabular mt-1.5 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[11px]">
          <span className="text-white/50">Na osi</span>
          <span>
            {tc(c.start, fps)} – {tc(e, fps)}
          </span>
          {!isTitle(c) && (
            <>
              <span className="text-white/50">W źródle</span>
              <span>
                {tc(vc.src_in, fps)} – {tc(vc.src_out, fps)}
              </span>
            </>
          )}
          {isV(hover.track) && !isTitle(c) && vc.speed !== 1 && (
            <>
              <span className="text-white/50">Prędkość</span>
              <span>{Math.round((vc.speed || 1) * 100)}%</span>
            </>
          )}
          {!isV(hover.track) && (
            <>
              <span className="text-white/50">Głośność</span>
              <span>
                {ac.gain_db >= 0 ? '+' : ''}
                {(ac.gain_db || 0).toFixed(1)} dB{ac.duck ? ' · ducking' : ''}
              </span>
            </>
          )}
          {isV(hover.track) && !isTitle(c) && vc.grade?.look && (
            <>
              <span className="text-white/50">Look</span>
              <span>{vc.grade.look}</span>
            </>
          )}
        </div>
        {isV(hover.track) && !isTitle(c) && (vc.effects || []).length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {(vc.effects || []).map((f, i) => (
              <span key={i} className="rounded bg-white/10 px-1.5 py-px text-[10px]">
                {f.type}
                {f.status && f.status !== 'done' ? ` (${f.status})` : ''}
              </span>
            ))}
          </div>
        )}
        {(c as VClip).reason && <Reason text={(c as VClip).reason} />}
        <div className="mt-1.5 text-[10px] text-white/40">Dwuklik = zaznacz zakres klipu</div>
      </>
    );
  }
  return createPortal(
    <div className="pointer-events-none fixed z-[250] rounded-xl bg-[#14141c]/95 px-3 py-2.5 text-white shadow-pop backdrop-blur" style={{ left, top, width: W, transform: 'translateY(-100%)' }}>
      {body}
    </div>,
    document.body,
  );
}

function Reason({ text }: { text: string }) {
  const m = agentMeta('director');
  return (
    <div className="mt-2 rounded-lg bg-white/[0.06] px-2 py-1.5">
      <div className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: m.color }}>
        Uzasadnienie reżysera
      </div>
      <div className="mt-0.5 text-[11.5px] leading-snug text-white/85">{text}</div>
    </div>
  );
}
