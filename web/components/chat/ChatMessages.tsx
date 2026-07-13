import { useState } from 'react';
import { LoaderCircleIcon } from 'lucide-react';

import { Markdown } from './Markdown';
import type { ChatBubble } from './types';
import { WorkflowTelemetry } from './workflow-telemetry/WorkflowTelemetry';
import { ApprovalRequestCard } from '@/components/workflows/ApprovalRequestCard';
import type { ApprovalEmail } from '@/components/workflows/ApprovalRequestCard';
import type { ApprovalRequest } from '@/lib/chatTelemetry';
import type { ChatTurnTelemetry } from '@/lib/chatTelemetry';
import { Bubble, BubbleContent } from '@/components/ui/bubble';
import { Marker, MarkerContent, MarkerIcon } from '@/components/ui/marker';
import { Message, MessageContent, MessageHeader } from '@/components/ui/message';
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from '@/components/ui/message-scroller';

interface ChatMessagesProps {
  messages: ReadonlyArray<ChatBubble>;
  isWaitingForResponse: boolean;
  pendingTelemetry?: ChatTurnTelemetry;
  onApprove: (request: ApprovalRequest, revision?: ApprovalEmail) => Promise<void>;
}

export function ChatMessages({
  messages,
  isWaitingForResponse,
  pendingTelemetry,
  onApprove,
}: ChatMessagesProps) {
  const lastUserMessageId = [...messages]
    .reverse()
    .find((message) => message.role === 'user')?.id;
  const newestTelemetryMessageId = [...messages]
    .reverse()
    .find((message) => message.role === 'assistant' && message.telemetry)?.id;

  return (
    <MessageScrollerProvider defaultScrollPosition="end">
      <MessageScroller className="h-[calc(100dvh-12rem)] min-h-[24rem] sm:h-[70vh]">
        <MessageScrollerViewport>
          <MessageScrollerContent aria-busy={isWaitingForResponse} className="gap-6 px-4 py-6 sm:px-7">
            {messages.length === 0 && <EmptyState />}

            {messages.map((message) => (
              <ChatMessage
                key={message.id}
                message={message}
                scrollAnchor={message.id === lastUserMessageId}
                defaultActivityOpen={message.id === newestTelemetryMessageId}
                onApprove={onApprove}
              />
            ))}

            {isWaitingForResponse && <TypingIndicator telemetry={pendingTelemetry} />}
          </MessageScrollerContent>
        </MessageScrollerViewport>
        <MessageScrollerButton className="shadow-md" />
      </MessageScroller>
    </MessageScrollerProvider>
  );
}

function ChatMessage({
  message,
  scrollAnchor,
  defaultActivityOpen,
  onApprove,
}: {
  message: ChatBubble;
  scrollAnchor: boolean;
  defaultActivityOpen: boolean;
  onApprove: (request: ApprovalRequest, revision?: ApprovalEmail) => Promise<void>;
}) {
  const [isApproving, setIsApproving] = useState(false);
  const isUser = message.role === 'user';
  const isDraft = message.role === 'draft';
  const telemetry = message.role === 'assistant' ? message.telemetry : undefined;
  const approval = telemetry?.approvalRequest;
  const approve = async (revision?: ApprovalEmail) => {
    if (!approval) return;
    setIsApproving(true);
    try {
      await onApprove(approval, revision);
    } catch {
      // The page presents the safe error and refreshes the current approval state.
    } finally {
      setIsApproving(false);
    }
  };

  return (
    <MessageScrollerItem messageId={message.id} scrollAnchor={scrollAnchor}>
      <Message align={isUser ? 'end' : 'start'}>
        <MessageContent>
          {!isUser && <MessageHeader>{isDraft ? 'Draft' : 'OpenMagic'}</MessageHeader>}
          <Bubble variant={isUser ? 'default' : isDraft ? 'outline' : 'ghost'}>
            <BubbleContent className={isUser ? 'max-w-[min(34rem,85vw)] whitespace-pre-wrap' : 'w-full'}>
              {isUser ? message.text : (
                <>
                  {approval ? (
                    <p className="mb-3 text-sm text-muted-foreground">
                      Your renewal email is ready. Review the exact message below.
                    </p>
                  ) : (
                    <Markdown>{message.text}</Markdown>
                  )}
                  {approval && (
                    <ApprovalRequestCard
                      revision={approval.revision}
                      email={{
                        from: approval.sender,
                        to: approval.to.join(', '),
                        cc: approval.cc.join(', '),
                        bcc: approval.bcc.join(', '),
                        subject: approval.subject,
                        body: approval.body,
                      }}
                      onApprove={(revision) => void approve(revision)}
                      disabled={isApproving}
                      statusMessage={isApproving ? 'Recording your approval...' : undefined}
                    />
                  )}
                  {telemetry && (
                    <WorkflowTelemetry
                      telemetry={telemetry}
                      defaultActivityOpen={defaultActivityOpen}
                    />
                  )}
                </>
              )}
            </BubbleContent>
          </Bubble>
        </MessageContent>
      </Message>
    </MessageScrollerItem>
  );
}

function TypingIndicator({ telemetry }: { telemetry?: ChatTurnTelemetry }) {
  return (
    <MessageScrollerItem>
      <div>
        <Marker role="status" aria-label="OpenMagic is working" className="px-1">
          <MarkerIcon>
            <LoaderCircleIcon className="animate-spin" />
          </MarkerIcon>
          <MarkerContent className="shimmer">Working on it...</MarkerContent>
        </Marker>
        {telemetry && (
          <WorkflowTelemetry
            telemetry={telemetry}
            defaultActivityOpen
            className="mt-1"
          />
        )}
      </div>
    </MessageScrollerItem>
  );
}

function EmptyState() {
  return (
    <MessageScrollerItem className="flex min-h-[22rem] items-center justify-center">
      <div className="mx-auto max-w-sm text-center">
        <div className="mx-auto mb-4 flex size-12 items-center justify-center rounded-2xl bg-primary text-xl text-primary-foreground shadow-sm">
          ✦
        </div>
        <h2 className="text-xl font-semibold tracking-tight">What can I help with?</h2>
        <p className="mt-2 text-sm leading-6 text-muted-foreground">
          Ask about your work, draft an email, or set a reminder.
        </p>
      </div>
    </MessageScrollerItem>
  );
}
