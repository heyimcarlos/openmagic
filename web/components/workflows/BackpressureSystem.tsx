'use client';

import { useCallback, useEffect, useMemo, useReducer, useState } from 'react';
import {
  ActivityIcon,
  BoxesIcon,
  Clock3Icon,
  DatabaseIcon,
  GaugeIcon,
  LoaderCircleIcon,
  PlusIcon,
  RadioIcon,
  ZapIcon,
} from 'lucide-react';

import { AppViewNav } from '@/components/app/AppViewNav';
import { Button } from '@/components/ui/button';
import { BackpressureFlow } from '@/components/workflows/BackpressureFlow';
import {
  BackpressureTimeline,
  backpressureTimelineReducer,
} from '@/components/workflows/BackpressureTimeline';
import {
  buildBackpressureFlow,
  parseBackpressureSnapshot,
  type BackpressureActivity,
  type BackpressureSnapshot,
} from '@/lib/backpressureDemo';
import { cn } from '@/lib/utils';

const endpoint = '/api/demo/backpressure';

export function BackpressureSystem() {
  const [timeline, dispatchTimeline] = useReducer(backpressureTimelineReducer, {
    frames: [],
    cursor: null,
  });
  const [error, setError] = useState<string>();
  const [submitting, setSubmitting] = useState<'single' | 'burst'>();
  const [burstJobs, setBurstJobs] = useState(10);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const response = await fetch(endpoint, { cache: 'no-store', signal });
      if (!response.ok) throw new Error(await response.text() || 'Live projection unavailable');
      const parsed = parseBackpressureSnapshot(await response.json());
      if (!parsed) throw new Error('Live projection returned an invalid contract');
      dispatchTimeline({ type: 'capture', snapshot: parsed });
      setError(undefined);
    } catch (cause) {
      if (cause instanceof DOMException && cause.name === 'AbortError') return;
      setError(cause instanceof Error ? cause.message : 'Live projection unavailable');
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    const poll = window.setInterval(() => void refresh(controller.signal), 400);
    return () => {
      controller.abort();
      window.clearInterval(poll);
    };
  }, [refresh]);

  const enqueue = async (jobCount: number, action: 'single' | 'burst') => {
    setSubmitting(action);
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_count: jobCount }),
      });
      if (!response.ok) throw new Error(await response.text() || 'Could not queue demo work');
      const parsed = parseBackpressureSnapshot(await response.json());
      if (!parsed) throw new Error('Queue command returned an invalid projection');
      dispatchTimeline({ type: 'capture', snapshot: parsed });
      setError(undefined);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not queue demo work');
    } finally {
      setSubmitting(undefined);
    }
  };

  const latestSnapshot = timeline.frames[timeline.frames.length - 1];
  const snapshot = timeline.cursor === null
    ? latestSnapshot
    : timeline.frames[timeline.cursor] ?? latestSnapshot;
  const flow = useMemo(() => snapshot ? buildBackpressureFlow(snapshot) : [], [snapshot]);

  return (
    <main className="min-h-screen bg-[#f7f4ee] text-foreground">
      <header className="border-b bg-card/95 px-4 py-3 backdrop-blur sm:px-6">
        <div className="mx-auto flex max-w-[110rem] items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="grid size-9 place-items-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
              OM
            </div>
            <div className="hidden sm:block">
              <p className="font-serif text-lg font-semibold tracking-tight">OpenMagic</p>
              <p className="text-[0.625rem] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                Live system map
              </p>
            </div>
          </div>
          <AppViewNav />
        </div>
      </header>

      <div className="mx-auto max-w-[110rem] space-y-5 p-3 sm:p-5 lg:p-6">
        <section className="grid gap-4 rounded-2xl border bg-card p-5 shadow-sm lg:grid-cols-[1fr_auto] lg:items-center lg:p-6">
          <div>
            <div className="flex items-center gap-2 text-[0.65rem] font-semibold uppercase tracking-[0.16em] text-primary">
              <RadioIcon className="size-3.5" />
              Real PostgreSQL state, polled every 400 ms
            </div>
            <h1 className="mt-3 max-w-4xl font-serif text-2xl font-semibold tracking-tight sm:text-3xl">
              Watch durable work absorb a burst, one isolated Workflow Job Run at a time.
            </h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
              The interaction boundary commits typed work and exits. PostgreSQL owns the backlog.
              Workers, execution agents, and notification delivery can progress independently.
            </p>
          </div>
          <div className="flex flex-wrap items-end gap-2 lg:justify-end">
            <label className="min-w-40 rounded-lg border bg-background px-3 py-2 text-[0.6rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
              Burst size: {burstJobs} Jobs
              <input
                aria-label="Burst Job count"
                className="mt-2 block h-1.5 w-full cursor-pointer accent-sky-600"
                type="range"
                min={2}
                max={40}
                step={2}
                value={burstJobs}
                disabled={submitting !== undefined}
                onChange={(event) => setBurstJobs(Number(event.target.value))}
              />
            </label>
            <Button variant="outline" onClick={() => void enqueue(2, 'single')} disabled={submitting !== undefined}>
              {submitting === 'single' ? <LoaderCircleIcon className="animate-spin" /> : <PlusIcon />}
              Create 1 Workflow
            </Button>
            <Button onClick={() => void enqueue(burstJobs, 'burst')} disabled={submitting !== undefined}>
              {submitting === 'burst' ? <LoaderCircleIcon className="animate-spin" /> : <ZapIcon />}
              Burst +{burstJobs} Jobs
            </Button>
          </div>
        </section>

        {error && (
          <div role="alert" className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
            {error}
          </div>
        )}

        {snapshot ? (
          <>
            <Metrics snapshot={snapshot} />
            <BackpressureTimeline
              timeline={timeline}
              onLive={() => dispatchTimeline({ type: 'live' })}
              onNext={() => dispatchTimeline({ type: 'next' })}
              onPause={() => dispatchTimeline({ type: 'pause' })}
              onPrevious={() => dispatchTimeline({ type: 'previous' })}
              onSeek={(cursor) => dispatchTimeline({ type: 'seek', cursor })}
            />
            <section className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
              <div className="space-y-3">
                <BackpressureFlow stages={flow} />
                <div className="flex items-center gap-3 rounded-xl border border-blue-200 bg-blue-50 px-4 py-3 text-xs text-blue-900">
                  <DatabaseIcon className="size-4 shrink-0 text-blue-600" />
                  <span>
                    PostgreSQL is the authority across every stage. The moving dots visualize real
                    durable transition evidence, active Workflow Job Runs, and pending delivery,
                    not model reasoning.
                  </span>
                </div>
              </div>
              <ActivityTrace
                activity={snapshot.activity}
                capturedAt={snapshot.capturedAt}
                live={timeline.cursor === null}
              />
            </section>
          </>
        ) : (
          <div className="grid min-h-[34rem] place-items-center rounded-2xl border bg-card">
            <div className="text-center text-sm text-muted-foreground">
              <LoaderCircleIcon className="mx-auto mb-3 size-6 animate-spin" />
              Loading durable state
            </div>
          </div>
        )}
      </div>
    </main>
  );
}

function Metrics({ snapshot }: { snapshot: BackpressureSnapshot }) {
  const { counts } = snapshot;
  const metrics = [
    {
      label: 'Queue pressure',
      value: `${counts.queued}:${snapshot.worker.configuredJobConcurrency}`,
      detail: `${counts.waiting} dependency-blocked, configured capacity only`,
      icon: GaugeIcon,
      urgent: counts.queued > 1,
    },
    {
      label: 'Active Workflow Job Runs',
      value: String(counts.runsRunning),
      detail: `${counts.runsSucceeded} fresh contexts already destroyed`,
      icon: BoxesIcon,
    },
    {
      label: 'Notification backlog',
      value: String(counts.notificationsQueued + counts.notificationsDelivering),
      detail: `${counts.notificationsDelivered} fresh Interaction turns completed`,
      icon: ActivityIcon,
    },
    {
      label: 'Oldest eligible Job',
      value: `${counts.oldestQueuedSeconds}s`,
      detail: `${counts.completedLastMinute} Jobs completed in the last minute`,
      icon: Clock3Icon,
    },
  ];
  return (
    <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {metrics.map(({ label, value, detail, icon: Icon, urgent }) => (
        <article key={label} className="rounded-xl border bg-card p-4 shadow-sm">
          <div className="flex items-center justify-between gap-3">
            <span className="text-[0.65rem] font-semibold uppercase tracking-[0.13em] text-muted-foreground">
              {label}
            </span>
            <Icon className={cn('size-4', urgent ? 'text-amber-600' : 'text-primary')} />
          </div>
          <p className={cn('mt-3 font-mono text-2xl font-bold', urgent && 'text-amber-700')}>{value}</p>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">{detail}</p>
        </article>
      ))}
    </section>
  );
}

function ActivityTrace({
  activity,
  capturedAt,
  live,
}: {
  activity: ReadonlyArray<BackpressureActivity>;
  capturedAt: string;
  live: boolean;
}) {
  return (
    <aside className="flex min-h-[31rem] flex-col overflow-hidden rounded-2xl border border-slate-800 bg-slate-950 text-slate-100 shadow-xl">
      <div className="border-b border-slate-800 px-4 py-4">
        <div className="flex items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <ActivityIcon className="size-4 text-sky-300" />
            Durable activity
          </h2>
          <span className={cn(
            'flex items-center gap-1.5 text-[0.6rem] uppercase tracking-[0.12em]',
            live ? 'text-emerald-300' : 'text-amber-300',
          )}>
            <span className={cn(
              'size-1.5 rounded-full',
              live ? 'animate-pulse bg-emerald-400 motion-reduce:animate-none' : 'bg-amber-400',
            )} />
            {live ? 'live' : 'historical'}
          </span>
        </div>
        <p className="mt-1 font-mono text-[0.58rem] text-slate-500">
          snapshot {new Date(capturedAt).toLocaleTimeString()}
        </p>
      </div>
      <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {activity.length === 0 ? (
          <div className="grid min-h-72 place-items-center text-center text-xs leading-5 text-slate-600">
            Create demo work to start the trace.
          </div>
        ) : (
          <ol className="relative ml-1 border-l border-slate-800 pl-4">
            {activity.slice(0, 30).map((item) => (
              <li key={item.id} className="relative pb-4">
                <span className={cn(
                  'absolute -left-[1.17rem] top-1 size-2 rounded-full ring-4 ring-slate-950',
                  item.source === 'notification' ? 'bg-violet-400' : 'bg-sky-400',
                )} />
                <div className="flex items-start justify-between gap-2">
                  <code className="break-all text-[0.66rem] font-semibold text-slate-200">
                    {item.type}
                  </code>
                  <time className="shrink-0 font-mono text-[0.55rem] text-slate-600">
                    {new Date(item.occurredAt).toLocaleTimeString([], {
                      minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3,
                    })}
                  </time>
                </div>
                <p className="mt-1 font-mono text-[0.55rem] text-slate-600">
                  wf {shortId(item.workflowId)}
                  {item.jobId ? ` · job ${shortId(item.jobId)}` : ''}
                  {item.runId ? ` · job run ${shortId(item.runId)}` : ''}
                </p>
              </li>
            ))}
          </ol>
        )}
      </div>
    </aside>
  );
}

function shortId(value: string): string {
  return value.slice(-8);
}
