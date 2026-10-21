"""BookingAgent (SPEC §7, §8): books and cancels test rides in the caller's language. Slots come from the
database (db/bookings.py), dates from code (rule 5), and nothing is booked unless the caller agreed to exactly
what was read back (rule 6, domain/booking_guard.py)."""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from livekit.agents import function_tool, llm

from honda_voice.agent.base_agent import MultilingualAgent
from honda_voice.agent.call_state import CallState
from honda_voice.agent.prompts import (
    BOOKED_LINE,
    BOOKING_INSTRUCTIONS,
    CONFIRM_SUMMARY,
    booking_turn_instructions,
)
from honda_voice.agent.sales_agent import SalesAgent
from honda_voice.db import bookings
from honda_voice.domain import phone as phone_mod
from honda_voice.domain.booking_guard import OfferedSlot
from honda_voice.domain.datetime_resolve import BUSINESS_TZ, parse_time_24h, resolve_day, speak_slot

logger = logging.getLogger("honda_voice.agent")

# Why a slot search couldn't match the request exactly, worded for the LLM.
REASON_NOTES = {
    "closed_day": "The showroom is closed that day; ask for another day.",
    "outside_hours": "That time is outside showroom hours; these are the nearest slots.",
    "not_on_slot_grid": "Slots start every 30 minutes; these are the nearest.",
    "too_soon_or_past": "That time is too soon or has passed; these are the next free slots.",
    "no_slots_left_today": "No slots left today; ask about another day.",
    "requested_time_full": "Both demo bikes are taken at that time; these are the nearest free slots.",
    "fully_booked": "No free slots that day; ask about another day or offer a callback.",
}
# Why booking or the read-back was refused, worded for the LLM.
REFUSAL_NOTES = {
    "SLOT_NOT_OFFERED": "Use a day_ref and time_24h that check_slots returned; call check_slots first if "
    "needed.",
    # The no-licence callback went to confirm_details instead of capture_lead (2026-10-20 eval run).
    "LICENCE_NOT_CONFIRMED": "If the caller said they have no licence, don't book: save the callback with "
    "capture_lead(reason='no_licence', name, phone). Otherwise ask if they have a valid two-wheeler driving "
    "licence and call record_licence.",
    "NAME_MISSING": "Ask the caller's name first.",
    "PHONE_NOT_VALIDATED": "Call validate_phone with the number the caller said first.",
    "NOT_CONFIRMED_BY_CALLER": "Only book after the caller says yes to the read-back.",
    "NO_READ_BACK": "Call confirm_details and read its summary to the caller first.",
    "NO_CALLER_REPLY_SINCE_READ_BACK": "Read the summary and wait for the caller's yes before booking.",
    "READ_BACK_NOT_LAST": "The caller said something else after the read-back: call confirm_details again "
    "and read the new summary.",
    "DETAILS_DIFFER_FROM_READ_BACK": "Details differ from what was read back: call confirm_details again and "
    "read the new summary.",
}


class BookingAgent(MultilingualAgent):
    # Read-back, booked line and callback consent are fixed text from code: spoken without a second LLM call.
    FIXED_SAY_TOOLS = ("confirm_details", "book_test_ride", "capture_lead")

    def __init__(
        self,
        state: CallState,
        *,
        chat_ctx: llm.ChatContext | None = None,
        slot_request: tuple[str, str | None] | None = None,
    ) -> None:
        """slot_request: (day_ref, time_24h) the caller already gave before the handoff."""
        super().__init__(instructions=BOOKING_INSTRUCTIONS, state=state, chat_ctx=chat_ctx)
        self._slot_request = slot_request
        self._entered_turn = state.caller_turns  # the licence answer can only come in a later caller turn

    async def on_enter(self) -> None:
        # Handoff tools don't reply themselves, so continue the caller's request here. If they already said a
        # day, search in code first: the first reply is then grounded in real slots (in the step 14 eval the
        # LLM once said "6 baje theek rahega" without checking) and needs one LLM call instead of two.
        if self._slot_request is None:
            self.session.generate_reply()
            return
        day_ref, time_24h = self._slot_request
        found = await self.check_slots(day_ref, time_24h)
        self.session.generate_reply(
            instructions=f"check_slots already ran for the caller's request (day_ref={day_ref!r}, "
            f"time_24h={time_24h!r}): {found}. Reply from this result; don't call check_slots again for it."
        )

    def _turn_instructions(self) -> str:
        return booking_turn_instructions(self.lang, self.state.clock(), self._state_line())

    def _state_line(self) -> str:
        """Booking progress for the LLM, re-rendered in the current language every turn."""
        st, draft = self.state, self.state.booking
        model = st.catalog.models[st.booking_model]["name"] if st.booking_model in st.catalog.models else None
        parts = [f"model: {model or 'not chosen yet'}"]
        if draft.requested_slot_id in draft.offered:
            requested = draft.offered[draft.requested_slot_id]
            parts.append(f"caller's chosen time: {self._spoken(requested)} = {self._ref_text(requested)}")
        if draft.offered:
            recent = list(draft.offered.values())[-6:]
            parts.append(
                "offered slots: "
                + ", ".join(f"{self._spoken(o)} = {self._ref_text(o)} ({o.model_name})" for o in recent)
            )
        licence = {None: "not asked yet", True: "yes", False: "no (can't book)"}[draft.licence]
        parts.append(f"driving licence: {licence}")
        parts.append(f"phone validated: {'yes' if draft.validated_phones else 'no'}")
        if draft.pending:
            slot = draft.offered[draft.pending.slot_id]
            parts.append(f"read back {self._spoken(slot)} for {draft.pending.name}; waiting for yes/no")
        if draft.references:
            parts.append(f"booked on this call: {', '.join(draft.references)}")
        return "[booking_state] " + "; ".join(parts)

    def _spoken(self, slot: OfferedSlot) -> str:
        return speak_slot(slot.starts_at, self.state.clock(), self.lang)

    @staticmethod
    def _ref(slot: OfferedSlot) -> dict[str, str]:
        """How the LLM names a slot back: day + time, not an opaque id (step 14 eval: ids got mixed up)."""
        local = slot.starts_at.astimezone(BUSINESS_TZ)  # asyncpg returns timestamptz in UTC
        return {"day_ref": local.date().isoformat(), "time_24h": local.strftime("%H:%M")}

    def _ref_text(self, slot: OfferedSlot) -> str:
        ref = self._ref(slot)
        return f"day_ref {ref['day_ref']} time_24h {ref['time_24h']}"

    def _offered_slot_id(self, day_ref: str, time_24h: str) -> int | None:
        """The offered slot at that day and time (for the chosen model if several match), else None."""
        try:
            day = resolve_day(day_ref, self.state.clock())
            starts_at = datetime.combine(day, parse_time_24h(time_24h), BUSINESS_TZ)
        except ValueError:
            return None
        matches = [o for o in self.state.booking.offered.values() if o.starts_at == starts_at]
        matches.sort(key=lambda o: o.bike_id != self.state.booking_model)
        return matches[0].slot_id if matches else None

    async def _db(self, work: Callable[[Any], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
        """Run `work(conn)`; a database failure becomes a callback offer, never a crash mid-call."""
        if self.state.pool is None:
            return {
                "error": "DB_UNAVAILABLE",
                "note": "Booking system unavailable: offer a callback (capture_lead).",
            }
        try:
            async with self.state.pool.acquire() as conn:
                return await work(conn)
        except Exception:
            logger.exception("booking database error")
            return {"error": "DB_ERROR", "note": "Booking system problem: apologise and offer a callback "
                    "(capture_lead, reason error)."}  # fmt: skip

    def _offers(self, bike_id: str, search: bookings.SlotSearch) -> list[dict[str, Any]]:
        name = self.catalog.models[bike_id]["name"]
        offered = [OfferedSlot(o.slot_id, bike_id, name, o.starts_at) for o in search.offers]
        self.state.booking.offer(offered)
        return [{"spoken": self._spoken(o), **self._ref(o)} for o in offered]

    @function_tool
    async def check_slots(
        self, day_ref: str, time_24h: str | None = None, model: str | None = None
    ) -> dict[str, Any]:
        """Find free test-ride slots. Never compute dates yourself.

        Args:
            day_ref: "today", "tomorrow", "day_after", a weekday ("mon" … "sun"), or "YYYY-MM-DD" if the
                caller said a date.
            time_24h: Preferred time, 24-hour "HH:MM" ("shaam 5 baje" -> "17:00"). Omit if none given.
            model: The model to ride, as the caller said it. Omit to use the model already chosen.
        """
        st = self.state
        bike_id = st.booking_model
        if model:
            found, candidates = await self.catalog.find_one(model)
            if found is None:
                return {
                    "error": "MODEL_UNCLEAR",
                    "candidates": [self.catalog.models[i]["name"] for i in candidates],
                    "note": "Ask which Honda model they mean.",
                }
            bike_id = found
        if bike_id is None:
            return {"error": "MODEL_UNKNOWN", "note": "Ask which model they'd like to ride."}
        bike = self.catalog.models[bike_id]
        if bike["status"] != "on_sale":
            note = f"{bike['name']} has no test rides yet; offer a callback (capture_lead, upcoming_model)."
            return {"error": "NOT_RIDEABLE", "note": note}
        st.booking_model = bike_id

        async def work(conn) -> dict[str, Any]:
            try:
                search = await bookings.find_open_slots(
                    conn, st.showroom_id, bike_id, day_ref, time_24h, st.clock()
                )
            except ValueError as e:
                return {
                    "error": "BAD_DAY_OR_TIME",
                    "detail": str(e),
                    "note": "Ask which day and time they mean.",
                }
            out: dict[str, Any] = {
                "model": bike["name"],
                "slots": self._offers(bike_id, search),
                "exact_time_available": search.exact,
            }
            draft = st.booking
            draft.requested_slot_id = (
                self._offered_slot_id(day_ref, time_24h) if search.exact and time_24h else None
            )
            if search.reason:
                out["reason"] = search.reason
                out["note"] = REASON_NOTES.get(search.reason, "")
            return out

        return await self._db(work)

    @function_tool
    async def record_licence(self, has_valid_licence: bool) -> dict[str, Any]:
        """Record the caller's answer to whether they hold a valid two-wheeler driving licence.

        Args:
            has_valid_licence: True only if the caller clearly said yes.
        """
        if self.state.caller_turns == self._entered_turn:  # step 14 eval: recorded before it was even asked
            return {
                "recorded": False,
                "note": "Ask the licence question first and wait for the caller's answer.",
            }
        self.state.booking.licence = has_valid_licence
        if has_valid_licence:
            return {"recorded": True, "note": "Next ask their name."}
        return {
            "recorded": True,
            "note": "A valid driving licence is needed for a test ride: say so politely. Offer a showroom "
            "callback (capture_lead, reason no_licence), or booking for someone who has one.",
        }

    @function_tool
    async def validate_phone(self, digits: str) -> dict[str, Any]:
        """Check the caller's mobile number before the read-back.

        Args:
            digits: The caller's words for the number, unchanged (digits or number words, e.g. "nau shunya").
                Don't convert number words yourself.
        """
        draft = self.state.booking
        if not phone_mod.has_number(digits):  # e.g. the caller's name (step 14 eval): not a failed try
            return {
                "valid": False,
                "note": "That isn't a phone number. Ask for their mobile number.",
            }
        normalized = draft.check_phone(digits)
        if normalized:
            return {
                "valid": True,
                "phone": normalized,
                "note": "Now call confirm_details; it reads the number back.",
            }
        if draft.phone_tries_exhausted:
            note = "Still not a valid mobile number: offer a callback instead (capture_lead, phone_failed)."
            return {"valid": False, "note": note}
        return {"valid": False, "note": "Not a valid mobile number; ask again, digit by digit."}

    @function_tool
    async def confirm_details(self, day_ref: str, time_24h: str, name: str, phone: str) -> dict[str, Any]:
        """Prepare the single read-back summary (model, slot, name, number). Say its "say" text word for word
        and wait for the caller's answer before booking.

        Args:
            day_ref: The chosen slot's day_ref, as check_slots returned it.
            time_24h: The chosen slot's time_24h, as check_slots returned it.
            name: The caller's name.
            phone: The validated mobile number.
        """
        st, draft = self.state, self.state.booking
        slot_id = self._offered_slot_id(day_ref, time_24h)
        result = draft.prepare(-1 if slot_id is None else slot_id, name, phone, st.caller_turns)
        if isinstance(result, str):
            return {"ok": False, "error": result, "note": REFUSAL_NOTES[result]}
        draft.requested_slot_id = result.slot_id  # the hint follows what was last read back
        slot = st.booking.offered[result.slot_id]
        say = CONFIRM_SUMMARY[self.lang].format(
            model=slot.model_name,
            slot=self._spoken(slot),
            name=result.name,
            phone=phone_mod.speak(result.phone),
        )
        return {"ok": True, "say": say}

    @function_tool
    async def book_test_ride(
        self, day_ref: str, time_24h: str, name: str, phone: str, caller_confirmed: bool
    ) -> dict[str, Any]:
        """Book the test ride. Only after confirm_details was read back and the caller clearly said yes.

        Args:
            day_ref: The day_ref that was read back.
            time_24h: The time_24h that was read back.
            name: The name that was read back.
            phone: The number that was read back.
            caller_confirmed: True only if the caller said yes to the read-back.
        """
        st = self.state
        slot_id = self._offered_slot_id(day_ref, time_24h)
        result = st.booking.authorize(
            -1 if slot_id is None else slot_id, name, phone, caller_confirmed, st.caller_turns
        )
        if isinstance(result, str):
            logger.warning("book_test_ride refused: %s", result)
            return {"booked": False, "error": result, "note": REFUSAL_NOTES[result]}
        slot = st.booking.offered[result.slot_id]

        async def work(conn) -> dict[str, Any]:
            res = await bookings.reserve(
                conn, result.slot_id, result.name, result.phone, st.room, st.lang,
                licence_confirmed=st.booking.licence is True,
            )  # fmt: skip
            if res.ok:
                st.booking.booked(res.reference)
                if st.calendar is not None:  # after the commit, in the background (rule 8)
                    st.calendar.schedule(res.booking_id)
                logger.info(
                    "booked %s slot=%s phone=%s", res.reference, result.slot_id, phone_mod.mask(result.phone)
                )
                say = BOOKED_LINE[self.lang].format(
                    model=slot.model_name,
                    slot=self._spoken(slot),
                    ref=bookings.speak_reference(res.reference),
                )
                return {"booked": True, "say": say}
            st.booking.pending = None
            st.booking.requested_slot_id = None  # that choice is gone (or already booked)
            if res.error == "DUPLICATE_BOOKING":
                return {
                    "booked": False,
                    "error": res.error,
                    "note": "This number already has this slot booked.",
                }
            local = slot.starts_at.astimezone(BUSINESS_TZ)  # asyncpg returns timestamptz in UTC
            try:
                search = await bookings.find_open_slots(
                    conn, st.showroom_id, slot.bike_id, local.date().isoformat(), local.strftime("%H:%M"),
                    st.clock(), count=2,
                )  # fmt: skip
                alternatives = self._offers(slot.bike_id, search)
            except ValueError:  # the day itself has passed
                alternatives = []
            return {"booked": False, "error": "SLOT_TAKEN", "alternatives": alternatives,
                    "note": "That slot was just taken: apologise and offer these. After they pick, call "
                    "confirm_details again."}  # fmt: skip

        return await self._db(work)

    @function_tool
    async def cancel_test_ride(self, reference: str, phone: str) -> dict[str, Any]:
        """Cancel a booked test ride. Needs the booking code and the mobile number used to book.

        Args:
            reference: The booking code as the caller said it, e.g. "HND 7K3Q9".
            phone: The mobile number as the caller said it.
        """
        ref, normalized = bookings.normalize_reference(reference), phone_mod.normalize(phone)
        if ref is None:
            return {
                "cancelled": False,
                "note": "Booking codes are H N D plus 5 letters or digits; ask again.",
            }
        if normalized is None:
            return {"cancelled": False, "note": "Not a valid mobile number; ask again."}

        async def work(conn) -> dict[str, Any]:
            ok = await bookings.cancel(conn, ref, normalized)
            if ok:
                self.state.outcomes.add("cancelled")
            if ok and (booking_id := await bookings.mark_calendar_pending(conn, ref)) is not None:
                if self.state.calendar is not None:
                    self.state.calendar.schedule(booking_id)
            logger.info("cancel %s phone=%s ok=%s", ref, phone_mod.mask(normalized), ok)
            return {
                "cancelled": ok,
                "reference_spoken": bookings.speak_reference(ref),
                "note": "Cancelled."
                if ok
                else "No active booking with that code and number; check both with the caller.",
            }

        return await self._db(work)

    @function_tool
    async def back_to_sales(self) -> Any:
        """Hand back to the sales assistant for questions about models, prices or showroom hours."""
        logger.info("handoff: booking -> sales")
        return SalesAgent(self.state, greet=False, resume=True, chat_ctx=self.chat_ctx)
