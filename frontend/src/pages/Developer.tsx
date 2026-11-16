import { useState } from 'react';
import { errMsg } from '../api/client';
import { dev } from '../api/endpoints';
import { GroupProgress } from '../components/common';
import { Badge, Button, Card, CardHeader, confirmDialog, EmptyState, Field, Input, PageHeader, Skeleton, Textarea } from '../components/ui';
import { cx, when } from '../lib/format';
import { useFetch } from '../lib/hooks';
import { CircleCheck, CircleX, CodeXml, FlaskConical, GitCompare, RefreshCw, RotateCcw, ShieldCheck, Sparkles, X } from '../lib/icons';
import { useLive } from '../store/live';
import { ui } from '../store/ui';
import type { DevProposal } from '../types';

function DiffView({ diff }: { diff: string }) {
  const lines = diff.split('\n');
  return (
    <div className="scroll-thin max-h-[520px] overflow-auto rounded-xl border border-border bg-surface-2 font-mono text-[11.5px] leading-[1.6]">
      {lines.map((l, i) => {
        const cls = l.startsWith('+++') || l.startsWith('---')
          ? 'text-text font-semibold bg-surface-3'
          : l.startsWith('@@')
            ? 'text-info bg-info/8'
            : l.startsWith('+')
              ? 'text-success bg-success/10'
              : l.startsWith('-')
                ? 'text-danger bg-danger/10'
                : l.startsWith('diff ') || l.startsWith('index ')
                  ? 'text-muted bg-surface-3'
                  : 'text-muted';
        return (
          <div key={i} className={cx('flex whitespace-pre', cls)}>
            <span className="w-10 shrink-0 pr-2 text-right text-subtle select-none">{i + 1}</span>
            <span className="pr-4">{l || ' '}</span>
          </div>
        );
      })}
    </div>
  );
}

function Diagnostics() {
  const online = useLive((s) => s.apiOnline);
  const { data, error, loading, reload } = useFetch(() => dev.diagnostics(), [online], { enabled: online !== false });
  const checks = data?.checks || [];
  const ok = checks.filter((c) => c.ok).length;
  return (
    <Card>
      <CardHeader
        icon={ShieldCheck}
        title="Diagnostyka"
        subtitle={checks.length ? `${ok}/${checks.length} testów środowiska OK` : 'Sprawdzenie środowiska, modeli i narzędzi'}
        right={
          <Button size="xs" variant="ghost" icon={RefreshCw} loading={loading} onClick={reload}>
            Odśwież
          </Button>
        }
      />
      <div className="space-y-1 px-5 pb-5">
        {loading && !data && [0, 1, 2, 3, 4].map((k) => <Skeleton key={k} className="h-8" />)}
        {error && !data && <div className="text-[12.5px] text-danger">{error}</div>}
        {checks.map((c) => (
          <div key={c.name} className="flex items-start gap-2.5 rounded-lg px-2 py-1.5 hover:bg-surface-2">
            {c.ok ? <CircleCheck size={15} className="mt-0.5 shrink-0 text-success" /> : <CircleX size={15} className="mt-0.5 shrink-0 text-danger" />}
            <div className="min-w-0">
              <div className="text-[12.5px] font-medium">{c.name}</div>
              {c.detail && <div className="text-[11.5px] break-words text-muted">{c.detail}</div>}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

function Backups({ refreshKey }: { refreshKey: number }) {
  const online = useLive((s) => s.apiOnline);
  const { data, loading, reload } = useFetch(() => dev.backups(), [online, refreshKey], { enabled: online !== false });
  const [busy, setBusy] = useState<string | null>(null);
  const restore = async (id: string) => {
    const ok = await confirmDialog({ title: 'Przywrócić kopię zapasową?', text: 'Pliki z tej kopii nadpiszą bieżące wersje w app/. Może być potrzebny restart serwera.', confirm: 'Przywróć', danger: true });
    if (!ok) return;
    setBusy(id);
    try {
      await dev.restore(id);
      ui.toast('Przywrócono kopię zapasową', 'success');
      reload();
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    } finally {
      setBusy(null);
    }
  };
  return (
    <Card>
      <CardHeader icon={RotateCcw} title="Kopie zapasowe" subtitle="Backups\dev — tworzone przed każdą zmianą" />
      <div className="space-y-2 px-5 pb-5">
        {loading && !data && [0, 1].map((k) => <Skeleton key={k} className="h-12" />)}
        {data && !data.length && <div className="text-[12.5px] text-subtle">Brak kopii zapasowych.</div>}
        {(data || [])
          .slice()
          .sort((a, b) => b.ts - a.ts)
          .map((b) => (
            <div key={b.id} className="flex items-center gap-3 rounded-xl border border-border px-3 py-2">
              <div className="min-w-0 flex-1">
                <div className="text-[12.5px] font-medium">{when(b.ts)}</div>
                <div className="truncate font-mono text-[11px] text-muted" title={b.files.join('\n')}>
                  {b.files.length} plików: {b.files.slice(0, 3).join(', ')}
                  {b.files.length > 3 ? '…' : ''}
                </div>
              </div>
              <Button size="xs" variant="secondary" icon={RotateCcw} loading={busy === b.id} onClick={() => restore(b.id)}>
                Przywróć
              </Button>
            </div>
          ))}
      </div>
    </Card>
  );
}

export default function DeveloperPage() {
  const [request, setRequest] = useState('');
  const [files, setFiles] = useState('');
  const [proposal, setProposal] = useState<DevProposal | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [testGroup, setTestGroup] = useState<string | null>(null);
  const [backupKey, setBackupKey] = useState(0);
  const [applied, setApplied] = useState<string | null>(null);

  const propose = async () => {
    if (!request.trim()) return ui.toast('Opisz, co ma zostać zmienione', 'warn');
    setBusy('propose');
    setProposal(null);
    setApplied(null);
    try {
      const list = files.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);
      setProposal(await dev.propose(request.trim(), list));
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    } finally {
      setBusy(null);
    }
  };
  const apply = async () => {
    if (!proposal) return;
    const ok = await confirmDialog({ title: 'Zastosować zmianę?', text: `Zmienione zostaną pliki: ${proposal.files.join(', ')}. Przed zmianą powstanie kopia zapasowa w Backups\\dev.`, confirm: 'Zatwierdź i zastosuj' });
    if (!ok) return;
    setBusy('apply');
    try {
      const r = await dev.apply(proposal.id);
      setApplied(r.backup);
      ui.toast('Zastosowano zmianę — kopia zapasowa utworzona', 'success');
      setProposal(null);
      setBackupKey((k) => k + 1);
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    } finally {
      setBusy(null);
    }
  };
  const tests = async () => {
    setBusy('tests');
    try {
      const r = await dev.tests();
      setTestGroup(r.group);
      ui.toast('Uruchomiono testy (pytest)', 'info');
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        icon={CodeXml}
        title="AI Developer"
        subtitle="Tryb bezpieczny: każda zmiana kodu wymaga Twojego zatwierdzenia i tworzy kopię zapasową."
        right={
          <Button variant="secondary" icon={FlaskConical} loading={busy === 'tests'} onClick={tests}>
            Uruchom testy
          </Button>
        }
      />
      {testGroup && (
        <Card className="px-5 py-3">
          <GroupProgress group={testGroup} label="Testy jednostkowe (pytest) — wyniki w strumieniu pracy" />
        </Card>
      )}
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1.6fr_1fr]">
        <div className="space-y-5">
          <Card>
            <CardHeader icon={Sparkles} title="Prośba o zmianę" subtitle="Opisz po polsku, co poprawić lub dodać w aplikacji" />
            <div className="space-y-3 px-5 pb-5">
              <Textarea rows={4} value={request} onChange={(e) => setRequest(e.target.value)} placeholder="Np. „Dodaj do eksportu preset Vimeo 1080p z AAC 256k”" />
              <Field label="Pliki (opcjonalnie)" hint="Ścieżki względne w app/, rozdzielone przecinkami — zawęża kontekst modelu.">
                <Input value={files} onChange={(e) => setFiles(e.target.value)} placeholder="studio/render/engine.py, studio/exports/premiere.py" className="font-mono !text-[12px]" />
              </Field>
              <div className="flex justify-end">
                <Button variant="primary" icon={GitCompare} loading={busy === 'propose'} onClick={propose}>
                  Przygotuj propozycję
                </Button>
              </div>
            </div>
          </Card>
          {applied && (
            <Card className="border-success/30 bg-success/5 px-5 py-3 text-[12.5px]">
              <CircleCheck size={14} className="mr-1.5 inline text-success" /> Zmiana zastosowana. Kopia zapasowa: <span className="font-mono">{applied}</span>
            </Card>
          )}
          {busy === 'propose' && <Skeleton className="h-[260px] rounded-2xl" />}
          {proposal && (
            <Card>
              <CardHeader
                icon={GitCompare}
                title="Propozycja zmian"
                subtitle={proposal.summary}
                right={proposal.files.map((f) => (
                  <Badge key={f} className="font-mono">
                    {f}
                  </Badge>
                ))}
              />
              <div className="space-y-3 px-5 pb-5">
                {proposal.diff ? <DiffView diff={proposal.diff} /> : <div className="text-[12.5px] text-muted">Brak zmian w kodzie.</div>}
                <div className="flex items-center justify-between gap-3">
                  <div className="text-[11.5px] text-muted">Przed zastosowaniem zmienione pliki zostaną skopiowane do Backups\dev\&lt;data&gt;.</div>
                  <div className="flex gap-2">
                    <Button variant="ghost" icon={X} onClick={() => setProposal(null)}>
                      Odrzuć
                    </Button>
                    <Button variant="primary" icon={CircleCheck} loading={busy === 'apply'} onClick={apply} disabled={!proposal.diff}>
                      Zatwierdź i zastosuj
                    </Button>
                  </div>
                </div>
              </div>
            </Card>
          )}
          {!proposal && busy !== 'propose' && (
            <Card>
              <EmptyState icon={GitCompare} title="Brak propozycji" compact>
                Po opisaniu zmiany AI Developer przygotuje kolorowany diff do przejrzenia. Nic nie zostanie zmienione bez Twojej zgody.
              </EmptyState>
            </Card>
          )}
        </div>
        <div className="space-y-5">
          <Diagnostics />
          <Backups refreshKey={backupKey} />
        </div>
      </div>
    </div>
  );
}
