import { AnimatePresence, motion } from 'framer-motion';
import { useCallback, useEffect, useState } from 'react';
import { errMsg } from '../api/client';
import { projects as P } from '../api/endpoints';
import { groupProgress, NoProject, useGroupTasks } from '../components/common';
import { BottomPanel } from '../components/studio/BottomPanel';
import { ChatPanel } from '../components/studio/ChatPanel';
import { MediaPanel } from '../components/studio/MediaPanel';
import { MontageWizard } from '../components/studio/MontageWizard';
import { Player } from '../components/studio/Player';
import { TimelinePanel } from '../components/studio/Timeline';
import { Button, Dialog, IconButton, Tip } from '../components/ui';
import { cx, pct } from '../lib/format';
import { isTypingTarget } from '../lib/hooks';
import {
  Keyboard,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Redo2,
  ShieldCheck,
  Sparkles,
  Undo2,
} from '../lib/icons';
import { player, usePlayer } from '../store/player';
import { project, useProject } from '../store/project';
import { ui, useUI } from '../store/ui';

const SHORTCUTS: [string, string][] = [
  ['Spacja', 'Odtwórz / pauza'],
  ['I / O', 'Ustaw IN / OUT zakresu'],
  ['L', 'Pętla zakresu'],
  ['← / →', '±1 klatka'],
  ['Shift + ← / →', '±1 sekunda'],
  ['Home / End', 'Początek / koniec'],
  ['Esc', 'Usuń zaznaczenie'],
  ['Ctrl + Z', 'Cofnij'],
  ['Ctrl + Shift + Z', 'Ponów'],
  ['Przeciągnij po linijce', 'Zaznacz zakres IN/OUT'],
  ['Shift + przeciągnij po ścieżkach', 'Zaznacz / rozszerz zakres'],
  ['Dwuklik na klipie', 'Zaznacz zakres klipu'],
  ['Ctrl + kółko', 'Zoom osi czasu'],
];

function PipelinePill() {
  const group = useUI((s) => s.pipelineGroup);
  const tasks = useGroupTasks(group);
  if (!group) return null;
  const g = groupProgress(tasks);
  const done = g.total > 0 && g.finished;
  return (
    <motion.button
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      onClick={() => ui.openWizard(true)}
      className={cx(
        'flex h-8 items-center gap-2 rounded-full border px-3 text-[12px] font-medium transition-colors cursor-pointer',
        done ? (g.failed ? 'border-warn/40 bg-warn/10 text-warn' : 'border-success/40 bg-success/10 text-success') : 'border-accent/30 bg-accent/8 text-accent',
      )}
    >
      {!done && <motion.span className="h-2 w-2 rounded-full bg-accent" animate={{ opacity: [1, 0.3, 1] }} transition={{ duration: 1.2, repeat: Infinity }} />}
      {done ? (g.failed ? 'Montaż zakończony z błędami' : 'Montaż gotowy') : `Montaż w toku · ${pct(g.progress)}`}
    </motion.button>
  );
}

export default function StudioPage() {
  const pid = useProject((s) => s.activeId);
  const proj = useProject((s) => s.projects.find((p) => p.id === s.activeId));
  const tl = useProject((s) => s.timeline);
  const [showMedia, setShowMedia] = useState(true);
  const [showChat, setShowChat] = useState(true);
  const [help, setHelp] = useState(false);
  const [qcBusy, setQcBusy] = useState(false);

  const undo = useCallback(async () => {
    const id = useProject.getState().activeId;
    if (!id) return;
    try {
      const t = await P.undo(id);
      project.setTimeline(t);
      project.loadVersions();
      ui.toast(`Cofnięto — wersja v${t?.version ?? '?'}`, 'info', 2500);
    } catch (e) {
      ui.toast(errMsg(e), 'warn');
    }
  }, []);

  const redo = useCallback(async () => {
    const id = useProject.getState().activeId;
    if (!id) return;
    try {
      const t = await P.redo(id);
      project.setTimeline(t);
      project.loadVersions();
      ui.toast(`Ponowiono — wersja v${t?.version ?? '?'}`, 'info', 2500);
    } catch (e) {
      ui.toast(errMsg(e), 'warn');
    }
  }, []);

  const runQc = async () => {
    if (!pid) return;
    setQcBusy(true);
    try {
      const r = await P.runQc(pid);
      ui.toast('Nadzorca Jakości sprawdza film…', 'info');
      ui.setBottom(true, 'qc');
      if (r?.group) useUI.setState({ focusGroup: r.group });
    } catch (e) {
      ui.toast(errMsg(e), 'error');
    } finally {
      setQcBusy(false);
    }
  };

  // skróty klawiszowe Studio
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target) || useUI.getState().wizardOpen) return;
      if (document.querySelector('[role="dialog"]')) return;
      const ctrl = e.ctrlKey || e.metaKey;
      const k = e.key;
      if (ctrl && (k === 'z' || k === 'Z')) {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
        return;
      }
      if (ctrl && (k === 'y' || k === 'Y')) {
        e.preventDefault();
        redo();
        return;
      }
      if (ctrl || e.altKey) return;
      switch (k) {
        case ' ':
          e.preventDefault();
          player.toggle();
          break;
        case 'i':
        case 'I':
          player.setIn();
          break;
        case 'o':
        case 'O':
          player.setOut();
          break;
        case 'l':
        case 'L':
          player.toggleLoop();
          break;
        case 'ArrowLeft':
          e.preventDefault();
          if (e.shiftKey) player.seek(usePlayer.getState().time - 1);
          else player.step(-1);
          break;
        case 'ArrowRight':
          e.preventDefault();
          if (e.shiftKey) player.seek(usePlayer.getState().time + 1);
          else player.step(1);
          break;
        case 'Home':
          e.preventDefault();
          player.seek(0);
          break;
        case 'End':
          e.preventDefault();
          player.seek(usePlayer.getState().duration);
          break;
        case 'Escape':
          player.setRange(null);
          break;
        case '?':
          setHelp((h) => !h);
          break;
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [undo, redo]);

  // pauza przy wyjściu ze Studio
  useEffect(() => () => player.pause(), []);

  if (!pid) return <NoProject what="Studio Montażowe" />;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* pasek Studio */}
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border bg-surface/80 px-3 backdrop-blur">
        <IconButton icon={showMedia ? PanelLeftClose : PanelLeftOpen} label={showMedia ? 'Ukryj media' : 'Pokaż media'} size="sm" onClick={() => setShowMedia((v) => !v)} />
        <div className="min-w-0">
          <div className="truncate text-[13.5px] font-semibold leading-tight">{proj?.name || 'Projekt'}</div>
          <div className="truncate text-[10.5px] text-muted">
            {tl ? `Oś czasu v${tl.version} · ${tl.width}×${tl.height} · ${tl.fps} fps` : 'Brak osi czasu'}
          </div>
        </div>
        <div className="mx-2 h-6 w-px bg-border" />
        <Tip text="Cofnij (Ctrl+Z)">
          <Button size="sm" variant="ghost" icon={Undo2} onClick={undo}>
            Cofnij
          </Button>
        </Tip>
        <Tip text="Ponów (Ctrl+Shift+Z)">
          <Button size="sm" variant="ghost" icon={Redo2} onClick={redo}>
            Ponów
          </Button>
        </Tip>
        <div className="flex-1" />
        <AnimatePresence>
          <PipelinePill />
        </AnimatePresence>
        <IconButton icon={Keyboard} label="Skróty klawiszowe (?)" size="sm" onClick={() => setHelp(true)} />
        <Button size="sm" variant="secondary" icon={ShieldCheck} loading={qcBusy} onClick={runQc}>
          Sprawdź jakość
        </Button>
        <Button size="sm" variant="primary" icon={Sparkles} onClick={() => ui.openWizard(true)}>
          Zmontuj film
        </Button>
        <IconButton icon={MessageSquare} label={showChat ? 'Ukryj czat' : 'Pokaż czat'} size="sm" active={showChat} onClick={() => setShowChat((v) => !v)} />
      </div>

      <div className="flex min-h-0 flex-1">
        <AnimatePresence initial={false}>
          {showMedia && (
            <motion.div
              key="media"
              initial={{ width: 0, opacity: 0 }}
              animate={{ width: 272, opacity: 1 }}
              exit={{ width: 0, opacity: 0 }}
              transition={{ type: 'spring', stiffness: 400, damping: 40 }}
              className="shrink-0 overflow-hidden border-r border-border"
            >
              <div className="h-full w-[272px]">
                <MediaPanel />
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="min-h-[180px] flex-1">
            <Player />
          </div>
          <div className="h-[272px] shrink-0">
            <TimelinePanel onUndo={undo} onRedo={redo} />
          </div>
          <BottomPanel />
        </div>

        <AnimatePresence initial={false}>
          {showChat && (
            <motion.div
              key="chat"
              initial={{ width: 0, opacity: 0 }}
              animate={{ width: 352, opacity: 1 }}
              exit={{ width: 0, opacity: 0 }}
              transition={{ type: 'spring', stiffness: 400, damping: 40 }}
              className="shrink-0 overflow-hidden border-l border-border"
            >
              <div className="h-full w-[352px]">
                <ChatPanel />
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <MontageWizard />
      <Dialog open={help} onClose={() => setHelp(false)} icon={Keyboard} title="Skróty klawiszowe" width={460}>
        <div className="divide-y divide-border px-5 py-2">
          {SHORTCUTS.map(([k, d]) => (
            <div key={k} className="flex items-center justify-between py-2 text-[12.5px]">
              <span className="text-muted">{d}</span>
              <span className="flex gap-1">
                {k.split(' + ').map((p) => (
                  <kbd key={p}>{p}</kbd>
                ))}
              </span>
            </div>
          ))}
        </div>
      </Dialog>
    </div>
  );
}
