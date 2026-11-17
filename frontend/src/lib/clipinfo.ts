// Opisy zawartości klipów na osi: co widać na ujęciu (z analizy archiwisty), jaki to efekt dźwiękowy.
import type { AClip, AnyClip, MediaItem, TitleClip, VClip } from '../types';

const SFX_DESC: Record<string, string> = {
  swoosh: 'Szybki świst - podkreśla przejście między ujęciami',
  whoosh: 'Szybki świst - podkreśla przejście między ujęciami',
  riser: 'Narastający dźwięk - buduje napięcie przed kulminacją',
  downlifter: 'Opadający dźwięk - rozładowuje napięcie po kulminacji',
  impact: 'Uderzenie - akcentuje najważniejszy moment',
  hit: 'Uderzenie - akcentuje najważniejszy moment',
  click: 'Klik - akcent pod grafikę lub napis',
  ambience: 'Tło przestrzeni (atmosfera miejsca)',
  room: 'Tło przestrzeni (atmosfera miejsca)',
};

const isTitle = (c: AnyClip): c is TitleClip => typeof (c as TitleClip).text === 'string';

/** Opis treści klipu po polsku albo pusty tekst. */
export function clipDescription(c: AnyClip, media: Record<string, MediaItem>): string {
  if (isTitle(c)) return `Napis: „${c.text}”`;
  const a = c as AClip;
  if (a.role === 'sfx') {
    const key = (a.label || (a.path || '').split(/[\\/]/).pop() || '').toLowerCase().replace(/[_\d.]+wav$|\.wav$/, '');
    const hit = Object.keys(SFX_DESC).find((k) => key.startsWith(k));
    return hit ? SFX_DESC[hit] : 'Efekt dźwiękowy';
  }
  if (a.role === 'music') return 'Podkład muzyczny' + (a.duck ? ' (ściszany pod głosem)' : '');
  if (a.role === 'roomtone') return 'Room tone - cisza pomieszczenia wypełniająca przerwy';
  const v = c as VClip;
  const m = v.media_id ? media[v.media_id] : undefined;
  if (!m) return '';
  const t = (v.src_in + v.src_out) / 2;
  const shot = m.analysis?.shots?.find((s) => s.s <= t && t < s.e) || m.analysis?.shots?.[0];
  const parts = [shot?.shot_size, shot?.description].filter(Boolean);
  if (parts.length) return parts.join(': ');
  return (m.analysis?.summary || '').replace(/^\s*\[[\d.\s-]+\]\s*/, '').split(/\n/)[0];
}
