'use client';

import { useEffect, useState } from 'react';
import { cn } from '@/lib/shadcn/utils';

// Bookings (polled every 5 s, SPEC §12), leads and the live caller profile (SPEC §20.5), and models. All data
// arrives already masked by the database views.
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
type Lead = {
  id: number;
  created_at: string;
  status: 'new' | 'contacted' | 'converted' | 'closed';
  score: 'hot' | 'warm' | 'cold' | null;
  score_value: number | null;
  reason: string;
  next_action: string | null;
  interest_model: string | null;
  customer_display: string | null;
  phone_masked: string | null;
  language: string;
  summary: string | null;
};
type Profile = {
  outcome: string | null;
  max_purchase_intent: number | null;
  intent: string | null;
  use_case: string | null;
  rider: string | null;
  priorities: string[];
  budget_band: string | null;
  timeline: string | null;
  experience: string | null;
  payment_interest: string[];
  objections: string[];
  models_discussed: string[];
  models_suggested: string[];
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
const SCORE_STYLE: Record<string, string> = {
  hot: 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-200',
  warm: 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200',
  cold: 'bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-200',
};
const words = (v: string | null | undefined) => (v ? v.replace(/_/g, ' ') : null);

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

function LeadsList({ onUnauthorized }: { onUnauthorized: () => void }) {
  const { data, error } = usePolled<Lead>('/api/leads', 'leads', POLL_MS * 2, onUnauthorized);
  if (error && !data) return <p className="text-destructive text-sm">{error}</p>;
  if (!data) return <p className="text-muted-foreground text-sm">Loading…</p>;
  return (
    <div className="space-y-2">
      <a
        href="/api/leads/export"
        download
        className="text-muted-foreground hover:text-foreground inline-block text-xs underline"
      >
        Download CSV
      </a>
      {data.length === 0 ? (
        <p className="text-muted-foreground text-sm">No callback requests yet.</p>
      ) : (
        <ul className="space-y-2">
          {data.map((l) => (
            <li key={l.id} className="rounded-md border p-3 text-sm">
              <div className="flex items-baseline justify-between gap-2">
                <span className="text-foreground font-semibold">
                  {l.interest_model?.replace(/^Honda /, '') ?? words(l.reason)}
                </span>
                {l.score && (
                  <span
                    className={cn(
                      'rounded px-1.5 py-0.5 text-xs font-medium',
                      SCORE_STYLE[l.score]
                    )}
                  >
                    {l.score} {l.score_value}
                  </span>
                )}
              </div>
              <div className="text-muted-foreground">
                {[l.customer_display, l.phone_masked, LANGUAGES[l.language] ?? l.language]
                  .filter(Boolean)
                  .join(' · ')}
              </div>
              {l.next_action && <div className="text-foreground mt-1">{l.next_action}</div>}
              {l.summary && <div className="text-muted-foreground mt-1 text-xs">{l.summary}</div>}
              <div className="text-muted-foreground mt-1 text-xs">
                {l.status} · {WHEN.format(new Date(l.created_at))}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CallerProfileCard({
  roomName,
  onUnauthorized,
}: {
  roomName: string;
  onUnauthorized: () => void;
}) {
  const [profile, setProfile] = useState<Profile | null>(null);
  useEffect(() => {
    let stopped = false;
    async function load() {
      if (document.hidden) return;
      try {
        const res = await fetch(`/api/profile?room=${encodeURIComponent(roomName)}`, {
          cache: 'no-store',
        });
        if (res.status === 401) return onUnauthorized();
        if (res.ok && !stopped) setProfile((await res.json()).profile);
      } catch {
        // the profile is a nice-to-have: keep the last one
      }
    }
    load();
    const timer = setInterval(load, 3000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [roomName, onUnauthorized]);
  const rows: [string, string | null][] = profile
    ? [
        ['Looking for', words(profile.intent)],
        ['Use', words(profile.use_case)],
        ['Rider', words(profile.rider)],
        ['Priorities', profile.priorities.map(words).join(', ') || null],
        ['Budget', words(profile.budget_band)?.replace(' l', ' lakh') ?? null],
        ['Timeline', words(profile.timeline)],
        ['Payment', profile.payment_interest.map(words).join(', ') || null],
        ['Concerns', profile.objections.map(words).join(', ') || null],
        ['Models', [...profile.models_discussed, ...profile.models_suggested].join(', ') || null],
        [
          'Buying interest',
          profile.max_purchase_intent ? `${profile.max_purchase_intent} / 5` : null,
        ],
      ]
    : [];
  const known = rows.filter(([, v]) => v);
  return (
    <section className="border-b p-3" aria-live="polite">
      <p className="text-foreground text-sm font-semibold">Caller profile</p>
      {known.length === 0 ? (
        <p className="text-muted-foreground text-xs">Builds up as the caller talks.</p>
      ) : (
        <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs">
          {known.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-muted-foreground">{k}</dt>
              <dd className="text-foreground">{v}</dd>
            </div>
          ))}
        </dl>
      )}
    </section>
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

const TABS = ['bookings', 'leads', 'models'] as const;
const TAB_NOTE: Record<(typeof TABS)[number], string> = {
  bookings: 'refreshes every 5 s',
  leads: 'hottest first',
  models: 'ex-showroom, Delhi',
};

export function SidePanel({
  onUnauthorized,
  roomName,
  className,
}: {
  onUnauthorized: () => void;
  roomName?: string | null;
  className?: string;
}) {
  const [tab, setTab] = useState<(typeof TABS)[number]>('bookings');
  return (
    <aside className={cn('bg-background flex flex-col border-l', className)}>
      {roomName && <CallerProfileCard roomName={roomName} onUnauthorized={onUnauthorized} />}
      <div className="flex gap-1 border-b p-2" role="tablist">
        {TABS.map((t) => (
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
          {TAB_NOTE[tab]}
        </span>
      </div>
      <div className="flex-1 overflow-y-auto p-3">
        {tab === 'bookings' && <BookingsList onUnauthorized={onUnauthorized} />}
        {tab === 'leads' && <LeadsList onUnauthorized={onUnauthorized} />}
        {tab === 'models' && <ModelsList onUnauthorized={onUnauthorized} />}
      </div>
    </aside>
  );
}
