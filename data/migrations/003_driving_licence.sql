-- 003_driving_licence.sql: a test ride needs a valid two-wheeler driving licence (product owner, 2026-10-15).
-- The caller is asked before booking; the answer is stored, and a booking without a confirmed licence is
-- rejected by the database as well as by the agent (domain/booking_guard.py).

-- Rows booked before this migration were never asked: false, and the check skips them (NOT VALID).
alter table public.bookings add column licence_confirmed boolean not null default false;
alter table public.bookings alter column licence_confirmed drop default;
alter table public.bookings
  add constraint bookings_licence_confirmed check (licence_confirmed) not valid;

drop function public.reserve_slot(bigint, text, text, text, text, text);

create function public.reserve_slot(
  p_slot_id bigint, p_name text, p_phone text, p_reference text, p_room text, p_language text,
  p_licence_confirmed boolean
) returns bigint
language plpgsql
security invoker
set search_path = public
as $$
declare v_id bigint;
begin
  update public.slots
     set booked = booked + 1
   where id = p_slot_id and booked < capacity and starts_at > now();
  if not found then
    raise exception 'SLOT_UNAVAILABLE' using errcode = 'P0001';
  end if;

  insert into public.bookings
    (reference, slot_id, customer_name, customer_phone, room_name, language, licence_confirmed)
  values
    (p_reference, p_slot_id, p_name, p_phone, p_room, coalesce(p_language, 'hi-IN'), p_licence_confirmed)
  returning id into v_id;
  return v_id;
end $$;

-- Only the backend may call it, as in 002 (rule 9).
revoke execute on function public.reserve_slot(bigint, text, text, text, text, text, boolean)
  from public, anon, authenticated;
