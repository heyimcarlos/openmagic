import { GitBranchIcon, WorkflowIcon } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import type { CockpitJob, WorkflowCockpitSnapshot } from '@/lib/chatTelemetry';

interface WorkflowGraphProps {
  snapshot?: WorkflowCockpitSnapshot;
}

const statusPresentation: Record<CockpitJob['status'], { badge: string; dot: string }> = {
  waiting: {
    badge: 'border-amber-200 bg-amber-50 text-amber-800',
    dot: 'bg-amber-500',
  },
  running: {
    badge: 'border-blue-200 bg-blue-50 text-blue-700',
    dot: 'bg-blue-500',
  },
  succeeded: {
    badge: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    dot: 'bg-emerald-500',
  },
  cancelled: {
    badge: 'border-border bg-muted text-muted-foreground',
    dot: 'bg-muted-foreground/50',
  },
  queued: {
    badge: 'border-sky-200 bg-sky-50 text-sky-700',
    dot: 'bg-sky-500',
  },
  failed: {
    badge: 'border-red-200 bg-red-50 text-red-700',
    dot: 'bg-red-500',
  },
};

export function WorkflowGraph({ snapshot }: WorkflowGraphProps) {
  return (
    <section className="h-full bg-[#fcfaf6] px-5 py-5">
      <div className="mb-5">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <GitBranchIcon className="size-4 text-primary" />
          Durable graph
        </h2>
        <p className="mt-1 text-[0.625rem] font-medium uppercase tracking-[0.14em] text-muted-foreground">
          Workflow state projection
        </p>
      </div>

      {!snapshot || snapshot.jobs.length === 0 ? (
        <div className="grid min-h-72 place-items-center rounded-xl border border-dashed bg-background/70 p-8 text-center">
          <div>
            <WorkflowIcon className="mx-auto size-6 text-muted-foreground/40" />
            <p className="mt-3 text-sm font-medium text-muted-foreground">No active Workflow</p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground/70">
              Send the renewal request to create the graph.
            </p>
          </div>
        </div>
      ) : (
        <div className="space-y-4">
          <Card className="gap-0 py-0">
            <CardHeader className="grid-cols-[1fr_auto] gap-3 px-4 py-4">
              <div>
                <p className="text-[0.625rem] font-semibold uppercase tracking-[0.12em] text-primary">
                  {snapshot.workflow.kind}
                </p>
                <CardTitle className="mt-2 text-sm">{snapshot.workflow.objective}</CardTitle>
                <p className="mt-1 font-mono text-[0.625rem] text-muted-foreground">
                  {snapshot.workflow.id.slice(0, 8)} · {snapshot.workflow.organization}
                </p>
              </div>
              <Badge
                variant="outline"
                className={cn(
                  snapshot.workflow.status === 'completed'
                    ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                    : 'border-blue-200 bg-blue-50 text-blue-700',
                )}
              >
                {snapshot.workflow.status}
              </Badge>
            </CardHeader>
          </Card>

          <div className="relative ml-3 space-y-3 border-l pl-5">
            {snapshot.jobs.map((job) => (
              <JobCard key={job.id} job={job} />
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function JobCard({ job }: { job: CockpitJob }) {
  return (
    <Card className="relative gap-0 py-0">
      <span
        className={cn(
          'absolute -left-[1.65rem] top-5 size-2.5 rounded-full ring-4 ring-[#fcfaf6]',
          statusPresentation[job.status].dot,
        )}
      />
      <CardContent className="flex items-start justify-between gap-3 px-4 py-4">
        <div className="min-w-0">
          <h3 className="text-xs font-semibold">{job.title}</h3>
          <p className="mt-1 text-[0.625rem] text-muted-foreground">{job.detail}</p>
        </div>
        <Badge
          variant="outline"
          className={cn('text-[0.625rem]', statusPresentation[job.status].badge)}
        >
          {job.status}
        </Badge>
      </CardContent>
    </Card>
  );
}
