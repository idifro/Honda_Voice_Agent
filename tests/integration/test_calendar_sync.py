"""Calendar mirror against the real database with a fake calendar (SPEC §11, Phase 4 exit: an outage leaves
the booking confirmed and FAILED until the retry fixes it). Same throwaway showroom as test_bookings.py."""

import asyncio

from honda_voice.db import bookings
from honda_voice.db.calendar_sync import CalendarSync, pending_booking_ids
from honda_voice.domain.calendar_event import event_id
from honda_voice.integrations.gcalendar import CalendarError

from . import test_bookings as tb

pytestmark = tb.pytestmark
pool = tb.pool  # fixture: test showroom, everything deleted afterwards


class FakeCalendar:
    def __init__(self, fail: CalendarError | None = None, delay: float = 0.0) -> None:
        self.events: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail = fail
        self.delay = delay

    async def create_event(self, event):
        self.calls.append(("create", event["id"]))
        await asyncio.sleep(self.delay)
        if self.fail:
            raise self.fail
        self.events[event["id"]] = event  # same id again: overwritten, like Google's 409 -> done
        return event["id"]

    async def delete_event(self, eid):
        self.calls.append(("delete", eid))
        if self.fail:
            raise self.fail
        self.events.pop(eid, None)


async def book(pool, hour: int, minute: int = 0) -> bookings.ReserveResult:
    slot = await tb.make_slot(pool, tb.tomorrow_at(hour, minute))
    async with pool.acquire() as c:
        res = await bookings.reserve(
            c, slot, "Ravi", "9000012345", "it-room", "hi-IN", licence_confirmed=True
        )
    assert res.ok
    return res


async def row(pool, booking_id: int):
    async with pool.acquire() as c:
        return await c.fetchrow(
            "select status, calendar_event_id, calendar_sync_status, calendar_sync_error "
            "from public.bookings where id = $1",
            booking_id,
        )


async def test_booking_synced(pool):
    res = await book(pool, 10)
    cal = FakeCalendar()
    sync = CalendarSync(pool, cal, retry_delays=(0, 0, 0))
    sync.schedule(res.booking_id)  # background, as the agent does
    await sync.drain()
    r = await row(pool, res.booking_id)
    assert (r["calendar_sync_status"], r["calendar_event_id"]) == ("SYNCED", event_id(res.reference))
    event = cal.events[event_id(res.reference)]
    assert event["summary"] == "Test ride: Honda Shine 100 (Ravi)"
    assert "xxxxxxx345" in event["description"]


async def test_outage_leaves_booking_confirmed_then_retry_fixes_it(pool):
    res = await book(pool, 10, 30)
    down = FakeCalendar(fail=CalendarError(503, "backend error"))
    assert await CalendarSync(pool, down, retry_delays=(0, 0, 0)).sync(res.booking_id) == "FAILED"
    assert len(down.calls) == 4  # first try + 3 retries
    r = await row(pool, res.booking_id)
    assert r["status"] == "CONFIRMED" and r["calendar_sync_status"] == "FAILED"
    assert "503" in r["calendar_sync_error"]
    async with pool.acquire() as c:
        assert res.booking_id in await pending_booking_ids(c)
    up = FakeCalendar()  # what scripts/retry_calendar_sync.py does, once the calendar is back
    assert await CalendarSync(pool, up).sync(res.booking_id) == "SYNCED"
    r = await row(pool, res.booking_id)
    assert r["calendar_sync_error"] is None and event_id(res.reference) in up.events


async def test_permanent_error_is_not_retried(pool):
    res = await book(pool, 11)
    cal = FakeCalendar(fail=CalendarError(403, "You need to have writer access to this calendar."))
    assert await CalendarSync(pool, cal, retry_delays=(0, 0, 0)).sync(res.booking_id) == "FAILED"
    assert len(cal.calls) == 1


async def test_cancel_deletes_the_event(pool):
    res = await book(pool, 11, 30)
    cal = FakeCalendar()
    sync = CalendarSync(pool, cal)
    await sync.sync(res.booking_id)
    async with pool.acquire() as c:
        assert await bookings.cancel(c, res.reference, "9000012345")
        assert await bookings.mark_calendar_pending(c, res.reference) == res.booking_id
    assert await sync.sync(res.booking_id) == "SYNCED"
    r = await row(pool, res.booking_id)
    assert r["status"] == "CANCELLED" and r["calendar_event_id"] is None and not cal.events


async def test_cancel_while_the_event_is_being_created(pool):
    res = await book(pool, 12)
    cal = FakeCalendar(delay=0.5)
    sync = CalendarSync(pool, cal)
    sync.schedule(res.booking_id)
    await asyncio.sleep(0.1)  # the insert is in flight
    async with pool.acquire() as c:
        await bookings.cancel(c, res.reference, "9000012345")
        await bookings.mark_calendar_pending(c, res.reference)
    sync.schedule(res.booking_id)  # the cancel's sync waits for the create, then deletes
    await sync.drain()
    r = await row(pool, res.booking_id)
    assert not cal.events and r["calendar_sync_status"] == "SYNCED" and r["calendar_event_id"] is None
    assert [c[0] for c in cal.calls] == ["create", "delete"]
