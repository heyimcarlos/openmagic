import {
  ChevronDownIcon,
  ChevronRightIcon,
  GitBranchIcon,
  WrenchIcon,
} from 'lucide-react';

import { StatusIcon } from './StatusIcon';
import type { PrototypeActivity, PrototypeTelemetryFixture, PrototypeWorkflow } from './types';
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion';
import { cn } from '@/lib/utils';

type ChevronVisibility = 'hover' | 'always';

interface CodexRailProps {
  fixture: PrototypeTelemetryFixture;
  chevronVisibility: ChevronVisibility;
}

export function CodexRail({ fixture, chevronVisibility }: CodexRailProps) {
  const workflowCount = fixture.workflows.length;
  const activitySummary = workflowCount === 0
    ? 'Found context, no workflows advanced'
    : workflowCount === 1
      ? 'Found context, advanced 1 workflow'
      : `Found context, advanced ${workflowCount} workflows`;

  return (
    <Accordion multiple className="mt-3 gap-0.5" aria-label="Workflow telemetry, Codex rail">
      <AccordionItem value="turn-activity" className="border-0 not-last:border-b-0">
        <AccordionTrigger className="min-h-8 justify-start rounded-md px-0 py-1.5 text-xs font-normal text-muted-foreground hover:no-underline [&>[data-slot=accordion-trigger-icon]]:hidden">
          <span className="flex min-w-0 items-center gap-2">
            <WrenchIcon className="size-3.5 shrink-0" />
            <span className="truncate">{activitySummary}</span>
            <CodexChevron visibility={chevronVisibility} />
          </span>
        </AccordionTrigger>
        <AccordionContent className="pb-1">
          <ActivityRail activity={fixture.turnActivity} />
        </AccordionContent>
      </AccordionItem>

      {fixture.workflows.map((workflow) => (
        <WorkflowRail
          key={workflow.id}
          workflow={workflow}
          chevronVisibility={chevronVisibility}
        />
      ))}
    </Accordion>
  );
}

function ActivityRail({ activity }: { activity: ReadonlyArray<PrototypeActivity> }) {
  return (
    <div className="space-y-1 px-0 py-1">
      {activity.map((item) => (
        <div key={item.id} className="flex min-h-7 items-center gap-2 text-xs">
          <StatusIcon status={item.status} className="size-3.5 border-0 bg-transparent text-muted-foreground" />
          <p className="min-w-0 font-normal text-muted-foreground">{item.label}</p>
        </div>
      ))}
    </div>
  );
}

function WorkflowRail({
  workflow,
  chevronVisibility,
}: {
  workflow: PrototypeWorkflow;
  chevronVisibility: ChevronVisibility;
}) {
  return (
    <AccordionItem value={workflow.id} className="border-0 not-last:border-b-0">
      <AccordionTrigger className="min-h-8 justify-start rounded-md px-0 py-1.5 text-xs font-normal hover:no-underline [&>[data-slot=accordion-trigger-icon]]:hidden">
        <span className="flex min-w-0 items-center gap-2">
          <GitBranchIcon className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="min-w-0 truncate text-muted-foreground">{workflow.title}</span>
          <span className="shrink-0 text-muted-foreground">· {workflow.statusLabel}</span>
          <CodexChevron visibility={chevronVisibility} />
        </span>
      </AccordionTrigger>
      <AccordionContent className="pb-1">
        <div className="space-y-1 px-0 py-1">
          {workflow.stages.map((stage) => (
            <div key={stage.id} className="flex min-h-7 items-center gap-2">
              <StatusIcon
                status={stage.status}
                kind={stage.kind}
                className="size-3.5 border-0 bg-transparent text-muted-foreground"
              />
              <p className="min-w-0 text-xs font-normal text-muted-foreground">{stage.label}</p>
            </div>
          ))}
        </div>
      </AccordionContent>
    </AccordionItem>
  );
}

function CodexChevron({ visibility }: { visibility: ChevronVisibility }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        'size-3 shrink-0 text-muted-foreground transition-opacity',
        visibility === 'hover' && 'opacity-0 group-hover/accordion-trigger:opacity-100 group-focus-visible/accordion-trigger:opacity-100 group-aria-expanded/accordion-trigger:opacity-100',
      )}
    >
      <ChevronRightIcon className="size-3 group-aria-expanded/accordion-trigger:hidden" />
      <ChevronDownIcon className="hidden size-3 group-aria-expanded/accordion-trigger:block" />
    </span>
  );
}
