'use client';

// Three workflow telemetry structures, switchable via ?variant=, on the existing chat route.

import { Bubble, BubbleContent } from '@/components/ui/bubble';
import { Message, MessageContent, MessageHeader } from '@/components/ui/message';
import { MessageScrollerItem } from '@/components/ui/message-scroller';
import { telemetryFixture } from './fixture';
import { CodexRail } from './CodexRail';
import { PrototypeSwitcher } from './PrototypeSwitcher';
import { QuietStack } from './QuietStack';
import { RequestLedger } from './RequestLedger';
import type { WorkflowTelemetryVariant } from './variants';

interface WorkflowTelemetryPrototypeProps {
  variant: WorkflowTelemetryVariant;
  onVariantChange: (variant: WorkflowTelemetryVariant) => void;
}

export function WorkflowTelemetryPrototype({ variant, onVariantChange }: WorkflowTelemetryPrototypeProps) {
  return (
    <>
      <MessageScrollerItem messageId="prototype-user" scrollAnchor>
        <Message align="end">
          <MessageContent>
            <Bubble variant="default">
              <BubbleContent className="max-w-[min(34rem,85vw)] whitespace-pre-wrap">
                {telemetryFixture.userMessage}
              </BubbleContent>
            </Bubble>
          </MessageContent>
        </Message>
      </MessageScrollerItem>

      <MessageScrollerItem messageId="prototype-assistant">
        <Message align="start">
          <MessageContent>
            <MessageHeader>OpenMagic</MessageHeader>
            <Bubble variant="ghost">
              <BubbleContent className="w-full">
                <p className="text-[0.9375rem] leading-7">{telemetryFixture.assistantMessage}</p>
                {variant === 'A' && <QuietStack fixture={telemetryFixture} />}
                {variant === 'B' && <RequestLedger fixture={telemetryFixture} />}
                {variant === 'C' && (
                  <CodexRail key="hover-arrow" fixture={telemetryFixture} chevronVisibility="hover" />
                )}
                {variant === 'D' && (
                  <CodexRail key="always-arrow" fixture={telemetryFixture} chevronVisibility="always" />
                )}
              </BubbleContent>
            </Bubble>
          </MessageContent>
        </Message>
      </MessageScrollerItem>

      <PrototypeSwitcher current={variant} onSelect={onVariantChange} />
    </>
  );
}
