"""Per-call state shared by every agent in the call (SPEC §4.1, §7). Handoffs create new Agent objects, so the
session language, turn count and booking draft live here, not on an agent."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import asyncpg

from honda_voice.agent.metrics import TurnMetrics
from honda_voice.db.calendar_sync import CalendarSync
from honda_voice.db.catalog import Catalog
from honda_voice.domain.booking_guard import BookingDraft
from honda_voice.domain.datetime_resolve import now_ist


@dataclass
class CallState:
    catalog: Catalog
    lang: str  # session language (code decides, SPEC §4.1)
    supported: list[str]
    tts: Any | None = None  # the session's Sarvam TTS, retargeted per turn
    metrics: TurnMetrics | None = None
    pool: asyncpg.Pool | None = None  # None offline: booking tools then report the database as unavailable
    showroom_id: str = "demo-showroom"
    room: str | None = None
    calendar: CalendarSync | None = None  # None when CALENDAR_SYNC=off: rows stay PENDING
    clock: Callable[[], datetime] = now_ist
    detected: str | None = None  # STT's latest detection, consumed by the next language decision
    tts_lang: str = ""  # language the TTS is currently set to
    last_user_msg_id: str | None = None
    caller_turns: int = 0
    turn_start_lang: str = ""  # session language when the current caller turn began
    booking_model: str | None = None  # bike_id the caller wants to ride, if known
    booking: BookingDraft = field(default_factory=BookingDraft)
    on_language_switch: Callable[[str], None] | None = None  # e.g. prepare the hold line in the new language
    before_reply: Callable[[], Awaitable[None]] | None = None  # before a reply's first audio (hold line)
    call_id: int | None = None  # public.calls row (SPEC §20.5), set once created
    outcomes: set[str] = field(default_factory=set)  # "lead", "cancelled"; bookings are booking.references
    advisor: Any = None  # agent.advisor.Advisor when ADVISOR is shadow/on (SPEC §20)
    lead_pending: dict[str, Any] | None = None  # callback read back, waiting for the caller's yes (consent)

    def __post_init__(self) -> None:
        self.tts_lang = self.tts_lang or self.lang
        self.turn_start_lang = self.turn_start_lang or self.lang
