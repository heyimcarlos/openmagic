import type { ReactNode } from 'react';
import { LoaderCircleIcon } from 'lucide-react';

import { Markdown } from './Markdown';
import type { ChatBubble } from './types';
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
  prototype?: ReactNode;
}

export function ChatMessages({ messages, isWaitingForResponse, prototype }: ChatMessagesProps) {
  return (
    <MessageScrollerProvider autoScroll defaultScrollPosition="end">
      <MessageScroller className="h-[calc(100dvh-12rem)] min-h-[24rem] sm:h-[70vh]">
        <MessageScrollerViewport>
          <MessageScrollerContent aria-busy={isWaitingForResponse} className="gap-6 px-4 py-6 sm:px-7">
            {messages.length === 0 && !prototype && <EmptyState />}

            {messages.map((message) => (
              <ChatMessage key={message.id} message={message} />
            ))}

            {prototype}

            {isWaitingForResponse && <TypingIndicator />}
          </MessageScrollerContent>
        </MessageScrollerViewport>
        <MessageScrollerButton className="shadow-md" />
      </MessageScroller>
    </MessageScrollerProvider>
  );
}

function ChatMessage({ message }: { message: ChatBubble }) {
  const isUser = message.role === 'user';
  const isDraft = message.role === 'draft';

  return (
    <MessageScrollerItem messageId={message.id} scrollAnchor={isUser}>
      <Message align={isUser ? 'end' : 'start'}>
        <MessageContent>
          {!isUser && <MessageHeader>{isDraft ? 'Draft' : 'OpenMagic'}</MessageHeader>}
          <Bubble variant={isUser ? 'default' : isDraft ? 'outline' : 'ghost'}>
            <BubbleContent className={isUser ? 'max-w-[min(34rem,85vw)] whitespace-pre-wrap' : 'w-full'}>
              {isUser ? message.text : <Markdown>{message.text}</Markdown>}
            </BubbleContent>
          </Bubble>
        </MessageContent>
      </Message>
    </MessageScrollerItem>
  );
}

function TypingIndicator() {
  return (
    <MessageScrollerItem>
      <Marker role="status" aria-label="OpenMagic is working" className="px-1">
        <MarkerIcon>
          <LoaderCircleIcon className="animate-spin" />
        </MarkerIcon>
        <MarkerContent className="shimmer">Working on it...</MarkerContent>
      </Marker>
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
