'use client';

import { useCallback, useEffect, useState } from 'react';
import SettingsModal, { useSettings } from '@/components/SettingsModal';
import { ChatHeader } from '@/components/chat/ChatHeader';
import { ChatInput } from '@/components/chat/ChatInput';
import { ChatMessages } from '@/components/chat/ChatMessages';
import { ErrorBanner } from '@/components/chat/ErrorBanner';
import type { ChatBubble } from '@/components/chat/types';

const POLL_INTERVAL_MS = 1500;
const RESPONSE_POLL_INTERVAL_MS = 1000;
const RESPONSE_POLL_ATTEMPTS = 30;

const wait = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

const formatEscapeCharacters = (text: string): string => {
  return text
    .replace(/\\n/g, '\n')
    .replace(/\\t/g, '\t')
    .replace(/\\r/g, '\r')
    .replace(/\\\\/g, '\\');
};

const isRenderableMessage = (entry: any) =>
  typeof entry?.role === 'string' &&
  typeof entry?.content === 'string' &&
  entry.content.trim().length > 0;

const toBubbles = (payload: any): ChatBubble[] => {
  if (!Array.isArray(payload?.messages)) return [];

  return payload.messages
    .filter(isRenderableMessage)
    .map((message: any, index: number) => ({
      id: `history-${index}`,
      role: message.role,
      text: formatEscapeCharacters(message.content),
    }));
};

export default function Page() {
  const { settings, setSettings } = useSettings();
  const [open, setOpen] = useState(false);
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<ChatBubble[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isWaitingForResponse, setIsWaitingForResponse] = useState(false);
  const openSettings = useCallback(() => setOpen(true), [setOpen]);
  const closeSettings = useCallback(() => setOpen(false), [setOpen]);

  const loadHistory = useCallback(async () => {
    try {
      const res = await fetch('/api/chat/history', { cache: 'no-store' });
      if (!res.ok) return;
      const data = await res.json();
      setMessages(toBubbles(data));
    } catch (err: any) {
      if (err?.name === 'AbortError') return;
      console.error('Failed to load chat history', err);
    }
  }, []);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

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

      const assistantCountBeforeSend = messages.filter((message) => message.role === 'assistant').length;
      setError(null);
      setIsWaitingForResponse(true);

      const sourceId = crypto.randomUUID();
      const userMessage: ChatBubble = {
        id: `user-${Date.now()}`,
        role: 'user',
        text: formatEscapeCharacters(trimmed),
      };
      setMessages((previous) => [...previous, userMessage]);

      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            messages: [{ id: sourceId, role: 'user', content: trimmed }],
          }),
        });

        if (!(res.ok || res.status === 202)) {
          const detail = await res.text();
          throw new Error(detail || `Request failed (${res.status})`);
        }
      } catch (err: any) {
        console.error('Failed to send message', err);
        setError(err?.message || 'Failed to send message');
        setMessages((previous) => previous.filter((message) => message.id !== userMessage.id));
        setIsWaitingForResponse(false);
        throw err instanceof Error ? err : new Error('Failed to send message');
      }

      for (let attempt = 0; attempt < RESPONSE_POLL_ATTEMPTS; attempt += 1) {
        await wait(RESPONSE_POLL_INTERVAL_MS);

        try {
          const response = await fetch('/api/chat/history', { cache: 'no-store' });
          if (!response.ok) continue;

          const currentMessages = toBubbles(await response.json());
          const assistantCount = currentMessages.filter((message) => message.role === 'assistant').length;
          const submittedMessageIsPresent = currentMessages.some(
            (message) => message.role === 'user' && message.text === trimmed,
          );

          if (submittedMessageIsPresent && assistantCount > assistantCountBeforeSend) {
            setMessages(currentMessages);
            setIsWaitingForResponse(false);
            return;
          }
        } catch (err) {
          console.error('Error polling for response:', err);
        }
      }

      setIsWaitingForResponse(false);
      await loadHistory();
    },
    [loadHistory, messages],
  );

  const handleClearHistory = useCallback(async () => {
    try {
      const res = await fetch('/api/chat/history', { method: 'DELETE' });
      if (!res.ok) {
        console.error('Failed to clear chat history', res.statusText);
        return;
      }
      setMessages([]);
    } catch (err) {
      console.error('Failed to clear chat history', err);
    }
  }, [setMessages]);

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

  return (
    <main className="min-h-screen bg-[radial-gradient(circle_at_top,_color-mix(in_oklch,var(--primary)_10%,transparent),_transparent_35%)] p-0 sm:p-6">
      <div className="mx-auto flex min-h-screen w-full max-w-3xl flex-col bg-card sm:min-h-0 sm:overflow-hidden sm:rounded-2xl sm:border sm:shadow-xl sm:shadow-foreground/5">
        <ChatHeader onOpenSettings={openSettings} onClearHistory={triggerClearHistory} />

        <div className="flex-1 overflow-hidden">
          <ChatMessages
            messages={messages}
            isWaitingForResponse={isWaitingForResponse}
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
