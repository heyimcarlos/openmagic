'use client';

import { useCallback, useEffect, useReducer, useState } from 'react';
import { LoaderCircleIcon } from 'lucide-react';

import { AppViewNav } from '@/components/app/AppViewNav';
import { SIMULATED_SMS_SENDERS } from '@/components/chat/SmsContactHeader';
import type { ApprovalEmail } from '@/components/workflows/ApprovalRequestCard';
import { BackpressureFlow } from '@/components/workflows/BackpressureFlow';
import { BackpressureTimeline } from '@/components/workflows/BackpressureTimeline';
import { backpressureTimelineReducer } from '@/lib/backpressureTimeline';
import { parseBackpressureSnapshot } from '@/lib/backpressureDemo';
import type { ApprovalRequest } from '@/lib/chatTelemetry';

const endpoint = '/api/demo/backpressure';
const broker = SIMULATED_SMS_SENDERS.find((sender) => sender.id === 'broker');
const parseAddresses = (value?: string) => (
  value?.split(',').map((item) => item.trim()).filter(Boolean) ?? []
);

export function BackpressureSystem() {
  const [timeline, dispatchTimeline] = useReducer(backpressureTimelineReducer, {
    frames: [],
    cursor: null,
  });
  const [error, setError] = useState<string>();
  const [submitting, setSubmitting] = useState<'workflows' | 'worker' | 'approval'>();
  const [removingWorkerId, setRemovingWorkerId] = useState<string>();

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
    let active = true;
    let timer: number | undefined;
    const poll = async () => {
      await refresh(controller.signal);
      if (active) timer = window.setTimeout(() => void poll(), 400);
    };
    void poll();
    return () => {
      active = false;
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [refresh]);

  const enqueueWorkflows = useCallback(async (workflowCount: number) => {
    setSubmitting('workflows');
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workflow_count: workflowCount, scenario: 'mixed' }),
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
  }, []);

  const addWorker = useCallback(async () => {
    setSubmitting('worker');
    try {
      const response = await fetch(`${endpoint}/workers`, { method: 'POST' });
      if (!response.ok) throw new Error(await response.text() || 'Could not add Worker');
      const parsed = parseBackpressureSnapshot(await response.json());
      if (!parsed) throw new Error('Worker command returned an invalid projection');
      dispatchTimeline({ type: 'capture', snapshot: parsed });
      setError(undefined);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not add Worker');
    } finally {
      setSubmitting(undefined);
    }
  }, []);

  const removeWorker = useCallback(async (workerId: string) => {
    setSubmitting('worker');
    setRemovingWorkerId(workerId);
    try {
      const response = await fetch(`${endpoint}/workers`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ worker_id: workerId }),
      });
      if (!response.ok) throw new Error(await response.text() || 'Could not remove Worker');
      const parsed = parseBackpressureSnapshot(await response.json());
      if (!parsed) throw new Error('Worker command returned an invalid projection');
      dispatchTimeline({ type: 'capture', snapshot: parsed });
      setError(undefined);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not remove Worker');
    } finally {
      setSubmitting(undefined);
      setRemovingWorkerId(undefined);
    }
  }, []);

  const approveExactEmail = useCallback(async (
    approval: ApprovalRequest,
    revision?: ApprovalEmail,
  ) => {
    if (!broker) {
      setError('The demo Broker identity is unavailable');
      return;
    }
    setSubmitting('approval');
    try {
      const response = await fetch('/api/chat/approval', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          sender_phone: broker.phone,
          cause_id: `ui-approval:${approval.jobId}:${approval.draftRevisionId}`,
          workflow_id: approval.workflowId,
          job_id: approval.jobId,
          expected_draft_revision_id: approval.draftRevisionId,
          ...(revision ? {
            revised_email: {
              to: parseAddresses(revision.to),
              cc: parseAddresses(revision.cc),
              bcc: parseAddresses(revision.bcc),
              subject: revision.subject.trim(),
              body: revision.body,
            },
          } : {}),
        }),
      });
      const payload: unknown = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = payload && typeof payload === 'object' && 'detail' in payload
          ? payload.detail
          : undefined;
        throw new Error(typeof detail === 'string' ? detail : 'Could not record exact approval');
      }
      const verificationRequired = payload && typeof payload === 'object' &&
        'status' in payload && payload.status === 'verification_required';
      if (verificationRequired) {
        setError('Verification is required. Continue in Chat as Carlos Broker to enter the code.');
      } else {
        setError(undefined);
      }
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not record exact approval');
    } finally {
      setSubmitting(undefined);
    }
  }, [refresh]);

  const latestSnapshot = timeline.frames[timeline.frames.length - 1];
  const snapshot = timeline.cursor === null
    ? latestSnapshot
    : timeline.frames[timeline.cursor] ?? latestSnapshot;

  return (
    <main className="flex h-dvh min-h-[38rem] flex-col overflow-hidden bg-background text-foreground">
      <header className="shrink-0 border-b bg-card/95 px-4 py-2.5 backdrop-blur sm:px-6">
        <div className="mx-auto flex max-w-[110rem] items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="grid size-9 place-items-center rounded-full bg-primary text-xs font-bold text-primary-foreground">
              OM
            </div>
            <div className="hidden sm:block">
              <p className="font-serif text-base font-semibold tracking-tight">OpenMagic</p>
              <p className="text-[0.58rem] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                Execution lab
              </p>
            </div>
          </div>
          <AppViewNav />
        </div>
      </header>

      <div className="mx-auto flex min-h-0 w-full max-w-[110rem] flex-1 p-2 sm:p-3">
        <section className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-2xl border bg-card shadow-sm">
          <div className="flex shrink-0 items-center justify-between gap-3 border-b px-4 py-2.5">
            <div className="min-w-0">
              <h1 className="truncate font-serif text-lg font-semibold tracking-tight">
                Durable execution playground
              </h1>
              <p className="truncate text-[0.62rem] text-muted-foreground">
                Click any Job, Worker, Run, Agent, Notification, or approval request to inspect it.
              </p>
            </div>
            {snapshot && (
              <div className="hidden shrink-0 text-right md:block">
                <p className="font-mono text-[0.62rem] text-muted-foreground">
                  {snapshot.scope.visibleWorkflows}/{snapshot.scope.totalWorkflows} Workflows visible
                </p>
                <p className="max-w-80 truncate font-mono text-[0.58rem] text-primary">
                  {snapshot.activity[0]?.type ?? 'waiting for activity'}
                </p>
              </div>
            )}
          </div>

          {error && (
            <div role="alert" className="absolute left-1/2 top-16 z-20 -translate-x-1/2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800 shadow-md">
              {error}
            </div>
          )}

          {snapshot ? (
            <>
              <BackpressureFlow
                snapshot={snapshot}
                addingWorkflows={submitting === 'workflows'}
                addingWorker={submitting === 'worker'}
                removingWorkerId={removingWorkerId}
                approving={submitting === 'approval'}
                onAddWorkflows={enqueueWorkflows}
                onAddWorker={addWorker}
                onRemoveWorker={removeWorker}
                onApprove={approveExactEmail}
              />
              <BackpressureTimeline
                timeline={timeline}
                onLive={() => dispatchTimeline({ type: 'live' })}
                onNext={() => dispatchTimeline({ type: 'next' })}
                onPause={() => dispatchTimeline({ type: 'pause' })}
                onPrevious={() => dispatchTimeline({ type: 'previous' })}
                onSeek={(cursor) => dispatchTimeline({ type: 'seek', cursor })}
              />
            </>
          ) : (
            <div className="grid min-h-0 flex-1 place-items-center">
              <div className="text-center text-sm text-muted-foreground">
                <LoaderCircleIcon className="mx-auto mb-3 size-6 animate-spin" />
                Loading durable state
              </div>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
