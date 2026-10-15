-- 001_init.sql: catalog, showroom, slots, bookings, leads (SPEC §5, §6).
-- Every public table has RLS enabled with no anon policies (CLAUDE.md rule 10); the backend connects as
-- `postgres` through the session pooler, which bypasses RLS. All times are timestamptz (rule 7).
-- Changes vs the first SPEC draft: 17 catalog segments instead of 5 categories, optional engine_cc + range_km
-- for EVs, nullable ex-showroom prices, no stored spoken_price, no consent columns, a `language` column on
-- bookings/leads, and a separate on-road price table with its breakdown.

create extension if not exists pg_trgm;

create table public.showrooms (
  id             text primary key,                 -- 'demo-showroom'
  name           text not null,
  city           text not null,                    -- reference city for prices
  open_time      time not null default '10:00',
  close_time     time not null default '19:00',
  closed_isodow  int[] not null default '{}',      -- ISO weekday numbers, e.g. '{7}' for Sunday
  slot_minutes   int  not null default 30 check (slot_minutes between 5 and 240),
  tz             text not null default 'Asia/Kolkata',
  check (open_time < close_time)
);

create table public.bikes (
  id                text primary key,              -- 'activa-110'
  name              text not null,                 -- 'Honda Activa 110'
  segment           text not null check (segment in (
                      'adventure', 'adventure-crossover', 'adventure-scooter', 'adventure-tourer', 'commuter',
                      'cruiser', 'dual-sport', 'electric-scooter', 'heritage-roadster', 'luxury-tourer',
                      'premium-commuter', 'retro-classic', 'retro-scrambler', 'scooter', 'sporty-commuter',
                      'street', 'superbike')),
  division          text not null,                 -- 'RedWing', 'BigWing', 'RedWing/BigWing', 'EV'
  fuel              text not null check (fuel in ('petrol', 'electric')),
  status            text not null check (status in ('on_sale', 'pre_booking', 'upcoming')),
  engine_cc         numeric(6,2),                  -- null for EVs
  range_km          numeric(5,1),                  -- EVs only: claimed range
  mileage_kmpl      numeric(4,1),
  mileage_basis     text,                          -- 'claimed', 'Autocar India tested efficiency', …
  specs             jsonb not null default '{}',   -- every spec as delivered; unverified ones filtered at query time
  features          text[] not null default '{}',
  colours           text[] not null default '{}',
  unverified_fields text[] not null default '{}',  -- never spoken until verified (SPEC §6)
  pitch             text not null,
  pitch_status      text not null default 'draft',
  official_url      text,
  price_available   boolean not null,
  price_min_inr     integer check (price_min_inr > 0),   -- ex-showroom
  price_max_inr     integer check (price_max_inr > 0),
  price_city        text not null default 'Delhi',
  price_as_of       date,
  price_precision   text not null default 'approx' check (price_precision in ('exact', 'approx', 'missing')),
  price_source      text,
  snapshot_date     date not null,
  check (price_available = (price_min_inr is not null)),
  check (price_max_inr is null or price_min_inr <= price_max_inr),
  check (fuel = 'electric' or engine_cc is not null),
  check (fuel = 'petrol' or mileage_kmpl is null)
);

create table public.bike_variants (
  id               text primary key,              -- 'activa-110-std'
  bike_id          text not null references public.bikes(id) on delete restrict,
  variant_name     text not null,                 -- 'STD', 'DLX', 'H-Smart'
  ex_showroom_inr  integer check (ex_showroom_inr > 0),   -- null: not confirmed, quote the model's range
  inferred         boolean not null default false,        -- mapped from the min/max range (SPEC §6)
  notes            text,
  unique (bike_id, variant_name)
);

-- On-road prices exactly as published by the source, per the source's own variant labels (which don't always
-- match bike_variants, e.g. BikeWale merges the CB350 H'ness into its CB350 listing). Never derived in code.
create table public.bike_on_road_prices (
  id                     bigserial primary key,
  bike_id                text not null references public.bikes(id) on delete cascade,
  variant_label          text not null,
  city                   text not null,
  on_road_inr            integer not null check (on_road_inr > 0),
  ex_showroom_basis_inr  integer check (ex_showroom_basis_inr > 0),  -- the source's ex-showroom figure
  rto_inr                integer check (rto_inr >= 0),
  insurance_inr          integer check (insurance_inr >= 0),
  other_charges          jsonb not null default '[]',  -- [{label, amount_inr}], e.g. TCS 1%, PM E-Drive subsidy
  source                 text not null,
  as_of                  date not null,
  needs_review           text,                          -- non-null: don't speak until a human clears it
  unique (bike_id, city, variant_label),
  check ((ex_showroom_basis_inr is null) = (rto_inr is null) and (rto_inr is null) = (insurance_inr is null))
);
create index bike_on_road_prices_bike on public.bike_on_road_prices (bike_id, city);

create table public.bike_aliases (
  alias   text primary key,                       -- lower-cased, any script: 'activa', 'ஆக்டிவா', 'hiness'
  bike_id text not null references public.bikes(id) on delete cascade
);
create index bike_aliases_trgm on public.bike_aliases using gin (alias gin_trgm_ops);
create index bike_aliases_bike on public.bike_aliases (bike_id);

create table public.slots (
  id           bigserial primary key,
  showroom_id  text not null references public.showrooms(id),
  bike_id      text not null references public.bikes(id),
  starts_at    timestamptz not null,
  capacity     int not null default 1 check (capacity >= 0),   -- demo units available
  booked       int not null default 0 check (booked >= 0),
  unique (showroom_id, bike_id, starts_at),
  check (booked <= capacity)
);
create index slots_lookup on public.slots (showroom_id, bike_id, starts_at);

create table public.bookings (
  id                    bigserial primary key,
  reference             text unique not null,          -- 'HND-7K3Q9'
  slot_id               bigint not null references public.slots(id),
  customer_name         text not null,
  customer_phone        text not null check (customer_phone ~ '^[6-9][0-9]{9}$'),
  status                text not null default 'CONFIRMED' check (status in ('CONFIRMED', 'CANCELLED')),
  language              text not null default 'hi-IN'
                        check (language in ('hi-IN', 'en-IN', 'ta-IN', 'te-IN', 'ml-IN', 'kn-IN')),
  calendar_event_id     text,
  calendar_sync_status  text not null default 'PENDING' check (calendar_sync_status in ('PENDING', 'SYNCED', 'FAILED')),
  calendar_sync_error   text,
  room_name             text,                          -- LiveKit room, for tracing
  created_at            timestamptz not null default now()
);
create unique index one_active_booking_per_phone_slot
  on public.bookings (slot_id, customer_phone) where status = 'CONFIRMED';

create table public.leads (                            -- fallback when booking can't complete
  id             bigserial primary key,
  customer_name  text,
  customer_phone text check (customer_phone is null or customer_phone ~ '^[6-9][0-9]{9}$'),
  interest       text,                                 -- bike_id or free text
  reason         text not null,                        -- 'no_slot', 'caller_requested', 'error', 'upcoming_model'
  language       text not null default 'hi-IN'
                 check (language in ('hi-IN', 'en-IN', 'ta-IN', 'te-IN', 'ml-IN', 'kn-IN')),
  created_at     timestamptz not null default now()
);

alter table public.showrooms           enable row level security;
alter table public.bikes               enable row level security;
alter table public.bike_variants       enable row level security;
alter table public.bike_on_road_prices enable row level security;
alter table public.bike_aliases        enable row level security;
alter table public.slots               enable row level security;
alter table public.bookings            enable row level security;
alter table public.leads               enable row level security;
