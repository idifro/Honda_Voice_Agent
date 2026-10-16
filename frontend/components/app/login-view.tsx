'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { PrivacyNotice } from '@/components/app/privacy-notice';
import { Button } from '@/components/ui/button';

/** Demo gate: the passcode goes to /api/login in the request body (never the URL); the server sets an
 * HttpOnly session cookie, then the page re-renders signed in. */
export function LoginView() {
  const router = useRouter();
  const [passcode, setPasscode] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ passcode }),
      });
      if (res.ok) {
        setPasscode('');
        router.refresh();
        return;
      }
      const body = await res.json().catch(() => ({}));
      setError(body.error ?? 'Sign-in failed');
    } catch {
      setError('Network error, please try again');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="mx-auto flex w-full max-w-md flex-col items-center px-4 text-center">
      <h1 className="text-foreground text-xl font-semibold">Honda two-wheeler voice assistant</h1>
      <p className="text-muted-foreground mt-1 text-sm">
        Ask about models and prices, check showroom hours, or book a test ride in Tamil, English,
        Hindi, Telugu, Kannada or Malayalam.
      </p>
      <form onSubmit={submit} className="mt-6 flex w-full flex-col gap-3">
        <label htmlFor="passcode" className="text-foreground text-left text-sm font-medium">
          Demo passcode
        </label>
        <input
          id="passcode"
          type="password"
          autoComplete="current-password"
          value={passcode}
          onChange={(e) => setPasscode(e.target.value)}
          className="border-input bg-background text-foreground focus-visible:ring-ring h-11 rounded-md border px-3 text-base outline-none focus-visible:ring-2"
          required
          maxLength={200}
        />
        {error && (
          <p role="alert" className="text-destructive text-left text-sm">
            {error}
          </p>
        )}
        <Button type="submit" size="lg" disabled={busy || !passcode} className="rounded-full">
          {busy ? 'Checking…' : 'Continue'}
        </Button>
      </form>
      <PrivacyNotice className="text-muted-foreground mt-6 text-left text-xs leading-5" />
    </section>
  );
}
