'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import SettingsModal, { useSettings } from '@/components/SettingsModal';
import { ChatInput } from '@/components/chat/ChatInput';
import { ChatMessages } from '@/components/chat/ChatMessages';
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
import type { ApprovalRequest } from '@/lib/chatTelemetry';

const POLL_INTERVAL_MS = 1500;
const RESPONSE_POLL_INTERVAL_MS = 1000;
const RESPONSE_POLL_ATTEMPTS = 30;

const wait = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

export default function Page() {
  const { settings, setSettings } = useSettings();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<ChatBubble[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isWaitingForResponse, setIsWaitingForResponse] = useState(false);
  const [showTelemetryDemo, setShowTelemetryDemo] = useState(false);
  const [senderId, setSenderId] = useState<SimulatedSenderId>('policyholder');
  const senderGeneration = useRef(0);
  const sender =
    SIMULATED_SMS_SENDERS.find((candidate) => candidate.id === senderId) ??
    SIMULATED_SMS_SENDERS[0];
  const historyUrl = `/api/chat/history?sender_phone=${encodeURIComponent(sender.phone)}`;
  const openSettings = useCallback(() => setOpen(true), [setOpen]);
  const closeSettings = useCallback(() => setOpen(false), [setOpen]);

  const loadHistory = useCallback(async () => {
    const requestGeneration = senderGeneration.current;
    try {
      const res = await fetch(historyUrl, { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      if (senderGeneration.current === requestGeneration) {
        setMessages(parseChatHistory(data));
      }
    } catch (err: any) {
      if (err?.name === 'AbortError') return;
      console.error('Failed to load chat history', err);
    }
  }, [historyUrl]);

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
          const response = await fetch(historyUrl, { cache: 'no-store' });
          if (!response.ok) continue;

          const currentMessages = parseChatHistory(await response.json());
          const assistantCount = currentMessages.filter((message) => message.role === 'assistant').length;

          if (
            senderGeneration.current === requestGeneration &&
            assistantCount > assistantCountBeforeSend
          ) {
            setMessages(currentMessages);
            setIsWaitingForResponse(false);
            return;
          }
        } catch (err) {
          console.error('Error polling for response:', err);
        }
      }

      if (senderGeneration.current === requestGeneration) {
        setIsWaitingForResponse(false);
        await loadHistory();
      }
    },
    [historyUrl, loadHistory, messages, sender],
  );

  const handleClearHistory = useCallback(async () => {
    try {
      const res = await fetch(historyUrl, { method: 'DELETE' });
      if (!res.ok) {
        console.error('Failed to clear chat history', res.statusText);
        return;
      }
      setMessages([]);
    } catch (err) {
      console.error('Failed to clear chat history', err);
    }
  }, [historyUrl, setMessages]);

  const handleSenderChange = useCallback((id: SimulatedSenderId) => {
    senderGeneration.current += 1;
    setSenderId(id);
    setMessages([]);
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

  const approveExactEmail = useCallback(async (approval: ApprovalRequest) => {
    setError(null);
    const response = await fetch('/api/chat/approval', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sender_phone: sender.phone,
        cause_id: `ui-approval:${approval.jobId}:${approval.draftRevisionId}`,
        workflow_id: approval.workflowId,
        job_id: approval.jobId,
        expected_draft_revision_id: approval.draftRevisionId,
      }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = typeof payload.detail === 'string'
        ? payload.detail
        : 'I could not record that approval. Please review the latest email and try again.';
      setError(message);
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

  const requestEmailChanges = useCallback((_approval: ApprovalRequest) => {
    setInput('Please revise the email: ');
    setError(null);
  }, []);

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top,_color-mix(in_oklch,var(--primary)_10%,transparent),_transparent_35%)] p-0 sm:p-6">
      <div className="mx-auto flex min-h-screen w-full max-w-3xl flex-col bg-card sm:min-h-0 sm:overflow-hidden sm:rounded-2xl sm:border sm:shadow-xl sm:shadow-foreground/5">
        <SmsContactHeader
          sender={sender}
          onSenderChange={handleSenderChange}
          onOpenSettings={openSettings}
          onClearHistory={triggerClearHistory}
        />

        <div className="flex-1 overflow-hidden">
          <ChatMessages
            messages={showTelemetryDemo ? workflowTelemetryDemoMessages : messages}
            isWaitingForResponse={showTelemetryDemo ? false : isWaitingForResponse}
            onApprove={approveExactEmail}
            onRequestChanges={requestEmailChanges}
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
