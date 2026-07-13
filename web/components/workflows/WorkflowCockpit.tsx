'use client';

import { useMemo, useState } from 'react';
import { RotateCcwIcon } from 'lucide-react';

import { AppViewNav } from '@/components/app/AppViewNav';
import { Button } from '@/components/ui/button';
import { CockpitConversation } from '@/components/workflows/CockpitConversation';
import { WorkflowEventTrace } from '@/components/workflows/WorkflowEventTrace';
import { WorkflowGraph } from '@/components/workflows/WorkflowGraph';
import { buildCockpitSnapshot, type CockpitStage } from '@/lib/workflowCockpit';

const originalBody =
  "Hi John,\n\nYour 2026 renewal is coming up. I'd like to review the options with you this week.\n\nBest,\nCarlos";
const revisedBody =
  "Hi John,\n\nYour 2026 renewal is coming up. I'd like to review the options with you next Tuesday.\n\nBest,\nCarlos";

export function WorkflowCockpit() {
  const [stage, setStage] = useState<CockpitStage>('ready');
  const [revision, setRevision] = useState<1 | 2>(1);
  const [body, setBody] = useState(originalBody);
  const snapshot = useMemo(
    () => buildCockpitSnapshot({ stage, revision }),
    [revision, stage],
  );

  const reset = () => {
    setStage('ready');
    setRevision(1);
    setBody(originalBody);
  };

  const requestChanges = () => {
    setRevision(2);
    setBody(revisedBody);
    setStage('editing');
  };

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
          <div className="flex items-center gap-2">
            <AppViewNav />
            <Button variant="outline" size="sm" onClick={reset}>
              <RotateCcwIcon />
              <span className="hidden sm:inline">Reset</span>
            </Button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-[96rem] p-3 sm:p-5 lg:p-6">
        <div className="grid min-h-[42rem] overflow-hidden rounded-2xl border bg-card shadow-xl shadow-foreground/5 lg:grid-cols-[0.95fr_0.82fr_1.1fr]">
          <CockpitConversation
            stage={stage}
            revision={revision}
            email={{
              from: 'broker@acme.example',
              to: 'john@example.com',
              subject: 'Your 2026 policy renewal',
              body,
            }}
            onStart={() => setStage('approval')}
            onApprove={() => setStage('sent')}
            onRequestChanges={requestChanges}
            onChangeBody={setBody}
            onSubmitRevision={() => setStage('reapproval')}
          />
          <WorkflowGraph snapshot={snapshot} />
          <WorkflowEventTrace events={snapshot.events} />
        </div>
      </div>
    </main>
  );
}
