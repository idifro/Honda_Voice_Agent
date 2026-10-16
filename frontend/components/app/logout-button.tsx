'use client';

import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';

export function LogoutButton() {
  const router = useRouter();
  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={async () => {
        await fetch('/api/logout', { method: 'POST' });
        router.refresh();
      }}
    >
      Sign out
    </Button>
  );
}
