"""Background mirror of bookings to Google Calendar (SPEC §11). The database is the source of truth (rule 8):
sync runs after the booking committed, never blocks or fails it, and a failure only marks the row FAILED for
scripts/retry_calendar_sync.py. Nothing about the calendar is ever said to the caller.

Row states: calendar_sync_status PENDING (to do) -> SYNCED (calendar matches the booking) or FAILED (error
stored). A confirmed booking needs an event; a cancelled one needs none, so cancelling marks the row PENDING
again and the next sync deletes the event.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import asyncpg

from honda_voice.domain.calendar_event import build_event, event_id
from honda_voice.integrations.gcalendar import CalendarError

logger = logging.getLogger("honda_voice.calendar")
RETRY_DELAYS = (1.0, 2.0, 4.0)  # 3 retries with backoff after the first try (SPEC §11)

_ROW_SQL = """
select b.id, b.reference, b.status, b.customer_name, b.customer_phone, b.language, b.calendar_event_id,
       s.starts_at, bk.name as model, sr.name as showroom, sr.city, sr.slot_minutes
from public.bookings b
join public.slots s on s.id = b.slot_id
join public.bikes bk on bk.id = s.bike_id
join public.showrooms sr on sr.id = s.showroom_id
where b.id = $1
"""
# Conditional on the booking's status at write time: if it was cancelled while the event was being created
# (or the reverse), the row stays PENDING so the next sync fixes the calendar.
_CREATED_SQL = """
update public.bookings
   set calendar_event_id = $2, calendar_sync_error = null,
       calendar_sync_status = case when status = 'CONFIRMED' then 'SYNCED' else 'PENDING' end
 where id = $1
"""
_DELETED_SQL = """
update public.bookings
   set calendar_event_id = null, calendar_sync_error = null,
       calendar_sync_status = case when status = 'CANCELLED' then 'SYNCED' else 'PENDING' end
 where id = $1
"""
_FAILED_SQL = """
update public.bookings set calendar_sync_status = 'FAILED', calendar_sync_error = $2 where id = $1
"""


class Calendar(Protocol):
    async def create_event(self, event: dict[str, Any]) -> str: ...
    async def delete_event(self, event_id: str) -> None: ...


class CalendarSync:
    def __init__(
        self, pool: asyncpg.Pool, client: Calendar, retry_delays: tuple[float, ...] = RETRY_DELAYS
    ) -> None:
        self.pool = pool
        self.client = client
        self.retry_delays = retry_delays
        self._tasks: set[asyncio.Task] = set()
        self._locks: dict[int, asyncio.Lock] = {}  # one sync at a time per booking

    def schedule(self, booking_id: int) -> None:
        """Sync in the background; returns at once (the caller's turn never waits for Google)."""
        task = asyncio.create_task(self._run(booking_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, booking_id: int) -> None:
        try:
            await self.sync(booking_id)
        except Exception:  # e.g. the database itself is down: the retry script picks the row up later
            logger.exception("calendar sync crashed for booking %s", booking_id)

    async def drain(self, timeout_s: float = 10.0) -> None:
        """Let in-flight syncs finish (call end); anything unfinished stays PENDING for the retry script."""
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=timeout_s)

    async def sync(self, booking_id: int) -> str:
        """Make the calendar match the booking. Returns the resulting calendar_sync_status (or 'MISSING')."""
        lock = self._locks.setdefault(booking_id, asyncio.Lock())
        async with lock:
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(_ROW_SQL, booking_id)
            if row is None:
                return "MISSING"
            if row["status"] == "CONFIRMED":
                event = build_event(
                    reference=row["reference"], model=row["model"], name=row["customer_name"],
                    phone=row["customer_phone"], language=row["language"], starts_at=row["starts_at"],
                    slot_minutes=row["slot_minutes"], showroom=f"{row['showroom']}, {row['city']}",
                )  # fmt: skip
                op, sql, what = (lambda: self.client.create_event(event)), _CREATED_SQL, "create"
            else:
                # Delete by the stored id, or by the id derived from the reference in case the insert
                # reached Google but its result never reached us.
                eid = row["calendar_event_id"] or event_id(row["reference"])
                op, sql, what = (lambda: self._delete(eid)), _DELETED_SQL, "delete"
            try:
                result = await self._with_retries(op)
            except CalendarError as e:
                logger.warning("calendar %s failed for %s: %s", what, row["reference"], e)
                async with self.pool.acquire() as conn:
                    await conn.execute(_FAILED_SQL, booking_id, str(e)[:500])
                return "FAILED"
            async with self.pool.acquire() as conn:
                if what == "create":
                    await conn.execute(sql, booking_id, result)
                else:
                    await conn.execute(sql, booking_id)
                status = await conn.fetchval(
                    "select calendar_sync_status from public.bookings where id = $1", booking_id
                )
            logger.info("calendar %s ok for %s -> %s", what, row["reference"], status)
            return status

    async def _delete(self, eid: str) -> None:
        await self.client.delete_event(eid)

    async def _with_retries(self, op: Callable[[], Awaitable[Any]]) -> Any:
        for attempt, delay in enumerate((*self.retry_delays, None)):
            try:
                return await op()
            except CalendarError as e:
                if not e.retryable or delay is None:
                    raise
                logger.info(
                    "calendar call failed (attempt %d), retrying in %.0f s: %s", attempt + 1, delay, e
                )
                await asyncio.sleep(delay)


async def pending_booking_ids(conn: asyncpg.Connection, limit: int = 500) -> list[int]:
    """Rows whose calendar copy is out of date or failed, oldest first (for the retry script)."""
    rows = await conn.fetch(
        "select id from public.bookings where calendar_sync_status in ('PENDING', 'FAILED') "
        "order by created_at limit $1",
        limit,
    )
    return [r["id"] for r in rows]
