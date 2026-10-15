"""SalesAgent (SPEC §7, §8): catalog Q&A, comparisons and showroom hours in the caller's language.
The catalog comes from the database (db/catalog.py), loaded once per call."""

import asyncio
import logging
from collections.abc import AsyncIterable
from typing import Any

from livekit.agents import Agent, ModelSettings, function_tool, llm

from honda_voice.agent.metrics import TurnMetrics
from honda_voice.agent.prompts import GREETING_TEXT, SALES_INSTRUCTIONS, turn_instructions
from honda_voice.agent.text_transforms import TransformStats, clean_text_stream
from honda_voice.db.catalog import Catalog
from honda_voice.domain.catalog_view import bike_summary, compare_view, list_item
from honda_voice.domain.datetime_resolve import now_ist, showroom_status
from honda_voice.domain.language import next_language, script_mismatch

logger = logging.getLogger("honda_voice.agent")


class SalesAgent(Agent):
    def __init__(
        self,
        *,
        catalog: Catalog,
        initial_language: str,
        supported_languages: list[str],
        tts: Any | None = None,  # the session's Sarvam TTS, retargeted per turn
        turn_metrics: TurnMetrics | None = None,
        greet: bool = True,
    ) -> None:
        super().__init__(instructions=SALES_INSTRUCTIONS)
        self.catalog = catalog
        self.lang = initial_language
        self.supported = supported_languages
        self._tts = tts
        self._metrics = turn_metrics
        self._greet = greet
        self._detected: str | None = None
        self._tts_lang = initial_language  # language the TTS is currently set to
        self._last_user_msg_id: str | None = None
        self._caller_turns = 0  # the intro line is offered on the first caller turn only

    async def on_enter(self) -> None:
        if self._greet:
            self.session.say(GREETING_TEXT, allow_interruptions=True)
            # The first LLM call of a session took 1–6 s (connection + empty prompt cache); warm both
            # while the greeting plays so the caller's first question doesn't pay for it.
            self._warmup = asyncio.create_task(self._warm_llm())

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

    def note_detected_language(self, code: str | None) -> None:
        """Record STT's detected language for the next language decision."""
        if code:
            self._detected = code

    def _update_language(self, transcript: str) -> None:
        new = next_language(self.lang, transcript, self._detected, None, self.supported)
        switched = new != self.lang
        if switched:
            logger.info("session language %s -> %s", self.lang, new)
            self.lang = new  # the TTS follows in tts_node, just before the reply's audio starts
        if self._metrics:
            self._metrics.record_language(self.lang, switched)
        self._detected = None

    # The language tag goes in llm_node, not on_user_turn_completed: preemptive generation skips that hook.
    async def llm_node(self, chat_ctx, tools, model_settings: ModelSettings):
        user_msg = next(
            (
                m
                for m in reversed(chat_ctx.items)
                if getattr(m, "type", None) == "message" and m.role == "user"
            ),
            None,
        )
        if user_msg is not None and user_msg.id != self._last_user_msg_id:
            self._last_user_msg_id = user_msg.id
            self._caller_turns += 1
            # Decided here, on the exact message the LLM answers: preemptive generation can start from a
            # preflight transcript before any final-transcript event (console runs 2 and 3), so event
            # timing can't be relied on. Detection comes from the latest transcript event, else the script.
            self._update_language(user_msg.text_content or "")
        ctx = chat_ctx.copy()
        if user_msg is not None and (facts := self._prefetched_facts(user_msg.text_content or "")):
            ctx.add_message(role="system", content=facts)
        # Last, so it outweighs long facts and earlier turns: English turns drifted into Hinglish when the
        # facts came after it (step 10 text check).
        ctx.add_message(
            role="system", content=turn_instructions(self.lang, include_intro=self._caller_turns == 1)
        )
        async for chunk in Agent.default.llm_node(self, ctx, tools, model_settings):
            yield chunk

    def _prefetched_facts(self, transcript: str) -> str | None:
        """find_bike output for models named in the transcript, so most product questions need one LLM
        call instead of two (step 6b). Same data and rules as the tool; the tool remains for anything else."""
        matches = self.catalog.index.matches(transcript)
        if not matches or len(matches) > 2:
            return None
        facts = []
        for alias, bike_id in matches:
            fact = {"found": True, **bike_summary(self.catalog.models[bike_id], self.lang)}
            if similar := self.catalog.index.similar(alias):
                fact["similar_models"] = [self.catalog.models[i]["name"] for i in similar]
                fact["similar_note"] = "The caller used a family name: ask which of these they mean first."
            facts.append(fact)
        return (
            "find_bike results for the model(s) the caller named, already fetched; answer from these "
            f"without calling find_bike for them: {facts}"
        )

    # All speech cleanup happens here, streaming (CLAUDE.md rule 3).
    async def tts_node(self, text: AsyncIterable[str], model_settings: ModelSettings):
        # Preemptive TTS can call this before llm_node has decided the turn's language. The first text chunk
        # only exists after llm_node ran, so wait for it, then set the TTS language, then open the stream.
        chunks = aiter(text)
        try:
            first = await anext(chunks)
        except StopAsyncIteration:
            return
        lang, stats, spoken = self.lang, TransformStats(), []
        if self._tts is not None and self._tts_lang != lang:
            self._tts.update_options(target_language_code=lang)
            self._tts_lang = lang

        async def rest() -> AsyncIterable[str]:
            yield first
            async for chunk in chunks:
                yield chunk

        async def cleaned() -> AsyncIterable[str]:
            async for piece in clean_text_stream(rest(), lang, stats):
                spoken.append(piece)
                yield piece

        async for frame in Agent.default.tts_node(self, cleaned(), model_settings):
            yield frame
        mismatch = script_mismatch(lang, "".join(spoken))
        if mismatch:
            stats.violations.append(mismatch)
        if stats.violations:
            logger.warning("guardrail violations: %s", stats.violations)
        if self._metrics:
            self._metrics.record_reply(lang, stats)

    @function_tool
    async def find_bike(self, query: str) -> dict[str, Any]:
        """Look up one Honda two-wheeler: ex-showroom and on-road (Delhi) prices with breakdown, variants,
        mileage or range, specs, features and pitch.

        Args:
            query: The model name as the caller said it, e.g. "Activa 125", "shine", "H'ness".
        """
        result = await self.catalog.find(query)
        if not result.bike_ids:
            return {
                "found": False,
                "note": (
                    "No Honda model by that name. If it's another brand's model, say nothing about it and "
                    "call find_bike for the closest Honda (commuter: Shine 100, scooter: Activa 125, 350cc: "
                    "CB350 H'ness). Otherwise say the showroom will check and offer a callback."
                ),
            }
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
            return showroom_status(day_ref, time_24h, now_ist())
        except ValueError as e:
            return {"error": str(e), "note": "Ask the caller which day (and time) they mean."}
