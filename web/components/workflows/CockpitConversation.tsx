import { ArrowRightIcon, CheckIcon, SparklesIcon } from 'lucide-react';

import { ApprovalRequestCard, type ApprovalEmail } from './ApprovalRequestCard';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import type { CockpitStage } from '@/lib/workflowCockpit';

interface CockpitConversationProps {
  stage: CockpitStage;
  revision: number;
  email: ApprovalEmail;
  onStart: () => void;
  onApprove: () => void;
  onRequestChanges: () => void;
  onChangeBody: (body: string) => void;
  onSubmitRevision: () => void;
}

export function CockpitConversation({
  stage,
  revision,
  email,
  onStart,
  onApprove,
  onRequestChanges,
  onChangeBody,
  onSubmitRevision,
}: CockpitConversationProps) {
  return (
    <section className="flex min-h-[42rem] flex-col border-b bg-card lg:border-b-0 lg:border-r">
      <header className="flex items-center gap-3 border-b px-5 py-4">
        <div className="grid size-9 place-items-center rounded-full bg-primary/10 font-serif font-bold text-primary">
          A
        </div>
        <div>
          <h2 className="text-sm font-semibold">Ava</h2>
          <p className="text-[0.6875rem] text-muted-foreground">Interaction Agent · online</p>
        </div>
      </header>

      <div className="flex-1 space-y-4 overflow-y-auto px-5 py-5">
        <ConversationBubble>Hi Carlos. What would you like to work on?</ConversationBubble>
        {stage !== 'ready' && (
          <ConversationBubble side="user">
            Prepare John Smith&apos;s 2026 renewal email. Show me the exact email before sending.
          </ConversationBubble>
        )}
        {stage !== 'ready' && (
          <ConversationBubble>
            I found one active 2026 renewal for John Smith at Acme Brokerage. The draft is ready
            for your review.
          </ConversationBubble>
        )}

        {(stage === 'approval' || stage === 'reapproval') && (
          <>
            {stage === 'reapproval' && (
              <ConversationBubble>
                Revision 2 is ready. The earlier Send Job was safely replaced. Please approve
                this exact email.
              </ConversationBubble>
            )}
            <ApprovalRequestCard
              revision={revision}
              email={email}
              onApprove={onApprove}
              onRequestChanges={onRequestChanges}
            />
          </>
        )}

        {stage === 'editing' && (
          <Card className="gap-0 border-orange-200 bg-orange-50/60 p-4 shadow-none">
            <div className="flex items-center gap-2 text-xs font-semibold text-orange-800">
              <SparklesIcon className="size-4" />
              Request changes
            </div>
            <p className="mt-2 text-xs leading-5 text-muted-foreground">
              A material edit creates a linked revision that requires exact approval again.
            </p>
            <textarea
              aria-label="Revised email body"
              value={email.body}
              onChange={(event) => onChangeBody(event.target.value)}
              className="mt-3 min-h-36 w-full resize-none rounded-lg border bg-background p-3 text-sm leading-5 outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30"
            />
            <div className="mt-3 flex justify-end">
              <Button size="sm" onClick={onSubmitRevision}>
                Submit revision
              </Button>
            </div>
          </Card>
        )}

        {stage === 'sent' && (
          <>
            <div className="flex items-center gap-3 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800">
              <span className="grid size-8 shrink-0 place-items-center rounded-full bg-emerald-600 text-white">
                <CheckIcon className="size-4" />
              </span>
              <div>
                <p className="font-semibold">Exact email revision {revision} approved</p>
                <p className="mt-1 text-xs text-emerald-700/70">
                  Approval consumed when dispatch started.
                </p>
              </div>
            </div>
            <ConversationBubble>The renewal email was sent successfully.</ConversationBubble>
          </>
        )}
      </div>

      <footer className="border-t bg-background/80 p-4">
        {stage === 'ready' ? (
          <Card className="gap-0 p-3 shadow-sm focus-within:border-primary">
            <textarea
              readOnly
              value="Prepare John Smith's 2026 renewal email. Show me the exact email before sending."
              aria-label="Renewal request"
              className="h-16 w-full resize-none bg-transparent text-sm leading-5 outline-none"
            />
            <div className="mt-2 flex items-center justify-between">
              <span className="text-[0.625rem] font-medium uppercase tracking-[0.14em] text-muted-foreground">
                Interaction Agent
              </span>
              <Button size="sm" onClick={onStart}>
                Send <ArrowRightIcon />
              </Button>
            </div>
          </Card>
        ) : (
          <div className="flex items-center justify-between rounded-full border bg-muted/40 px-4 py-3 text-xs text-muted-foreground">
            <span>{stage === 'sent' ? 'Workflow completed' : 'Complete the approval request above'}</span>
            <SparklesIcon className="size-3.5" />
          </div>
        )}
      </footer>
    </section>
  );
}

function ConversationBubble({
  side = 'assistant',
  children,
}: {
  side?: 'assistant' | 'user';
  children: React.ReactNode;
}) {
  return (
    <div className={cn('flex', side === 'user' ? 'justify-end' : 'justify-start')}>
      <div
        className={cn(
          'max-w-[88%] rounded-2xl px-4 py-3 text-sm leading-5',
          side === 'user'
            ? 'rounded-tr-md bg-primary text-primary-foreground'
            : 'rounded-tl-md bg-muted text-foreground',
        )}
      >
        {children}
      </div>
    </div>
  );
}
