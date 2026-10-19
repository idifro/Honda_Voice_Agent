-- 002_booking_functions.sql: atomic booking and cancellation (SPEC §5, §7).
-- reserve_slot: the capacity check and the increment are one UPDATE ... WHERE booked < capacity; concurrent callers
-- lock the row and re-check the condition, so a slot can never be over-booked. The insert runs in the same
-- transaction, so a failed insert (e.g. the same phone booking the same slot twice) undoes the increment.

-- Two demo bikes per model per slot (product owner, 2026-10-15).
alter table public.slots alter column capacity set default 2;

create or replace function public.reserve_slot(
  p_slot_id bigint, p_name text, p_phone text, p_reference text, p_room text, p_language text
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

  insert into public.bookings (reference, slot_id, customer_name, customer_phone, room_name, language)
  values (p_reference, p_slot_id, p_name, p_phone, p_room, coalesce(p_language, 'hi-IN'))
  returning id into v_id;
  return v_id;
end $$;

create or replace function public.cancel_booking(p_reference text, p_phone text)
returns boolean
language plpgsql
security invoker
set search_path = public
as $$
declare v_slot bigint;
begin
  update public.bookings set status = 'CANCELLED'
   where reference = p_reference and customer_phone = p_phone and status = 'CONFIRMED'
   returning slot_id into v_slot;
  if v_slot is null then
    return false;
  end if;
  update public.slots set booked = booked - 1 where id = v_slot and booked > 0;
  return true;
end $$;

-- Only the backend (postgres role via the pooler) may call these; not Supabase's API roles (rule 9).
revoke execute on function public.reserve_slot(bigint, text, text, text, text, text) from public, anon, authenticated;
revoke execute on function public.cancel_booking(text, text) from public, anon, authenticated;
