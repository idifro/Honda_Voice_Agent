"""Re-push bookings whose calendar copy is PENDING or FAILED (SPEC §11): creates events for confirmed
bookings, deletes them for cancelled ones. Safe to re-run.

Run: `uv run python scripts/retry_calendar_sync.py [--limit 500]`
"""

import argparse
import asyncio
import sys
from collections import Counter

from honda_voice.config import get_settings
from honda_voice.db.calendar_sync import CalendarSync, pending_booking_ids
from honda_voice.db.pool import close_pool, get_pool
from honda_voice.integrations.gcalendar import CalendarClient


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    args = ap.parse_args()
    s = get_settings()
    if s.calendar_sync != "on":
        print("CALENDAR_SYNC is off; nothing to do")
        return 1
    pool = await get_pool()
    client = CalendarClient(s.google_service_account_json, s.google_calendar_id)
    sync = CalendarSync(pool, client)
    try:
        async with pool.acquire() as conn:
            ids = await pending_booking_ids(conn, args.limit)
        results = Counter([await sync.sync(i) for i in ids])
    finally:
        await client.close()
        await close_pool()
    print(f"{len(ids)} bookings to sync: {dict(results) or 'none'}")
    return 0 if not results.get("FAILED") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
