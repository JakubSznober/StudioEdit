// Odtwarzacz: czas (playhead), odtwarzanie, zakres IN/OUT, pętla, porównanie Przed/Po.
// Gdy brak pliku podglądu, czas płynie „wirtualnie” (playhead osi czasu nadal działa).
import { create } from 'zustand';
import type { Range } from '../types';
import { clamp, snap } from '../lib/format';

interface PlayerState {
  time: number;
  playing: boolean;
  duration: number;
  fps: number;
  range: Range | null;
  loop: boolean;
  compare: boolean;
  comparePos: number;
  muted: boolean;
  /** znacznik zmiany seek (do odświeżania klatek Przed/Po) */
  seekSeq: number;
}

export const usePlayer = create<PlayerState>(() => ({
  time: 0,
  playing: false,
  duration: 0,
  fps: 30,
  range: null,
  loop: false,
  compare: false,
  comparePos: 0.5,
  muted: false,
  seekSeq: 0,
}));

const set = usePlayer.setState;
const get = usePlayer.getState;

let video: HTMLVideoElement | null = null;
let raf = 0;
let lastTick = 0;

function tick(now: number) {
  const s = get();
  if (!s.playing) {
    raf = 0;
    return;
  }
  let t: number;
  if (video) {
    t = video.currentTime;
  } else {
    const dt = lastTick ? (now - lastTick) / 1000 : 0;
    t = s.time + dt;
  }
  lastTick = now;
  const r = s.range;
  if (s.loop && r && r.out > r.in && t >= r.out) {
    t = r.in;
    if (video) video.currentTime = t;
  }
  const end = s.duration || (video ? video.duration : 0) || 0;
  if (!video && end && t >= end) {
    t = end;
    set({ time: t, playing: false });
    raf = 0;
    return;
  }
  set({ time: t });
  raf = requestAnimationFrame(tick);
}

function startLoop() {
  if (!raf) {
    lastTick = 0;
    raf = requestAnimationFrame(tick);
  }
}

export const player = {
  register(el: HTMLVideoElement | null) {
    video = el;
    if (el) {
      el.muted = get().muted;
      const t = get().time;
      if (isFinite(t) && Math.abs(el.currentTime - t) > 0.05) {
        try {
          el.currentTime = t;
        } catch {
          /* jeszcze nie załadowany */
        }
      }
    }
  },
  /** zdarzenia z <video> (np. koniec, pauza z natywnych kontrolek) */
  syncFromVideo(playing: boolean) {
    if (!video) return;
    set({ playing, time: video.currentTime });
    if (playing) startLoop();
  },
  setDuration(d: number, fps?: number) {
    set(fps ? { duration: d, fps } : { duration: d });
  },
  seek(t: number, opts: { snapFrame?: boolean } = {}) {
    const s = get();
    const max = s.duration || (video && isFinite(video.duration) ? video.duration : 0) || Number.MAX_SAFE_INTEGER;
    let v = clamp(t, 0, max);
    if (opts.snapFrame !== false) v = snap(v, s.fps);
    if (video) {
      try {
        video.currentTime = v;
      } catch {
        /* ignoruj */
      }
    }
    set({ time: v, seekSeq: s.seekSeq + 1 });
  },
  step(frames: number) {
    const s = get();
    if (s.playing) player.pause();
    player.seek(s.time + frames / Math.max(1, s.fps));
  },
  play() {
    const s = get();
    let t = s.time;
    if (s.loop && s.range && (t < s.range.in || t >= s.range.out)) {
      t = s.range.in;
      player.seek(t);
    } else if (s.duration && t >= s.duration - 0.01) {
      player.seek(0);
    }
    if (video) {
      video.play().catch(() => set({ playing: false }));
    }
    set({ playing: true });
    startLoop();
  },
  pause() {
    if (video) video.pause();
    set({ playing: false, seekSeq: get().seekSeq + 1, time: video ? video.currentTime : get().time });
  },
  toggle() {
    if (get().playing) player.pause();
    else player.play();
  },
  setIn(t?: number) {
    const s = get();
    const v = snap(t ?? s.time, s.fps);
    const out = s.range && s.range.out > v ? s.range.out : Math.min(s.duration || v + 5, v + 5);
    set({ range: { in: v, out: Math.max(out, v + 1 / s.fps) } });
  },
  setOut(t?: number) {
    const s = get();
    const v = snap(t ?? s.time, s.fps);
    const inn = s.range && s.range.in < v ? s.range.in : Math.max(0, v - 5);
    set({ range: { in: Math.min(inn, v - 1 / s.fps), out: v } });
  },
  setRange(r: Range | null) {
    if (!r) return set({ range: null, loop: false });
    const s = get();
    const a = snap(Math.min(r.in, r.out), s.fps);
    const b = snap(Math.max(r.in, r.out), s.fps);
    set({ range: b - a >= 1 / s.fps ? { in: Math.max(0, a), out: b } : null });
  },
  toggleLoop() {
    const s = get();
    const loop = !s.loop;
    set({ loop });
    if (loop && s.range && !s.playing) {
      player.seek(s.range.in);
      player.play();
    }
  },
  toggleCompare() {
    set({ compare: !get().compare });
  },
  setComparePos(p: number) {
    set({ comparePos: clamp(p, 0.02, 0.98) });
  },
  toggleMute() {
    const m = !get().muted;
    if (video) video.muted = m;
    set({ muted: m });
  },
  reset() {
    if (video) video.pause();
    set({ time: 0, playing: false, range: null, loop: false });
  },
};
