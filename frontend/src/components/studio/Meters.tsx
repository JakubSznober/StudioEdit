// Mierniki: LUFS (łuk), true peak, LRA, VRAM
import { motion } from 'framer-motion';
import { clamp, cx } from '../../lib/format';
import { AudioWaveform } from '../../lib/icons';
import { useProject } from '../../store/project';
import { Sparkline, VramBar, VramOwner } from '../system';

const ARC_MIN = -36;
const ARC_MAX = 0;

function polar(cx0: number, cy0: number, r: number, deg: number) {
  const a = ((deg - 180) * Math.PI) / 180;
  return [cx0 + r * Math.cos(a), cy0 + r * Math.sin(a)];
}

function arcPath(cx0: number, cy0: number, r: number, d0: number, d1: number) {
  const [x0, y0] = polar(cx0, cy0, r, d0);
  const [x1, y1] = polar(cx0, cy0, r, d1);
  const large = d1 - d0 > 180 ? 1 : 0;
  return `M${x0.toFixed(2)},${y0.toFixed(2)} A${r},${r} 0 ${large} 1 ${x1.toFixed(2)},${y1.toFixed(2)}`;
}

const toDeg = (lufs: number) => ((clamp(lufs, ARC_MIN, ARC_MAX) - ARC_MIN) / (ARC_MAX - ARC_MIN)) * 180;

export function LufsGauge({ lufs, target }: { lufs: number | null; target: number }) {
  const W = 220;
  const R = 86;
  const C = { x: W / 2, y: 100 };
  const diff = lufs == null ? null : lufs - target;
  const color = diff == null ? 'var(--c-subtle)' : Math.abs(diff) <= 1 ? 'var(--c-success)' : Math.abs(diff) <= 2.5 ? 'var(--c-warn)' : 'var(--c-danger)';
  const deg = lufs == null ? 0 : toDeg(lufs);
  const tDeg = toDeg(target);
  const [tx0, ty0] = polar(C.x, C.y, R - 12, tDeg);
  const [tx1, ty1] = polar(C.x, C.y, R + 8, tDeg);
  return (
    <div className="flex flex-col items-center">
      <svg viewBox={`0 0 ${W} 116`} className="w-full max-w-[240px]">
        <defs>
          <linearGradient id="lufs-grad" x1="0" x2="1">
            <stop offset="0" stopColor="var(--c-info)" />
            <stop offset="0.7" stopColor="var(--c-success)" />
            <stop offset="0.9" stopColor="var(--c-warn)" />
            <stop offset="1" stopColor="var(--c-danger)" />
          </linearGradient>
        </defs>
        <path d={arcPath(C.x, C.y, R, 0.01, 180)} stroke="var(--c-surface-3)" strokeWidth="12" fill="none" strokeLinecap="round" />
        {lufs != null && (
          <motion.path
            d={arcPath(C.x, C.y, R, 0.01, 180)}
            stroke="url(#lufs-grad)"
            strokeWidth="12"
            fill="none"
            strokeLinecap="round"
            pathLength={1}
            initial={{ pathLength: 0 }}
            animate={{ pathLength: Math.max(0.005, deg / 180) }}
            transition={{ type: 'spring', stiffness: 60, damping: 16 }}
          />
        )}
        {[-36, -30, -24, -18, -12, -6, 0].map((v) => {
          const [a0, b0] = polar(C.x, C.y, R - 9, toDeg(v));
          const [a1, b1] = polar(C.x, C.y, R - 15, toDeg(v));
          const [lx, ly] = polar(C.x, C.y, R - 25, toDeg(v));
          return (
            <g key={v}>
              <line x1={a0} y1={b0} x2={a1} y2={b1} stroke="var(--c-border-strong)" strokeWidth="1.2" />
              <text x={lx} y={ly + 3} textAnchor="middle" fontSize="8.5" fill="var(--c-subtle)">
                {v}
              </text>
            </g>
          );
        })}
        <line x1={tx0} y1={ty0} x2={tx1} y2={ty1} stroke="var(--c-text)" strokeWidth="2.2" strokeLinecap="round" />
        <text x={C.x} y={C.y - 22} textAnchor="middle" fontSize="26" fontWeight="650" fill={color} className="tabular">
          {lufs == null ? '—' : lufs.toFixed(1)}
        </text>
        <text x={C.x} y={C.y - 6} textAnchor="middle" fontSize="10" fill="var(--c-muted)">
          LUFS zintegr. · cel {target}
        </text>
      </svg>
      <div className={cx('-mt-1 text-[11.5px] font-medium')} style={{ color }}>
        {diff == null ? 'Brak pomiaru' : Math.abs(diff) <= 1 ? 'W normie platformy' : diff > 0 ? `Za głośno o ${diff.toFixed(1)} LU` : `Za cicho o ${Math.abs(diff).toFixed(1)} LU`}
      </div>
    </div>
  );
}

function VBar({ label, value, min, max, unit, limit, format }: { label: string; value: number | null; min: number; max: number; unit: string; limit?: number; format?: (v: number) => string }) {
  const r = value == null ? 0 : (clamp(value, min, max) - min) / (max - min);
  const lr = limit == null ? null : (clamp(limit, min, max) - min) / (max - min);
  const over = value != null && limit != null && value > limit;
  return (
    <div className="flex flex-col items-center gap-1.5">
      <div className="relative h-[110px] w-4 overflow-hidden rounded-full bg-surface-3">
        <motion.div
          className="absolute inset-x-0 bottom-0 rounded-full"
          style={{ background: over ? 'var(--c-danger)' : 'linear-gradient(0deg, var(--c-success), var(--c-warn))' }}
          initial={false}
          animate={{ height: `${r * 100}%` }}
          transition={{ type: 'spring', stiffness: 90, damping: 18 }}
        />
        {lr != null && <div className="absolute inset-x-0 h-[2px] bg-text" style={{ bottom: `${lr * 100}%` }} />}
      </div>
      <div className="text-center">
        <div className={cx('tabular text-[13px] font-semibold', over && 'text-danger')}>{value == null ? '—' : format ? format(value) : value.toFixed(1)}</div>
        <div className="text-[10px] text-muted">
          {label} {unit}
        </div>
      </div>
    </div>
  );
}

export function LoudnessMeter() {
  const l = useProject((s) => s.loudness);
  const mix = useProject((s) => s.timeline?.mix);
  const target = mix?.target_lufs ?? -14;
  const tp = mix?.true_peak ?? -1;
  return (
    <div className="flex h-full items-center gap-5">
      <div className="w-[220px] shrink-0">
        <LufsGauge lufs={l?.lufs ?? null} target={target} />
      </div>
      <VBar label="True peak" unit="dBTP" value={l?.true_peak ?? null} min={-24} max={0} limit={tp} />
      <VBar label="LRA" unit="LU" value={l?.lra ?? null} min={0} max={20} />
      <div className="min-w-0 flex-1">
        {l?.momentary?.length ? (
          <>
            <div className="mb-1 text-[11px] font-medium text-muted">Głośność chwilowa (momentary)</div>
            <Sparkline values={l.momentary.map((v) => (clamp(v, -50, 0) + 50) / 50)} color="var(--c-success)" height={70} />
          </>
        ) : (
          <div className="flex items-center gap-2 text-[12px] text-muted">
            <AudioWaveform size={15} className="text-success" />
            {l ? 'Pomiar po ostatnim miksie.' : 'Pomiar pojawi się po miksie dźwięku (Reżyser Dźwięku).'} Cel platformy: {target} LUFS, true peak ≤ {tp} dBTP.
          </div>
        )}
      </div>
    </div>
  );
}

export function MetersTab() {
  return (
    <div className="grid h-full grid-cols-1 gap-4 p-4 lg:grid-cols-[minmax(240px,0.8fr)_2fr]">
      <div className="space-y-3">
        <VramBar big />
        <VramOwner />
      </div>
      <div className="rounded-xl border border-border p-3">
        <LoudnessMeter />
      </div>
    </div>
  );
}
