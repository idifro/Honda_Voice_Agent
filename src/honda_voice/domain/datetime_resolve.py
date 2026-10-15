"""Day/time resolution in IST (SPEC §10; CLAUDE.md rules 5 and 7). The LLM only extracts `day_ref` and
`time_24h`; all date arithmetic happens here. Slot snapping and `speak_slot` arrive with booking (step 11)."""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

BUSINESS_TZ = ZoneInfo("Asia/Kolkata")

_WEEKDAYS = {
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5, "sun": 6, "sunday": 6,
}  # fmt: skip
_RELATIVE = {"today": 0, "tomorrow": 1, "day_after": 2, "day_after_tomorrow": 2}


@dataclass(frozen=True)
class ShowroomHours:
    """Mirrors the `showrooms` row (SPEC §5). Phase 2 loads it from the database."""

    open_time: time = time(10, 0)
    close_time: time = time(19, 0)
    closed_isodow: frozenset[int] = field(default_factory=frozenset)  # ISO weekday: 1 = Mon … 7 = Sun


DEMO_SHOWROOM = ShowroomHours()


def now_ist() -> datetime:
    return datetime.now(BUSINESS_TZ)


def resolve_day(day_ref: str, now: datetime) -> date:
    """'today' / 'tomorrow' / 'day_after' / a weekday (next occurrence, today included) / 'YYYY-MM-DD'."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    today = now.astimezone(BUSINESS_TZ).date()
    ref = day_ref.strip().lower().replace(" ", "_").replace("-", "_") if day_ref else ""
    if ref in _RELATIVE:
        return today + timedelta(days=_RELATIVE[ref])
    if ref in _WEEKDAYS:
        return today + timedelta(days=(_WEEKDAYS[ref] - today.weekday()) % 7)
    try:
        day = date.fromisoformat(day_ref.strip())
    except (ValueError, AttributeError):
        raise ValueError(f"unrecognised day_ref {day_ref!r}") from None
    if day < today:
        raise ValueError(f"{day.isoformat()} is in the past")
    return day


def parse_time_24h(value: str) -> time:
    """'17:00', '17', '9:30' -> time. 24-hour only; the LLM maps 'shaam 5 baje' to '17:00'."""
    parts = value.strip().split(":")
    if not 1 <= len(parts) <= 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"time_24h must look like 'HH:MM', got {value!r}")
    hour, minute = int(parts[0]), int(parts[1]) if len(parts) == 2 else 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"time_24h out of range: {value!r}")
    return time(hour, minute)


def showroom_status(
    day_ref: str, time_24h: str | None, now: datetime, showroom: ShowroomHours = DEMO_SHOWROOM
) -> dict[str, Any]:
    """Whether the showroom is open on a day (and at a time), as facts for the LLM to speak."""
    day = resolve_day(day_ref, now)
    now_local = now.astimezone(BUSINESS_TZ)
    is_today = day == now_local.date()
    open_that_day = day.isoweekday() not in showroom.closed_isodow
    out: dict[str, Any] = {
        "date": day.isoformat(),
        "weekday": day.strftime("%A"),
        "is_today": is_today,
        "open_that_day": open_that_day,
        "opens": showroom.open_time.strftime("%H:%M"),
        "closes": showroom.close_time.strftime("%H:%M"),
    }
    if is_today:
        out["open_now"] = open_that_day and showroom.open_time <= now_local.time() < showroom.close_time
    if time_24h:
        asked = parse_time_24h(time_24h)
        out["time_asked"] = asked.strftime("%H:%M")
        out["open_at_time_asked"] = open_that_day and showroom.open_time <= asked < showroom.close_time
        if is_today and asked < now_local.time().replace(second=0, microsecond=0):
            out["note"] = "That time has already passed today."
    return out
