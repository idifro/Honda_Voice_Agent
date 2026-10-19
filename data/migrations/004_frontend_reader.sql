-- 004_frontend_reader.sql: a read-only, least-privilege database role for the Next.js server routes
-- (SPEC §12, rule 9). It can read only two views in schema `frontend`; bookings are masked in SQL, so the
-- frontend never receives a full phone number or full name, and cannot modify anything.
-- The role is created without a password (NOLOGIN). scripts/set_frontend_db_password.py sets a generated
-- password and enables login; the password never appears in the repo.

do $$
begin
  if not exists (select from pg_roles where rolname = 'frontend_reader') then
    create role frontend_reader nologin;
  end if;
end $$;
alter role frontend_reader connection limit 3;
alter role frontend_reader set default_transaction_read_only = on;
alter role frontend_reader set statement_timeout = '3s';

-- A separate schema: Supabase's REST API (PostgREST) only exposes `public`, and anon/authenticated get no
-- grants here, so these views are reachable only through this role.
create schema if not exists frontend;
revoke all on schema frontend from public;

-- Today's and upcoming bookings, masked: "Ravi K.", "90xxx xx345".
create view frontend.bookings as
select b.reference,
       b.status,
       bk.name                                                     as model,
       s.starts_at,
       sr.slot_minutes,
       split_part(btrim(b.customer_name), ' ', 1)
         || case when btrim(b.customer_name) like '% %'
                 then ' ' || left(split_part(btrim(b.customer_name), ' ', 2), 1) || '.'
                 else '' end                                       as customer_display,
       left(b.customer_phone, 2) || 'xxx xx' || right(b.customer_phone, 3) as phone_masked,
       b.language,
       b.licence_confirmed,
       b.calendar_sync_status,
       b.created_at
  from public.bookings b
  join public.slots s      on s.id = b.slot_id
  join public.bikes bk     on bk.id = s.bike_id
  join public.showrooms sr on sr.id = s.showroom_id
 where s.starts_at >= date_trunc('day', now() at time zone 'Asia/Kolkata') at time zone 'Asia/Kolkata';

-- Models and base ex-showroom prices for the side panel (public information).
create view frontend.catalog as
select id, name, segment, status, fuel, price_available, price_min_inr, price_max_inr
  from public.bikes;

-- The views run with their owner's rights (they read the base tables); the role gets nothing else.
revoke all on all tables in schema frontend from public, anon, authenticated;
grant usage on schema frontend to frontend_reader;
grant select on frontend.bookings, frontend.catalog to frontend_reader;
revoke all on schema public from frontend_reader;
