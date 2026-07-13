import { ActivityIcon, ChevronDownIcon, CircleDotIcon, GitBranchIcon } from 'lucide-react';

import { StatusIcon, statusText } from './StatusIcon';
import type { PrototypeTelemetryFixture } from './types';

export function RequestLedger({ fixture }: { fixture: PrototypeTelemetryFixture }) {
  return (
    <section aria-label="Workflow telemetry, request ledger" className="mt-4 overflow-hidden rounded-2xl border bg-card shadow-sm">
      <div className="border-b bg-gradient-to-r from-slate-50 to-sky-50/70 px-4 py-3.5">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex size-9 items-center justify-center rounded-xl bg-slate-900 text-white shadow-sm">
            <CircleDotIcon className="size-4" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-[0.6875rem] font-semibold uppercase tracking-[0.16em] text-slate-500">Request ledger</p>
            <p className="mt-0.5 text-sm font-semibold">2 workflows are moving independently</p>
          </div>
          <span className="rounded-full border border-sky-200 bg-white px-2.5 py-1 text-[0.6875rem] font-medium text-sky-700">Live</span>
        </div>
      </div>

      <details className="group border-b">
        <summary className="flex cursor-pointer list-none items-center gap-2.5 px-4 py-3 text-sm [&::-webkit-details-marker]:hidden">
          <ActivityIcon className="size-4 text-violet-600" />
          <span className="font-medium">How OpenMagic handled this turn</span>
          <span className="text-xs text-muted-foreground">{fixture.turnActivity.length} steps</span>
          <ChevronDownIcon className="ml-auto size-4 text-muted-foreground transition-transform group-open:rotate-180" />
        </summary>
        <div className="hidden gap-px border-t bg-border group-open:grid sm:grid-cols-3">
          {fixture.turnActivity.map((activity, index) => (
            <div key={activity.id} className="bg-card p-3.5">
              <div className="flex items-center justify-between">
                <span className="text-[0.625rem] font-semibold uppercase tracking-wider text-muted-foreground">Step {index + 1}</span>
                <span className="text-[0.625rem] text-muted-foreground">{activity.duration}</span>
              </div>
              <p className="mt-2 text-xs font-semibold">{activity.label}</p>
              <p className="mt-1 text-[0.6875rem] leading-4 text-muted-foreground">{activity.detail}</p>
            </div>
          ))}
        </div>
      </details>

      <div className="divide-y">
        {fixture.workflows.map((workflow) => (
          <details key={workflow.id} className="group">
            <summary className="cursor-pointer list-none px-4 py-4 [&::-webkit-details-marker]:hidden">
              <div className="flex items-center gap-3">
                <span className="flex size-8 items-center justify-center rounded-full border bg-background text-sky-700">
                  <GitBranchIcon className="size-3.5" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="truncate text-sm font-semibold">{workflow.title}</span>
                    <span className="text-[0.6875rem] text-muted-foreground">{workflow.eyebrow}</span>
                  </span>
                  <span className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
                    <span>{workflow.statusLabel}</span>
                    <span aria-hidden="true">·</span>
                    <span>{workflow.succeededJobs}/{workflow.totalJobs} jobs succeeded</span>
                  </span>
                </span>
                <ChevronDownIcon className="size-4 text-muted-foreground transition-transform group-open:rotate-180" />
              </div>
            </summary>
            <div className="hidden border-t bg-slate-50/60 group-open:grid lg:grid-cols-[1fr_13rem]">
              <div className="px-4 py-4">
                <div className="grid gap-3 sm:grid-cols-3">
                  {workflow.stages.map((stage, index) => (
                    <div key={stage.id} className="relative rounded-xl border bg-card p-3 shadow-sm">
                      <div className="flex items-start justify-between gap-2">
                        <StatusIcon status={stage.status} kind={stage.kind} />
                        <span className="text-[0.625rem] font-medium uppercase tracking-wide text-muted-foreground">{stage.kind === 'checkpoint' ? 'Gate' : `Job ${stage.kind === 'job' ? index === 0 ? '1' : '2' : ''}`}</span>
                      </div>
                      <p className="mt-3 text-xs font-semibold">{stage.label}</p>
                      <p className="mt-1 text-[0.6875rem] leading-4 text-muted-foreground">{stage.detail}</p>
                      <p className="mt-2 text-[0.625rem] font-semibold uppercase tracking-wide text-muted-foreground">{statusText(stage.status)}</p>
                    </div>
                  ))}
                </div>
              </div>
              <div className="border-t bg-card px-4 py-4 lg:border-l lg:border-t-0">
                <p className="text-[0.625rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground">Recent activity</p>
                <div className="mt-3 space-y-3">
                  {workflow.activity.map((activity) => (
                    <div key={activity.id} className="flex gap-2">
                      <StatusIcon status={activity.status} className="size-5" />
                      <div className="min-w-0">
                        <p className="text-[0.6875rem] font-medium">{activity.label}</p>
                        <p className="text-[0.625rem] text-muted-foreground">{activity.duration}</p>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </details>
        ))}
      </div>
    </section>
  );
}
