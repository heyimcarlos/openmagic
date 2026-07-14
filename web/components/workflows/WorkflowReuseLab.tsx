'use client';

// One editable interaction canvas with selectable route visibility.

import { useCallback, useEffect, useReducer, useState, type Dispatch } from 'react';
import { FlaskConicalIcon } from 'lucide-react';

import { AppViewNav } from '@/components/app/AppViewNav';
import { WorkflowReuseCanvasVariant } from '@/components/workflows/WorkflowReuseCanvasVariant';
import {
  createPrototypeState,
  prototypeReducer,
  simulationFinished,
  type PrototypeAction,
  type PrototypeState,
  type ScenarioKey,
} from '@/lib/workflowReusePrototype';

export interface WorkflowReuseVariantProps {
  state: PrototypeState;
  dispatch: Dispatch<PrototypeAction>;
  playing: boolean;
  setPlaying: (playing: boolean) => void;
  speed: number;
  setSpeed: (speed: number) => void;
  selectScenario: (scenarioKey: ScenarioKey) => void;
}

export function WorkflowReuseLab() {
  const [state, dispatch] = useReducer(prototypeReducer, undefined, () => createPrototypeState());
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const finished = simulationFinished(state);

  useEffect(() => {
    if (!playing || finished) return;
    const timer = window.setInterval(() => {
      dispatch({ type: 'advance' });
    }, 1200 / speed);
    return () => window.clearInterval(timer);
  }, [finished, playing, speed]);

  useEffect(() => {
    if (finished) setPlaying(false);
  }, [finished]);

  const selectScenario = useCallback((scenarioKey: ScenarioKey) => {
    setPlaying(false);
    dispatch({ type: 'select', scenarioKey });
  }, []);
  const props: WorkflowReuseVariantProps = {
    state,
    dispatch,
    playing,
    setPlaying,
    speed,
    setSpeed,
    selectScenario,
  };

  return (
    <main className="flex h-dvh min-h-[42rem] flex-col overflow-hidden bg-background text-foreground">
      <header className="shrink-0 border-b bg-card/95 px-4 py-2.5 backdrop-blur sm:px-6">
        <div className="mx-auto flex max-w-[120rem] items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="grid size-9 place-items-center rounded-full bg-slate-950 text-xs font-bold text-white">
              OM
            </div>
            <div className="hidden sm:block">
              <p className="font-serif text-base font-semibold tracking-tight">OpenMagic</p>
              <p className="flex items-center gap-1.5 text-[0.58rem] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                <FlaskConicalIcon className="size-3 text-cyan-600" />
                Workflow reuse prototype
              </p>
            </div>
          </div>
          <AppViewNav />
        </div>
      </header>

      <div className="min-h-0 flex-1">
        <WorkflowReuseCanvasVariant {...props} />
      </div>
    </main>
  );
}
