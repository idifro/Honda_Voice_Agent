"""Day/time resolution in IST (SPEC §10; CLAUDE.md rules 5 and 7). The LLM only extracts `day_ref` and
`time_24h`; all date arithmetic happens here: resolving the day, snapping to the slot grid, and saying a slot
in the caller's language. Pure: no I/O."""

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
    slot_minutes: int = 30


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


# --- Slots (booking) ---------------------------------------------------------------------------

LEAD_TIME = timedelta(minutes=30)  # no booking for a slot that starts sooner than this


@dataclass(frozen=True)
class SlotCandidates:
    """Grid times to offer for a request. Capacity is checked against the database separately."""

    day: date
    slots: list[datetime]  # up to `count`, ordered by closeness to the requested time, else by time
    requested: datetime | None = None
    exact: bool = False  # the requested time itself is on the grid and bookable
    reason: str | None = None  # why the request couldn't be met as asked


def slot_grid(day: date, showroom: ShowroomHours = DEMO_SHOWROOM) -> list[datetime]:
    """Slot start times for a day: every `slot_minutes` from opening, each slot ending by closing time."""
    step = timedelta(minutes=showroom.slot_minutes)
    t = datetime.combine(day, showroom.open_time, BUSINESS_TZ)
    close = datetime.combine(day, showroom.close_time, BUSINESS_TZ)
    out = []
    while t + step <= close:
        out.append(t)
        t += step
    return out


def candidate_slots(
    day_ref: str,
    time_24h: str | None,
    now: datetime,
    showroom: ShowroomHours = DEMO_SHOWROOM,
    count: int = 3,
) -> SlotCandidates:
    """Resolve a day/time to bookable grid times. Raises ValueError for an unrecognised or past day."""
    day = resolve_day(day_ref, now)
    if day.isoweekday() in showroom.closed_isodow:
        return SlotCandidates(day, [], reason="closed_day")
    earliest = now.astimezone(BUSINESS_TZ) + LEAD_TIME
    grid = [t for t in slot_grid(day, showroom) if t >= earliest]
    if not time_24h:
        reason = None if grid else "no_slots_left_today"
        return SlotCandidates(day, grid[:count], reason=reason)

    requested = datetime.combine(day, parse_time_24h(time_24h), BUSINESS_TZ)
    if not grid:
        return SlotCandidates(day, [], requested, reason="no_slots_left_today")
    exact = requested in grid
    nearest = sorted(grid, key=lambda t: (abs(t - requested), t))[:count]
    reason = None
    if not exact:
        if requested < earliest:
            reason = "too_soon_or_past"
        elif not (showroom.open_time <= requested.time() < showroom.close_time):
            reason = "outside_hours"
        else:
            reason = "not_on_slot_grid"
    return SlotCandidates(day, sorted(nearest), requested, exact, reason)


# --- Speaking a slot ---------------------------------------------------------------------------
# Native-language words need a native-speaker review before the demo (SPEC §8).
# Hindi is Roman-script Hinglish.

_DAY_WORDS = {  # today, tomorrow, day after tomorrow
    "hi-IN": ("aaj", "kal", "parson"),
    "en-IN": ("today", "tomorrow", "day after tomorrow"),
    "ta-IN": ("இன்று", "நாளை", "நாளை மறுநாள்"),
    "te-IN": ("ఈరోజు", "రేపు", "ఎల్లుండి"),
    "ml-IN": ("ഇന്ന്", "നാളെ", "മറ്റന്നാൾ"),
    "kn-IN": ("ಇಂದು", "ನಾಳೆ", "ನಾಡಿದ್ದು"),
}
_WEEKDAY_NAMES = {  # Monday … Sunday, as spoken
    "hi-IN": ("somvaar", "mangalvaar", "budhvaar", "guruvaar", "shukravaar", "shanivaar", "ravivaar"),
    "en-IN": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "ta-IN": ("திங்கள்கிழமை", "செவ்வாய்க்கிழமை", "புதன்கிழமை", "வியாழக்கிழமை", "வெள்ளிக்கிழமை",
              "சனிக்கிழமை", "ஞாயிற்றுக்கிழமை"),
    "te-IN": ("సోమవారం", "మంగళవారం", "బుధవారం", "గురువారం", "శుక్రవారం", "శనివారం", "ఆదివారం"),
    "ml-IN": ("തിങ്കളാഴ്ച", "ചൊവ്വാഴ്ച", "ബുധനാഴ്ച", "വ്യാഴാഴ്ച", "വെള്ളിയാഴ്ച", "ശനിയാഴ്ച", "ഞായറാഴ്ച"),
    "kn-IN": ("ಸೋಮವಾರ", "ಮಂಗಳವಾರ", "ಬುಧವಾರ", "ಗುರುವಾರ", "ಶುಕ್ರವಾರ", "ಶನಿವಾರ", "ಭಾನುವಾರ"),
}  # fmt: skip
_PERIODS = {  # morning (<12), afternoon (12–16), evening (≥16): SPEC §10
    "hi-IN": ("subah", "dopahar", "shaam"),
    "ta-IN": ("காலை", "மதியம்", "மாலை"),
    "te-IN": ("ఉదయం", "మధ్యాహ్నం", "సాయంత్రం"),
    "ml-IN": ("രാവിലെ", "ഉച്ചയ്ക്ക്", "വൈകുന്നേരം"),
    "kn-IN": ("ಬೆಳಿಗ್ಗೆ", "ಮಧ್ಯಾಹ್ನ", "ಸಂಜೆ"),
}
_HOUR_SUFFIX = {"ta-IN": "மணிக்கு", "te-IN": "గంటలకు", "ml-IN": "മണിക്ക്", "kn-IN": "ಗಂಟೆಗೆ"}
# Hour + minutes, written the way TTS expands "10:30" itself; "10:30 மணிக்கு" was read as "ten o'clock thirty
# minutes o'clock" (step 11 round trip).
_HOUR_MINUTES = {
    "ta-IN": "{h} மணி {m} நிமிடத்துக்கு",
    "te-IN": "{h} గంటల {m} నిమిషాలకు",
    "ml-IN": "{h} മണി {m} മിനിറ്റിന്",
    "kn-IN": "{h} ಗಂಟೆ {m} ನಿಮಿಷಕ್ಕೆ",
}
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")  # fmt: skip


def _period(t: time) -> int:
    return 0 if t.hour < 12 else 1 if t.hour < 16 else 2


def _clock(t: time, lang: str) -> str:
    h12 = t.hour % 12 or 12
    if lang == "en-IN":
        return f"{h12}{f':{t.minute:02d}' if t.minute else ''} {'am' if t.hour < 12 else 'pm'}"
    if lang == "hi-IN":
        if t.minute == 30:
            hours = {1: "dedh", 2: "dhaai"}.get(h12, f"saade {h12}")
        else:
            hours = f"{h12}{f':{t.minute:02d}' if t.minute else ''}"
        return f"{_PERIODS[lang][_period(t)]} {hours} baje"
    when = _HOUR_MINUTES[lang].format(h=h12, m=t.minute) if t.minute else f"{h12} {_HOUR_SUFFIX[lang]}"
    return f"{_PERIODS[lang][_period(t)]} {when}"


def _day_phrase(day: date, today: date, lang: str) -> str:
    offset = (day - today).days
    if 0 <= offset <= 2:
        return _DAY_WORDS[lang][offset]
    weekday = _WEEKDAY_NAMES[lang][day.weekday()]
    if offset <= 6:
        return weekday
    return f"{weekday}, {day.day} {_MONTHS[day.month - 1]}"


def speak_slot(slot: datetime, now: datetime, lang: str) -> str:
    """A slot as the agent says it: 'kal shaam 5 baje', 'நாளை மாலை 5 மணிக்கு', 'tomorrow at 5 pm'."""
    if slot.tzinfo is None or now.tzinfo is None:
        raise ValueError("slot and now must be timezone-aware")
    if lang not in _DAY_WORDS:
        raise ValueError(f"unsupported language {lang!r}")
    local = slot.astimezone(BUSINESS_TZ)
    day = _day_phrase(local.date(), now.astimezone(BUSINESS_TZ).date(), lang)
    clock = _clock(local.time(), lang)
    if lang == "en-IN":
        on = "on " if day[0].isupper() else ""
        return f"{on}{day} at {clock}"
    return f"{day} {clock}"
