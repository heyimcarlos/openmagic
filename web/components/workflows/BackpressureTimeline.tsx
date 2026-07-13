'use client';

import {
  ArrowLeftIcon,
  ArrowRightIcon,
  Clock3Icon,
  PauseIcon,
  PlayIcon,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import type { BackpressureSnapshot } from '@/lib/backpressureDemo';

const maxTimelineFrames = 450;

export interface BackpressureTimelineState {
  frames: ReadonlyArray<BackpressureSnapshot>;
  cursor: number | null;
}

export type BackpressureTimelineAction =
  | { type: 'capture'; snapshot: BackpressureSnapshot }
  | { type: 'pause' }
  | { type: 'previous' }
  | { type: 'next' }
  | { type: 'seek'; cursor: number }
  | { type: 'live' };

export function backpressureTimelineReducer(
  state: BackpressureTimelineState,
  action: BackpressureTimelineAction,
): BackpressureTimelineState {
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

export function BackpressureTimeline({
  timeline,
  onLive,
  onNext,
  onPause,
  onPrevious,
  onSeek,
}: {
  timeline: BackpressureTimelineState;
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
          <span className="before:mr-1 before:inline-block before:size-2 before:rounded-full before:bg-sky-500">Job Runs</span>
          <span className="before:mr-1 before:inline-block before:size-2 before:rounded-full before:bg-violet-500">Notifications</span>
        </div>
      </div>
      <svg
        aria-label="Queue, Workflow Job Run, and Notification counts across captured frames"
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
