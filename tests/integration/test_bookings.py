"""Booking functions against the real database (SPEC §5, Phase 3 exit criterion). Uses its own showroom and
slots and deletes everything it created, so the demo showroom's data is never touched.

Run: `uv run pytest -q -m integration`.
"""

import asyncio
from datetime import datetime, timedelta

import asyncpg
import pytest

from honda_voice.db import bookings
from honda_voice.db.pool import create_pool
from honda_voice.domain.datetime_resolve import BUSINESS_TZ, now_ist

pytestmark = pytest.mark.integration
SHOWROOM = "it-test-showroom"
BIKE = "shine-100"


@pytest.fixture
async def pool():
    p = await create_pool(min_size=2, max_size=14)
    async with p.acquire() as c:
        await c.execute(
            "insert into public.showrooms (id, name, city) values ($1, 'IT test', 'Delhi') "
            "on conflict do nothing",
            SHOWROOM,
        )
    try:
        yield p
    finally:
        async with p.acquire() as c:
            await c.execute(
                "delete from public.bookings "
                "where slot_id in (select id from public.slots where showroom_id = $1)",
                SHOWROOM,
            )
            await c.execute("delete from public.slots where showroom_id = $1", SHOWROOM)
            await c.execute("delete from public.showrooms where id = $1", SHOWROOM)
            await c.execute("delete from public.leads where reason = 'it-test'")
        await p.close()


async def make_slot(pool, starts_at: datetime, capacity: int = 2) -> int:
    async with pool.acquire() as c:
        return await c.fetchval(
            "insert into public.slots (showroom_id, bike_id, starts_at, capacity) values ($1, $2, $3, $4) "
            "returning id",
            SHOWROOM, BIKE, starts_at, capacity,
        )  # fmt: skip


def tomorrow_at(hour: int, minute: int = 0) -> datetime:
    d = (now_ist() + timedelta(days=1)).date()
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=BUSINESS_TZ)


async def booked(pool, slot_id: int) -> int:
    async with pool.acquire() as c:
        return await c.fetchval("select booked from public.slots where id = $1", slot_id)


async def test_20_simultaneous_reservations_on_capacity_1_give_exactly_1(pool):
    """Phase 3 exit criterion. The Supabase session pooler caps clients at 15, so 14 connections are opened
    first and all 20 calls fire at once: 14 truly simultaneous, 6 queued behind them."""
    slot = await make_slot(pool, tomorrow_at(11), capacity=1)
    conns = [await pool.acquire() for _ in range(14)]
    go = asyncio.Event()

    async def attempt(i: int):
        c = conns[i % len(conns)]
        await go.wait()
        async with locks[i % len(conns)]:  # one statement at a time per connection
            return await bookings.reserve(
                c, slot, f"Caller {i}", f"98765{i:05d}", "it-room", "en-IN", licence_confirmed=True
            )

    locks = [asyncio.Lock() for _ in conns]
    tasks = [asyncio.create_task(attempt(i)) for i in range(20)]
    await asyncio.sleep(0)
    go.set()
    results = await asyncio.gather(*tasks)
    for c in conns:
        await pool.release(c)
    assert sum(r.ok for r in results) == 1
    assert sum(r.error == "SLOT_UNAVAILABLE" for r in results) == 19
    assert await booked(pool, slot) == 1


@pytest.mark.parametrize(("first_commits", "second_ok"), [(True, False), (False, True)])
async def test_race_second_caller_waits_for_the_first(pool, first_commits, second_ok):
    """Deterministic version of the race: B blocks on A's row lock, then sees A's outcome."""
    slot = await make_slot(pool, tomorrow_at(11, 30), capacity=1)
    async with pool.acquire() as a, pool.acquire() as b:
        tx = a.transaction()
        await tx.start()
        held = await bookings.reserve(a, slot, "A", "9000000001", None, "en-IN", licence_confirmed=True)
        assert held.ok
        second = asyncio.create_task(
            bookings.reserve(b, slot, "B", "9000000002", None, "en-IN", licence_confirmed=True)
        )
        await asyncio.sleep(0.5)
        assert not second.done()  # B is waiting on A's lock, not reading a stale count
        await (tx.commit() if first_commits else tx.rollback())
        result = await asyncio.wait_for(second, timeout=10)
    assert result.ok is second_ok
    assert await booked(pool, slot) == 1


async def test_capacity_2(pool):
    slot = await make_slot(pool, tomorrow_at(10, 30), capacity=2)
    async with pool.acquire() as c:
        r = [
            await bookings.reserve(c, slot, "A", f"900000000{i}", None, "hi-IN", licence_confirmed=True)
            for i in range(3)
        ]
    assert [x.ok for x in r] == [True, True, False] and r[2].error == "SLOT_UNAVAILABLE"
    assert all(x.reference and x.reference.startswith("HND-") for x in r[:2])


async def test_past_slot_refused(pool):
    slot = await make_slot(pool, now_ist() - timedelta(hours=1))
    async with pool.acquire() as c:
        r = await bookings.reserve(c, slot, "Ravi", "9876543210", None, "en-IN", licence_confirmed=True)
    assert r.error == "SLOT_UNAVAILABLE" and await booked(pool, slot) == 0


async def test_same_phone_same_slot_twice(pool):
    slot = await make_slot(pool, tomorrow_at(12))
    async with pool.acquire() as c:
        first = await bookings.reserve(c, slot, "Ravi", "9876543210", None, "ta-IN", licence_confirmed=True)
        second = await bookings.reserve(c, slot, "Ravi", "9876543210", None, "ta-IN", licence_confirmed=True)
    assert first.ok and second.error == "DUPLICATE_BOOKING"
    assert await booked(pool, slot) == 1  # the failed insert undid its increment


async def test_cancel(pool):
    slot = await make_slot(pool, tomorrow_at(12, 30))
    async with pool.acquire() as c:
        r = await bookings.reserve(c, slot, "Ravi", "9876543210", None, "en-IN", licence_confirmed=True)
        assert await bookings.cancel(c, r.reference, "9123456789") is False  # wrong phone: nothing happens
        assert await booked(pool, slot) == 1
        assert await bookings.cancel(c, r.reference.lower(), "9876543210") is True  # case-insensitive ref
        assert await booked(pool, slot) == 0
        assert await bookings.cancel(c, r.reference, "9876543210") is False  # already cancelled
        status = await c.fetchval("select status from public.bookings where reference = $1", r.reference)
    assert status == "CANCELLED"


async def test_find_open_slots_respects_capacity(pool):
    full, free = tomorrow_at(17), tomorrow_at(17, 30)
    full_id = await make_slot(pool, full, capacity=1)
    await make_slot(pool, free)
    await make_slot(pool, tomorrow_at(16, 30))
    async with pool.acquire() as c:
        await bookings.reserve(c, full_id, "A", "9000000001", None, "en-IN", licence_confirmed=True)
        search = await bookings.find_open_slots(c, SHOWROOM, BIKE, "tomorrow", "17:00", now_ist())
    assert not search.exact and search.reason == "requested_time_full"
    assert full not in [o.starts_at for o in search.offers]
    assert free in [o.starts_at for o in search.offers]


async def test_generate_slots_idempotent(pool):
    async with pool.acquire() as c:
        created = await bookings.generate_slots(c, SHOWROOM, days=2, now=now_ist())
        again = await bookings.generate_slots(c, SHOWROOM, days=2, now=now_ist())
        rideable = len(await bookings.rideable_bike_ids(c))
        capacities = {r["capacity"] for r in await c.fetch(
            "select distinct capacity from public.slots where showroom_id = $1", SHOWROOM
        )}  # fmt: skip
    assert created > 0 and created % rideable == 0 and again == 0
    assert capacities == {2}


async def test_capture_lead(pool):
    async with pool.acquire() as c:
        lead_id = await bookings.capture_lead(
            c, "it-test", "kn-IN", name="Ravi", phone="9876543210", interest="qc1"
        )
        row = await c.fetchrow("select * from public.leads where id = $1", lead_id)
    assert row["language"] == "kn-IN" and row["interest"] == "qc1"


async def test_licence_required(pool):
    slot = await make_slot(pool, tomorrow_at(13))
    async with pool.acquire() as c:
        r = await bookings.reserve(c, slot, "Ravi", "9876543210", None, "en-IN", licence_confirmed=False)
        assert r.error == "LICENCE_REQUIRED" and await booked(pool, slot) == 0
        with pytest.raises(asyncpg.CheckViolationError):  # the database refuses it too (migration 003)
            await c.fetchval(
                "select public.reserve_slot($1, 'Ravi', '9876543210', 'HND-ZZZZZ', null, 'en-IN', false)",
                slot,
            )
    assert await booked(pool, slot) == 0  # the failed insert undid the increment
