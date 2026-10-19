"""The calendar event for a booking (SPEC §11). Pure: no I/O.

Data minimisation (SPEC §17): Google Calendar is a third-party copy, so the event carries the name, model,
slot and booking code but a masked phone; the full number stays in the database.
"""

import base64
from datetime import datetime, timedelta
from typing import Any

from honda_voice.domain import phone as phone_mod
from honda_voice.domain.datetime_resolve import BUSINESS_TZ
from honda_voice.domain.language import LANGUAGES


def event_id(reference: str) -> str:
    """A stable Calendar event id from the booking reference: base32hex (Calendar allows a-v and 0-9), so a
    retried insert hits 409 instead of creating a duplicate."""
    return "hnd" + base64.b32hexencode(reference.encode()).decode().rstrip("=").lower()


def build_event(
    *,
    reference: str,
    model: str,
    name: str,
    phone: str,
    language: str,
    starts_at: datetime,
    slot_minutes: int,
    showroom: str,
) -> dict[str, Any]:
    if starts_at.tzinfo is None:
        raise ValueError("starts_at must be timezone-aware")
    start = starts_at.astimezone(BUSINESS_TZ)
    end = start + timedelta(minutes=slot_minutes)
    language_name = LANGUAGES[language].name if language in LANGUAGES else language
    return {
        "id": event_id(reference),
        "summary": f"Test ride: {model} ({name})",
        "location": showroom,
        "description": "\n".join(
            [
                f"Booking code: {reference}",
                f"Model: {model}",
                f"Customer: {name}",
                f"Phone: {phone_mod.mask(phone)} (full number in the bookings database)",
                f"Language: {language_name}",
                "Driving licence: confirmed by the caller",
                "Booked by the Honda voice agent. Cancelling the booking removes this event.",
            ]
        ),
        "start": {"dateTime": start.isoformat(), "timeZone": str(BUSINESS_TZ)},
        "end": {"dateTime": end.isoformat(), "timeZone": str(BUSINESS_TZ)},
        "extendedProperties": {"private": {"booking_reference": reference}},
        "reminders": {"useDefault": False},
    }
