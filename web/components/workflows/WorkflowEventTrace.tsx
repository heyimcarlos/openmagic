import { DatabaseIcon } from 'lucide-react';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import type { WorkflowEventView } from '@/lib/workflowCockpit';

interface WorkflowEventTraceProps {
  events: ReadonlyArray<WorkflowEventView>;
}

export function WorkflowEventTrace({ events }: WorkflowEventTraceProps) {
  return (
    <Card className="h-full gap-0 rounded-none border-0 bg-[#020617] py-0 text-slate-100 shadow-none">
      <CardHeader className="gap-1 px-5 pb-4 pt-5">
        <CardTitle className="flex items-center gap-2 text-sm font-semibold">
          <DatabaseIcon className="size-4 text-cyan-400" />
          Event trace
        </CardTitle>
        <p className="text-[0.625rem] font-medium uppercase tracking-[0.16em] text-slate-600">
          Meaningful transition history
        </p>
      </CardHeader>
      <CardContent className="px-4 pb-6 sm:px-5">
        {events.length === 0 ? (
          <div className="grid min-h-72 place-items-center rounded-xl border border-dashed border-slate-800 px-8 text-center">
            <div>
              <DatabaseIcon className="mx-auto size-6 text-slate-700" />
              <p className="mt-3 text-sm font-medium text-slate-400">Trace is empty</p>
              <p className="mt-1 text-xs leading-5 text-slate-600">
                Meaningful durable transitions appear here.
              </p>
            </div>
          </div>
        ) : (
          <ol aria-label="Workflow event trace">
            {events.map((event, index) => (
              <EventRow
                key={event.id}
                event={event}
                connected={index < events.length - 1}
              />
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

function EventRow({ event, connected }: { event: WorkflowEventView; connected: boolean }) {
  return (
    <li className="grid grid-cols-[4.75rem_0.75rem_minmax(0,1fr)] gap-3 text-xs">
      <time className="py-2 font-mono text-[0.6875rem] text-slate-600">{event.time}</time>
      <div className="relative flex justify-center">
        <span
          className={cn(
            'mt-3 size-2 rounded-full',
            event.tone === 'terminal' ? 'bg-violet-400' : 'bg-emerald-400',
          )}
        />
        {connected && <span className="absolute bottom-0 top-5 w-px bg-slate-800" />}
      </div>
      <div className="min-w-0 border-b border-slate-900 py-2">
        <p className="break-words font-mono text-[0.6875rem] font-medium text-slate-300">
          {event.type}
        </p>
        <p className="mt-1 text-[0.6875rem] text-slate-600">
          {event.aggregate} · {event.detail}
        </p>
      </div>
    </li>
  );
}
