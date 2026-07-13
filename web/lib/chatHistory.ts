import type { ChatBubble } from '@/components/chat/types';

import { parseChatTurnTelemetry } from './chatTelemetry';
import { isRecord } from './typeGuards';

function formatEscapeCharacters(text: string): string {
  return text
    .replace(/\\n/g, '\n')
    .replace(/\\t/g, '\t')
    .replace(/\\r/g, '\r')
    .replace(/\\\\/g, '\\');
}

function isRenderableMessage(value: unknown): value is Record<string, unknown> & {
  role: string;
  content: string;
} {
  return isRecord(value)
    && typeof value.role === 'string'
    && typeof value.content === 'string'
    && value.content.trim().length > 0;
}

export function parseChatHistory(payload: unknown): ChatBubble[] {
  if (!isRecord(payload) || !Array.isArray(payload.messages)) return [];

  return payload.messages
    .filter(isRenderableMessage)
    .map((message, index) => ({
      id: typeof message.id === 'string' && message.id ? message.id : `history-${index}`,
      role: message.role,
      text: formatEscapeCharacters(message.content),
      telemetry: parseChatTurnTelemetry(message.telemetry),
    }));
}

export type ChatHistorySnapshot = {
  raw: string;
  messages: ChatBubble[];
  changed: boolean;
};

export type SequencedChatHistorySnapshot = ChatHistorySnapshot & {
  requestSequence: number;
};

export function parseChatHistorySnapshot(
  raw: string,
  previousRaw?: string,
): ChatHistorySnapshot {
  return {
    raw,
    messages: parseChatHistory(JSON.parse(raw)),
    changed: raw !== previousRaw,
  };
}

export function parseSequencedChatHistorySnapshot(
  raw: string,
  previousRaw: string | undefined,
  requestSequence: number,
  appliedSequence: number,
): SequencedChatHistorySnapshot | undefined {
  if (requestSequence < appliedSequence) return undefined;
  return {
    ...parseChatHistorySnapshot(raw, previousRaw),
    requestSequence,
  };
}

export function hasAssistantReplyForCause(
  messages: ReadonlyArray<ChatBubble>,
  causeId: string,
): boolean {
  const replyId = `reply:${causeId}`;
  return messages.some(
    (message) => message.role === 'assistant'
      && (message.id === replyId || message.id.startsWith(`${replyId}:`)),
  );
}
