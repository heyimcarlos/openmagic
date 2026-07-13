import { RotateCcwIcon, SettingsIcon } from 'lucide-react';

import { AppViewNav } from '@/components/app/AppViewNav';
import { Button } from '@/components/ui/button';

interface ChatHeaderProps {
  onOpenSettings: () => void;
  onClearHistory: () => void;
}

export function ChatHeader({ onOpenSettings, onClearHistory }: ChatHeaderProps) {
  return (
    <header className="flex items-center justify-between gap-3 border-b px-4 py-3 sm:px-6">
      <div className="min-w-0">
        <h1 className="font-semibold tracking-tight">OpenMagic</h1>
        <p className="hidden text-xs text-muted-foreground sm:block">Your personal assistant</p>
      </div>
      <div className="flex items-center gap-2">
        <AppViewNav />
        <div className="flex items-center gap-1">
        <Button variant="ghost" size="icon-sm" onClick={onClearHistory} aria-label="Clear conversation">
          <RotateCcwIcon />
        </Button>
        <Button variant="ghost" size="icon-sm" onClick={onOpenSettings} aria-label="Open settings">
          <SettingsIcon />
        </Button>
        </div>
      </div>
    </header>
  );
}
