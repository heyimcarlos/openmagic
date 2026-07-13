import {
  CheckIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CircleIcon,
  CircleXIcon,
  Clock3Icon,
  GitBranchIcon,
  Loader2Icon,
  ShieldCheckIcon,
  WrenchIcon,
} from 'lucide-react';

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion';
import { cn } from '@/lib/utils';
import type {
  AgentActivity,
  ChatTurnTelemetry,
  WorkflowStage,
  WorkflowTelemetry as WorkflowTelemetryData,
} from '@/lib/chatTelemetry';

interface WorkflowTelemetryProps {
  telemetry: ChatTurnTelemetry;
  className?: string;
}

export function WorkflowTelemetry({ telemetry, className }: WorkflowTelemetryProps) {
  return (
    <Accordion
      multiple
      className={cn('mt-3 gap-0.5', className)}
      aria-label="Agent activity and Workflow progress"
    >
      <ActivityDisclosure summary={telemetry.activitySummary} activity={telemetry.activity} />

      {telemetry.workflows.map((workflow) => (
        <WorkflowDisclosure key={workflow.id} workflow={workflow} />
      ))}
    </Accordion>
  );
}

function ActivityDisclosure({
  summary,
  activity,
}: {
  summary: string;
  activity: ReadonlyArray<AgentActivity>;
}) {
  return (
    <AccordionItem
      value="turn-activity"
      disabled={activity.length === 0}
      className="border-0 not-last:border-b-0"
    >
      <TelemetryTrigger icon={WrenchIcon} summary={summary} />
      {activity.length > 0 && (
        <AccordionContent className="pb-1">
          <TelemetryRows rows={activity} />
        </AccordionContent>
      )}
    </AccordionItem>
  );
}

function WorkflowDisclosure({ workflow }: { workflow: WorkflowTelemetryData }) {
  const summary = (
    <>
      <span className="min-w-0 truncate">{workflow.title}</span>
      <span className="shrink-0">· {workflow.statusLabel}</span>
    </>
  );

  return (
    <AccordionItem
      value={`workflow-${workflow.id}`}
      disabled={workflow.stages.length === 0}
      className="border-0 not-last:border-b-0"
    >
      <TelemetryTrigger icon={GitBranchIcon} summary={summary} />
      {workflow.stages.length > 0 && (
        <AccordionContent className="pb-1">
          <TelemetryRows rows={workflow.stages} />
        </AccordionContent>
      )}
    </AccordionItem>
  );
}

function TelemetryTrigger({
  icon: Icon,
  summary,
}: {
  icon: typeof WrenchIcon;
  summary: React.ReactNode;
}) {
  return (
    <AccordionTrigger className="min-h-8 justify-start rounded-md px-0 py-1.5 text-xs font-normal text-muted-foreground hover:no-underline disabled:opacity-100 [&>[data-slot=accordion-trigger-icon]]:hidden">
      <span className="flex min-w-0 items-center gap-2">
        <Icon className="size-3.5 shrink-0" />
        <span className="flex min-w-0 items-center gap-1">{summary}</span>
        <DisclosureChevron />
      </span>
    </AccordionTrigger>
  );
}

function TelemetryRows({
  rows,
}: {
  rows: ReadonlyArray<AgentActivity | WorkflowStage>;
}) {
  return (
    <div className="space-y-1 py-1">
      {rows.map((row) => (
        <div key={row.id} className="flex min-h-7 items-center gap-2 text-xs text-muted-foreground">
          <StatusIcon row={row} />
          <p className="min-w-0 font-normal">{row.label}</p>
        </div>
      ))}
    </div>
  );
}

function DisclosureChevron() {
  return (
    <span
      aria-hidden="true"
      className="size-3 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover/accordion-trigger:opacity-100 group-focus-visible/accordion-trigger:opacity-100 group-aria-expanded/accordion-trigger:opacity-100 group-aria-disabled/accordion-trigger:hidden"
    >
      <ChevronRightIcon className="size-3 group-aria-expanded/accordion-trigger:hidden" />
      <ChevronDownIcon className="hidden size-3 group-aria-expanded/accordion-trigger:block" />
    </span>
  );
}

function StatusIcon({ row }: { row: AgentActivity | WorkflowStage }) {
  const Icon = row.status === 'succeeded' || row.status === 'satisfied'
    ? CheckIcon
    : row.status === 'running'
      ? Loader2Icon
      : row.status === 'waiting' || row.status === 'queued'
        ? Clock3Icon
        : row.status === 'failed'
          ? CircleXIcon
          : 'kind' in row && row.kind === 'checkpoint'
            ? ShieldCheckIcon
            : CircleIcon;

  return (
    <span
      role="img"
      aria-label={statusLabel(row.status)}
      className="flex size-3.5 shrink-0 items-center justify-center text-muted-foreground"
    >
      <Icon className={cn('size-3.5', row.status === 'running' && 'animate-spin')} />
    </span>
  );
}

function statusLabel(status: AgentActivity['status'] | WorkflowStage['status']): string {
  return {
    succeeded: 'Succeeded',
    satisfied: 'Satisfied',
    running: 'Running',
    waiting: 'Waiting',
    queued: 'Queued',
    unavailable: 'Not ready',
    failed: 'Failed',
    cancelled: 'Cancelled',
  }[status];
}
