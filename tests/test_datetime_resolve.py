from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from honda_voice.domain.datetime_resolve import (
    ShowroomHours,
    parse_time_24h,
    resolve_day,
    showroom_status,
)

IST = ZoneInfo("Asia/Kolkata")
THU_NOON = datetime(2026, 10, 15, 12, 0, tzinfo=IST)  # a Thursday


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("today", date(2026, 10, 15)),
        ("tomorrow", date(2026, 10, 16)),
        ("day_after", date(2026, 10, 17)),
        ("Day after tomorrow", date(2026, 10, 17)),
        ("thu", date(2026, 10, 15)),  # today's weekday resolves to today
        ("fri", date(2026, 10, 16)),
        ("wednesday", date(2026, 10, 21)),  # wraps to next week
        ("Sun", date(2026, 10, 18)),
        ("2026-10-30", date(2026, 10, 30)),
    ],
)
def test_resolve_day(ref, expected):
    assert resolve_day(ref, THU_NOON) == expected


def test_resolve_day_uses_ist_not_utc():
    # 23:30 UTC on Wed 14 Oct is 05:00 IST on Thu 15 Oct.
    late_utc = datetime(2026, 10, 14, 23, 30, tzinfo=ZoneInfo("UTC"))
    assert resolve_day("today", late_utc) == date(2026, 10, 15)


@pytest.mark.parametrize("ref", ["2026-10-01", "yesterday", "next month", "", "30/10/2026"])
def test_resolve_day_rejects(ref):
    with pytest.raises(ValueError):
        resolve_day(ref, THU_NOON)


def test_resolve_day_requires_aware_datetime():
    with pytest.raises(ValueError):
        resolve_day("today", datetime(2026, 10, 15, 12, 0))  # noqa: DTZ001


@pytest.mark.parametrize(
    ("value", "expected"), [("17:00", time(17)), ("9:30", time(9, 30)), ("17", time(17))]
)
def test_parse_time_24h(value, expected):
    assert parse_time_24h(value) == expected


@pytest.mark.parametrize("value", ["5 pm", "25:00", "17:60", "", "17:00:00"])
def test_parse_time_24h_rejects(value):
    with pytest.raises(ValueError):
        parse_time_24h(value)


def test_open_at_a_time():
    out = showroom_status("tomorrow", "17:00", THU_NOON)
    assert out == {
        "date": "2026-10-16",
        "weekday": "Friday",
        "is_today": False,
        "open_that_day": True,
        "opens": "10:00",
        "closes": "19:00",
        "time_asked": "17:00",
        "open_at_time_asked": True,
    }


@pytest.mark.parametrize(
    ("t", "open_"), [("09:59", False), ("10:00", True), ("18:59", True), ("19:00", False)]
)
def test_opening_hours_boundaries(t, open_):
    assert showroom_status("tomorrow", t, THU_NOON)["open_at_time_asked"] is open_


def test_closed_day():
    sunday_closed = ShowroomHours(closed_isodow=frozenset({7}))
    out = showroom_status("sunday", "11:00", THU_NOON, sunday_closed)
    assert out["open_that_day"] is False and out["open_at_time_asked"] is False


def test_today_reports_open_now_and_past_times():
    out = showroom_status("today", "11:00", THU_NOON)
    assert out["open_now"] is True
    assert out["note"] == "That time has already passed today."
    evening = datetime(2026, 10, 15, 20, 0, tzinfo=IST)
    assert showroom_status("today", None, evening)["open_now"] is False
