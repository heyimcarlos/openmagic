import {
  CheckIcon,
  CircleIcon,
  Clock3Icon,
  Loader2Icon,
  ShieldCheckIcon,
} from 'lucide-react';

import type { PrototypeStage, PrototypeStatus } from './types';
import { cn } from '@/lib/utils';

const statusStyles: Record<PrototypeStatus, string> = {
  succeeded: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  running: 'border-sky-200 bg-sky-50 text-sky-700',
  waiting: 'border-amber-200 bg-amber-50 text-amber-700',
  unavailable: 'border-border bg-muted text-muted-foreground',
};

interface StatusIconProps {
  status: PrototypeStatus;
  kind?: PrototypeStage['kind'];
  className?: string;
}

export function StatusIcon({ status, kind = 'job', className }: StatusIconProps) {
  const Icon = status === 'succeeded'
    ? CheckIcon
    : status === 'running'
      ? Loader2Icon
      : status === 'waiting'
        ? Clock3Icon
        : kind === 'checkpoint'
          ? ShieldCheckIcon
          : CircleIcon;

  return (
    <span className={cn('flex size-7 shrink-0 items-center justify-center rounded-full border', statusStyles[status], className)}>
      <Icon className={cn('size-3.5', status === 'running' && 'animate-spin')} />
    </span>
  );
}

export function statusText(status: PrototypeStatus) {
  return {
    succeeded: 'Done',
    running: 'In progress',
    waiting: 'Waiting',
    unavailable: 'Not ready',
  }[status];
}
