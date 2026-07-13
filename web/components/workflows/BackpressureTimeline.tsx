'use client';

import {
  ArrowLeftIcon,
  ArrowRightIcon,
  Clock3Icon,
  PauseIcon,
  PlayIcon,
} from 'lucide-react';

import { Button } from '@/components/ui/button';
import type { BackpressureTimelineState } from '@/lib/backpressureTimeline';
import type { BackpressureSnapshot } from '@/lib/backpressureDemo';

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
    <footer className="grid shrink-0 gap-3 border-t bg-card/95 px-3 py-2 backdrop-blur md:grid-cols-[17rem_minmax(0,1fr)] md:items-center">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          <Clock3Icon className="size-3.5 text-primary" />
          <span className="text-[0.65rem] font-medium uppercase tracking-[0.12em]">Observed history</span>
          <span className="ml-auto flex items-center gap-1.5 font-mono text-[0.58rem] text-muted-foreground">
            <span className={`size-1.5 rounded-full ${live ? 'animate-pulse bg-emerald-500 motion-reduce:animate-none' : 'bg-amber-500'}`} />
            {live ? 'LIVE' : `${selectedIndex + 1}/${timeline.frames.length}`}
          </span>
        </div>
        <div className="mt-2 flex items-center gap-1.5">
          <Button
            size="icon"
            variant="ghost"
            className="size-7"
            onClick={onPrevious}
            disabled={selectedIndex <= 0}
            aria-label="Previous captured frame"
          >
            <ArrowLeftIcon />
          </Button>
          {live ? (
            <Button size="icon" variant="outline" className="size-8" onClick={onPause} aria-label="Pause live history">
              <PauseIcon />
            </Button>
          ) : (
            <Button size="icon" className="size-8" onClick={onLive} aria-label="Return to live history">
              <PlayIcon />
            </Button>
          )}
          <Button
            size="icon"
            variant="ghost"
            className="size-7"
            onClick={onNext}
            disabled={live || selectedIndex >= lastIndex}
            aria-label="Next captured frame"
          >
            <ArrowRightIcon />
          </Button>
          <input
            aria-label="Captured system frame"
            className="ml-1 h-1.5 min-w-0 flex-1 cursor-pointer accent-blue-600"
            type="range"
            min={0}
            max={Math.max(0, lastIndex)}
            value={Math.max(0, selectedIndex)}
            onChange={(event) => onSeek(Number(event.target.value))}
          />
        </div>
      </div>
      <BackpressureTrace frames={chartFrames} selectedIndex={selectedChartIndex} />
    </footer>
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
  const height = 58;
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
      <div className="mb-1 flex items-center justify-end gap-3 text-[0.58rem] text-muted-foreground">
        <span className="before:mr-1 before:inline-block before:size-1.5 before:rounded-full before:bg-amber-500">Eligible</span>
        <span className="before:mr-1 before:inline-block before:size-1.5 before:rounded-full before:bg-sky-500">Runs</span>
        <span className="before:mr-1 before:inline-block before:size-1.5 before:rounded-full before:bg-violet-500">Notifications</span>
      </div>
      <svg
        aria-label="Eligible Jobs, active Runs, and Notifications across captured frames"
        className="h-[58px] w-full overflow-visible"
        role="img"
        viewBox={`0 0 ${width} ${height}`}
      >
        <line x1="0" y1={height - 4} x2={width} y2={height - 4} stroke="var(--border)" />
        <polyline fill="none" stroke="#f59e0b" strokeWidth="2.5" points={points((frame) => frame.counts.queued)} />
        <polyline fill="none" stroke="#0ea5e9" strokeWidth="2.5" points={points((frame) => frame.counts.runsRunning)} />
        <polyline
          fill="none"
          stroke="#8b5cf6"
          strokeWidth="2.5"
          points={points((frame) => frame.counts.notificationsQueued + frame.counts.notificationsDelivering)}
        />
        <line x1={cursorX} y1="0" x2={cursorX} y2={height} stroke="var(--foreground)" strokeDasharray="3 4" />
      </svg>
    </div>
  );
}
