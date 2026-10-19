import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from honda_voice.domain.calendar_event import build_event, event_id

IST = ZoneInfo("Asia/Kolkata")


def test_event_id_is_valid_stable_and_unique():
    ids = {event_id(f"HND-{c}7K3Q") for c in "ABCDEFGH23456789"}
    assert len(ids) == 16
    assert all(re.fullmatch(r"[a-v0-9]{5,1024}", i) for i in ids)  # Calendar's allowed id alphabet
    assert event_id("HND-7K3Q9") == event_id("HND-7K3Q9")


def test_build_event():
    start = datetime(2026, 10, 16, 11, 30, tzinfo=ZoneInfo("UTC"))  # 17:00 IST, as asyncpg returns it
    e = build_event(
        reference="HND-7K3Q9", model="Honda Activa 125", name="Ravi", phone="9876543210", language="ta-IN",
        starts_at=start, slot_minutes=30, showroom="Demo showroom, Delhi",
    )  # fmt: skip
    assert e["start"] == {"dateTime": "2026-10-16T17:00:00+05:30", "timeZone": "Asia/Kolkata"}
    assert e["end"]["dateTime"] == "2026-10-16T17:30:00+05:30"
    assert e["summary"] == "Test ride: Honda Activa 125 (Ravi)" and "Language: Tamil" in e["description"]
    assert "xxxxxxx210" in e["description"] and "9876543210" not in json.dumps(e)  # masked (SPEC §17)
    assert e["id"] == event_id("HND-7K3Q9") and "attendees" not in e


def test_build_event_needs_aware_time():
    with pytest.raises(ValueError):
        build_event(reference="HND-7K3Q9", model="m", name="n", phone="9876543210", language="en-IN",
                    starts_at=datetime(2026, 10, 16, 17, 0), slot_minutes=30, showroom="s")  # noqa: DTZ001  # fmt: skip
