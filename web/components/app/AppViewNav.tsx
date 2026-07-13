'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { ActivityIcon, MessageSquareIcon } from 'lucide-react';

import { Button } from '@/components/ui/button';

const views = [
  { href: '/', label: 'Chat', icon: MessageSquareIcon },
  { href: '/cockpit', label: 'Cockpit', icon: ActivityIcon },
] as const;

export function AppViewNav() {
  const pathname = usePathname();

  return (
    <nav aria-label="OpenMagic views" className="flex items-center rounded-lg bg-muted p-1">
      {views.map(({ href, label, icon: Icon }) => {
        const active = href === '/' ? pathname === href : pathname.startsWith(href);
        return (
          <Button
            key={href}
            asChild
            variant={active ? 'secondary' : 'ghost'}
            size="sm"
            className={active ? 'bg-background shadow-sm hover:bg-background' : ''}
          >
            <Link href={href} aria-current={active ? 'page' : undefined}>
              <Icon />
              <span className="hidden sm:inline">{label}</span>
            </Link>
          </Button>
        );
      })}
    </nav>
  );
}
