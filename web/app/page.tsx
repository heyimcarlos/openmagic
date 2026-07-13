'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import SettingsModal, { useSettings } from '@/components/SettingsModal';
import { ChatInput } from '@/components/chat/ChatInput';
import { ChatMessages } from '@/components/chat/ChatMessages';
import type { ApprovalEmail } from '@/components/workflows/ApprovalRequestCard';
import { ErrorBanner } from '@/components/chat/ErrorBanner';
import {
  SIMULATED_SMS_SENDERS,
  SmsContactHeader,
  type SimulatedSenderId,
} from '@/components/chat/SmsContactHeader';
import type { ChatBubble } from '@/components/chat/types';
import { workflowTelemetryDemoMessages } from '@/components/chat/workflow-telemetry/demo';
import { messageForDisplay } from '@/lib/chatDisplay';
import { parseChatHistory } from '@/lib/chatHistory';
import { isWorkflowTelemetryDemoVariant } from '@/lib/workflowTelemetryDemo';
import { parseChatTurnTelemetry, type ApprovalRequest, type ChatTurnTelemetry } from '@/lib/chatTelemetry';

const POLL_INTERVAL_MS = 1500;
const RESPONSE_POLL_INTERVAL_MS = 1000;
const RESPONSE_POLL_ATTEMPTS = 30;

const wait = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
const parseAddresses = (value?: string) => (
  value?.split(',').map((address) => address.trim()).filter(Boolean) ?? []
);

export default function Page() {
  const { settings, setSettings } = useSettings();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<ChatBubble[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isWaitingForResponse, setIsWaitingForResponse] = useState(false);
  const [pendingTelemetry, setPendingTelemetry] = useState<ChatTurnTelemetry>();
  const [showTelemetryDemo, setShowTelemetryDemo] = useState(false);
  const [senderId, setSenderId] = useState<SimulatedSenderId>('policyholder');
  const senderGeneration = useRef(0);
  const historySnapshot = useRef<string | undefined>(undefined);
  const approvalCauseBySubmission = useRef(new Map<string, string>());
  const sender =
    SIMULATED_SMS_SENDERS.find((candidate) => candidate.id === senderId) ??
    SIMULATED_SMS_SENDERS[0];
  const historyUrl = `/api/chat/history?sender_phone=${encodeURIComponent(sender.phone)}`;
  const openSettings = useCallback(() => setOpen(true), [setOpen]);
  const closeSettings = useCallback(() => setOpen(false), [setOpen]);

  const readChangedHistory = useCallback(async (
    response: Response,
    requestGeneration: number,
  ): Promise<ChatBubble[] | undefined> => {
    if (!response.ok) return undefined;
    const raw = await response.text();
    if (senderGeneration.current !== requestGeneration || raw === historySnapshot.current) {
      return undefined;
    }
    const currentMessages = parseChatHistory(JSON.parse(raw));
    historySnapshot.current = raw;
    return currentMessages;
  }, []);

  const loadHistory = useCallback(async () => {
    const requestGeneration = senderGeneration.current;
    try {
      const res = await fetch(historyUrl, { cache: 'no-store' });
      const currentMessages = await readChangedHistory(res, requestGeneration);
      if (currentMessages) setMessages(currentMessages);
    } catch (err: any) {
      if (err?.name === 'AbortError') return;
      console.error('Failed to load chat history', err);
    }
  }, [historyUrl, readChangedHistory]);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  useEffect(() => {
    if (process.env.NODE_ENV === 'production') return;
    const candidate = new URLSearchParams(window.location.search).get('variant');
    setShowTelemetryDemo(isWorkflowTelemetryDemoVariant(candidate));
  }, []);

  // Detect and store browser timezone on first load
  useEffect(() => {
    const detectAndStoreTimezone = async () => {
      // Only run if timezone not already stored
      if (settings.timezone) return;
      
      try {
        const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
        
        // Send to server
        const response = await fetch('/api/timezone', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ timezone: browserTimezone }),
        });
        
        if (response.ok) {
          // Update local settings
          setSettings({ ...settings, timezone: browserTimezone });
        }
      } catch (error) {
        // Fail silently - timezone detection is not critical
        console.debug('Timezone detection failed:', error);
      }
    };

    void detectAndStoreTimezone();
  }, [settings, setSettings]);


  useEffect(() => {
    if (isWaitingForResponse) return;

    const intervalId = window.setInterval(() => {
      void loadHistory();
    }, POLL_INTERVAL_MS);

    return () => window.clearInterval(intervalId);
  }, [isWaitingForResponse, loadHistory]);

  const canSubmit = input.trim().length > 0 && !isWaitingForResponse;
  const inputPlaceholder = isWaitingForResponse ? 'Waiting for OpenMagic...' : 'Message OpenMagic';

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      const requestGeneration = senderGeneration.current;

      const assistantCountBeforeSend = messages.filter((message) => message.role === 'assistant').length;
      setError(null);
      setIsWaitingForResponse(true);
      setPendingTelemetry(undefined);

      const sourceId = crypto.randomUUID();
      const userMessage: ChatBubble = {
        id: `user-${Date.now()}`,
        role: 'user',
        text: messageForDisplay(trimmed),
      };
      setMessages((previous) => [...previous, userMessage]);

      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            messages: [{ id: sourceId, role: 'user', content: trimmed }],
            interaction: {
              channel: 'sms',
              sender_phone: sender.phone,
            },
          }),
        });

        if (!(res.ok || res.status === 202)) {
          const detail = await res.text();
          throw new Error(detail || `Request failed (${res.status})`);
        }
      } catch (err: any) {
        console.error('Failed to send message', err);
        setError(err?.message || 'Failed to send message');
        if (senderGeneration.current === requestGeneration) {
          setMessages((previous) => previous.filter((message) => message.id !== userMessage.id));
          setIsWaitingForResponse(false);
        }
        throw err instanceof Error ? err : new Error('Failed to send message');
      }

      for (let attempt = 0; attempt < RESPONSE_POLL_ATTEMPTS; attempt += 1) {
        await wait(RESPONSE_POLL_INTERVAL_MS);

        try {
          const telemetryUrl = `/api/chat/telemetry/latest?sender_phone=${encodeURIComponent(sender.phone)}&cause_id=${encodeURIComponent(sourceId)}`;
          const [response, telemetryResponse] = await Promise.all([
            fetch(historyUrl, { cache: 'no-store' }),
            fetch(telemetryUrl, { cache: 'no-store' }),
          ]);
          if (telemetryResponse.ok && senderGeneration.current === requestGeneration) {
            const telemetryPayload = await telemetryResponse.json();
            setPendingTelemetry(parseChatTurnTelemetry(telemetryPayload.telemetry));
          }
          const currentMessages = await readChangedHistory(response, requestGeneration);
          if (!currentMessages) continue;
          const assistantCount = currentMessages.filter((message) => message.role === 'assistant').length;

          if (assistantCount > assistantCountBeforeSend) {
            setMessages(currentMessages);
            setPendingTelemetry(undefined);
            setIsWaitingForResponse(false);
            return;
          }
        } catch (err) {
          console.error('Error polling for response:', err);
        }
      }

      if (senderGeneration.current === requestGeneration) {
        setPendingTelemetry(undefined);
        setIsWaitingForResponse(false);
        await loadHistory();
      }
    },
    [historyUrl, loadHistory, messages, readChangedHistory, sender.phone],
  );

  const handleClearHistory = useCallback(async () => {
    if (isWaitingForResponse) return;
    try {
      const res = await fetch('/api/chat/demo/reset', { method: 'POST' });
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        throw new Error(
          typeof payload.detail === 'string' ? payload.detail : 'Failed to reset the demo',
        );
      }
      historySnapshot.current = undefined;
      approvalCauseBySubmission.current.clear();
      senderGeneration.current += 1;
      setMessages([]);
      setPendingTelemetry(undefined);
      setIsWaitingForResponse(false);
      setError(null);
    } catch (err) {
      console.error('Failed to clear chat history', err);
      setError(err instanceof Error ? err.message : 'Failed to reset the demo');
    }
  }, [isWaitingForResponse]);

  const handleSenderChange = useCallback((id: SimulatedSenderId) => {
    senderGeneration.current += 1;
    setSenderId(id);
    setMessages([]);
    historySnapshot.current = undefined;
    approvalCauseBySubmission.current.clear();
    setPendingTelemetry(undefined);
    setError(null);
    setIsWaitingForResponse(false);
  }, []);

  const triggerClearHistory = useCallback(() => {
    void handleClearHistory();
  }, [handleClearHistory]);

  const handleSubmit = useCallback(async () => {
    if (!canSubmit) return;
    const value = input;
    setInput('');
    try {
      await sendMessage(value);
    } catch {
      setInput(value);
    }
  }, [canSubmit, input, sendMessage, setInput]);

  const handleInputChange = useCallback((value: string) => {
    setInput(value);
  }, [setInput]);

  const clearError = useCallback(() => setError(null), [setError]);

  const approveExactEmail = useCallback(async (
    approval: ApprovalRequest,
    revision?: ApprovalEmail,
  ) => {
    setError(null);
    const revisedEmail = revision ? {
      to: parseAddresses(revision.to),
      cc: parseAddresses(revision.cc),
      bcc: parseAddresses(revision.bcc),
      subject: revision.subject.trim(),
      body: revision.body,
    } : undefined;
    const submissionKey = JSON.stringify({
      senderPhone: sender.phone,
      workflowId: approval.workflowId,
      jobId: approval.jobId,
      draftRevisionId: approval.draftRevisionId,
      revisedEmail,
    });
    let causeId = approvalCauseBySubmission.current.get(submissionKey);
    if (!causeId) {
      causeId = `ui-approval:${crypto.randomUUID()}`;
      approvalCauseBySubmission.current.set(submissionKey, causeId);
    }
    const response = await fetch('/api/chat/approval', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sender_phone: sender.phone,
        cause_id: causeId,
        workflow_id: approval.workflowId,
        job_id: approval.jobId,
        expected_draft_revision_id: approval.draftRevisionId,
        ...(revisedEmail ? { revised_email: revisedEmail } : {}),
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = typeof payload.detail === 'string'
        ? payload.detail
        : 'I could not record that approval. Please review the latest email and try again.';
      setError(message);
      await loadHistory();
      throw new Error(message);
    }
    if (payload.status === 'verification_required') {
      const destination = typeof payload.masked_destination === 'string'
        ? ` at ${payload.masked_destination}`
        : '';
      setError(`I sent a verification code${destination}. Reply with the code to continue.`);
      return;
    }
    await loadHistory();
  }, [loadHistory, sender.phone]);

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top,_color-mix(in_oklch,var(--primary)_10%,transparent),_transparent_35%)] p-0 sm:p-6">
      <div className="mx-auto flex min-h-screen w-full max-w-3xl flex-col bg-card sm:min-h-0 sm:overflow-hidden sm:rounded-2xl sm:border sm:shadow-xl sm:shadow-foreground/5">
        <SmsContactHeader
          sender={sender}
          onSenderChange={handleSenderChange}
          onOpenSettings={openSettings}
          onClearHistory={triggerClearHistory}
          resetDisabled={isWaitingForResponse}
        />

        <div className="flex-1 overflow-hidden">
          <ChatMessages
            messages={showTelemetryDemo ? workflowTelemetryDemoMessages : messages}
            isWaitingForResponse={showTelemetryDemo ? false : isWaitingForResponse}
            pendingTelemetry={showTelemetryDemo ? undefined : pendingTelemetry}
            onApprove={approveExactEmail}
          />

          <div className="border-t bg-background/80 p-3 backdrop-blur sm:p-4">
            {error && <ErrorBanner message={error} onDismiss={clearError} />}

            <ChatInput
              value={input}
              canSubmit={canSubmit}
              placeholder={inputPlaceholder}
              onChange={handleInputChange}
              onSubmit={handleSubmit}
            />
          </div>
        </div>

        <SettingsModal open={open} onClose={closeSettings} settings={settings} onSave={setSettings} />
      </div>
    </main>
  );
}
