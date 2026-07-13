import { UserCheckIcon } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Separator } from '@/components/ui/separator';

export interface ApprovalEmail {
  from: string;
  to: string;
  subject: string;
  body: string;
}

interface ApprovalRequestCardProps {
  revision: number;
  email: ApprovalEmail;
  onApprove: () => void;
  onRequestChanges: () => void;
}

export function ApprovalRequestCard({
  revision,
  email,
  onApprove,
  onRequestChanges,
}: ApprovalRequestCardProps) {
  return (
    <Card className="gap-0 overflow-hidden border-primary/30 py-0 shadow-lg shadow-primary/10">
      <CardHeader className="grid-cols-[1fr_auto] border-b bg-primary/8 px-4 py-3">
        <CardTitle className="flex items-center gap-2 text-xs font-semibold text-primary">
          <UserCheckIcon className="size-4" />
          Authority · exact approval
        </CardTitle>
        <Badge variant="outline" className="border-primary/20 bg-background text-[0.625rem] text-primary">
          Revision {revision}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-2 px-4 py-4 text-xs">
        <ApprovalField label="From" value={email.from} />
        <ApprovalField label="To" value={email.to} />
        <ApprovalField label="Subject" value={email.subject} />
        <div className="mt-3 whitespace-pre-line rounded-lg bg-muted/70 p-3 text-sm leading-5 text-foreground/80">
          {email.body}
        </div>
      </CardContent>
      <Separator />
      <CardFooter className="grid grid-cols-2 gap-2 px-3 py-3">
        <Button variant="outline" size="sm" onClick={onRequestChanges}>
          Request changes
        </Button>
        <Button size="sm" onClick={onApprove}>
          Approve exact email
        </Button>
      </CardFooter>
    </Card>
  );
}

function ApprovalField({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[3.5rem_1fr] gap-2">
      <span className="font-medium text-muted-foreground">{label}</span>
      <span className="min-w-0 font-medium text-foreground/75">{value}</span>
    </div>
  );
}
