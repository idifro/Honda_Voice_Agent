"""Slots, bookings, cancellations and leads (SPEC §5, §7, §10). Parameterized SQL only (rule 2); booking and
cancellation go through the atomic Postgres functions from migration 002. The database is the source of truth
for bookings (rule 8)."""

import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import asyncpg

from honda_voice.domain.datetime_resolve import BUSINESS_TZ, ShowroomHours, candidate_slots, slot_grid

DEFAULT_CAPACITY = 2  # demo bikes per model per slot (product owner, 2026-10-15)
REFERENCE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L: easier to read back and hear


# --- Pure helpers ----------------------------------------------------------------------------------------


def new_reference() -> str:
    """'HND-7K3Q9': five characters with no look-alikes (0/O, 1/I/L)."""
    return "HND-" + "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(5))


def speak_reference(reference: str) -> str:
    """'HND-7K3Q9' -> 'H N D, 7 K 3 Q 9': spelled with gaps so TTS reads it character by character."""
    prefix, _, code = reference.partition("-")
    return f"{' '.join(prefix)}, {' '.join(code)}"


def normalize_reference(raw: str) -> str | None:
    """What the caller (or STT) said -> 'HND-7K3Q9', or None: 'h n d 7 k 3 q 9', '7K3Q9', 'hnd-7k3q9'."""
    code = "".join(ch for ch in raw.upper() if ch.isalnum())
    code = code.removeprefix("HND")
    if len(code) != 5 or any(ch not in REFERENCE_ALPHABET for ch in code):
        return None
    return f"HND-{code}"


def planned_slots(
    showroom: ShowroomHours, bike_ids: list[str], first_day: date, days: int, now: datetime
) -> list[tuple[str, datetime]]:
    """(bike_id, starts_at) for every future grid time on open days in [first_day, first_day + days)."""
    out = []
    for offset in range(days):
        day = first_day + timedelta(days=offset)
        if day.isoweekday() in showroom.closed_isodow:
            continue
        for starts_at in slot_grid(day, showroom):
            if starts_at > now:
                out.extend((bike_id, starts_at) for bike_id in bike_ids)
    return out


# --- Queries ---------------------------------------------------------------------------------------------


async def load_showroom(conn: asyncpg.Connection, showroom_id: str) -> ShowroomHours:
    row = await conn.fetchrow(
        "select open_time, close_time, closed_isodow, slot_minutes from public.showrooms where id = $1",
        showroom_id,
    )
    if row is None:
        raise ValueError(f"unknown showroom {showroom_id!r}")
    return ShowroomHours(
        row["open_time"], row["close_time"], frozenset(row["closed_isodow"]), row["slot_minutes"]
    )


async def rideable_bike_ids(conn: asyncpg.Connection) -> list[str]:
    """Models that can be test-ridden: on sale (not pre-booking or upcoming) (SPEC §6)."""
    return [
        r["id"] for r in await conn.fetch("select id from public.bikes where status = 'on_sale' order by id")
    ]


async def generate_slots(
    conn: asyncpg.Connection, showroom_id: str, days: int, now: datetime, capacity: int = DEFAULT_CAPACITY
) -> int:
    """Create missing slots for the next `days` days. Existing slots (and their bookings) are left untouched.
    Returns the number of slots created."""
    showroom = await load_showroom(conn, showroom_id)
    planned = planned_slots(
        showroom, await rideable_bike_ids(conn), now.astimezone(BUSINESS_TZ).date(), days, now
    )
    before = await conn.fetchval("select count(*) from public.slots where showroom_id = $1", showroom_id)
    await conn.executemany(
        "insert into public.slots (showroom_id, bike_id, starts_at, capacity) values ($1, $2, $3, $4) "
        "on conflict (showroom_id, bike_id, starts_at) do nothing",
        [(showroom_id, bike_id, starts_at, capacity) for bike_id, starts_at in planned],
    )
    after = await conn.fetchval("select count(*) from public.slots where showroom_id = $1", showroom_id)
    return after - before


@dataclass(frozen=True)
class SlotOffer:
    slot_id: int
    starts_at: datetime
    free: int


@dataclass(frozen=True)
class SlotSearch:
    offers: list[SlotOffer]  # up to `count`, nearest to the requested time first, then by time
    day: date
    exact: bool  # the requested time itself has a free slot
    reason: str | None  # from candidate_slots, or 'fully_booked' when grid times exist but none are free


async def find_open_slots(
    conn: asyncpg.Connection,
    showroom_id: str,
    bike_id: str,
    day_ref: str,
    time_24h: str | None,
    now: datetime,
    count: int = 3,
) -> SlotSearch:
    """Grid times from the caller's day/time (code, not the LLM: rule 5), filtered by real free capacity."""
    showroom = await load_showroom(conn, showroom_id)
    grid = candidate_slots(day_ref, time_24h, now, showroom, count=10_000)  # every grid time, closest first
    if not grid.slots:
        return SlotSearch([], grid.day, False, grid.reason)
    rows = await conn.fetch(
        "select id, starts_at, capacity - booked as free from public.slots "
        "where showroom_id = $1 and bike_id = $2 and starts_at = any($3::timestamptz[]) "
        "and booked < capacity",
        showroom_id, bike_id, grid.slots,
    )  # fmt: skip
    free = {r["starts_at"]: SlotOffer(r["id"], r["starts_at"], r["free"]) for r in rows}
    if grid.requested is not None:
        order = sorted(free, key=lambda t: (abs(t - grid.requested), t))
    else:
        order = sorted(free)
    offers = sorted((free[t] for t in order[:count]), key=lambda o: o.starts_at)
    exact = grid.requested is not None and grid.requested in free
    if not offers:
        reason = "fully_booked"
    elif exact:
        reason = None
    elif grid.exact:  # a valid time, but both demo bikes are taken
        reason = "requested_time_full"
    else:  # off the grid, outside hours, too soon, or no time given
        reason = grid.reason
    return SlotSearch(offers, grid.day, exact, reason)


@dataclass(frozen=True)
class ReserveResult:
    ok: bool
    booking_id: int | None = None
    reference: str | None = None
    error: str | None = None  # 'SLOT_UNAVAILABLE', 'DUPLICATE_BOOKING' or 'LICENCE_REQUIRED'


async def reserve(
    conn: asyncpg.Connection,
    slot_id: int,
    name: str,
    phone: str,
    room: str | None,
    language: str,
    *,
    licence_confirmed: bool,
) -> ReserveResult:
    """Book a slot through reserve_slot(). Phone must already be normalized (domain/phone.py). The database
    rejects a booking without a confirmed driving licence (migration 003)."""
    if not licence_confirmed:
        return ReserveResult(False, error="LICENCE_REQUIRED")
    for _ in range(3):  # a reference collision (1 in ~28 million per pair) just retries
        reference = new_reference()
        try:
            booking_id = await conn.fetchval(
                "select public.reserve_slot($1, $2, $3, $4, $5, $6, $7)",
                slot_id, name.strip(), phone, reference, room, language, licence_confirmed,
            )  # fmt: skip
            return ReserveResult(True, booking_id, reference)
        except asyncpg.RaiseError as e:
            if "SLOT_UNAVAILABLE" in str(e):
                return ReserveResult(False, error="SLOT_UNAVAILABLE")
            raise
        except asyncpg.UniqueViolationError as e:
            if e.constraint_name == "one_active_booking_per_phone_slot":
                return ReserveResult(False, error="DUPLICATE_BOOKING")
            if e.constraint_name != "bookings_reference_key":
                raise
    raise RuntimeError("could not generate a unique booking reference")


async def cancel(conn: asyncpg.Connection, reference: str, phone: str) -> bool:
    """Cancel through cancel_booking(): needs the reference and the phone that booked it."""
    return await conn.fetchval("select public.cancel_booking($1, $2)", reference.strip().upper(), phone)


async def mark_calendar_pending(conn: asyncpg.Connection, reference: str) -> int | None:
    """After a cancellation: the calendar copy is now out of date. Returns the booking id to sync."""
    return await conn.fetchval(
        "update public.bookings set calendar_sync_status = 'PENDING' "
        "where reference = $1 and status = 'CANCELLED' returning id",
        reference.strip().upper(),
    )


CONSENT_TEXT_VERSION = "callback-v1-2026-10-20"  # prompts.LEAD_CONSENT; bump when its wording changes


async def upsert_customer(
    conn: asyncpg.Connection, phone: str, name: str | None, language: str, *, callback_consent: bool
) -> str:
    """The customer list (SPEC §20.5): one row per number. Consent is only ever added here, never removed by a
    later call without it; a name already on file is kept unless a new one is given."""
    return str(
        await conn.fetchval(
            "insert into public.customers (phone, name, preferred_language, callback_consent, consent_at, "
            "consent_text_version) "
            "values ($1, $2, $3, $4, case when $4 then now() end, case when $4 then $5 end) "
            "on conflict (phone) do update set name = coalesce(excluded.name, customers.name), "
            "preferred_language = excluded.preferred_language, last_contact_at = now(), "
            "callback_consent = customers.callback_consent or excluded.callback_consent, "
            "consent_at = case when excluded.callback_consent and not customers.callback_consent then now() "
            "else customers.consent_at end, "
            "consent_text_version = case when excluded.callback_consent and not customers.callback_consent "
            "then excluded.consent_text_version else customers.consent_text_version end "
            "returning id",
            phone,
            name,
            language,
            callback_consent,
            CONSENT_TEXT_VERSION,
        )  # fmt: skip
    )


async def capture_lead(
    conn: asyncpg.Connection,
    reason: str,
    language: str,
    name: str | None = None,
    phone: str | None = None,
    interest: str | None = None,
    *,
    interest_bike_id: str | None = None,
    call_id: int | None = None,
    customer_id: str | None = None,
) -> int:
    return await conn.fetchval(
        "insert into public.leads (customer_name, customer_phone, interest, reason, language, "
        "interest_bike_id, call_id, customer_id) values ($1, $2, $3, $4, $5, $6, $7, $8::uuid) returning id",
        name, phone, interest, reason, language, interest_bike_id, call_id, customer_id,
    )  # fmt: skip
