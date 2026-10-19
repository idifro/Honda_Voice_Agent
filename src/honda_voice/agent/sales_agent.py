"""SalesAgent (SPEC §7, §8): catalog Q&A, comparisons and showroom hours in the caller's language.
The catalog comes from the database (db/catalog.py), loaded once per call."""

import asyncio
import logging
from typing import Any

from livekit.agents import function_tool, llm

from honda_voice.agent.base_agent import MultilingualAgent
from honda_voice.agent.call_state import CallState
from honda_voice.agent.prompts import GREETING_TEXT, INTRO, SALES_INSTRUCTIONS, turn_instructions
from honda_voice.domain.catalog_view import bike_summary, compare_view, list_item
from honda_voice.domain.competitors import competitor_pivot
from honda_voice.domain.datetime_resolve import showroom_status
from honda_voice.domain.language import is_language_pick
from honda_voice.domain.scope import asks_about_price, out_of_scope_topic

logger = logging.getLogger("honda_voice.agent")
PRICE_KEYS = {"variants", "on_road", "variant_price_instruction"}
COMPETITOR_NOTE = (
    "Say nothing at all about the other product (no price, mileage or opinion). Acknowledge it briefly, "
    "then offer this Honda with one fact and a question."
)


class SalesAgent(MultilingualAgent):
    def __init__(
        self,
        state: CallState,
        *,
        greet: bool = True,
        resume: bool = False,
        chat_ctx: llm.ChatContext | None = None,
    ) -> None:
        """greet: say the fixed greeting on entry (call start). resume: reply on entry (handed back)."""
        super().__init__(instructions=SALES_INSTRUCTIONS, state=state, chat_ctx=chat_ctx)
        self._greet = greet
        self._resume = resume

    async def on_enter(self) -> None:
        if self._greet:
            self.session.say(GREETING_TEXT, allow_interruptions=True)
            # The first LLM call of a session took 1–6 s (connection + empty prompt cache); warm both
            # while the greeting plays so the caller's first question doesn't pay for it.
            self._warmup = asyncio.create_task(self._warm_llm())
        elif self._resume:
            self.session.generate_reply()

    async def _warm_llm(self) -> None:
        try:
            ctx = llm.ChatContext()
            ctx.add_message(role="system", content=self.instructions)
            ctx.add_message(role="user", content="ok")
            async with self.session.llm.chat(chat_ctx=ctx, tools=self.tools) as stream:
                async for _ in stream:
                    break
        except Exception:  # best effort: a failed warm-up must never affect the call
            logger.debug("LLM warm-up failed", exc_info=True)

    def _turn_context(self, transcript: str) -> list[str]:
        if topic := out_of_scope_topic(transcript):  # in code: pre-fetched facts pull the LLM into answering
            note = f"The caller is asking about {topic}, which is out of scope: don't answer it or ask which "
            return [note + "model; say the out-of-scope line."]
        facts = self._prefetched_facts(transcript)
        return [facts] if facts else []

    def _fixed_reply(self, transcript: str) -> str | None:
        """The intro, straight from code, when the caller's first turn only picks a language ("Tamil",
        "English please"): no LLM call. Anything more (a model, a price, a question) goes to the LLM."""
        if self.state.caller_turns != 1 or not is_language_pick(transcript, self.state.supported):
            return None
        if self.catalog.index.mentions(transcript) or asks_about_price(transcript):
            return None
        if out_of_scope_topic(transcript) or competitor_pivot(transcript)[0]:
            return None
        return INTRO[self.lang]

    def _turn_instructions(self) -> str:
        return turn_instructions(self.lang, include_intro=self.state.caller_turns == 1)

    def _not_found(self, query: str) -> dict[str, Any]:
        """No Honda model by that name: pivot from a competitor in code (rule 11), else offer a callback."""
        competitor, honda_id = competitor_pivot(query)
        if honda_id:
            return {
                "found": False,
                "competitor_mentioned": True,
                "closest_honda": {"found": True, **bike_summary(self.catalog.models[honda_id], self.lang)},
                "note": COMPETITOR_NOTE,
            }
        if competitor:
            return {
                "found": False,
                "competitor_mentioned": True,
                "note": "Another brand: say nothing about it. Ask what kind of bike or budget they want, "
                "then use list_bikes.",
            }
        note = "No Honda model by that name: say the showroom will check and offer a callback."
        return {"found": False, "note": note}

    def _prefetched_facts(self, transcript: str) -> str | None:
        """find_bike output for models named in the transcript, so most product questions need one LLM
        call instead of two (step 6b). Same data and rules as the tool; the tool remains for anything else."""
        matches = self.catalog.index.matches(transcript)
        if not matches:
            _, honda_id = competitor_pivot(transcript)
            if honda_id is None:
                return None
            fact = {"found": True, **bike_summary(self.catalog.models[honda_id], self.lang)}
            return f"The caller named another brand's model. {COMPETITOR_NOTE} Closest Honda, fetched: {fact}"
        if len(matches) > 2:
            return None
        competitor, _ = competitor_pivot(transcript)
        # Comparisons kept adding unasked prices (55+ words, step 14 eval): leave them out unless asked.
        drop_prices = len(matches) == 2 and not asks_about_price(transcript)
        facts = []
        for alias, bike_id in matches:
            fact = {"found": True, **bike_summary(self.catalog.models[bike_id], self.lang)}
            if drop_prices:
                fact = {k: v for k, v in fact.items() if k not in PRICE_KEYS and not k.startswith("price")}
                fact["prices"] = "not asked: don't mention prices"
            if similar := self.catalog.index.similar(alias):
                fact["similar_models"] = [self.catalog.models[i]["name"] for i in similar]
                fact["similar_note"] = "The caller used a family name: ask which of these they mean first."
            facts.append(fact)
        note = f" The caller also named another brand. {COMPETITOR_NOTE}" if competitor else ""
        return (
            "find_bike results for the model(s) the caller named, already fetched; answer from these "
            f"without calling find_bike for them: {facts}{note}"
        )

    @function_tool
    async def find_bike(self, query: str) -> dict[str, Any]:
        """Look up one Honda two-wheeler: ex-showroom and on-road (Delhi) prices with breakdown, variants,
        mileage or range, specs, features and pitch.

        Args:
            query: The model name as the caller said it, e.g. "Activa 125", "shine", "H'ness".
        """
        result = await self.catalog.find(query)
        if not result.bike_ids:
            return self._not_found(query)
        if len(result.bike_ids) > 1:
            return {
                "found": False,
                "candidates": [self.catalog.models[i]["name"] for i in result.bike_ids],
                "note": "Ask which one they mean.",
            }
        out = {"found": True, **bike_summary(self.catalog.models[result.bike_ids[0]], self.lang)}
        if result.similar:
            out["similar_models"] = [self.catalog.models[i]["name"] for i in result.similar]
            out["similar_note"] = (
                "If the caller didn't say which model exactly, ask which of these they mean."
            )
        return out

    @function_tool
    async def list_bikes(
        self, segment: str | None = None, max_price_inr: int | None = None
    ) -> dict[str, Any]:
        """Suggest up to 4 Honda models, cheapest first, optionally by segment and/or ex-showroom budget.

        Args:
            segment: One of: adventure, adventure-crossover, adventure-scooter, adventure-tourer, commuter,
                cruiser, dual-sport, electric-scooter, heritage-roadster, luxury-tourer, premium-commuter,
                retro-classic, retro-scrambler, scooter, sporty-commuter, street, superbike. Omit for any.
            max_price_inr: Budget in rupees as a plain number (e.g. "under 1 lakh" -> 100000). Omit for none.
        """
        models = self.catalog.list_models(segment, max_price_inr)
        if not models:
            return {"found": False, "note": "Nothing matches; offer the nearest segment or a higher budget."}
        return {"found": True, "models": [list_item(m, self.lang) for m in models]}

    @function_tool
    async def compare_bikes(self, a: str, b: str) -> dict[str, Any]:
        """Compare two Honda models side by side on key fields. Both must be Honda models.

        Args:
            a: First model name as the caller said it.
            b: Second model name as the caller said it.
        """
        found = []
        for query in (a, b):
            result = await self.catalog.find(query)
            if len(result.bike_ids) != 1:
                return {
                    "found": False,
                    "unresolved": query,
                    "candidates": [self.catalog.models[i]["name"] for i in result.bike_ids],
                    "note": "Ask which model they mean; only Honda models can be compared.",
                }
            found.append(compare_view(self.catalog.models[result.bike_ids[0]], self.lang))
        return {"found": True, "a": found[0], "b": found[1]}

    @function_tool
    async def check_showroom_open(self, day_ref: str, time_24h: str | None = None) -> dict[str, Any]:
        """Check whether the showroom is open on a day, optionally at a time. Never compute dates yourself.

        Args:
            day_ref: "today", "tomorrow", "day_after", a weekday ("mon" … "sun"), or "YYYY-MM-DD" if the
                caller said a date.
            time_24h: The time asked about, 24-hour "HH:MM" (e.g. "shaam 5 baje" / "5 pm" -> "17:00").
                Omit if the caller didn't give a time.
        """
        try:
            return showroom_status(day_ref, time_24h, self.state.clock())
        except ValueError as e:
            return {"error": str(e), "note": "Ask the caller which day (and time) they mean."}

    @function_tool
    async def start_booking(
        self, model: str | None = None, day_ref: str | None = None, time_24h: str | None = None
    ) -> Any:
        """Hand the call over to test-ride booking, also for cancelling a booking.

        Args:
            model: The model the caller wants to ride, as they said it. Omit if they haven't said.
            day_ref: The day if the caller already said one: "today", "tomorrow", "day_after", "mon" … "sun",
                or "YYYY-MM-DD". Omit otherwise.
            time_24h: The time if already said, 24-hour "HH:MM" ("shaam 6 baje" -> "18:00"). Omit otherwise.
        """
        from honda_voice.agent.booking_agent import BookingAgent  # booking_agent imports this module

        if model:
            bike_id, _ = await self.catalog.find_one(model)
            if bike_id is not None:
                bike = self.catalog.models[bike_id]
                if bike["status"] != "on_sale":
                    return {
                        "handed_over": False,
                        "note": f"{bike['name']} is {bike['status'].replace('_', ' ')}: no test rides yet. "
                        "Offer a callback when it's available (capture_lead, reason upcoming_model).",
                    }
                self.state.booking_model = bike["id"]
        logger.info("handoff: sales -> booking (model=%s)", self.state.booking_model)
        request = (day_ref, time_24h) if day_ref and self.state.booking_model else None
        return BookingAgent(self.state, chat_ctx=self.chat_ctx, slot_request=request)
