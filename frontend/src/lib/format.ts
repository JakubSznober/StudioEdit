// Formatowanie czasu, rozmiarów i dat (po polsku)

const pad = (n: number, w = 2) => String(Math.floor(Math.abs(n))).padStart(w, '0');

/** Czas osi w formacie mm:ss.ff (ff = numer klatki w sekundzie) lub h:mm:ss.ff */
export function tc(sec: number | null | undefined, fps = 30): string {
  if (sec == null || !isFinite(sec)) return '--:--.--';
  const neg = sec < 0;
  const f = Math.max(1, Math.round(fps));
  let totalFrames = Math.round(Math.abs(sec) * f);
  const ff = totalFrames % f;
  totalFrames = Math.floor(totalFrames / f);
  const s = totalFrames % 60;
  const m = Math.floor(totalFrames / 60) % 60;
  const h = Math.floor(totalFrames / 3600);
  const core = h > 0 ? `${h}:${pad(m)}:${pad(s)}.${pad(ff)}` : `${pad(m)}:${pad(s)}.${pad(ff)}`;
  return neg ? `-${core}` : core;
}

/** Krótki czas trwania: 4:05, 1:02:10, 12 s */
export function dur(sec: number | null | undefined): string {
  if (sec == null || !isFinite(sec) || sec <= 0) return '0:00';
  const t = Math.round(sec);
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** Długi opis czasu: „4 min 05 s” */
export function durLong(sec: number | null | undefined): string {
  if (sec == null || !isFinite(sec) || sec <= 0) return '—';
  const t = Math.round(sec);
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  if (h) return `${h} h ${pad(m)} min`;
  if (m) return `${m} min ${pad(s)} s`;
  return `${s} s`;
}

export function gb(v: number | null | undefined, digits = 1): string {
  if (v == null || !isFinite(v)) return '—';
  if (v < 1 && v > 0) return `${Math.round(v * 1024)} MB`;
  return `${v.toFixed(digits)} GB`;
}

export function mb(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return '—';
  if (v >= 1024) return `${(v / 1024).toFixed(2)} GB`;
  return `${v.toFixed(v < 10 ? 1 : 0)} MB`;
}

export function bytes(v: number | null | undefined): string {
  if (v == null || !isFinite(v)) return '—';
  if (v < 1024) return `${v} B`;
  if (v < 1024 ** 2) return `${(v / 1024).toFixed(0)} KB`;
  if (v < 1024 ** 3) return `${(v / 1024 ** 2).toFixed(1)} MB`;
  return `${(v / 1024 ** 3).toFixed(2)} GB`;
}

export function pct(v: number | null | undefined, digits = 0): string {
  if (v == null || !isFinite(v)) return '—';
  return `${(v * 100).toFixed(digits)}%`;
}

/** Znacznik czasu (sekundy unix) → „dziś 14:02” / „12.11 09:15” */
export function when(ts: number | null | undefined): string {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  const now = new Date();
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (d.toDateString() === now.toDateString()) return `dziś ${hm}`;
  const y = new Date(now.getTime() - 86400000);
  if (d.toDateString() === y.toDateString()) return `wczoraj ${hm}`;
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}${d.getFullYear() !== now.getFullYear() ? '.' + d.getFullYear() : ''} ${hm}`;
}

export function clock(ts: number | null | undefined): string {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function ago(ts: number | null | undefined): string {
  if (!ts) return '—';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 10) return 'przed chwilą';
  if (s < 60) return `${Math.round(s)} s temu`;
  if (s < 3600) return `${Math.round(s / 60)} min temu`;
  if (s < 86400) return `${Math.round(s / 3600)} h temu`;
  return when(ts);
}

export function elapsed(s: number | null | undefined): string {
  if (s == null || !isFinite(s)) return '';
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} s`;
  return dur(s);
}

export function plural(n: number, one: string, few: string, many: string): string {
  if (n === 1) return one;
  const d = n % 10;
  const dd = n % 100;
  if (d >= 2 && d <= 4 && !(dd >= 12 && dd <= 14)) return few;
  return many;
}

export const clamp = (v: number, a: number, b: number) => Math.min(b, Math.max(a, v));

export function snap(t: number, fps: number): number {
  const f = Math.max(1, fps);
  return Math.round(t * f) / f;
}

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(' ');
}

/** Parsuje „4:30”, „90”, „1:02:00”, „4 min” → sekundy */
export function parseDuration(s: string): number | null {
  const v = s.trim().toLowerCase();
  if (!v) return null;
  const minMatch = v.match(/^([\d.,]+)\s*(min|m)$/);
  if (minMatch) return parseFloat(minMatch[1].replace(',', '.')) * 60;
  const secMatch = v.match(/^([\d.,]+)\s*(s|sek)$/);
  if (secMatch) return parseFloat(secMatch[1].replace(',', '.'));
  if (v.includes(':')) {
    const parts = v.split(':').map((x) => parseFloat(x.replace(',', '.')));
    if (parts.some((x) => isNaN(x))) return null;
    return parts.reduce((acc, x) => acc * 60 + x, 0);
  }
  const n = parseFloat(v.replace(',', '.'));
  return isNaN(n) ? null : n;
}
