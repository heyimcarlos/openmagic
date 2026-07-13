import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'OpenMagic',
  description: 'Your personal assistant for email, reminders, and durable work.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen">
        {children}
      </body>
    </html>
  );
}
