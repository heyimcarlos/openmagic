'use client';

import { useEffect } from 'react';
import { ArrowLeftIcon, ArrowRightIcon } from 'lucide-react';

import { workflowTelemetryVariants, type WorkflowTelemetryVariant } from './variants';

interface PrototypeSwitcherProps {
  current: WorkflowTelemetryVariant;
  onSelect: (variant: WorkflowTelemetryVariant) => void;
}

export function PrototypeSwitcher({
  current,
  onSelect,
}: PrototypeSwitcherProps) {
  const currentIndex = workflowTelemetryVariants.findIndex((option) => option.key === current);
  const selectOffset = (offset: number) => {
    const nextIndex = (currentIndex + offset + workflowTelemetryVariants.length) % workflowTelemetryVariants.length;
    onSelect(workflowTelemetryVariants[nextIndex].key);
  };

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.matches('input, textarea, [contenteditable="true"]')) return;
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        selectOffset(-1);
      }
      if (event.key === 'ArrowRight') {
        event.preventDefault();
        selectOffset(1);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  });

  if (process.env.NODE_ENV === 'production') return null;

  const option = workflowTelemetryVariants[currentIndex];

  return (
    <div className="fixed bottom-5 left-1/2 z-50 flex -translate-x-1/2 items-center gap-1 rounded-full border border-white/15 bg-slate-950 p-1.5 text-white shadow-2xl shadow-slate-950/30">
      <button
        type="button"
        onClick={() => selectOffset(-1)}
        className="flex size-8 items-center justify-center rounded-full text-slate-300 transition hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70"
        aria-label="Previous prototype variant"
      >
        <ArrowLeftIcon className="size-4" />
      </button>
      <div className="min-w-44 px-3 text-center text-xs font-medium">
        <span className="text-sky-300">{option.key}</span>
        <span className="mx-1.5 text-slate-500">/</span>
        <span>{option.name}</span>
      </div>
      <button
        type="button"
        onClick={() => selectOffset(1)}
        className="flex size-8 items-center justify-center rounded-full text-slate-300 transition hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/70"
        aria-label="Next prototype variant"
      >
        <ArrowRightIcon className="size-4" />
      </button>
    </div>
  );
}
