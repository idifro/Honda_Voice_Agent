-- 005_advisor_leads.sql: sales advisor and leads (SPEC §20.6). Fit facts and demo finance schemes for the
-- advisor's tools; calls, caller profiles and per-turn signals; a customer list built from callers who consent
-- to a callback; leads and bookings linked to both. RLS on every new table (rule 10); the backend connects as
-- `postgres` (bypasses RLS), the frontend role reads masked views only.

-- Facts recommend_bikes ranks on (seeded from data/catalog/_enrichment.json). is_estimate: a value with no
-- published source, used for ranking only and never spoken.
create table public.bike_fit (
  bike_id         text primary key references public.bikes(id) on delete cascade,
  kerb_weight_kg  numeric(5,1),
  seat_height_mm  int,
  ease_score      smallint not null check (ease_score between 1 and 5),
  use_cases       text[] not null default '{}'
                  check (use_cases <@ array['commute','family_errands','long_rides','college','delivery','weekend_fun']),
  rider_fit       text[] not null default '{}'
                  check (rider_fit <@ array['first_time','shorter_riders','taller_riders','experienced','pillion_comfort','budget','upgrading']),
  source          text not null,
  is_estimate     boolean not null default false
);

-- Demo EMI plans (seeded from data/catalog/_finance_schemes.json). The EMI itself is computed in code
-- (domain/finance.py) from a plan and the catalog price; nothing here is a real lender's offer.
create table public.finance_schemes (
  id                    text primary key,
  name                  text not null,
  interest_rate_pct     numeric(5,2) not null check (interest_rate_pct >= 0 and interest_rate_pct < 40),
  tenure_months         int[] not null check (cardinality(tenure_months) > 0),
  min_down_payment_pct  numeric(5,2) not null check (min_down_payment_pct between 0 and 100),
  processing_fee_inr    int not null default 0 check (processing_fee_inr >= 0),
  is_demo               boolean not null default true,
  as_of                 date not null
);

-- The customer list: one row per mobile number that consented to a callback (or booked).
create table public.customers (
  id                    uuid primary key default gen_random_uuid(),
  phone                 text not null unique check (phone ~ '^[6-9][0-9]{9}$'),
  name                  text,
  preferred_language    text not null default 'hi-IN'
                        check (preferred_language in ('hi-IN', 'en-IN', 'ta-IN', 'te-IN', 'ml-IN', 'kn-IN')),
  callback_consent      boolean not null default false,
  consent_at            timestamptz,
  consent_text_version  text,
  first_seen_at         timestamptz not null default now(),
  last_contact_at       timestamptz not null default now(),
  check (not callback_consent or (consent_at is not null and consent_text_version is not null))
);

-- One row per call, including anonymous ones (insights). summary: written after the call, no name or number.
create table public.calls (
  id                   bigserial primary key,
  room_name            text not null unique,
  started_at           timestamptz not null default now(),
  ended_at             timestamptz,
  languages            text[] not null default '{}',
  customer_id          uuid references public.customers(id),
  outcome              text check (outcome in ('info', 'booked', 'lead', 'cancelled', 'dropped')),
  summary              text,
  max_purchase_intent  smallint check (max_purchase_intent between 1 and 5)
);

-- What the caller is looking for (SPEC §20.2), merged turn by turn. Categories only, no transcript text.
create table public.caller_profiles (
  call_id           bigint primary key references public.calls(id) on delete cascade,
  intent            text,
  use_case          text,
  rider             text,
  priorities        text[] not null default '{}',
  budget_band       text,
  timeline          text,
  experience        text,
  payment_interest  text[] not null default '{}',
  objections        text[] not null default '{}',
  models_discussed  text[] not null default '{}',
  models_suggested  text[] not null default '{}',
  confidence        jsonb not null default '{}',
  updated_at        timestamptz not null default now()
);

-- Raw per-turn answers, to measure providers and later train Laya (SPEC §23.2b).
create table public.call_signals (
  id          bigserial primary key,
  call_id     bigint not null references public.calls(id) on delete cascade,
  turn_index  int not null,
  question    text not null,
  answer      text not null,
  confidence  numeric(4,3),
  provider    text not null,                       -- 'gpt-5.4-nano', 'laya-multilingual@0.4.7'
  latency_ms  int,
  created_at  timestamptz not null default now()
);
create index call_signals_call on public.call_signals (call_id, turn_index);

alter table public.leads
  add column customer_id       uuid references public.customers(id),
  add column call_id           bigint references public.calls(id),
  add column score             text check (score in ('hot', 'warm', 'cold')),
  add column score_value       smallint check (score_value between 0 and 100),
  add column status            text not null default 'new'
                               check (status in ('new', 'contacted', 'converted', 'closed')),
  add column next_action       text,
  add column callback_after    timestamptz,
  add column contacted_at      timestamptz,
  add column interest_bike_id  text references public.bikes(id);

alter table public.bookings
  add column customer_id  uuid references public.customers(id),
  add column call_id      bigint references public.calls(id);

alter table public.bike_fit         enable row level security;
alter table public.finance_schemes  enable row level security;
alter table public.customers        enable row level security;
alter table public.calls            enable row level security;
alter table public.caller_profiles  enable row level security;
alter table public.call_signals     enable row level security;

-- Frontend views (masked, read-only role from 004).
create view frontend.leads as
select l.id,
       l.created_at,
       l.status,
       l.score,
       l.score_value,
       l.reason,
       l.next_action,
       l.callback_after,
       bk.name                                                     as interest_model,
       split_part(btrim(coalesce(c.name, l.customer_name)), ' ', 1) as customer_display,
       left(coalesce(c.phone, l.customer_phone), 2) || 'xxx xx'
         || right(coalesce(c.phone, l.customer_phone), 3)          as phone_masked,
       coalesce(c.preferred_language, l.language)                  as language,
       ca.summary
  from public.leads l
  left join public.customers c on c.id = l.customer_id
  left join public.calls ca    on ca.id = l.call_id
  left join public.bikes bk    on bk.id = l.interest_bike_id;

create view frontend.caller_profiles as
select ca.room_name,
       ca.started_at,
       ca.outcome,
       ca.max_purchase_intent,
       p.intent, p.use_case, p.rider, p.priorities, p.budget_band, p.timeline, p.experience,
       p.payment_interest, p.objections, p.models_discussed, p.models_suggested, p.updated_at
  from public.calls ca
  join public.caller_profiles p on p.call_id = ca.id;

revoke all on frontend.leads, frontend.caller_profiles from public, anon, authenticated;
grant select on frontend.leads, frontend.caller_profiles to frontend_reader;
