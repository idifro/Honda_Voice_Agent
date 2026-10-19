"""Phase 4 exit check against the real Google Calendar (SPEC §11, §14):
1. a booking appears in the shared calendar within 10 s;
2. a calendar outage leaves the booking CONFIRMED with status FAILED, until scripts/retry_calendar_sync.py
   fixes it;
3. cancelling removes the event.
Bookings go to the demo showroom (room 'eval-calendar') and are cancelled, un-mirrored and deleted at the end.

Run: `uv run python -m evals.calendar_e2e` (needs CALENDAR_SYNC=on and the Google settings in .env).
"""

import asyncio
import sys
import time

from dotenv import load_dotenv

load_dotenv(".env")

from honda_voice.config import get_settings  # noqa: E402
from honda_voice.db import bookings  # noqa: E402
from honda_voice.db.calendar_sync import CalendarSync  # noqa: E402
from honda_voice.db.pool import close_pool, get_pool  # noqa: E402
from honda_voice.domain.calendar_event import event_id  # noqa: E402
from honda_voice.domain.datetime_resolve import now_ist  # noqa: E402
from honda_voice.integrations.gcalendar import CalendarClient  # noqa: E402

ROOM = "eval-calendar"


async def reserve(conn, s, time_24h: str, phone: str) -> bookings.ReserveResult:
    search = await bookings.find_open_slots(conn, s.showroom_id, "shine-100", "tomorrow", time_24h, now_ist())
    res = await bookings.reserve(
        conn, search.offers[0].slot_id, "Calendar Check", phone, ROOM, "en-IN", licence_confirmed=True
    )
    assert res.ok, res
    return res


async def status(conn, booking_id: int) -> tuple[str, str]:
    r = await conn.fetchrow(
        "select status, calendar_sync_status from public.bookings where id = $1", booking_id
    )
    return r["status"], r["calendar_sync_status"]


async def main() -> int:
    s = get_settings()
    if s.calendar_sync != "on":
        print("CALENDAR_SYNC is off")
        return 1
    pool = await get_pool()
    client = CalendarClient(s.google_service_account_json, s.google_calendar_id)
    sync = CalendarSync(pool, client)
    ok = True
    made: list[bookings.ReserveResult] = []
    try:
        # 1. Booking -> calendar within 10 s (measured from the booking commit to the event being readable).
        async with pool.acquire() as conn:
            res = await reserve(conn, s, "18:30", "9000020001")
        made.append(res)
        t0 = time.perf_counter()
        sync.schedule(res.booking_id)
        seen = None
        while time.perf_counter() - t0 < 10 and seen is None:
            seen = await client.get_event(event_id(res.reference))
            if seen is None:
                await asyncio.sleep(0.25)
        elapsed = time.perf_counter() - t0
        await sync.drain()
        print(
            f"1. booking {res.reference} in calendar after {elapsed:.1f} s: {'ok' if seen else 'NOT FOUND'}"
        )
        ok &= seen is not None and elapsed < 10

        # 2. Outage: a client for a calendar that doesn't exist -> FAILED, booking still CONFIRMED.
        async with pool.acquire() as conn:
            res2 = await reserve(conn, s, "18:00", "9000020002")
        made.append(res2)
        broken = CalendarClient(s.google_service_account_json, "does-not-exist@group.calendar.google.com")
        result = await CalendarSync(pool, broken, retry_delays=(0.1, 0.1, 0.1)).sync(res2.booking_id)
        await broken.close()
        async with pool.acquire() as conn:
            st = await status(conn, res2.booking_id)
        print(f"2. outage: sync -> {result}; booking {res2.reference} is {st[0]}, calendar {st[1]}")
        ok &= result == "FAILED" and st == ("CONFIRMED", "FAILED")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "scripts/retry_calendar_sync.py", stdout=asyncio.subprocess.PIPE
        )
        retry_out = (await proc.communicate())[0].decode().strip()
        async with pool.acquire() as conn:
            st = await status(conn, res2.booking_id)
        found = await client.get_event(event_id(res2.reference)) is not None
        print(f"   retry script: {retry_out} -> calendar {st[1]}, event {'found' if found else 'MISSING'}")
        ok &= st == ("CONFIRMED", "SYNCED") and found

        # 3. Cancel -> event removed.
        async with pool.acquire() as conn:
            await bookings.cancel(conn, res.reference, "9000020001")
            await bookings.mark_calendar_pending(conn, res.reference)
        print(f"3. cancel: sync -> {await sync.sync(res.booking_id)}; ", end="")
        gone = await client.get_event(event_id(res.reference)) is None
        print(f"event {'removed' if gone else 'STILL THERE'}")
        ok &= gone
    finally:
        async with pool.acquire() as conn:  # cleanup: cancel, remove events, delete the rows
            for r in made:
                if await bookings.cancel(conn, r.reference, ("9000020001", "9000020002")[made.index(r)]):
                    await bookings.mark_calendar_pending(conn, r.reference)
        for r in made:
            await sync.sync(r.booking_id)
        async with pool.acquire() as conn:
            await conn.execute("delete from public.bookings where room_name = $1", ROOM)
        await client.close()
        await close_pool()
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
