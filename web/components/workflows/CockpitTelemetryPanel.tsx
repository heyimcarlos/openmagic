'use client';

import { useCallback, useEffect, useState } from 'react';
import { RadioTowerIcon } from 'lucide-react';

import { SIMULATED_SMS_SENDERS } from '@/components/chat/SmsContactHeader';
import { WorkflowTelemetry } from '@/components/chat/workflow-telemetry/WorkflowTelemetry';
import { parseChatTurnTelemetry, type ChatTurnTelemetry } from '@/lib/chatTelemetry';
import { isRecord } from '@/lib/typeGuards';

const POLL_INTERVAL_MS = 1500;
const broker = SIMULATED_SMS_SENDERS.find((sender) => sender.id === 'broker');

type TelemetryState =
  | { status: 'loading' | 'failed'; telemetry?: undefined }
  | { status: 'ready'; telemetry?: ChatTurnTelemetry; stale: boolean };

export function CockpitTelemetryPanel({
  onTelemetry,
}: {
  onTelemetry?: (telemetry: ChatTurnTelemetry | undefined) => void;
}) {
  const [state, setState] = useState<TelemetryState>({ status: 'loading' });
  const load = useCallback(async () => {
    if (!broker) {
      setState({ status: 'failed' });
      return;
    }
    try {
      const response = await fetch(
        `/api/chat/telemetry/latest?sender_phone=${encodeURIComponent(broker.phone)}`,
        { cache: 'no-store' },
      );
      if (!response.ok) throw new Error(`Telemetry request failed (${response.status})`);
      const payload: unknown = await response.json();
      const telemetry = isRecord(payload) ? parseChatTurnTelemetry(payload.telemetry) : undefined;
      setState({ status: 'ready', telemetry, stale: false });
      onTelemetry?.(telemetry);
    } catch {
      setState((current) => current.status === 'ready'
        ? { ...current, stale: true }
        : { status: 'failed' });
    }
  }, [onTelemetry]);

  useEffect(() => {
    let cancelled = false;
    let timeoutId: number | undefined;
    const poll = async (): Promise<void> => {
      await load();
      if (!cancelled) {
        timeoutId = window.setTimeout(() => void poll(), POLL_INTERVAL_MS);
      }
    };
    void poll();
    return () => {
      cancelled = true;
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    };
  }, [load]);

  return (
    <section className="border-b bg-card px-5 py-5" aria-live="polite">
      <div className="flex items-center gap-2">
        <RadioTowerIcon className="size-4 text-primary" />
        <h2 className="text-sm font-semibold">Latest Workflow activity</h2>
      </div>
      <p className="mt-1 text-[0.625rem] font-medium uppercase tracking-[0.14em] text-muted-foreground">
        Authorized chat telemetry
        {state.status === 'ready' && state.stale ? ' · Reconnecting' : ''}
      </p>
      {state.telemetry ? (
        <WorkflowTelemetry telemetry={state.telemetry} className="mt-2" />
      ) : (
        <p className="mt-4 text-xs leading-5 text-muted-foreground">
          {state.status === 'failed'
            ? 'Live telemetry is temporarily unavailable.'
            : state.status === 'loading'
              ? 'Loading the latest Broker turn...'
              : 'No Workflow telemetry yet. Start from Chat.'}
        </p>
      )}
    </section>
  );
}
