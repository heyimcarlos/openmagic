'use client';

import { useCallback, useState } from 'react';

import { AppViewNav } from '@/components/app/AppViewNav';
import { CockpitTelemetryPanel } from '@/components/workflows/CockpitTelemetryPanel';
import { WorkflowEventTrace } from '@/components/workflows/WorkflowEventTrace';
import { WorkflowGraph } from '@/components/workflows/WorkflowGraph';
import type { ChatTurnTelemetry } from '@/lib/chatTelemetry';

export function WorkflowCockpit() {
  const [telemetry, setTelemetry] = useState<ChatTurnTelemetry>();
  const receiveTelemetry = useCallback((next: ChatTurnTelemetry | undefined) => {
    setTelemetry(next);
  }, []);
  const snapshot = telemetry?.cockpit;

  return (
    <main className="min-h-screen bg-[#faf7f2] text-foreground">
      <header className="border-b bg-card/95 px-4 py-3 backdrop-blur sm:px-6">
        <div className="mx-auto flex max-w-[96rem] items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="grid size-9 place-items-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
              OM
            </div>
            <div className="hidden sm:block">
              <p className="font-serif text-lg font-semibold tracking-tight">OpenMagic</p>
              <p className="text-[0.625rem] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                Workflow control plane
              </p>
            </div>
          </div>
          <AppViewNav />
        </div>
      </header>

      <div className="mx-auto max-w-[96rem] p-3 sm:p-5 lg:p-6">
        <div className="grid min-h-[42rem] overflow-hidden rounded-2xl border bg-card shadow-xl shadow-foreground/5 lg:grid-cols-[0.95fr_0.82fr_1.1fr]">
          <CockpitTelemetryPanel onTelemetry={receiveTelemetry} />
          <WorkflowGraph snapshot={snapshot} />
          <WorkflowEventTrace events={snapshot?.events ?? []} />
        </div>
      </div>
    </main>
  );
}
