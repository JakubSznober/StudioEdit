import { useEffect, useState } from 'react';
import { errMsg } from '../api/client';
import { sys } from '../api/endpoints';
import { Button, Card, CardHeader, EmptyState, Field, Input, PageHeader, Segmented, Select, Skeleton, Switch } from '../components/ui';
import { AudioWaveform, Film, Monitor, Moon, Scissors, Server, Settings2, Sun } from '../lib/icons';
import { useLive } from '../store/live';
import { ui, useUI, type ThemeMode } from '../store/ui';
import type { Config } from '../types';

function get(o: Config | null, path: string, d: any = undefined): any {
  let cur: any = o;
  for (const p of path.split('.')) {
    if (cur == null || typeof cur !== 'object') return d;
    cur = cur[p];
  }
  return cur ?? d;
}

function setPath(o: Config, path: string, v: unknown): Config {
  const parts = path.split('.');
  const out: Config = { ...o };
  let cur: any = out;
  parts.slice(0, -1).forEach((p) => {
    cur[p] = { ...(cur[p] || {}) };
    cur = cur[p];
  });
  cur[parts[parts.length - 1]] = v;
  return out;
}

function Num({ value, onChange, step = 1, min, max, suffix }: { value: number; onChange: (v: number) => void; step?: number; min?: number; max?: number; suffix?: string }) {
  return (
    <div className="relative">
      <Input type="number" step={step} min={min} max={max} value={Number.isFinite(value) ? value : ''} onChange={(e) => onChange(e.target.value === '' ? NaN : Number(e.target.value))} className={suffix ? 'pr-12' : ''} />
      {suffix && <span className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-[11.5px] text-subtle">{suffix}</span>}
    </div>
  );
}

export default function SettingsPage() {
  const online = useLive((s) => s.apiOnline);
  const theme = useUI((s) => s.theme);
  const [cfg, setCfg] = useState<Config | null>(null);
  const [patch, setPatch] = useState<Config>({});
  const [draft, setDraft] = useState<Config | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const c = await sys.config();
      setCfg(c);
      setDraft(c);
      setPatch({});
      setError(null);
    } catch (e) {
      setError(errMsg(e));
    }
  };
  useEffect(() => {
    if (online !== false) load();
  }, [online]);

  const v = (path: string, d?: any) => get(draft, path, d);
  const set = (path: string, val: unknown) => {
    setDraft((cur) => setPath(cur || {}, path, val));
    setPatch((cur) => setPath(cur, path, val));
  };
  const dirty = Object.keys(patch).length > 0;

  const save = async () => {
    setSaving(true);
    try {
      const c = await sys.patchConfig(patch);
      setCfg(c && typeof c === 'object' ? c : draft);
      setDraft(c && typeof c === 'object' ? c : draft);
      setPatch({});
      ui.toast('Zapisano ustawienia', 'success');
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    } finally {
      setSaving(false);
    }
  };

  const setTheme = (t: ThemeMode) => {
    ui.setTheme(t);
    if (cfg) sys.patchConfig({ ui: { theme: t } }).catch(() => undefined);
  };

  const lufs = v('audio.target_lufs', -14);

  return (
    <div className="space-y-6 pb-16">
      <PageHeader
        icon={Settings2}
        title="Ustawienia"
        subtitle="Kluczowe pola konfiguracji (config/config.json). Zmiany zapisują się atomowo."
        right={
          <>
            {dirty && (
              <Button variant="ghost" onClick={() => { setDraft(cfg); setPatch({}); }}>
                Odrzuć zmiany
              </Button>
            )}
            <Button variant="primary" loading={saving} disabled={!dirty} onClick={save}>
              Zapisz ustawienia
            </Button>
          </>
        }
      />

      <Card>
        <CardHeader icon={Monitor} title="Wygląd" subtitle="Motyw interfejsu — zapisywany lokalnie i w konfiguracji" />
        <div className="px-5 pb-5">
          <Segmented
            value={theme}
            onChange={setTheme}
            options={[
              { value: 'light', label: 'Jasny', icon: Sun },
              { value: 'dark', label: 'Ciemny', icon: Moon },
              { value: 'system', label: 'Systemowy', icon: Monitor },
            ]}
          />
        </div>
      </Card>

      {!draft ? (
        error ? (
          <Card>
            <EmptyState icon={Server} title="Nie udało się wczytać konfiguracji" action={<Button onClick={load}>Spróbuj ponownie</Button>}>
              {online === false ? 'Serwer StudioEdit nie odpowiada.' : error}
            </EmptyState>
          </Card>
        ) : (
          <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
            {[0, 1, 2, 3].map((k) => (
              <Skeleton key={k} className="h-[260px] rounded-2xl" />
            ))}
          </div>
        )
      ) : (
        <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
          <Card>
            <CardHeader icon={AudioWaveform} iconColor="#10b981" title="Audio" subtitle="Normalizacja głośności i ducking muzyki" />
            <div className="space-y-4 px-5 pb-5">
              <Field label="Docelowa głośność (integrated LUFS)" hint="-14 LUFS: YouTube / Spotify / social media · -23 LUFS: broadcast EBU R128 · -16: podcasty Apple">
                <div className="flex items-center gap-2">
                  <Segmented
                    value={String(lufs)}
                    onChange={(x) => set('audio.target_lufs', Number(x))}
                    options={[
                      { value: '-14', label: '-14 Social' },
                      { value: '-16', label: '-16 Podcast' },
                      { value: '-23', label: '-23 TV' },
                    ]}
                  />
                  <div className="w-28">
                    <Num value={lufs} step={0.5} min={-36} max={-6} onChange={(x) => set('audio.target_lufs', x)} suffix="LUFS" />
                  </div>
                </div>
              </Field>
              <div className="grid grid-cols-3 gap-3">
                <Field label="True peak">
                  <Num value={v('audio.true_peak', -1)} step={0.1} min={-6} max={0} onChange={(x) => set('audio.true_peak', x)} suffix="dBTP" />
                </Field>
                <Field label="Ducking muzyki">
                  <Num value={v('audio.duck_db', -15)} step={1} min={-40} max={0} onChange={(x) => set('audio.duck_db', x)} suffix="dB" />
                </Field>
                <Field label="Mikro-przenikania">
                  <Num value={v('audio.micro_fade_ms', 80)} step={10} min={0} max={500} onChange={(x) => set('audio.micro_fade_ms', x)} suffix="ms" />
                </Field>
              </div>
              <Field label="Odszumianie głosu">
                <Select
                  value={String(v('audio.denoise', 'auto'))}
                  onChange={(x) => set('audio.denoise', x)}
                  options={[
                    { value: 'auto', label: 'Automatycznie' },
                    { value: 'off', label: 'Wyłączone' },
                    { value: 'light', label: 'Delikatne' },
                    { value: 'strong', label: 'Mocne' },
                  ]}
                />
              </Field>
            </div>
          </Card>

          <Card>
            <CardHeader icon={Film} iconColor="#6366f1" title="Render" subtitle="Kodowanie NVENC i podgląd" />
            <div className="space-y-4 px-5 pb-5">
              <div className="grid grid-cols-2 gap-3">
                <Field label="Kodek wideo">
                  <Select
                    value={String(v('render.video_codec', 'hevc_nvenc'))}
                    onChange={(x) => set('render.video_codec', x)}
                    options={[
                      { value: 'h264_nvenc', label: 'H.264 (NVENC)' },
                      { value: 'hevc_nvenc', label: 'H.265 / HEVC (NVENC)' },
                      { value: 'av1_nvenc', label: 'AV1 (NVENC)' },
                      { value: 'libx264', label: 'H.264 (CPU)' },
                      { value: 'libx265', label: 'H.265 (CPU)' },
                    ]}
                  />
                </Field>
                <Field label="Preset NVENC" hint="p1 najszybszy · p7 najlepsza jakość">
                  <Select value={String(v('render.preset', 'p6'))} onChange={(x) => set('render.preset', x)} options={[1, 2, 3, 4, 5, 6, 7].map((n) => ({ value: `p${n}`, label: `p${n}` }))} />
                </Field>
                <Field label="Jakość CQ" hint="niżej = lepiej (16–23 typowo)">
                  <Num value={v('render.cq', 19)} min={0} max={51} onChange={(x) => set('render.cq', x)} />
                </Field>
                <Field label="Wysokość podglądu">
                  <Select value={Number(v('render.preview_height', 720))} onChange={(x) => set('render.preview_height', x)} options={[360, 480, 540, 720, 1080].map((n) => ({ value: n, label: `${n}p` }))} />
                </Field>
                <Field label="Bitrate audio">
                  <Select value={String(v('render.audio_bitrate', '320k'))} onChange={(x) => set('render.audio_bitrate', x)} options={['192k', '256k', '320k'].map((n) => ({ value: n, label: n }))} />
                </Field>
                <Field label="Sesje NVENC">
                  <Num value={v('render.nvenc_sessions', 3)} min={1} max={8} onChange={(x) => set('render.nvenc_sessions', x)} />
                </Field>
              </div>
              <label className="flex items-center justify-between rounded-xl bg-surface-2 px-3 py-2.5 text-[12.5px] font-medium">
                <span>
                  Kodowanie 10-bit
                  <span className="block text-[11px] font-normal text-muted">Płynniejsze gradienty (HEVC/AV1). Wyłącz dla starszych odtwarzaczy.</span>
                </span>
                <Switch checked={!!v('render.ten_bit', true)} onChange={(x) => set('render.ten_bit', x)} label="10-bit" />
              </label>
            </div>
          </Card>

          <Card>
            <CardHeader icon={Scissors} iconColor="#f97316" title="Edycja" subtitle="Zachowanie Reżysera Montażu" />
            <div className="space-y-4 px-5 pb-5">
              <div className="grid grid-cols-2 gap-3">
                <Field label="Punch-in (skala)" hint="Cyfrowe zbliżenie przy cięciach w A-Roll">
                  <Num value={v('edit.punch_in_scale', 1.15)} step={0.01} min={1} max={1.6} onChange={(x) => set('edit.punch_in_scale', x)} suffix="×" />
                </Field>
                <Field label="Slow zoom (skala)" hint="Powolny najazd na statycznych ujęciach">
                  <Num value={v('edit.slow_zoom_scale', 1.08)} step={0.01} min={1} max={1.4} onChange={(x) => set('edit.slow_zoom_scale', x)} suffix="×" />
                </Field>
                <Field label="Min. długość ujęcia">
                  <Num value={v('edit.min_shot_s', 0.8)} step={0.1} min={0.1} max={10} onChange={(x) => set('edit.min_shot_s', x)} suffix="s" />
                </Field>
                <Field label="Maks. długość B-Roll">
                  <Num value={v('edit.max_broll_s', 5)} step={0.5} min={0.5} max={30} onChange={(x) => set('edit.max_broll_s', x)} suffix="s" />
                </Field>
              </div>
              <Field label="Wypełniacze do wycinania" hint="Słowa oddzielone przecinkami — usuwane z dialogów (eee, yyy…)">
                <Input
                  value={(v('edit.filler_words', []) as string[]).join(', ')}
                  onChange={(e) => set('edit.filler_words', e.target.value.split(',').map((s) => s.trim()).filter(Boolean))}
                />
              </Field>
            </div>
          </Card>

          <Card>
            <CardHeader icon={Server} iconColor="#64748b" title="Serwer i pamięć" subtitle="Zmiana portu wymaga restartu backendu" />
            <div className="space-y-4 px-5 pb-5">
              <div className="grid grid-cols-2 gap-3">
                <Field label="Host">
                  <Input value={String(v('server.host', '127.0.0.1'))} onChange={(e) => set('server.host', e.target.value)} className="font-mono" />
                </Field>
                <Field label="Port">
                  <Num value={v('server.port', 8765)} min={1024} max={65535} onChange={(x) => set('server.port', x)} />
                </Field>
                <Field label="Limit dysku StudioEdit">
                  <Num value={v('storage.limit_gb', 500)} min={20} max={10000} onChange={(x) => set('storage.limit_gb', x)} suffix="GB" />
                </Field>
                <Field label="URL Ollama">
                  <Input value={String(v('services.ollama.url', ''))} onChange={(e) => set('services.ollama.url', e.target.value)} className="font-mono !text-[12px]" />
                </Field>
              </div>
              <label className="flex items-center justify-between rounded-xl bg-surface-2 px-3 py-2.5 text-[12.5px] font-medium">
                <span>Otwieraj przeglądarkę przy starcie serwera</span>
                <Switch checked={!!v('server.open_browser', true)} onChange={(x) => set('server.open_browser', x)} label="Otwieraj przeglądarkę" />
              </label>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
