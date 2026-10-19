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


# --- Slots (step 11) -------------------------------------------------------------------------------

from honda_voice.domain.datetime_resolve import (  # noqa: E402
    candidate_slots,
    slot_grid,
    speak_slot,
)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=IST)


def test_slot_grid_ends_by_closing():
    grid = slot_grid(date(2026, 10, 16))
    assert grid[0] == at(16, 10) and grid[-1] == at(16, 18, 30) and len(grid) == 18


def test_exact_slot():
    c = candidate_slots("tomorrow", "17:00", THU_NOON)
    assert c.exact and c.reason is None and c.requested == at(16, 17)
    assert c.slots == [at(16, 16, 30), at(16, 17), at(16, 17, 30)]  # nearest three, in time order


@pytest.mark.parametrize(
    ("time_24h", "reason", "nearest"),
    [
        ("17:15", "not_on_slot_grid", at(16, 17)),  # between grid points: ties go to the earlier one
        ("09:00", "outside_hours", at(16, 10)),
        ("19:00", "outside_hours", at(16, 18, 30)),  # closing time itself: last slot is 18:30
        ("21:00", "outside_hours", at(16, 18, 30)),
    ],
)
def test_snapping(time_24h, reason, nearest):
    c = candidate_slots("tomorrow", time_24h, THU_NOON)
    assert not c.exact and c.reason == reason and nearest in c.slots


def test_no_time_gives_first_three_of_the_day():
    assert candidate_slots("tomorrow", None, THU_NOON).slots == [at(16, 10), at(16, 10, 30), at(16, 11)]


def test_lead_time_today():
    c = candidate_slots("today", None, at(15, 12, 10))  # 12:10 now: 12:30 is too soon, 12:40 earliest
    assert c.slots[0] == at(15, 13)
    c = candidate_slots("today", "12:30", at(15, 12, 10))
    assert not c.exact and c.reason == "too_soon_or_past" and c.slots[0] == at(15, 13)


def test_day_used_up():
    c = candidate_slots("today", None, at(15, 18, 45))
    assert c.slots == [] and c.reason == "no_slots_left_today"


def test_closed_day_has_no_slots():
    c = candidate_slots("sunday", "11:00", THU_NOON, ShowroomHours(closed_isodow=frozenset({7})))
    assert c.slots == [] and c.reason == "closed_day"


def test_past_day_raises():
    with pytest.raises(ValueError):
        candidate_slots("2026-10-01", "11:00", THU_NOON)


@pytest.mark.parametrize(
    ("lang", "tomorrow_5pm", "fri_10_30am"),
    [
        ("hi-IN", "kal shaam 5 baje", "kal subah saade 10 baje"),
        ("en-IN", "tomorrow at 5 pm", "tomorrow at 10:30 am"),
        ("ta-IN", "நாளை மாலை 5 மணிக்கு", "நாளை காலை 10 மணி 30 நிமிடத்துக்கு"),
        ("te-IN", "రేపు సాయంత్రం 5 గంటలకు", "రేపు ఉదయం 10 గంటల 30 నిమిషాలకు"),
        ("ml-IN", "നാളെ വൈകുന്നേരം 5 മണിക്ക്", "നാളെ രാവിലെ 10 മണി 30 മിനിറ്റിന്"),
        ("kn-IN", "ನಾಳೆ ಸಂಜೆ 5 ಗಂಟೆಗೆ", "ನಾಳೆ ಬೆಳಿಗ್ಗೆ 10 ಗಂಟೆ 30 ನಿಮಿಷಕ್ಕೆ"),
    ],
)
def test_speak_slot_each_language(lang, tomorrow_5pm, fri_10_30am):
    assert speak_slot(at(16, 17), THU_NOON, lang) == tomorrow_5pm
    assert speak_slot(at(16, 10, 30), THU_NOON, lang) == fri_10_30am


@pytest.mark.parametrize(
    ("slot", "lang", "expected"),
    [
        (at(15, 13), "hi-IN", "aaj dopahar 1 baje"),
        (at(15, 13, 30), "hi-IN", "aaj dopahar dedh baje"),
        (at(15, 14, 30), "hi-IN", "aaj dopahar dhaai baje"),
        (at(17, 12), "hi-IN", "parson dopahar 12 baje"),
        (at(19, 16), "hi-IN", "somvaar shaam 4 baje"),  # within a week: weekday name
        (at(19, 16), "en-IN", "on Monday at 4 pm"),
        (at(26, 11), "en-IN", "on Monday, 26 October at 11 am"),  # beyond a week: date too
        (at(18, 11), "ta-IN", "ஞாயிற்றுக்கிழமை காலை 11 மணிக்கு"),
    ],
)
def test_speak_slot_days_and_half_hours(slot, lang, expected):
    assert speak_slot(slot, THU_NOON, lang) == expected


def test_speak_slot_uses_ist_dates():
    # 23:30 UTC on Thu 15 Oct is 05:00 IST on Fri 16 Oct: "tomorrow" relative to an IST "now".
    slot = datetime(2026, 10, 15, 23, 30, tzinfo=ZoneInfo("UTC"))
    assert speak_slot(slot, THU_NOON, "en-IN") == "tomorrow at 5 am"
