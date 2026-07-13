'use client';

import {
  MessageCircleIcon,
  RotateCcwIcon,
  SettingsIcon,
  UserRoundIcon,
} from 'lucide-react';

import { AppViewNav } from '@/components/app/AppViewNav';
import { Button } from '@/components/ui/button';

export type SimulatedSenderId = 'policyholder' | 'broker' | 'unknown';

export type SimulatedSmsSender = {
  id: SimulatedSenderId;
  name: string;
  phone: string;
  context: string;
  initials: string;
};

export const SIMULATED_SMS_SENDERS: readonly SimulatedSmsSender[] = [
  {
    id: 'policyholder',
    name: 'John Smith',
    phone: '+1 (416) 555-0142',
    context: 'Policyholder',
    initials: 'JS',
  },
  {
    id: 'broker',
    name: 'Carlos Broker',
    phone: '+1 (416) 555-0101',
    context: 'Broker at Acme Brokerage',
    initials: 'CB',
  },
  {
    id: 'unknown',
    name: 'New number',
    phone: '+1 (416) 555-0199',
    context: 'Provisional Party',
    initials: '?',
  },
];

interface SmsContactHeaderProps {
  sender: SimulatedSmsSender;
  onSenderChange: (id: SimulatedSenderId) => void;
  onOpenSettings: () => void;
  onClearHistory: () => void;
  resetDisabled?: boolean;
}

export function SmsContactHeader({
  sender,
  onSenderChange,
  onOpenSettings,
  onClearHistory,
  resetDisabled = false,
}: SmsContactHeaderProps) {
  return (
    <header className="relative border-b px-4 pb-3 pt-2 sm:px-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
          <MessageCircleIcon className="size-3.5" />
          OpenMagic SMS simulator
        </div>
        <div className="flex items-center gap-2">
          <AppViewNav />
          <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={onClearHistory}
            disabled={resetDisabled}
            aria-label="Reset demo data"
            title="Reset demo data"
          >
            <RotateCcwIcon />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={onOpenSettings}
            aria-label="Open settings"
          >
            <SettingsIcon />
          </Button>
          </div>
        </div>
      </div>
      <div className="mt-1 flex items-center gap-3">
        <div className="flex size-11 shrink-0 items-center justify-center rounded-full bg-primary text-sm font-semibold text-primary-foreground shadow-sm">
          {sender.initials === '?' ? <UserRoundIcon className="size-5" /> : sender.initials}
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate font-semibold">{sender.name}</p>
          <p className="truncate text-xs text-muted-foreground">
            {sender.phone} · {sender.context}
          </p>
        </div>
        <label className="relative cursor-pointer rounded-full border bg-background px-3 py-1.5 text-xs font-medium shadow-sm hover:bg-accent">
          Switch sender
          <select
            className="absolute inset-0 cursor-pointer opacity-0"
            aria-label="Switch simulated SMS sender"
            value={sender.id}
            onChange={(event) => onSenderChange(event.target.value as SimulatedSenderId)}
          >
            {SIMULATED_SMS_SENDERS.map((option) => (
              <option key={option.id} value={option.id}>
                {option.name} · {option.phone}
              </option>
            ))}
          </select>
        </label>
      </div>
    </header>
  );
}
