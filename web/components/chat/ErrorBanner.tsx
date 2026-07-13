import { AlertCircleIcon, XIcon } from 'lucide-react';

import { Button } from '@/components/ui/button';

interface ErrorBannerProps {
  message: string;
  onDismiss: () => void;
}

export function ErrorBanner({ message, onDismiss }: ErrorBannerProps) {
  return (
    <div role="alert" className="mb-3 flex items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
      <AlertCircleIcon className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="font-medium">Message not sent</p>
        <p className="mt-1 break-words text-xs opacity-80">{message}</p>
      </div>
      <Button variant="ghost" size="icon-xs" onClick={onDismiss} aria-label="Dismiss error">
        <XIcon />
      </Button>
    </div>
  );
}
