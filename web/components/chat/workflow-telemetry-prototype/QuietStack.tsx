import { ActivityIcon, ChevronDownIcon, GitBranchIcon } from 'lucide-react';

import { StatusIcon, statusText } from './StatusIcon';
import type { PrototypeTelemetryFixture, PrototypeWorkflow } from './types';

export function QuietStack({ fixture }: { fixture: PrototypeTelemetryFixture }) {
  return (
    <section aria-label="Workflow telemetry, quiet stack" className="mt-4 space-y-2.5">
      <details className="group rounded-xl border bg-card shadow-sm open:shadow-md">
        <summary className="flex cursor-pointer list-none items-center gap-3 px-3.5 py-3 [&::-webkit-details-marker]:hidden">
          <span className="flex size-8 items-center justify-center rounded-lg bg-violet-50 text-violet-700">
            <ActivityIcon className="size-4" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-medium">Agent activity</span>
            <span className="block truncate text-xs text-muted-foreground">{fixture.turnActivity.length} completed steps</span>
          </span>
          <ChevronDownIcon className="size-4 text-muted-foreground transition-transform group-open:rotate-180" />
        </summary>
        <div className="hidden border-t px-3.5 py-3 group-open:block">
          <div className="space-y-3">
            {fixture.turnActivity.map((activity) => (
              <div key={activity.id} className="flex gap-3">
                <StatusIcon status={activity.status} className="size-6" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-3">
                    <p className="text-sm font-medium">{activity.label}</p>
                    <span className="shrink-0 text-[0.6875rem] text-muted-foreground">{activity.duration}</span>
                  </div>
                  <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{activity.detail}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </details>

      {fixture.workflows.map((workflow) => <QuietWorkflow key={workflow.id} workflow={workflow} />)}
    </section>
  );
}

function QuietWorkflow({ workflow }: { workflow: PrototypeWorkflow }) {
  const progress = `${Math.round((workflow.succeededJobs / workflow.totalJobs) * 100)}%`;

  return (
    <details className="group rounded-xl border bg-card shadow-sm open:shadow-md">
      <summary className="cursor-pointer list-none px-3.5 py-3 [&::-webkit-details-marker]:hidden">
        <div className="flex items-center gap-3">
          <span className="flex size-8 items-center justify-center rounded-lg bg-sky-50 text-sky-700">
            <GitBranchIcon className="size-4" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium">{workflow.title}</span>
            <span className="block truncate text-xs text-muted-foreground">
              {workflow.succeededJobs} of {workflow.totalJobs} jobs done · {workflow.statusLabel}
            </span>
          </span>
          <ChevronDownIcon className="size-4 text-muted-foreground transition-transform group-open:rotate-180" />
        </div>
        <span className="mt-2.5 block h-1 overflow-hidden rounded-full bg-muted">
          <span className="block h-full rounded-full bg-sky-500 transition-[width]" style={{ width: progress }} />
        </span>
      </summary>
      <div className="hidden border-t px-3.5 py-4 group-open:block">
        <div className="space-y-0">
          {workflow.stages.map((stage, index) => (
            <div key={stage.id} className="relative flex gap-3 pb-5 last:pb-1">
              {index < workflow.stages.length - 1 && <span className="absolute left-3.5 top-7 h-[calc(100%-1.25rem)] w-px bg-border" />}
              <StatusIcon status={stage.status} kind={stage.kind} className="relative z-10" />
              <div className="min-w-0 pt-0.5">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <p className="text-sm font-medium">{stage.label}</p>
                  {stage.kind === 'checkpoint' && <span className="rounded-full bg-amber-50 px-2 py-0.5 text-[0.625rem] font-semibold uppercase tracking-wide text-amber-700">Checkpoint</span>}
                  <span className="text-xs text-muted-foreground">{statusText(stage.status)}</span>
                </div>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">{stage.detail}</p>
              </div>
            </div>
          ))}
        </div>

        <details className="group/activity mt-3 border-t pt-3">
          <summary className="flex cursor-pointer list-none items-center gap-2 text-xs font-medium text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden">
            <ActivityIcon className="size-3.5" />
            Show workflow activity
            <ChevronDownIcon className="ml-auto size-3.5 transition-transform group-open/activity:rotate-180" />
          </summary>
          <div className="mt-3 hidden space-y-2 rounded-lg bg-muted/60 p-3 group-open/activity:block">
            {workflow.activity.map((activity) => (
              <div key={activity.id} className="flex items-start gap-2 text-xs">
                <StatusIcon status={activity.status} className="size-5" />
                <div className="min-w-0 flex-1">
                  <p className="font-medium text-foreground">{activity.label}</p>
                  <p className="mt-0.5 leading-5 text-muted-foreground">{activity.detail}</p>
                </div>
                <span className="shrink-0 text-muted-foreground">{activity.duration}</span>
              </div>
            ))}
          </div>
        </details>
      </div>
    </details>
  );
}
