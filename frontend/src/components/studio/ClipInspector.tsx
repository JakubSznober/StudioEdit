// Panel ręcznej edycji zaznaczonego klipu: przycinanie, kolejność, głośność, wyciszenie, zanikanie,
// czyszczenie głosu, ducking muzyki, prędkość przebitki, usuwanie.
import { useEffect, useRef, useState } from 'react';
import { errMsg } from '../../api/client';
import { projects as P, type TimelineEdit } from '../../api/endpoints';
import { clipDescription } from '../../lib/clipinfo';
import { ROLE_LABEL } from '../../lib/constants';
import { tc } from '../../lib/format';
import { ChevronLeft, ChevronRight, Trash, Volume2, VolumeX, X } from '../../lib/icons';
import { project } from '../../store/project';
import { ui } from '../../store/ui';
import type { AClip, AnyClip, MediaItem, TitleClip, VClip } from '../../types';
import { Button, IconButton, Select, Switch } from '../ui';

const MUTE_DB = -60;
const isTitle = (c: AnyClip): c is TitleClip => typeof (c as TitleClip).text === 'string';

export async function applyEdit(pid: string, body: TimelineEdit) {
  try {
    const t = await P.editTimeline(pid, body);
    project.setTimeline(t);
    project.loadVersions();
    return t;
  } catch (e) {
    ui.toast(errMsg(e), 'error');
    return null;
  }
}

async function patch(pid: string, clipId: string, body: Record<string, unknown>) {
  try {
    const t = await P.patchClip(pid, clipId, body);
    project.setTimeline(t);
    project.loadVersions();
  } catch (e) {
    ui.toast(errMsg(e), 'error');
  }
}

/** Suwak z zatwierdzaniem po puszczeniu (nie wysyłamy zmiany przy każdym pikselu). */
function CommitSlider({
  value,
  min,
  max,
  step,
  format,
  onCommit,
  label,
}: {
  value: number;
  min: number;
  max: number;
  step: number;
  format: (v: number) => string;
  onCommit: (v: number) => void;
  label: string;
}) {
  const [v, setV] = useState(value);
  const dirty = useRef(false);
  useEffect(() => {
    if (!dirty.current) setV(value);
  }, [value]);
  const commit = () => {
    if (dirty.current) {
      dirty.current = false;
      onCommit(v);
    }
  };
  return (
    <div className="flex items-center gap-2">
      <span className="w-[86px] shrink-0 text-[11px] text-muted">{label}</span>
      <input
        type="range"
        className="range min-w-0 flex-1"
        min={min}
        max={max}
        step={step}
        value={v}
        aria-label={label}
        onChange={(e) => {
          dirty.current = true;
          setV(Number(e.target.value));
        }}
        onPointerUp={commit}
        onKeyUp={commit}
        onBlur={commit}
      />
      <span className="tabular w-[52px] shrink-0 text-right text-[11px]">{format(v)}</span>
    </div>
  );
}

function Nudge({ label, onDelta, fps }: { label: string; onDelta: (d: number) => void; fps: number }) {
  const f = 1 / fps;
  return (
    <div className="flex items-center gap-1">
      <span className="w-[86px] shrink-0 text-[11px] text-muted">{label}</span>
      {[
        { d: -0.5, t: '−0,5 s' },
        { d: -f, t: '−1 kl.' },
        { d: f, t: '+1 kl.' },
        { d: 0.5, t: '+0,5 s' },
      ].map((b) => (
        <button
          key={b.t}
          className="tabular rounded-md border border-border bg-surface-2 px-1.5 py-0.5 text-[10.5px] hover:border-accent hover:text-accent cursor-pointer"
          onClick={() => onDelta(b.d)}
        >
          {b.t}
        </button>
      ))}
    </div>
  );
}

export function ClipInspector({
  pid,
  track,
  clip,
  media,
  fps,
  v1Index,
  v1Count,
  onClose,
}: {
  pid: string;
  track: string;
  clip: AnyClip;
  media: Record<string, MediaItem>;
  fps: number;
  v1Index: number;
  v1Count: number;
  onClose: () => void;
}) {
  const isVideo = track.startsWith('V');
  const vc = clip as VClip;
  const ac = clip as AClip;
  const name = isTitle(clip) ? clip.text : vc.label || (vc.media_id && media[vc.media_id]?.name) || (ac.path || '').split(/[\\/]/).pop() || 'Klip';
  const role = isTitle(clip) ? 'title' : (vc as VClip | AClip).role;
  const trim = (edge: 'in' | 'out') => (delta: number) => applyEdit(pid, { op: 'trim', clip_id: clip.id, edge, delta });
  const muted = !isVideo && (ac.gain_db ?? 0) <= MUTE_DB;
  const proc = (ac.processing || {}) as Record<string, unknown>;

  return (
    <div
      className="scroll-thin absolute top-2 right-3 z-40 max-h-[calc(100%-1rem)] w-[310px] overflow-y-auto rounded-xl border border-border bg-elevated p-3 shadow-pop"
      onPointerDown={(e) => e.stopPropagation()}
    >
      <div className="mb-2 flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="truncate text-[12.5px] font-semibold" title={name}>
            {name}
          </div>
          <div className="text-[11px] text-muted">
            {track} · {ROLE_LABEL[role] || role} · {tc(clip.start, fps)}
          </div>
        </div>
        <IconButton icon={X} label="Zamknij (Esc)" size="sm" onClick={onClose} />
      </div>

      {clipDescription(clip, media) && (
        <div className="mb-2 rounded-lg bg-surface-2 px-2 py-1.5 text-[11.5px] leading-snug">
          <span className="font-semibold text-muted">{isVideo ? 'Na klipie: ' : 'Dźwięk: '}</span>
          {clipDescription(clip, media)}
        </div>
      )}
      <div className="flex flex-col gap-1.5">
        {!isTitle(clip) && (
          <>
            <div className="tabular text-[11px] text-subtle">
              Źródło: {tc(vc.src_in, fps)} – {tc(vc.src_out, fps)}
            </div>
            <Nudge label="Początek" fps={fps} onDelta={trim('in')} />
            <Nudge label="Koniec" fps={fps} onDelta={trim('out')} />
          </>
        )}
        {isTitle(clip) && <Nudge label="Długość" fps={fps} onDelta={trim('out')} />}

        {track === 'V1' && (
          <div className="flex items-center gap-1">
            <span className="w-[86px] shrink-0 text-[11px] text-muted">Kolejność</span>
            <Button size="xs" variant="secondary" icon={ChevronLeft} disabled={v1Index <= 0} onClick={() => applyEdit(pid, { op: 'reorder', clip_id: clip.id, index: v1Index - 1 })}>
              Wcześniej
            </Button>
            <Button size="xs" variant="secondary" icon={ChevronRight} disabled={v1Index >= v1Count - 1} onClick={() => applyEdit(pid, { op: 'reorder', clip_id: clip.id, index: v1Index + 1 })}>
              Później
            </Button>
          </div>
        )}

        {isVideo && track !== 'V1' && !isTitle(clip) && (
          <div className="flex items-center gap-2">
            <span className="w-[86px] shrink-0 text-[11px] text-muted">Prędkość</span>
            <Select
              value={String(vc.speed || 1)}
              onChange={(v) => patch(pid, clip.id, { speed: Number(v), speed_ramp: [] })}
              options={[0.25, 0.5, 0.75, 1, 1.5, 2].map((s) => ({ value: String(s), label: `${Math.round(s * 100)}%` }))}
            />
          </div>
        )}

        {!isVideo && (
          <>
            <div className="flex items-center gap-2">
              <CommitSlider
                label="Głośność"
                value={muted ? -30 : ac.gain_db || 0}
                min={-30}
                max={12}
                step={0.5}
                format={(v) => `${v > 0 ? '+' : ''}${v.toFixed(1)} dB`}
                onCommit={(v) => patch(pid, clip.id, { gain_db: v })}
              />
            </div>
            <div className="flex items-center gap-2">
              <span className="w-[86px] shrink-0 text-[11px] text-muted">Wyciszenie</span>
              <Button
                size="xs"
                variant={muted ? 'primary' : 'secondary'}
                icon={muted ? VolumeX : Volume2}
                onClick={() =>
                  muted
                    ? patch(pid, clip.id, { gain_db: Number(proc.pre_mute_db ?? 0) })
                    : patch(pid, clip.id, { gain_db: MUTE_DB, processing: { ...proc, pre_mute_db: ac.gain_db || 0 } })
                }
              >
                {muted ? 'Wyciszony — przywróć' : 'Wycisz'}
              </Button>
            </div>
            <CommitSlider label="Narastanie" value={ac.fade_in || 0} min={0} max={3} step={0.05} format={(v) => `${v.toFixed(2)} s`} onCommit={(v) => patch(pid, clip.id, { fade_in: v })} />
            <CommitSlider label="Wygaszanie" value={ac.fade_out || 0} min={0} max={3} step={0.05} format={(v) => `${v.toFixed(2)} s`} onCommit={(v) => patch(pid, clip.id, { fade_out: v })} />
            {(role === 'dialog' || role === 'vo') && (
              <>
                <div className="mt-1 text-[10.5px] font-semibold uppercase tracking-wide text-subtle">Głos</div>
                <div className="flex items-center gap-2">
                  <span className="w-[86px] shrink-0 text-[11px] text-muted">Odszumianie</span>
                  <Select
                    value={String(proc.denoise ?? 'auto')}
                    onChange={(v) => patch(pid, clip.id, { processing: { ...proc, denoise: v } })}
                    options={[
                      { value: 'auto', label: 'Automatyczne' },
                      { value: 'off', label: 'Wyłączone' },
                      { value: 'light', label: 'Lekkie' },
                      { value: 'strong', label: 'Mocne' },
                    ]}
                  />
                </div>
                <CommitSlider
                  label="Wyrazistość"
                  value={Number(proc.presence_db ?? 2.5)}
                  min={0}
                  max={6}
                  step={0.5}
                  format={(v) => `+${v.toFixed(1)} dB`}
                  onCommit={(v) => patch(pid, clip.id, { processing: { ...proc, presence_db: v } })}
                />
                <CommitSlider
                  label="Pogłos (redukcja)"
                  value={Number(proc.dereverb ?? 0)}
                  min={0}
                  max={1}
                  step={0.1}
                  format={(v) => `${Math.round(v * 100)}%`}
                  onCommit={(v) => patch(pid, clip.id, { processing: { ...proc, dereverb: v } })}
                />
              </>
            )}
            {role === 'music' && (
              <div className="flex items-center gap-2">
                <span className="w-[86px] shrink-0 text-[11px] text-muted">Ducking</span>
                <Switch size="sm" checked={!!ac.duck} onChange={(v) => patch(pid, clip.id, { duck: v })} label="Ściszaj muzykę pod głosem" />
                <span className="text-[11px] text-muted">ściszaj pod głosem</span>
              </div>
            )}
          </>
        )}

        <div className="mt-1.5 flex items-center justify-between border-t border-border pt-2">
          <span className="text-[10.5px] text-subtle">
            {track === 'V1' ? 'Przeciągnij, aby zmienić kolejność' : 'Przeciągnij, aby przesunąć'} · krawędzie = przycinanie
          </span>
          <Button size="xs" variant="danger" icon={Trash} onClick={() => applyEdit(pid, { op: 'delete', clip_id: clip.id }).then(onClose)}>
            Usuń
          </Button>
        </div>
      </div>
    </div>
  );
}
