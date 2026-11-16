import { useEffect, useMemo, useState } from 'react';
import { errMsg } from '../api/client';
import { sys } from '../api/endpoints';
import { AgentAvatar, groupProgress, useGroupTasks } from '../components/common';
import { ServicesGrid, StorageSummary, useModels } from '../components/system';
import { Badge, Button, Card, CardHeader, confirmDialog, EmptyState, PageHeader, Progress, SectionTitle, Segmented, Skeleton, Tip } from '../components/ui';
import { agentMeta } from '../lib/agents';
import { MODEL_KIND, PROVIDER } from '../lib/constants';
import { gb } from '../lib/format';
import { Boxes, CircleCheck, Download, HardDrive, Server, Trash } from '../lib/icons';
import { useLive } from '../store/live';
import { useProject } from '../store/project';
import { ui } from '../store/ui';
import type { ModelEntry, StorageInfo } from '../types';

function InstallProgress({ group, onDone }: { group: string; onDone: () => void }) {
  const tasks = useGroupTasks(group);
  const g = groupProgress(tasks);
  useEffect(() => {
    if (g.total && g.finished) onDone();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [g.finished, g.total]);
  const msg = tasks.find((t) => t.status === 'running')?.message;
  return (
    <div className="w-[160px]">
      <Progress value={g.progress} height={5} striped indeterminate={!g.total} />
      <div className="mt-0.5 truncate text-[10.5px] text-muted">{msg || (g.total ? 'Pobieranie…' : 'W kolejce…')}</div>
    </div>
  );
}

function ModelsTable() {
  const { models, error, reload } = useModels();
  const [kind, setKind] = useState<string>('all');
  const [installing, setInstalling] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const kinds = useMemo(() => Array.from(new Set((models || []).map((m) => m.kind))), [models]);
  const list = useMemo(() => (models || []).filter((m) => kind === 'all' || m.kind === kind).sort((a, b) => a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name)), [models, kind]);
  const totalInstalled = (models || []).filter((m) => m.installed).reduce((s, m) => s + (m.size_gb || 0), 0);

  const install = async (m: ModelEntry) => {
    setBusy(m.key);
    try {
      const r = await sys.installModel(m.key);
      if (r?.group) setInstalling((s) => ({ ...s, [m.key]: r.group }));
      ui.toast(`Pobieranie modelu ${m.name}…`, 'info');
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    } finally {
      setBusy(null);
    }
  };
  const remove = async (m: ModelEntry) => {
    const ok = await confirmDialog({ title: `Usunąć model ${m.name}?`, text: `Zwolni ok. ${gb(m.size_gb)} na dysku. Agenci korzystający z modelu przejdą na metodę zapasową.`, confirm: 'Usuń model', danger: true });
    if (!ok) return;
    setBusy(m.key);
    try {
      await sys.deleteModel(m.key);
      ui.toast(`Usunięto ${m.name}`, 'success');
      reload();
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card>
      <CardHeader
        icon={Boxes}
        title="Modele AI"
        subtitle={models ? `${models.filter((m) => m.installed).length}/${models.length} zainstalowanych · ${gb(totalInstalled)} na dysku` : 'Katalog modeli lokalnych'}
        right={
          kinds.length > 1 ? (
            <Segmented size="sm" value={kind} onChange={setKind} options={[{ value: 'all', label: 'Wszystkie' }, ...kinds.map((k) => ({ value: k, label: MODEL_KIND[k] || k }))]} />
          ) : null
        }
      />
      <div className="px-5 pb-5">
        {!models && !error && (
          <div className="space-y-2">
            {[0, 1, 2, 3, 4].map((k) => (
              <Skeleton key={k} className="h-12" />
            ))}
          </div>
        )}
        {error && !models && <EmptyState icon={Boxes} title="Nie udało się pobrać listy modeli" compact>{error}</EmptyState>}
        {models && (
          <div className="overflow-x-auto rounded-xl border border-border">
            <table className="w-full min-w-[860px] text-[12.5px]">
              <thead className="bg-surface-2 text-left text-[11px] text-muted">
                <tr>
                  <th className="px-3 py-2 font-medium">Model</th>
                  <th className="px-3 py-2 font-medium">Rodzaj</th>
                  <th className="px-3 py-2 font-medium">Dostawca</th>
                  <th className="px-3 py-2 font-medium">Rozmiar</th>
                  <th className="px-3 py-2 font-medium">Używany przez</th>
                  <th className="px-3 py-2 font-medium">Stan</th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {list.map((m) => (
                  <tr key={m.key} className="border-t border-border align-middle hover:bg-surface-2/50">
                    <td className="max-w-[320px] px-3 py-2.5">
                      <div className="truncate font-medium">{m.name}</div>
                      <div className="truncate text-[11px] text-muted" title={m.description}>
                        {m.role ? `${m.role} · ` : ''}
                        {m.description}
                      </div>
                    </td>
                    <td className="px-3 py-2.5">
                      <Badge>{MODEL_KIND[m.kind] || m.kind}</Badge>
                    </td>
                    <td className="max-w-[160px] truncate px-3 py-2.5 text-muted" title={m.provider}>{PROVIDER[m.provider] || PROVIDER[m.provider.split(':')[0]] || m.provider.split('/')[0]}</td>
                    <td className="tabular px-3 py-2.5 text-muted">{gb(m.size_gb)}</td>
                    <td className="px-3 py-2.5">
                      <div className="flex -space-x-1">
                        {(m.used_by || []).map((a) => (
                          <Tip key={a} text={agentMeta(a).name}>
                            <span>
                              <AgentAvatar agent={a} size={20} ring />
                            </span>
                          </Tip>
                        ))}
                      </div>
                    </td>
                    <td className="px-3 py-2.5">
                      {installing[m.key] ? (
                        <InstallProgress
                          group={installing[m.key]}
                          onDone={() => {
                            setInstalling((s) => {
                              const n = { ...s };
                              delete n[m.key];
                              return n;
                            });
                            reload();
                          }}
                        />
                      ) : m.installed ? (
                        <Badge tone="success">
                          <CircleCheck size={10} /> Zainstalowany
                        </Badge>
                      ) : (
                        <Badge tone="neutral">Brak</Badge>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-right">
                      {m.installed ? (
                        <Button size="xs" variant="ghost" icon={Trash} loading={busy === m.key} onClick={() => remove(m)}>
                          Usuń
                        </Button>
                      ) : (
                        <Button size="xs" variant="soft" icon={Download} loading={busy === m.key} disabled={!!installing[m.key]} onClick={() => install(m)}>
                          Instaluj
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Card>
  );
}

const CLEAN: { target: 'cache' | 'renders' | 'proxies' | 'comfy' | 'all_cache'; label: string; desc: string }[] = [
  { target: 'cache', label: 'Cache analizy', desc: 'Pliki pośrednie analizy i miksu (odtworzą się w razie potrzeby).' },
  { target: 'renders', label: 'Rendery podglądu', desc: 'Plastry renderu i pliki podglądu (render przyrostowy zacznie od nowa).' },
  { target: 'proxies', label: 'Proxy', desc: 'Lekkie kopie robocze klipów — zostaną wygenerowane ponownie.' },
  { target: 'comfy', label: 'Wyniki ComfyUI', desc: 'Pliki tymczasowe ComfyUI (output/temp).' },
  { target: 'all_cache', label: 'Cały cache', desc: 'Wszystkie powyższe naraz.' },
];

function StorageCard() {
  const storage = useLive((s) => s.storage);
  const pid = useProject((s) => s.activeId);
  const [data, setData] = useState<StorageInfo | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const load = async () => {
    try {
      const s = await sys.storage();
      setData(s);
      useLive.setState({ storage: s });
    } catch {
      /* ignoruj */
    }
  };
  useEffect(() => {
    load();
  }, []);
  const s = data || storage;
  const max = Math.max(1, ...(s?.folders || []).map((f) => f.size_gb));
  const clean = async (c: (typeof CLEAN)[number]) => {
    const ok = await confirmDialog({ title: `Wyczyścić: ${c.label}?`, text: c.desc, confirm: 'Wyczyść', danger: c.target === 'all_cache' });
    if (!ok) return;
    setBusy(c.target);
    try {
      const r = await sys.clean(c.target, pid || undefined);
      ui.toast(`Zwolniono ${gb(r?.freed_gb ?? 0)}`, 'success');
      load();
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    } finally {
      setBusy(null);
    }
  };
  return (
    <Card>
      <CardHeader icon={HardDrive} title="Zajętość dysku" subtitle="Wszystko w C:\StudioEdit" />
      <div className="space-y-4 px-5 pb-5">
        <StorageSummary />
        <div className="space-y-2">
          {!s && [0, 1, 2, 3].map((k) => <Skeleton key={k} className="h-8" />)}
          {(s?.folders || [])
            .slice()
            .sort((a, b) => b.size_gb - a.size_gb)
            .map((f) => (
              <div key={f.key}>
                <div className="flex items-center justify-between text-[12px]">
                  <span className="font-medium" title={f.path}>
                    {f.label}
                  </span>
                  <span className="tabular text-muted">{gb(f.size_gb)}</span>
                </div>
                <Progress value={f.size_gb / max} height={4} className="mt-1" tone="info" />
              </div>
            ))}
        </div>
        <div className="border-t border-border pt-3">
          <div className="mb-2 text-[11.5px] font-semibold text-muted">Czyszczenie{pid ? ' (aktywny projekt)' : ''}</div>
          <div className="flex flex-wrap gap-1.5">
            {CLEAN.map((c) => (
              <Tip key={c.target} text={c.desc}>
                <Button size="xs" variant={c.target === 'all_cache' ? 'danger' : 'secondary'} loading={busy === c.target} onClick={() => clean(c)}>
                  {c.label}
                </Button>
              </Tip>
            ))}
          </div>
        </div>
      </div>
    </Card>
  );
}

export default function ModelsPage() {
  return (
    <div className="space-y-6">
      <PageHeader icon={Server} title="Modele i usługi" subtitle="Lokalne modele AI, usługi Ollama / ComfyUI i miejsce na dysku (limit 500 GB)." />
      <div className="grid grid-cols-1 gap-5 2xl:grid-cols-[2fr_1fr]">
        <ModelsTable />
        <StorageCard />
      </div>
      <section>
        <SectionTitle>Usługi lokalne</SectionTitle>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <ServicesGrid />
        </div>
      </section>
    </div>
  );
}
