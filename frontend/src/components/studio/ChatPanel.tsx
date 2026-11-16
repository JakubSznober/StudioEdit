// Czat reżyserski (AI Chat – Command Center)
import { AnimatePresence, motion } from 'framer-motion';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { errMsg } from '../../api/client';
import { projects as P } from '../../api/endpoints';
import { agentMeta } from '../../lib/agents';
import { clock, cx, tc } from '../../lib/format';
import { CircleCheck, CircleX, MessageSquare, Scissors, Send, Sparkles, X } from '../../lib/icons';
import { player, usePlayer } from '../../store/player';
import { project, useProject } from '../../store/project';
import { ui } from '../../store/ui';
import type { ChatAction, ChatMessage } from '../../types';
import { AgentAvatar, GroupProgress } from '../common';
import { Skeleton } from '../ui';

export const SUGGESTIONS = [
  'Zmontuj dynamiczny film na YouTube, max 4 min, ciepłe kolory',
  'Zrób z tego wersję 9:16 na TikToka',
  'Tutaj zrób slow-motion',
  'Przyciemnij niebo',
  'Usuń ten obiekt',
  'Dodaj lektora: ',
  'Dodaj napisy',
  'Sprawdź jakość',
  'Wyeksportuj do Premiere',
];

function actionInfo(a: ChatAction): { agent?: string; title: string; detail?: string; status?: string } {
  if (typeof a === 'string') return { title: a };
  const o = a as Record<string, unknown>;
  const s = (k: string) => (typeof o[k] === 'string' ? (o[k] as string) : undefined);
  const title = s('summary') || s('label') || s('title') || s('text') || s('description') || s('action') || s('type') || s('kind') || 'Akcja';
  let detail = s('detail') || s('message');
  if (!detail && o.params && typeof o.params === 'object') {
    const p = Object.entries(o.params as Record<string, unknown>)
      .filter(([, v]) => v != null && typeof v !== 'object')
      .slice(0, 4)
      .map(([k, v]) => `${k}: ${v}`)
      .join(' · ');
    detail = p || undefined;
  }
  if (!detail && s('action') && title !== s('action')) detail = s('action');
  return { agent: s('agent'), title, detail, status: s('status') };
}

function ActionCard({ a }: { a: ChatAction }) {
  const info = actionInfo(a);
  const m = agentMeta(info.agent || 'system');
  const failed = info.status === 'failed' || info.status === 'error';
  const done = info.status === 'done' || info.status === 'ok' || info.status === 'applied';
  return (
    <div className="flex items-center gap-2 rounded-lg border border-border bg-surface px-2 py-1.5">
      <AgentAvatar agent={info.agent || 'system'} size={20} />
      <div className="min-w-0 flex-1">
        <div className="truncate text-[11.5px] font-medium" title={info.title}>
          {info.title}
        </div>
        <div className="truncate text-[10.5px] text-muted">
          {m.short}
          {info.detail ? ` · ${info.detail}` : ''}
        </div>
      </div>
      {failed ? <CircleX size={14} className="shrink-0 text-danger" /> : done ? <CircleCheck size={14} className="shrink-0 text-success" /> : null}
    </div>
  );
}

function Bubble({ m, fps }: { m: ChatMessage; fps: number }) {
  if (m.role === 'system') {
    return (
      <div className="flex justify-center px-2">
        <div className="max-w-[90%] rounded-full bg-surface-3 px-3 py-1 text-center text-[11px] text-muted">{m.text}</div>
      </div>
    );
  }
  const user = m.role === 'user';
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }} className={cx('flex gap-2', user ? 'flex-row-reverse' : 'flex-row')}>
      {!user && <AgentAvatar agent="producer" size={26} className="mt-0.5" />}
      <div className={cx('flex max-w-[86%] min-w-0 flex-col', user ? 'items-end' : 'items-start')}>
        <div
          className={cx(
            'rounded-2xl px-3 py-2 text-[13px] leading-relaxed break-words whitespace-pre-wrap',
            user ? 'accent-gradient rounded-br-md text-white' : 'rounded-bl-md border border-border bg-surface-2 text-text',
            m.pending && 'opacity-70',
          )}
        >
          {m.range && (
            <button
              onClick={() => {
                player.setRange(m.range!);
                player.seek(m.range!.in);
              }}
              className={cx(
                'tabular mb-1 flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10.5px] font-semibold cursor-pointer',
                user ? 'bg-white/20 text-white' : 'bg-accent/10 text-accent',
              )}
            >
              <Scissors size={10} /> {tc(m.range.in, fps)} – {tc(m.range.out, fps)}
            </button>
          )}
          {m.text}
        </div>
        {m.actions && m.actions.length > 0 && (
          <div className="mt-1.5 w-full space-y-1">
            {m.actions.map((a, i) => (
              <ActionCard key={i} a={a} />
            ))}
          </div>
        )}
        {m.group && (
          <div className="w-full min-w-[220px]">
            <GroupProgress group={m.group} />
          </div>
        )}
        <div className="mt-0.5 px-1 text-[10px] text-subtle">{m.pending ? 'wysyłanie…' : clock(m.ts)}</div>
      </div>
    </motion.div>
  );
}

function Typing() {
  return (
    <div className="flex items-center gap-2">
      <AgentAvatar agent="producer" size={26} busy />
      <div className="flex items-center gap-1 rounded-2xl rounded-bl-md border border-border bg-surface-2 px-3 py-2.5">
        {[0, 1, 2].map((i) => (
          <motion.span key={i} className="h-1.5 w-1.5 rounded-full bg-muted" animate={{ y: [0, -3, 0], opacity: [0.4, 1, 0.4] }} transition={{ duration: 0.9, repeat: Infinity, delay: i * 0.15 }} />
        ))}
        <span className="ml-1.5 text-[11.5px] text-muted">Producent analizuje polecenie…</span>
      </div>
    </div>
  );
}

export function ChatPanel() {
  const pid = useProject((s) => s.activeId);
  const chat = useProject((s) => s.chat);
  const loaded = useProject((s) => s.chatLoaded);
  const fps = useProject((s) => s.timeline?.fps || 30);
  const range = usePlayer((s) => s.range);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [useRange, setUseRange] = useState(true);
  const listRef = useRef<HTMLDivElement | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  const atBottom = useRef(true);

  // nowe zaznaczenie = ponownie dołącz jako kontekst
  useEffect(() => {
    if (range) setUseRange(true);
  }, [range?.in, range?.out]);

  useLayoutEffect(() => {
    const el = listRef.current;
    if (el && atBottom.current) el.scrollTop = el.scrollHeight;
  }, [chat.length, sending]);

  useLayoutEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(160, ta.scrollHeight)}px`;
  }, [text]);

  const send = async (raw?: string) => {
    const body = (raw ?? text).trim();
    if (!body || !pid || sending) return;
    const r = range && useRange ? { in: range.in, out: range.out } : null;
    const tmpId = `tmp_${Date.now()}`;
    project.addChat([{ id: tmpId, ts: Date.now() / 1000, role: 'user', text: body, range: r, pending: true }]);
    setText('');
    setSending(true);
    atBottom.current = true;
    try {
      const res = await P.sendChat(pid, body, r);
      project.removeChat(tmpId);
      const msgs: ChatMessage[] = [];
      if (res?.message) msgs.push(res.message);
      if (res?.reply) msgs.push(res.reply.group || !res.group ? res.reply : { ...res.reply, group: res.group });
      else if (res?.group && res.message) msgs[0] = { ...res.message, group: res.group };
      project.addChat(msgs);
      if (res?.group) ui.focusGroup(res.group, 'stream');
    } catch (e) {
      project.removeChat(tmpId);
      setText(body);
      ui.toast(`Nie wysłano polecenia: ${errMsg(e)}`, 'error');
    } finally {
      setSending(false);
      taRef.current?.focus();
    }
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-surface">
      <div className="flex h-12 shrink-0 items-center gap-2.5 border-b border-border px-4">
        <AgentAvatar agent="producer" size={26} busy={sending} />
        <div className="min-w-0">
          <div className="text-[13px] font-semibold leading-tight">Czat reżyserski</div>
          <div className="text-[10.5px] text-muted">AI Chat · Command Center</div>
        </div>
      </div>

      <div
        ref={listRef}
        onScroll={(e) => {
          const el = e.currentTarget;
          atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
        }}
        className="scroll-thin min-h-0 flex-1 space-y-3 overflow-y-auto px-3 py-4"
      >
        {!loaded && pid && chat.length === 0 && (
          <div className="space-y-3">
            <Skeleton className="h-12 w-3/4" />
            <Skeleton className="ml-auto h-9 w-2/3" />
            <Skeleton className="h-16 w-4/5" />
          </div>
        )}
        {loaded && chat.length === 0 && (
          <div className="flex flex-col items-center px-3 pt-6 text-center">
            <div className="accent-gradient flex h-12 w-12 items-center justify-center rounded-2xl text-white shadow-card">
              <MessageSquare size={21} />
            </div>
            <div className="mt-3 text-[14px] font-semibold">Wydaj polecenie ekipie</div>
            <div className="mt-1 text-[12.5px] text-muted">
              Pisz po polsku, jak do montażysty. Zaznacz fragment na osi czasu (przeciągnij po linijce lub I / O), aby polecenie dotyczyło tylko tego miejsca.
            </div>
          </div>
        )}
        {chat.map((m) => (
          <Bubble key={m.id} m={m} fps={fps} />
        ))}
        {sending && <Typing />}
      </div>

      <div className="shrink-0 border-t border-border p-3">
        {!text && (
          <div className="no-scrollbar -mx-3 mb-2 flex gap-1.5 overflow-x-auto px-3 pb-0.5">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                onClick={() => {
                  if (s.endsWith(': ')) {
                    setText(s);
                    taRef.current?.focus();
                  } else send(s);
                }}
                disabled={!pid || sending}
                className="flex shrink-0 items-center gap-1 rounded-full border border-border bg-surface px-2.5 py-1 text-[11.5px] text-muted transition-colors hover:border-accent/40 hover:text-accent disabled:opacity-50 cursor-pointer"
              >
                <Sparkles size={11} /> {s.replace(/: $/, '…')}
              </button>
            ))}
          </div>
        )}
        <div className="rounded-2xl border border-border bg-surface-2/60 transition-all focus-within:border-accent/50 focus-within:ring-3 focus-within:ring-accent/12">
          <AnimatePresence>
            {range && useRange && (
              <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
                <div className="px-2.5 pt-2.5">
                  <span className="tabular inline-flex items-center gap-1.5 rounded-lg bg-accent/12 py-1 pr-1 pl-2 text-[11.5px] font-medium text-accent">
                    <Scissors size={12} /> Zaznaczony fragment: {tc(range.in, fps)} – {tc(range.out, fps)}
                    <button onClick={() => setUseRange(false)} className="rounded p-0.5 hover:bg-accent/15 cursor-pointer" aria-label="Usuń kontekst zaznaczenia">
                      <X size={12} />
                    </button>
                  </span>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
          <div className="flex items-end gap-2 p-2">
            <textarea
              ref={taRef}
              rows={1}
              value={text}
              disabled={!pid}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  send();
                }
              }}
              placeholder={pid ? 'Np. „Skróć intro o połowę i dodaj muzykę”…' : 'Wybierz projekt, aby rozmawiać z ekipą'}
              className="max-h-40 min-h-[36px] flex-1 resize-none bg-transparent px-1.5 py-2 text-[13px] leading-snug text-text outline-none placeholder:text-subtle"
            />
            <button
              onClick={() => send()}
              disabled={!text.trim() || !pid || sending}
              className="accent-gradient flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-white shadow-card transition-all hover:brightness-110 disabled:opacity-40 disabled:shadow-none cursor-pointer"
              aria-label="Wyślij"
            >
              <Send size={15} />
            </button>
          </div>
        </div>
        <div className="mt-1.5 flex justify-between px-1 text-[10.5px] text-subtle">
          <span>
            <kbd>Enter</kbd> wysyła · <kbd>Shift</kbd>+<kbd>Enter</kbd> nowa linia
          </span>
        </div>
      </div>
    </div>
  );
}
