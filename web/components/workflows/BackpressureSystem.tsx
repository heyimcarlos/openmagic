'use client';

import { useCallback, useEffect, useMemo, useReducer, useState } from 'react';
import {
  ActivityIcon,
  ArrowLeftIcon,
  ArrowRightIcon,
  BoxesIcon,
  Clock3Icon,
  DatabaseIcon,
  GaugeIcon,
  LoaderCircleIcon,
  PauseIcon,
  PlayIcon,
  PlusIcon,
  RadioIcon,
  ZapIcon,
} from 'lucide-react';

import { AppViewNav } from '@/components/app/AppViewNav';
import { Button } from '@/components/ui/button';
import { BackpressureFlow } from '@/components/workflows/BackpressureFlow';
import {
  buildBackpressureFlow,
  parseBackpressureSnapshot,
  type BackpressureActivity,
  type BackpressureSnapshot,
} from '@/lib/backpressureDemo';
import { cn } from '@/lib/utils';

const endpoint = '/api/demo/backpressure';
const maxTimelineFrames = 450;

interface TimelineState {
  frames: ReadonlyArray<BackpressureSnapshot>;
  cursor: number | null;
}

type TimelineAction =
  | { type: 'capture'; snapshot: BackpressureSnapshot }
  | { type: 'pause' }
  | { type: 'previous' }
  | { type: 'next' }
  | { type: 'seek'; cursor: number }
  | { type: 'live' };

function timelineReducer(state: TimelineState, action: TimelineAction): TimelineState {
  const lastIndex = state.frames.length - 1;
  if (action.type === 'capture') {
    const overflow = Math.max(0, state.frames.length + 1 - maxTimelineFrames);
    const frames = [...state.frames, action.snapshot].slice(overflow);
    const cursor = state.cursor === null ? null : Math.max(0, state.cursor - overflow);
    return { frames, cursor };
  }
  if (action.type === 'live') return { ...state, cursor: null };
  if (action.type === 'pause') {
    return lastIndex >= 0 ? { ...state, cursor: lastIndex } : state;
  }
  if (action.type === 'previous') {
    const cursor = state.cursor ?? lastIndex;
    return cursor >= 0 ? { ...state, cursor: Math.max(0, cursor - 1) } : state;
  }
  if (action.type === 'next') {
    return state.cursor === null
      ? state
      : { ...state, cursor: Math.min(lastIndex, state.cursor + 1) };
  }
  return lastIndex >= 0
    ? { ...state, cursor: Math.max(0, Math.min(lastIndex, action.cursor)) }
    : state;
}

export function BackpressureSystem() {
  const [timeline, dispatchTimeline] = useReducer(timelineReducer, {
    frames: [],
    cursor: null,
  });
  const [error, setError] = useState<string>();
  const [submitting, setSubmitting] = useState<number>();

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

  const enqueue = async (jobCount: number) => {
    setSubmitting(jobCount);
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
              Watch durable work absorb a burst, one isolated Run at a time.
            </h1>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
              The interaction boundary commits typed work and exits. PostgreSQL owns the backlog.
              Workers, execution agents, and notification delivery can progress independently.
            </p>
          </div>
          <div className="flex flex-wrap gap-2 lg:justify-end">
            <Button variant="outline" onClick={() => void enqueue(2)} disabled={submitting !== undefined}>
              {submitting === 2 ? <LoaderCircleIcon className="animate-spin" /> : <PlusIcon />}
              Create 1 Workflow
            </Button>
            <Button onClick={() => void enqueue(10)} disabled={submitting !== undefined}>
              {submitting === 10 ? <LoaderCircleIcon className="animate-spin" /> : <ZapIcon />}
              Burst +10 Jobs
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
            <TimelinePanel
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
                    eligible work, active Runs, and pending delivery, not model reasoning.
                  </span>
                </div>
              </div>
              <ActivityTrace activity={snapshot.activity} capturedAt={snapshot.capturedAt} />
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

function TimelinePanel({
  timeline,
  onLive,
  onNext,
  onPause,
  onPrevious,
  onSeek,
}: {
  timeline: TimelineState;
  onLive: () => void;
  onNext: () => void;
  onPause: () => void;
  onPrevious: () => void;
  onSeek: (cursor: number) => void;
}) {
  const lastIndex = timeline.frames.length - 1;
  const selectedIndex = timeline.cursor ?? lastIndex;
  const live = timeline.cursor === null;
  const chartFrames = timeline.frames.slice(-180);
  const chartOffset = timeline.frames.length - chartFrames.length;
  const selectedChartIndex = Math.max(0, selectedIndex - chartOffset);
  return (
    <section className="grid gap-4 rounded-2xl border bg-card p-4 shadow-sm lg:grid-cols-[auto_minmax(20rem,1fr)] lg:items-center">
      <div className="min-w-64">
        <div className="flex items-center gap-2 text-[0.65rem] font-semibold uppercase tracking-[0.14em] text-primary">
          <Clock3Icon className="size-3.5" />
          Observed-state timeline
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={onPrevious}
            disabled={selectedIndex <= 0}
          >
            <ArrowLeftIcon />
            Back
          </Button>
          {live ? (
            <Button size="sm" variant="outline" onClick={onPause}>
              <PauseIcon />
              Pause
            </Button>
          ) : (
            <Button size="sm" onClick={onLive}>
              <PlayIcon />
              Return live
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            onClick={onNext}
            disabled={live || selectedIndex >= lastIndex}
          >
            Next
            <ArrowRightIcon />
          </Button>
        </div>
        <div className="mt-3 flex items-center gap-3">
          <input
            aria-label="Captured system frame"
            className="h-1.5 min-w-0 flex-1 cursor-pointer accent-sky-600"
            type="range"
            min={0}
            max={Math.max(0, lastIndex)}
            value={Math.max(0, selectedIndex)}
            onChange={(event) => onSeek(Number(event.target.value))}
          />
          <span className="shrink-0 font-mono text-[0.62rem] text-muted-foreground">
            {live ? 'LIVE' : `${selectedIndex + 1}/${timeline.frames.length}`}
          </span>
        </div>
        <p className="mt-2 text-[0.65rem] leading-5 text-muted-foreground">
          Every frame is a real 400 ms PostgreSQL capture. Scrubbing never replays invented events.
        </p>
      </div>
      <BackpressureTrace frames={chartFrames} selectedIndex={selectedChartIndex} />
    </section>
  );
}

function BackpressureTrace({
  frames,
  selectedIndex,
}: {
  frames: ReadonlyArray<BackpressureSnapshot>;
  selectedIndex: number;
}) {
  const width = 720;
  const height = 96;
  const maxValue = Math.max(
    1,
    ...frames.flatMap((frame) => [
      frame.counts.queued,
      frame.counts.runsRunning,
      frame.counts.notificationsQueued + frame.counts.notificationsDelivering,
    ]),
  );
  const points = (read: (frame: BackpressureSnapshot) => number) => frames.map((frame, index) => {
    const x = frames.length <= 1 ? width : (index / (frames.length - 1)) * width;
    const y = height - (read(frame) / maxValue) * (height - 8) - 4;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const cursorX = frames.length <= 1
    ? width
    : (Math.min(selectedIndex, frames.length - 1) / (frames.length - 1)) * width;
  return (
    <div className="min-w-0">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <p className="text-[0.65rem] font-semibold uppercase tracking-[0.13em] text-muted-foreground">
          Pressure profile
        </p>
        <div className="flex gap-3 text-[0.6rem] text-muted-foreground">
          <span className="before:mr-1 before:inline-block before:size-2 before:rounded-full before:bg-amber-500">Eligible</span>
          <span className="before:mr-1 before:inline-block before:size-2 before:rounded-full before:bg-sky-500">Runs</span>
          <span className="before:mr-1 before:inline-block before:size-2 before:rounded-full before:bg-violet-500">Notifications</span>
        </div>
      </div>
      <svg
        aria-label="Queue, Run, and Notification counts across captured frames"
        className="h-24 w-full overflow-visible rounded-lg bg-slate-950"
        role="img"
        viewBox={`0 0 ${width} ${height}`}
      >
        <line x1="0" y1={height - 4} x2={width} y2={height - 4} stroke="#334155" />
        <polyline fill="none" stroke="#f59e0b" strokeWidth="3" points={points((frame) => frame.counts.queued)} />
        <polyline fill="none" stroke="#0ea5e9" strokeWidth="3" points={points((frame) => frame.counts.runsRunning)} />
        <polyline
          fill="none"
          stroke="#8b5cf6"
          strokeWidth="3"
          points={points((frame) => frame.counts.notificationsQueued + frame.counts.notificationsDelivering)}
        />
        <line x1={cursorX} y1="0" x2={cursorX} y2={height} stroke="#f8fafc" strokeDasharray="3 4" />
      </svg>
    </div>
  );
}

function Metrics({ snapshot }: { snapshot: BackpressureSnapshot }) {
  const { counts } = snapshot;
  const metrics = [
    {
      label: 'Queue pressure',
      value: `${counts.queued}:1`,
      detail: `${counts.waiting} dependency-blocked Jobs are separate`,
      icon: GaugeIcon,
      urgent: counts.queued > 1,
    },
    {
      label: 'Active Runs',
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
}: {
  activity: ReadonlyArray<BackpressureActivity>;
  capturedAt: string;
}) {
  return (
    <aside className="flex min-h-[31rem] flex-col overflow-hidden rounded-2xl border border-slate-800 bg-slate-950 text-slate-100 shadow-xl">
      <div className="border-b border-slate-800 px-4 py-4">
        <div className="flex items-center justify-between gap-3">
          <h2 className="flex items-center gap-2 text-sm font-semibold">
            <ActivityIcon className="size-4 text-sky-300" />
            Durable activity
          </h2>
          <span className="flex items-center gap-1.5 text-[0.6rem] uppercase tracking-[0.12em] text-emerald-300">
            <span className="size-1.5 animate-pulse rounded-full bg-emerald-400" />
            live
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
                  {item.runId ? ` · run ${shortId(item.runId)}` : ''}
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
