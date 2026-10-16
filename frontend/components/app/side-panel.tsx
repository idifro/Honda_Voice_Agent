'use client';

import { useEffect, useState } from 'react';
import { cn } from '@/lib/shadcn/utils';

// Bookings (polled every 5 s, SPEC §12) and models. All data arrives already masked by the database view.
type Booking = {
  reference: string;
  status: 'CONFIRMED' | 'CANCELLED';
  model: string;
  starts_at: string;
  customer_display: string;
  phone_masked: string;
  language: string;
  licence_confirmed: boolean;
  calendar_sync_status: 'PENDING' | 'SYNCED' | 'FAILED';
};
type Model = {
  id: string;
  name: string;
  segment: string;
  status: 'on_sale' | 'pre_booking' | 'upcoming';
  price_available: boolean;
  price_min_inr: number | null;
  price_max_inr: number | null;
};

const LANGUAGES: Record<string, string> = {
  'hi-IN': 'Hindi',
  'en-IN': 'English',
  'ta-IN': 'Tamil',
  'te-IN': 'Telugu',
  'ml-IN': 'Malayalam',
  'kn-IN': 'Kannada',
};
const WHEN = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  hour: 'numeric',
  minute: '2-digit',
});
const INR = new Intl.NumberFormat('en-IN', {
  style: 'currency',
  currency: 'INR',
  maximumFractionDigits: 0,
});
const POLL_MS = 5000;

function usePolled<T>(
  url: string,
  key: string,
  everyMs: number | null,
  onUnauthorized: () => void
) {
  const [data, setData] = useState<T[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let stopped = false;
    async function load() {
      if (document.hidden) return; // don't poll a background tab
      try {
        const res = await fetch(url, { cache: 'no-store' });
        if (res.status === 401) return onUnauthorized();
        if (!res.ok) throw new Error(String(res.status));
        const body = await res.json();
        if (!stopped) {
          setData(body[key]);
          setError(null);
        }
      } catch {
        if (!stopped) setError('Could not load');
      }
    }
    load();
    const timer = everyMs ? setInterval(load, everyMs) : undefined;
    return () => {
      stopped = true;
      if (timer) clearInterval(timer);
    };
  }, [url, key, everyMs, onUnauthorized]);
  return { data, error };
}

function BookingsList({ onUnauthorized }: { onUnauthorized: () => void }) {
  const { data, error } = usePolled<Booking>('/api/bookings', 'bookings', POLL_MS, onUnauthorized);
  if (error && !data) return <p className="text-destructive text-sm">{error}</p>;
  if (!data) return <p className="text-muted-foreground text-sm">Loading…</p>;
  if (data.length === 0)
    return <p className="text-muted-foreground text-sm">No bookings today or upcoming yet.</p>;
  return (
    <ul className="space-y-2">
      {data.map((b) => (
        <li
          key={b.reference}
          className={cn('rounded-md border p-3 text-sm', b.status === 'CANCELLED' && 'opacity-50')}
        >
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-foreground font-semibold">{b.model.replace(/^Honda /, '')}</span>
            <span className="text-muted-foreground font-mono text-xs">{b.reference}</span>
          </div>
          <div className="text-foreground">{WHEN.format(new Date(b.starts_at))}</div>
          <div className="text-muted-foreground">
            {b.customer_display} · {b.phone_masked} · {LANGUAGES[b.language] ?? b.language}
          </div>
          <div className="mt-1 flex flex-wrap gap-2 text-xs">
            <span className={b.status === 'CONFIRMED' ? 'text-green-600 dark:text-green-400' : ''}>
              {b.status === 'CONFIRMED' ? 'Confirmed' : 'Cancelled'}
            </span>
            {b.licence_confirmed && <span className="text-muted-foreground">Licence ✓</span>}
            <span
              className={cn(
                'text-muted-foreground',
                b.calendar_sync_status === 'FAILED' && 'text-destructive'
              )}
            >
              Calendar: {b.calendar_sync_status.toLowerCase()}
            </span>
          </div>
        </li>
      ))}
    </ul>
  );
}

function ModelsList({ onUnauthorized }: { onUnauthorized: () => void }) {
  const { data, error } = usePolled<Model>('/api/catalog', 'models', null, onUnauthorized);
  if (error && !data) return <p className="text-destructive text-sm">{error}</p>;
  if (!data) return <p className="text-muted-foreground text-sm">Loading…</p>;
  return (
    <ul className="divide-y text-sm">
      {data.map((m) => (
        <li key={m.id} className="flex items-baseline justify-between gap-3 py-2">
          <span className="text-foreground">
            {m.name.replace(/^Honda /, '')}
            {m.status !== 'on_sale' && (
              <span className="text-muted-foreground ml-1 text-xs">
                ({m.status === 'upcoming' ? 'upcoming' : 'pre-booking'})
              </span>
            )}
          </span>
          <span className="text-muted-foreground shrink-0 text-right tabular-nums">
            {m.price_available && m.price_min_inr
              ? m.price_max_inr && m.price_max_inr !== m.price_min_inr
                ? `${INR.format(m.price_min_inr)} – ${INR.format(m.price_max_inr)}`
                : INR.format(m.price_min_inr)
              : 'Price TBA'}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function SidePanel({
  onUnauthorized,
  className,
}: {
  onUnauthorized: () => void;
  className?: string;
}) {
  const [tab, setTab] = useState<'bookings' | 'models'>('bookings');
  return (
    <aside className={cn('bg-background flex flex-col border-l', className)}>
      <div className="flex gap-1 border-b p-2" role="tablist">
        {(['bookings', 'models'] as const).map((t) => (
          <button
            key={t}
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={cn(
              'rounded-md px-3 py-1.5 text-sm font-medium capitalize',
              tab === t ? 'bg-muted text-foreground' : 'text-muted-foreground hover:text-foreground'
            )}
          >
            {t}
          </button>
        ))}
        <span className="text-muted-foreground ml-auto self-center pr-2 text-xs">
          {tab === 'bookings' ? 'refreshes every 5 s' : 'ex-showroom, Delhi'}
        </span>
      </div>
      <div className="flex-1 overflow-y-auto p-3">
        {tab === 'bookings' ? (
          <BookingsList onUnauthorized={onUnauthorized} />
        ) : (
          <ModelsList onUnauthorized={onUnauthorized} />
        )}
      </div>
    </aside>
  );
}
