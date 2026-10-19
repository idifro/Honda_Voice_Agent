import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from honda_voice.db.bookings import REFERENCE_ALPHABET, new_reference, planned_slots, speak_reference
from honda_voice.domain.datetime_resolve import ShowroomHours

IST = ZoneInfo("Asia/Kolkata")


def test_reference_format_and_alphabet():
    refs = {new_reference() for _ in range(2000)}
    assert all(re.fullmatch(r"HND-[A-Z2-9]{5}", r) for r in refs)
    assert not set("".join(r[4:] for r in refs)) - set(REFERENCE_ALPHABET)
    assert not set("01OIL") & set(REFERENCE_ALPHABET)  # no look-alikes
    assert len(refs) > 1990  # random, practically no collisions


def test_speak_reference():
    assert speak_reference("HND-7K3Q9") == "H N D, 7 K 3 Q 9"


def test_planned_slots_counts_and_skips():
    now = datetime(2026, 10, 15, 12, 10, tzinfo=IST)  # Thursday
    sunday_closed = ShowroomHours(closed_isodow=frozenset({7}))
    plan = planned_slots(sunday_closed, ["a", "b"], date(2026, 10, 15), days=4, now=now)
    days = {t.date() for _, t in plan}
    assert date(2026, 10, 18) not in days  # Sunday closed
    today = [t for b, t in plan if b == "a" and t.date() == date(2026, 10, 15)]
    assert today[0] == datetime(2026, 10, 15, 12, 30, tzinfo=IST)  # past times skipped
    assert len([1 for b, t in plan if b == "a" and t.date() == date(2026, 10, 16)]) == 18  # full day
    assert len(plan) == 2 * (len(today) + 18 + 18)  # Thu (rest) + Fri + Sat, both bikes
