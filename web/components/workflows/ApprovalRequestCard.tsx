import { useState } from 'react';
import { UserCheckIcon } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Separator } from '@/components/ui/separator';
import { Textarea } from '@/components/ui/textarea';

export interface ApprovalEmail {
  from: string;
  to: string;
  cc?: string;
  bcc?: string;
  subject: string;
  body: string;
}

interface ApprovalRequestCardProps {
  revision: number;
  email: ApprovalEmail;
  onApprove: (revision?: ApprovalEmail) => void;
  disabled?: boolean;
  statusMessage?: string;
}

export function ApprovalRequestCard({
  revision,
  email,
  onApprove,
  disabled = false,
  statusMessage,
}: ApprovalRequestCardProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [editedEmail, setEditedEmail] = useState<ApprovalEmail>(email);
  const startEditing = () => {
    setEditedEmail(email);
    setIsEditing(true);
  };
  const canApproveRevision = Boolean(
    editedEmail.to.trim() && editedEmail.subject.trim() && editedEmail.body.trim(),
  );

  return (
    <Card className="gap-0 overflow-hidden border-primary/30 py-0 shadow-lg shadow-primary/10">
      <CardHeader className="grid-cols-[1fr_auto] border-b bg-muted/40 px-4 py-3">
        <CardTitle className="flex items-center gap-2 text-sm font-semibold">
          <UserCheckIcon className="size-4" />
          Review before sending
        </CardTitle>
        <Badge variant="outline" className="border-primary/20 bg-background text-[0.625rem] text-primary">
          Revision {revision}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-2 px-4 py-4 text-xs">
        {isEditing ? (
          <EmailEditor email={editedEmail} onChange={setEditedEmail} />
        ) : (
          <>
            <ApprovalField label="From" value={email.from} />
            <ApprovalField label="To" value={email.to} />
            <ApprovalField label="Cc" value={email.cc || 'None'} />
            <ApprovalField label="Bcc" value={email.bcc || 'None'} />
            <ApprovalField label="Subject" value={email.subject} />
            <div className="mt-3 whitespace-pre-line rounded-lg bg-muted/70 p-3 text-sm leading-5 text-foreground/80">
              {email.body}
            </div>
          </>
        )}
      </CardContent>
      <Separator />
      {statusMessage && (
        <p className="px-4 pt-3 text-xs text-muted-foreground" role="status">
          {statusMessage}
        </p>
      )}
      <CardFooter className="grid grid-cols-2 gap-2 px-3 py-3">
        {isEditing ? (
          <>
            <Button variant="outline" size="sm" onClick={() => setIsEditing(false)} disabled={disabled}>
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={() => onApprove(editedEmail)}
              disabled={disabled || !canApproveRevision}
            >
              Approve
            </Button>
          </>
        ) : (
          <>
            <Button variant="outline" size="sm" onClick={startEditing} disabled={disabled}>
              Request changes
            </Button>
            <Button size="sm" onClick={() => onApprove()} disabled={disabled}>
              Approve
            </Button>
          </>
        )}
      </CardFooter>
    </Card>
  );
}

function EmailEditor({
  email,
  onChange,
}: {
  email: ApprovalEmail;
  onChange: (email: ApprovalEmail) => void;
}) {
  const update = (field: keyof ApprovalEmail, value: string) => {
    onChange({ ...email, [field]: value });
  };

  return (
    <div className="space-y-3">
      <ApprovalField label="From" value={email.from} />
      <EditableField label="To" value={email.to} onChange={(value) => update('to', value)} />
      <EditableField label="Cc" value={email.cc || ''} onChange={(value) => update('cc', value)} />
      <EditableField label="Bcc" value={email.bcc || ''} onChange={(value) => update('bcc', value)} />
      <EditableField
        label="Subject"
        value={email.subject}
        onChange={(value) => update('subject', value)}
      />
      <label className="block space-y-1.5">
        <span className="font-medium text-muted-foreground">Message</span>
        <Textarea
          value={email.body}
          onChange={(event) => update('body', event.target.value)}
          rows={9}
          className="resize-y text-sm leading-5"
        />
      </label>
    </div>
  );
}

function EditableField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="grid grid-cols-[3.5rem_1fr] items-center gap-2">
      <span className="font-medium text-muted-foreground">{label}</span>
      <Input value={value} onChange={(event) => onChange(event.target.value)} className="h-8 text-xs" />
    </label>
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
