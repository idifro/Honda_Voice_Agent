"""Check the Calendar setup (SPEC §11): create a test event on GOOGLE_CALENDAR_ID, read it back, delete it.

Run: `uv run python scripts/check_calendar.py`
"""

import asyncio
import sys
from datetime import timedelta

from honda_voice.config import get_settings
from honda_voice.domain.datetime_resolve import now_ist
from honda_voice.integrations.gcalendar import CalendarClient, CalendarError


async def main() -> int:
    s = get_settings()
    client = CalendarClient(s.google_service_account_json, s.google_calendar_id)
    start = (now_ist() + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    event = {
        "summary": "Honda voice agent: setup check (auto-deleted)",
        "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Kolkata"},
        "end": {"dateTime": (start + timedelta(minutes=30)).isoformat(), "timeZone": "Asia/Kolkata"},
    }
    try:
        event_id = await client.create_event(event)
        print(f"created test event at {start:%Y-%m-%d %H:%M} IST")
        found = await client.get_event(event_id)
        print("read back:", "ok" if found and found.get("summary") == event["summary"] else "MISSING")
        await asyncio.sleep(float(sys.argv[1]) if len(sys.argv) > 1 else 0)  # optional: time to look at it
        await client.delete_event(event_id)
        print("deleted:", "ok" if await client.get_event(event_id) is None else "STILL THERE")
        return 0
    except CalendarError as e:
        print(f"FAILED: {e}")
        if e.status == 404:
            print("-> calendar not found: check GOOGLE_CALENDAR_ID and that it's shared with the account")
        elif e.status == 403:
            print("-> no access: share the calendar with the service account, 'Make changes to events'")
        return 1
    finally:
        await client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
