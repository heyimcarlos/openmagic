import type { ChatTurnTelemetry } from '@/lib/chatTelemetry';

export interface ChatBubble {
  id: string;
  role: string;
  text: string;
  telemetry?: ChatTurnTelemetry;
}
