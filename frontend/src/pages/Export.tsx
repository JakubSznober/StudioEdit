import { motion } from 'framer-motion';
import { useEffect, useMemo, useState } from 'react';
import { errMsg } from '../api/client';
import { projects as P, type ExportRequest } from '../api/endpoints';
import { GroupProgress, NoProject } from '../components/common';
import { Badge, Button, Card, CardHeader, Chip, EmptyState, Field, PageHeader, Progress, Segmented, Select, Skeleton } from '../components/ui';
import { MODULE_STATE, platformLabel, PLATFORMS } from '../lib/constants';
import { mb, pct, when } from '../lib/format';
import { Download, ExternalLink, Film, FileVideoCamera, Layers, Rocket, Sparkles } from '../lib/icons';
import { useLive } from '../store/live';
import { project, useProject } from '../store/project';
import { ui } from '../store/ui';

type Codec = NonNullable<ExportRequest['codec']>;
type Container = NonNullable<ExportRequest['container']>;

const CODECS: { value: Codec; label: string; hint: string }[] = [
  { value: 'h264', label: 'H.264', hint: 'Największa zgodność (social media, przeglądarki). NVENC.' },
  { value: 'hevc', label: 'H.265 / HEVC', hint: 'Mniejsze pliki przy tej samej jakości, 10-bit. NVENC.' },
  { value: 'av1', label: 'AV1', hint: 'Najlepsza kompresja (YouTube). NVENC AV1 na RTX 40/50.' },
  { value: 'prores', label: 'ProRes 422 HQ', hint: 'Mezzanine do dalszej obróbki. Duże pliki, wymaga MOV.' },
  { value: 'dnxhr', label: 'DNxHR HQX', hint: 'Mezzanine (Avid / Premiere). Duże pliki, wymaga MOV/MXF.' },
];

export default function ExportPage() {
  const pid = useProject((s) => s.activeId);
  const tl = useProject((s) => s.timeline);
  const exportsList = useProject((s) => s.exports);
  const exportsLoaded = useProject((s) => s.exportsLoaded);
  const renders = useLive((s) => s.renders);
  const modules = useLive((s) => s.modules);
  const available = useMemo(() => {
    const v = (tl?.variants || []).map((x) => x.name);
    const base = v.length ? v : ['youtube'];
    return [...base, ...PLATFORMS.map((p) => p.key).filter((k) => !base.includes(k)), 'master'].filter((x, i, a) => a.indexOf(x) === i);
  }, [tl?.variants]);
  const [variants, setVariants] = useState<string[]>([]);
  const [resolution, setResolution] = useState<NonNullable<ExportRequest['resolution']>>('1080p');
  const [fps, setFps] = useState<number>(0);
  const [codec, setCodec] = useState<Codec>('hevc');
  const [container, setContainer] = useState<Container>('mp4');
  const [audio, setAudio] = useState<NonNullable<ExportRequest['audio_codec']>>('aac');
  const [quality, setQuality] = useState<'auto' | 'high' | 'max' | 'custom'>('auto');
  const [cq, setCq] = useState(19);
  const [busy, setBusy] = useState<string | null>(null);
  const [group, setGroup] = useState<string | null>(null);
  const [adobeGroup, setAdobeGroup] = useState<string | null>(null);

  useEffect(() => {
    const enabled = (tl?.variants || []).filter((v) => v.enabled !== false).map((v) => v.name);
    setVariants(enabled.length ? enabled : ['youtube']);
  }, [tl?.version]);

  useEffect(() => {
    if ((codec === 'prores' || codec === 'dnxhr') && container === 'mp4') setContainer('mov');
    if ((codec === 'prores' || codec === 'dnxhr') && audio === 'aac') setAudio('wav');
  }, [codec]);

  useEffect(() => {
    project.loadExports();
  }, [pid]);

  const activeRenders = useMemo(
    () => Object.values(renders).filter((r) => r.project_id === pid && r.quality !== 'preview' && (r.progress < 1 || (r.ts || 0) > Date.now() / 1000 - 30)).sort((a, b) => a.variant.localeCompare(b.variant)),
    [renders, pid],
  );

  if (!pid) return <NoProject what="eksport" />;

  const render = async () => {
    if (!variants.length) return ui.toast('Wybierz co najmniej jeden wariant', 'warn');
    const body: ExportRequest = { variants, codec, resolution, container, audio_codec: audio, quality: quality === 'custom' ? cq : quality };
    if (fps) body.fps = fps;
    setBusy('render');
    try {
      const r = await P.export(pid, body);
      setGroup(r.group);
      ui.toast('Rozpoczęto render Final Master', 'success');
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    } finally {
      setBusy(null);
    }
  };

  const adobe = async (targets: ('premiere' | 'ae_jsx' | 'ae_run')[], key: string) => {
    setBusy(key);
    try {
      const r = await P.adobe(pid, targets);
      setAdobeGroup(r.group);
      ui.toast('Zlecono eksport do Adobe', 'success');
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    } finally {
      setBusy(null);
    }
  };

  const mod = (k: string) => modules.find((m) => m.key === k);
  const hasTimeline = !!tl && Object.values(tl.tracks || {}).some((x) => (x || []).length);

  return (
    <div className="space-y-6">
      <PageHeader icon={Download} title="Eksport" subtitle="Render Final Master w pełnej jakości (NVENC) oraz integracja z Adobe Premiere i After Effects." />
      {!hasTimeline && tl !== undefined && (
        <Card className="border-warn/30 bg-warn/5 px-4 py-3 text-[12.5px] text-warn">Oś czasu jest pusta — najpierw zmontuj film w Studio Montażowym.</Card>
      )}
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1.25fr_1fr]">
        <Card>
          <CardHeader icon={Film} title="Ustawienia renderu" subtitle={tl ? `Oś czasu v${tl.version} · ${tl.width}×${tl.height} · ${tl.fps} fps` : undefined} />
          <div className="space-y-5 px-5 pb-5">
            <div>
              <div className="mb-1.5 text-[12px] font-medium text-muted">Warianty</div>
              <div className="flex flex-wrap gap-1.5">
                {available.map((v) => {
                  const vv = tl?.variants.find((x) => x.name === v);
                  return (
                    <Chip key={v} active={variants.includes(v)} onClick={() => setVariants((c) => (c.includes(v) ? c.filter((x) => x !== v) : [...c, v]))}>
                      {platformLabel(v)} {vv && <span className="opacity-60">{vv.aspect}</span>}
                    </Chip>
                  );
                })}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
              <Field label="Rozdzielczość">
                <Select
                  value={resolution}
                  onChange={setResolution}
                  options={[
                    { value: '1080p', label: '1080p (Full HD)' },
                    { value: '1440p', label: '1440p (2K)' },
                    { value: '2160p', label: '2160p (4K UHD)' },
                    { value: 'source', label: 'Jak źródło' },
                  ]}
                />
              </Field>
              <Field label="Klatkaż (FPS)">
                <Select value={fps} onChange={setFps} options={[{ value: 0, label: `Jak oś czasu (${tl?.fps || 30})` }, ...[24, 25, 30, 50, 60].map((f) => ({ value: f, label: `${f} fps` }))]} />
              </Field>
              <Field label="Kontener">
                <Select
                  value={container}
                  onChange={setContainer}
                  options={[
                    { value: 'mp4', label: 'MP4' },
                    { value: 'mov', label: 'MOV' },
                    { value: 'mkv', label: 'MKV' },
                  ]}
                />
              </Field>
            </div>
            <Field label="Kodek wideo" hint={CODECS.find((c) => c.value === codec)?.hint}>
              <Segmented value={codec} onChange={setCodec} options={CODECS.map((c) => ({ value: c.value, label: c.label.split(' ')[0] }))} />
            </Field>
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <Field label="Audio">
                <Segmented
                  value={audio}
                  onChange={setAudio}
                  options={[
                    { value: 'aac', label: 'AAC 320k' },
                    { value: 'wav', label: 'WAV / PCM' },
                  ]}
                />
              </Field>
              <Field label="Jakość" hint={quality === 'custom' ? `CQ ${cq} — niższa wartość = wyższa jakość i większy plik` : quality === 'auto' ? 'Dobór wg kodeka i rozdzielczości' : quality === 'max' ? 'Najwyższa jakość, duże pliki' : 'Wysoka jakość do publikacji'}>
                <Segmented
                  value={quality}
                  onChange={setQuality}
                  options={[
                    { value: 'auto', label: 'Auto' },
                    { value: 'high', label: 'Wysoka' },
                    { value: 'max', label: 'Maks.' },
                    { value: 'custom', label: 'Własna' },
                  ]}
                />
              </Field>
            </div>
            {quality === 'custom' && <input type="range" min={10} max={35} value={cq} onChange={(e) => setCq(Number(e.target.value))} className="range w-full" aria-label="CQ" />}
            <div className="flex items-center justify-between gap-3 border-t border-border pt-4">
              <div className="text-[12px] text-muted">
                {variants.length} {variants.length === 1 ? 'wariant' : 'warianty'} · {CODECS.find((c) => c.value === codec)?.label} · {container.toUpperCase()}
              </div>
              <Button variant="primary" size="lg" icon={Rocket} loading={busy === 'render'} onClick={render} disabled={!hasTimeline}>
                Renderuj Final Master
              </Button>
            </div>
            {group && <GroupProgress group={group} label="Render Final Master" />}
          </div>
        </Card>

        <div className="space-y-5">
          <Card>
            <CardHeader icon={Sparkles} title="Postęp renderu" subtitle="Na żywo z render.progress" />
            <div className="space-y-3 px-5 pb-5">
              {activeRenders.length === 0 && <div className="text-[12.5px] text-subtle">Brak aktywnych renderów.</div>}
              {activeRenders.map((r) => (
                <motion.div key={`${r.group}:${r.variant}:${r.quality}`} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }}>
                  <div className="flex items-center justify-between text-[12.5px]">
                    <span className="font-medium">
                      {platformLabel(r.variant)} <span className="text-muted">· {r.quality}</span>
                    </span>
                    <span className="tabular text-muted">
                      {r.stage} · {pct(r.progress)}
                    </span>
                  </div>
                  <Progress value={r.progress} height={6} striped={r.progress < 1} tone={r.progress >= 1 ? 'success' : 'accent'} className="mt-1" />
                </motion.div>
              ))}
            </div>
          </Card>

          <Card>
            <CardHeader icon={Layers} title="Integracja Adobe (opcjonalnie)" subtitle="Przekaż montaż do Premiere / After Effects zamiast renderu" />
            <div className="space-y-2 px-5 pb-5">
              {[
                { key: 'premiere', mod: 'premiere_xml', title: 'Premiere Pro XML', desc: 'FCP7 XML (xmeml v4) — importuj w Premiere: Plik → Importuj.', targets: ['premiere'] as const },
                { key: 'ae_jsx', mod: 'ae_jsx', title: 'After Effects JSX', desc: 'Skrypt budujący kompozycję z warstwami, efektami i tytułami.', targets: ['ae_jsx'] as const },
                { key: 'ae_run', mod: 'ae_runtime', title: 'Uruchom w After Effects', desc: 'Generuje JSX i od razu uruchamia go w zainstalowanym AE.', targets: ['ae_jsx', 'ae_run'] as const },
              ].map((x) => {
                const m = mod(x.mod);
                const st = m ? MODULE_STATE[m.state] : null;
                return (
                  <div key={x.key} className="flex items-center gap-3 rounded-xl border border-border p-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 text-[13px] font-semibold">
                        {x.title} {st && <Badge tone={st.tone}>{st.label}</Badge>}
                      </div>
                      <div className="text-[11.5px] text-muted">{m?.detail || x.desc}</div>
                    </div>
                    <Button size="sm" variant="secondary" loading={busy === x.key} disabled={!hasTimeline || m?.state === 'off'} onClick={() => adobe([...x.targets], x.key)}>
                      {x.key === 'ae_run' ? 'Uruchom' : 'Eksportuj'}
                    </Button>
                  </div>
                );
              })}
              {adobeGroup && <GroupProgress group={adobeGroup} label="Eksport Adobe" />}
            </div>
          </Card>
        </div>
      </div>

      <Card>
        <CardHeader icon={FileVideoCamera} title="Gotowe pliki" subtitle="Folder Exports projektu" right={<Button size="xs" variant="ghost" onClick={() => project.loadExports()}>Odśwież</Button>} />
        <div className="px-5 pb-5">
          {!exportsLoaded ? (
            <div className="space-y-2">
              {[0, 1, 2].map((k) => (
                <Skeleton key={k} className="h-11" />
              ))}
            </div>
          ) : exportsList.length === 0 ? (
            <EmptyState icon={FileVideoCamera} title="Nie ma jeszcze wyeksportowanych plików" compact>
              Po renderze Final Master pliki pojawią się tutaj z możliwością pobrania.
            </EmptyState>
          ) : (
            <div className="overflow-hidden rounded-xl border border-border">
              <table className="w-full text-[12.5px]">
                <thead className="bg-surface-2 text-left text-[11px] text-muted">
                  <tr>
                    <th className="px-3 py-2 font-medium">Plik</th>
                    <th className="px-3 py-2 font-medium">Wariant</th>
                    <th className="px-3 py-2 font-medium">Rozmiar</th>
                    <th className="px-3 py-2 font-medium">Utworzono</th>
                    <th className="px-3 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {exportsList.map((f) => (
                    <tr key={f.path} className="border-t border-border hover:bg-surface-2/60">
                      <td className="max-w-[360px] truncate px-3 py-2 font-medium" title={f.path}>
                        {f.name}
                      </td>
                      <td className="px-3 py-2">
                        <Badge tone="info">{platformLabel(f.variant)}</Badge>
                      </td>
                      <td className="tabular px-3 py-2 text-muted">{mb(f.size_mb)}</td>
                      <td className="px-3 py-2 text-muted">{when(f.created)}</td>
                      <td className="px-3 py-2">
                        <div className="flex justify-end gap-1.5">
                          <a href={f.url} target="_blank" rel="noreferrer">
                            <Button size="xs" variant="ghost" icon={ExternalLink}>
                              Otwórz
                            </Button>
                          </a>
                          <a href={f.url} download={f.name}>
                            <Button size="xs" variant="secondary" icon={Download}>
                              Pobierz
                            </Button>
                          </a>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}
