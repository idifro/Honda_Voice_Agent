"""BookingAgent tools against the real database: offer, read back, book, slot taken, cancel (SPEC §7, §8).
Same throwaway showroom and cleanup as test_bookings.py."""

from datetime import timedelta

from honda_voice.agent.booking_agent import BookingAgent
from honda_voice.agent.call_state import CallState
from honda_voice.db import bookings
from honda_voice.domain.datetime_resolve import now_ist

from . import test_bookings as tb

pytestmark = tb.pytestmark
pool = tb.pool  # fixture: test showroom, everything deleted afterwards


def agent_for(catalog, pool, lang="en-IN") -> BookingAgent:
    st = CallState(
        catalog=catalog, lang=lang, supported=[lang], pool=pool, showroom_id=tb.SHOWROOM, room="it-room"
    )
    st.caller_turns = 1
    return BookingAgent(st)


async def read_back_and_book(agent: BookingAgent, time_24h: str, name: str, number: str) -> dict:
    agent.state.caller_turns += 1  # the caller answers the licence question
    assert (await agent.record_licence(True))["recorded"]
    assert (await agent.validate_phone(number))["valid"]
    assert (await agent.confirm_details("tomorrow", time_24h, name, number))["ok"]
    agent.state.caller_turns += 1  # caller says yes
    return await agent.book_test_ride("tomorrow", time_24h, name, number, True)


async def test_book_full_flow_then_cancel(pool, catalog):
    five = await tb.make_slot(pool, tb.tomorrow_at(17), capacity=1)
    await tb.make_slot(pool, tb.tomorrow_at(17, 30), capacity=1)
    agent = agent_for(catalog, pool, "hi-IN")
    found = await agent.check_slots("tomorrow", "17:00", "Shine 100")
    tomorrow = tb.tomorrow_at(17).date().isoformat()
    assert found["exact_time_available"]
    assert {"spoken": "kal shaam 5 baje", "day_ref": tomorrow, "time_24h": "17:00"} in found["slots"]

    booked = await read_back_and_book(agent, "17:00", "Ravi", "9000012345")
    assert booked["booked"] and "H N D," in booked["say"] and "kal shaam 5 baje" in booked["say"]
    ref = agent.state.booking.references[0]
    async with pool.acquire() as c:
        row = await c.fetchrow("select * from public.bookings where reference = $1", ref)
    assert (row["customer_name"], row["customer_phone"], row["language"], row["room_name"]) == (
        "Ravi", "9000012345", "hi-IN", "it-room",
    )  # fmt: skip
    assert row["licence_confirmed"] is True

    other = agent_for(catalog, pool)  # a second caller now finds 5 pm full
    again = await other.check_slots("tomorrow", "17:00", "Shine 100")
    assert again["reason"] == "requested_time_full" and "17:00" not in [s["time_24h"] for s in again["slots"]]

    spoken_ref = " ".join(ref.replace("-", "")).lower()  # "h n d 7 k 3 q 9", as STT might write it
    assert (await agent.cancel_test_ride(spoken_ref, "90000 12345"))["cancelled"] is True
    assert await tb.booked(pool, five) == 0


async def test_slot_taken_between_read_back_and_yes(pool, catalog):
    half = await tb.make_slot(pool, tb.tomorrow_at(16, 30), capacity=1)
    await tb.make_slot(pool, tb.tomorrow_at(16), capacity=1)  # the alternative
    agent = agent_for(catalog, pool)
    await agent.check_slots("tomorrow", "16:30", "Shine 100")
    async with pool.acquire() as c:  # someone else books it first
        assert (
            await bookings.reserve(c, half, "Other", "9000099999", None, "en-IN", licence_confirmed=True)
        ).ok
    out = await read_back_and_book(agent, "16:30", "Ravi", "9000012345")
    assert out["error"] == "SLOT_TAKEN" and "16:00" in [s["time_24h"] for s in out["alternatives"]]
    assert agent.state.booking.pending is None  # must read back the new slot before booking


async def test_past_day_and_bad_input(pool, catalog):
    agent = agent_for(catalog, pool)
    yesterday = (now_ist() - timedelta(days=1)).date().isoformat()
    out = await agent.check_slots(yesterday, None, "Shine 100")
    assert out["error"] == "BAD_DAY_OR_TIME"
    assert (await agent.check_slots("tomorrow", "25:00", "Shine 100"))["error"] == "BAD_DAY_OR_TIME"


async def test_agent_mirrors_booking_and_cancel_to_calendar(pool, catalog):
    from honda_voice.db.calendar_sync import CalendarSync
    from honda_voice.domain.calendar_event import event_id

    from .test_calendar_sync import FakeCalendar

    await tb.make_slot(pool, tb.tomorrow_at(15), capacity=1)
    cal = FakeCalendar()
    agent = agent_for(catalog, pool)
    agent.state.calendar = CalendarSync(pool, cal)
    await agent.check_slots("tomorrow", "15:00", "Shine 100")
    assert (await read_back_and_book(agent, "15:00", "Ravi", "9000012345"))["booked"]
    await agent.state.calendar.drain()
    ref = agent.state.booking.references[0]
    assert event_id(ref) in cal.events  # mirrored in the background after the booking committed
    assert (await agent.cancel_test_ride(ref, "9000012345"))["cancelled"]
    await agent.state.calendar.drain()
    assert not cal.events
