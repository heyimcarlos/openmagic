import { ArrowUpIcon } from 'lucide-react';
import { FormEvent, KeyboardEvent } from 'react';

import { Button } from '@/components/ui/button';

interface ChatInputProps {
  value: string;
  canSubmit: boolean;
  placeholder: string;
  onChange: (value: string) => void;
  onSubmit: () => Promise<void> | void;
}

export function ChatInput({ value, canSubmit, placeholder, onChange, onSubmit }: ChatInputProps) {
  const submit = () => {
    if (canSubmit) void onSubmit();
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    submit();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <form className="rounded-2xl border bg-card p-2 shadow-sm focus-within:ring-2 focus-within:ring-ring/30" onSubmit={handleSubmit}>
      <label htmlFor="chat-message" className="sr-only">Message OpenMagic</label>
      <textarea
        id="chat-message"
        className="max-h-40 min-h-12 w-full resize-none bg-transparent px-2 py-2 text-[0.9375rem] outline-none placeholder:text-muted-foreground"
        rows={1}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder}
      />
      <div className="flex items-center justify-between gap-3 px-1 pb-1">
        <span className="text-xs text-muted-foreground">Shift + Enter for a new line</span>
        <Button type="submit" size="icon-sm" disabled={!canSubmit} aria-label="Send message" className="rounded-full">
          <ArrowUpIcon />
        </Button>
      </div>
    </form>
  );
}
