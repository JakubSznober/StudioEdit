// Stałe domenowe (etykiety po polsku)
import type { ModuleState } from '../types';
import type { Tone } from '../components/ui';

export const PLATFORMS: { key: string; label: string; aspect: string }[] = [
  { key: 'youtube', label: 'YouTube', aspect: '16:9' },
  { key: 'tiktok', label: 'TikTok', aspect: '9:16' },
  { key: 'reels', label: 'Reels', aspect: '9:16' },
  { key: 'shorts', label: 'Shorts', aspect: '9:16' },
  { key: 'instagram_feed', label: 'Instagram feed', aspect: '4:5' },
];

export const platformLabel = (k: string) =>
  PLATFORMS.find((p) => p.key === k)?.label || (k === 'master' ? 'Master' : k);

export const STYLES: { key: string; label: string }[] = [
  { key: 'vlog', label: 'Vlog' },
  { key: 'dokument', label: 'Dokument' },
  { key: 'reklama', label: 'Reklama' },
  { key: 'teledysk', label: 'Teledysk' },
  { key: 'rolka', label: 'Rolka' },
  { key: 'podcast', label: 'Podcast' },
  { key: 'tutorial', label: 'Tutorial' },
  { key: 'gaming', label: 'Gaming' },
];

export const SCALES: { key: 'small' | 'medium' | 'massive'; label: string; desc: string }[] = [
  { key: 'small', label: 'Mały', desc: 'Do ~15 min materiału. Najdokładniejsza analiza (4 klatki/ujęcie), szybki montaż.' },
  { key: 'medium', label: 'Średni', desc: 'Do ~2 h materiału. Zbalansowana analiza (3 klatki/ujęcie).' },
  { key: 'massive', label: 'Masywny', desc: '2 h+ materiału. Reżyser hierarchiczny, oszczędna analiza (2 klatki/ujęcie).' },
];

export const MUSIC_MOODS: { key: string; label: string }[] = [
  { key: '', label: 'Automatycznie (wg briefu)' },
  { key: 'energetic', label: 'Energetyczna' },
  { key: 'uplifting', label: 'Pozytywna / podnosząca' },
  { key: 'chill', label: 'Spokojna / chill' },
  { key: 'cinematic', label: 'Filmowa / epicka' },
  { key: 'emotional', label: 'Emocjonalna' },
  { key: 'dark', label: 'Mroczna / napięcie' },
  { key: 'corporate', label: 'Korporacyjna' },
  { key: 'none', label: 'Bez muzyki' },
];

export const BINS: { key: string; label: string }[] = [
  { key: 'A-Roll', label: 'A-Roll' },
  { key: 'B-Roll', label: 'B-Roll' },
  { key: 'Audio', label: 'Audio' },
  { key: 'Muzyka', label: 'Muzyka' },
  { key: 'SFX', label: 'SFX' },
  { key: 'Grafika', label: 'Grafika' },
  { key: 'Wygenerowane', label: 'Wygenerowane' },
  { key: 'Odrzucone_Duble', label: 'Odrzucone duble' },
];

export const FLAGS: Record<string, { label: string; tone: Tone; desc: string }> = {
  NEEDS_EXPOSURE_RECOVERY: { label: 'Ekspozycja', tone: 'warn', desc: 'Prześwietlenie lub niedoświetlenie — wymaga odzyskania' },
  HFR: { label: 'HFR', tone: 'info', desc: 'Wysoki klatkaż — nadaje się do slow-motion' },
  LOG: { label: 'LOG', tone: 'accent', desc: 'Profil LOG — wymaga konwersji kolorów' },
  SHAKY: { label: 'Drgania', tone: 'warn', desc: 'Drgania kamery — kandydat do stabilizacji' },
  BLURRY: { label: 'Nieostre', tone: 'danger', desc: 'Nieostre ujęcia' },
  REJECTED: { label: 'Odrzucone', tone: 'danger', desc: 'Odrzucone przez archiwistę' },
  NOISY: { label: 'Szum', tone: 'warn', desc: 'Zaszumiony obraz lub dźwięk' },
  LOW_AUDIO: { label: 'Cichy dźwięk', tone: 'warn', desc: 'Niski poziom audio' },
  CLIPPING: { label: 'Przester', tone: 'danger', desc: 'Przesterowany dźwięk' },
  NO_AUDIO: { label: 'Bez audio', tone: 'neutral', desc: 'Brak ścieżki dźwiękowej' },
  VERTICAL: { label: 'Pion', tone: 'info', desc: 'Materiał pionowy' },
  HDR: { label: 'HDR', tone: 'accent', desc: 'Materiał HDR' },
  DUPLICATE: { label: 'Duplikat', tone: 'neutral', desc: 'Prawdopodobny duplikat' },
};

export const flagMeta = (f: string) => FLAGS[f] || { label: f.replace(/_/g, ' ').toLowerCase(), tone: 'neutral' as Tone, desc: f };

export const MODULE_STATE: Record<ModuleState, { label: string; tone: Tone; color: string }> = {
  ok: { label: 'Gotowy', tone: 'success', color: 'var(--c-success)' },
  warn: { label: 'Ograniczony', tone: 'warn', color: 'var(--c-warn)' },
  error: { label: 'Błąd', tone: 'danger', color: 'var(--c-danger)' },
  off: { label: 'Wyłączony', tone: 'neutral', color: 'var(--c-subtle)' },
  busy: { label: 'Pracuje', tone: 'accent', color: 'var(--c-accent)' },
};

export const MODULE_NAMES: Record<string, string> = {
  ai_director: 'AI Director',
  ai_chat: 'AI Chat',
  ai_montage: 'AI Montage',
  ai_effects: 'AI Effects',
  ai_colorist: 'AI Colorist',
  ai_audio: 'AI Audio',
  ai_subtitles: 'AI Subtitles',
  ai_analysis: 'AI Analysis',
  region_editor: 'Region Editor',
  export_studio: 'Export Studio',
  premiere_xml: 'Premiere XML',
  ae_jsx: 'After Effects JSX',
  ae_runtime: 'After Effects runtime',
  ai_developer: 'AI Developer',
  ai_qc: 'AI QC Supervisor',
};

export const MODEL_KIND: Record<string, string> = {
  llm: 'LLM',
  vlm: 'Wizja (VLM)',
  stt: 'Mowa → tekst',
  tts: 'Synteza mowy',
  music: 'Muzyka',
  vfx: 'VFX',
  video: 'Generacja wideo',
  detector: 'Detekcja / segmentacja',
  diarization: 'Diaryzacja',
  denoise: 'Odszumianie',
  segmentation: 'Segmentacja',
  detection: 'Detekcja',
  inpainting: 'Inpainting',
  face: 'Twarze',
  video_generation: 'Generacja wideo',
};

export const PROVIDER: Record<string, string> = {
  ollama: 'Ollama',
  hf: 'Hugging Face',
  file: 'Plik wag',
  comfy: 'ComfyUI',
  pip: 'Pakiet Python',
  huggingface: 'Hugging Face',
  piper: 'Piper TTS',
  ultralytics: 'Ultralytics',
  opencv_zoo: 'OpenCV Zoo',
};

export const TRACKS = ['V1', 'V2', 'V3', 'A1', 'A2', 'A3', 'A4', 'A5'] as const;

export const TRACK_LABEL: Record<string, string> = {
  V1: 'A-Roll',
  V2: 'B-Roll',
  V3: 'VFX / Tytuły',
  A1: 'Dialogi',
  A2: 'Room tone',
  A3: 'Muzyka',
  A4: 'SFX',
  A5: 'Lektor',
};

export const ROLE_COLOR: Record<string, string> = {
  a_roll: '#7c5cff',
  b_roll: '#3b82f6',
  generated: '#ec4899',
  vfx: '#f59e0b',
  image: '#14b8a6',
  title: '#a855f7',
  dialog: '#10b981',
  roomtone: '#64748b',
  music: '#06b6d4',
  sfx: '#eab308',
  vo: '#f43f5e',
};

export const ROLE_LABEL: Record<string, string> = {
  a_roll: 'A-Roll',
  b_roll: 'B-Roll',
  generated: 'Wygenerowane',
  vfx: 'VFX',
  image: 'Grafika',
  title: 'Tytuł',
  dialog: 'Dialog',
  roomtone: 'Room tone',
  music: 'Muzyka',
  sfx: 'SFX',
  vo: 'Lektor',
};

export const TRANSITION_LABEL: Record<string, string> = {
  cut: 'Cięcie',
  dissolve: 'Przenikanie',
  dip_black: 'Przez czerń',
  dip_white: 'Przez biel',
  whip: 'Whip pan',
  light_leak: 'Light leak',
  zoom: 'Zoom',
  slide: 'Przesunięcie',
};

export const MARKER_COLOR: Record<string, string> = {
  hook: '#f97316',
  climax: '#ef4444',
  outro: '#6366f1',
  beat: '#06b6d4',
  qc: '#f59e0b',
  note: '#7c5cff',
  info: '#7c5cff',
};

export const MARKER_LABEL: Record<string, string> = {
  hook: 'Hook',
  climax: 'Kulminacja',
  outro: 'Outro',
  beat: 'Beat',
  qc: 'QC',
  note: 'Notatka',
  info: 'Znacznik',
};
