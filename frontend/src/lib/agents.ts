import {
  Archive,
  AudioWaveform,
  Bot,
  Clapperboard,
  Cpu,
  Film,
  MessageSquare,
  Palette,
  Settings2,
  ShieldCheck,
  Sparkles,
  WandSparkles,
  type LucideIcon,
} from './icons';
import type { Agent } from '../types';
import { useLive } from '../store/live';

export interface AgentMeta {
  key: string;
  name: string;
  short: string;
  role: string;
  color: string;
  icon: LucideIcon;
}

export const AGENT_ORDER = ['producer', 'archivist', 'director', 'colorist', 'sound', 'vfx', 'qc', 'render'] as const;

const DEFAULT_META: Record<string, AgentMeta> = {
  producer: { key: 'producer', name: 'Producent', short: 'Producent', role: 'Czat i koordynacja', color: '#7c5cff', icon: MessageSquare },
  archivist: { key: 'archivist', name: 'Asystent & Archiwista', short: 'Archiwista', role: 'Analiza materiału', color: '#0ea5e9', icon: Archive },
  director: { key: 'director', name: 'Reżyser Montażu & Narracji', short: 'Reżyser', role: 'Montaż i narracja', color: '#f97316', icon: Clapperboard },
  colorist: { key: 'colorist', name: 'Kolorysta & Lighting Artist', short: 'Kolorysta', role: 'Korekcja barwna', color: '#ec4899', icon: Palette },
  sound: { key: 'sound', name: 'Reżyser Dźwięku & Kompozytor', short: 'Dźwięk', role: 'Audio i napisy', color: '#10b981', icon: AudioWaveform },
  vfx: { key: 'vfx', name: 'Artysta VFX & B-Roll', short: 'VFX', role: 'Efekty i generacja', color: '#f59e0b', icon: WandSparkles },
  qc: { key: 'qc', name: 'Nadzorca Jakości', short: 'QC', role: 'Kontrola jakości', color: '#ef4444', icon: ShieldCheck },
  render: { key: 'render', name: 'Montażysta Online (Render)', short: 'Render', role: 'Render i eksport', color: '#6366f1', icon: Film },
  system: { key: 'system', name: 'System', short: 'System', role: 'Rdzeń', color: '#64748b', icon: Settings2 },
};

const ICONS_BY_NAME: Record<string, LucideIcon> = {
  'message-square': MessageSquare,
  'messages-square': MessageSquare,
  message: MessageSquare,
  chat: MessageSquare,
  archive: Archive,
  clapperboard: Clapperboard,
  palette: Palette,
  'audio-waveform': AudioWaveform,
  audio: AudioWaveform,
  'audio-lines': AudioWaveform,
  sparkles: Sparkles,
  wand: WandSparkles,
  'wand-sparkles': WandSparkles,
  'shield-check': ShieldCheck,
  shield: ShieldCheck,
  film: Film,
  cpu: Cpu,
  bot: Bot,
  settings: Settings2,
};

export function agentMeta(key: string | null | undefined, agentArg?: Agent | null): AgentMeta {
  const k = key || 'system';
  const agent = agentArg ?? useLive.getState().agents[k];
  const base = DEFAULT_META[k] || {
    key: k,
    name: k,
    short: k,
    role: '',
    color: '#64748b',
    icon: Bot,
  };
  if (!agent) return base;
  const iconKey = (agent.icon || '').toLowerCase().replace(/_/g, '-');
  return {
    key: k,
    name: agent.name || base.name,
    short: base.short || agent.name,
    role: agent.role || base.role,
    color: agent.color || base.color,
    icon: ICONS_BY_NAME[iconKey] || base.icon,
  };
}

export const EFFORTS: { key: 'low' | 'medium' | 'high' | 'max'; label: string; desc: string }[] = [
  { key: 'low', label: 'Niski', desc: 'Najszybciej. Mniejsze modele, mniej klatek do analizy — szkic lub podgląd.' },
  { key: 'medium', label: 'Średni', desc: 'Kompromis: dobra jakość przy umiarkowanym czasie pracy.' },
  { key: 'high', label: 'Wysoki', desc: 'Domyślny. Pełne modele i dokładna analiza — jakość produkcyjna.' },
  { key: 'max', label: 'Maksymalny', desc: 'Najwyższa jakość, rozumowanie modeli i wielokrotna weryfikacja. Nawet kilka razy wolniej.' },
];
