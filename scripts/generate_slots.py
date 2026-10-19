"""Create test-ride slots for the next N days (SPEC §5). Safe to re-run: existing slots and bookings are kept.

Run: `uv run python scripts/generate_slots.py --days 14 [--capacity 2] [--showroom demo-showroom]`
"""

import argparse
import asyncio

from honda_voice.config import get_settings
from honda_voice.db.bookings import DEFAULT_CAPACITY, generate_slots
from honda_voice.db.pool import connect
from honda_voice.domain.datetime_resolve import now_ist


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--capacity", type=int, default=DEFAULT_CAPACITY, help="demo bikes per model per slot")
    ap.add_argument("--showroom", default=get_settings().showroom_id)
    args = ap.parse_args()
    conn = await connect()
    try:
        async with conn.transaction():
            created = await generate_slots(conn, args.showroom, args.days, now_ist(), args.capacity)
        total = await conn.fetchval(
            "select count(*) from public.slots where showroom_id = $1 and starts_at > now()", args.showroom
        )
    finally:
        await conn.close()
    print(f"created {created} slots; {total} future slots for {args.showroom}")


if __name__ == "__main__":
    asyncio.run(main())
