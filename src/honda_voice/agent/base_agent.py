"""Behaviour shared by SalesAgent and BookingAgent (SPEC §4.1, §9): the per-turn language decision, the
language tag at the end of the turn context, TTS retargeting, streaming speech cleanup, and lead capture."""

import ast
import logging
from collections.abc import AsyncIterable
from typing import Any

from livekit.agents import NOT_GIVEN, Agent, ModelSettings, function_tool, llm

from honda_voice.agent.call_state import CallState
from honda_voice.agent.prompts import LEAD_CONSENT
from honda_voice.agent.text_transforms import InternalTagFilter, TransformStats, clean_text_stream
from honda_voice.db import bookings
from honda_voice.domain import phone as phone_mod
from honda_voice.domain.language import next_language, requested_language, script_mismatch, tokens

logger = logging.getLogger("honda_voice.agent")


class MultilingualAgent(Agent):
    def __init__(
        self, *, instructions: str, state: CallState, chat_ctx: llm.ChatContext | None = None
    ) -> None:
        super().__init__(instructions=instructions, chat_ctx=chat_ctx if chat_ctx is not None else NOT_GIVEN)
        self.state = state

    @property
    def catalog(self):
        return self.state.catalog

    @property
    def lang(self) -> str:
        return self.state.lang

    @lang.setter
    def lang(self, value: str) -> None:
        self.state.lang = value

    @property
    def _tts_lang(self) -> str:
        return self.state.tts_lang

    def note_detected_language(self, code: str | None) -> None:
        """Record STT's detected language for the next language decision."""
        if code:
            self.state.detected = code

    def _update_language(self, transcript: str) -> None:
        st = self.state
        new = next_language(st.lang, transcript, st.detected, None, st.supported)
        switched = new != st.lang
        if switched:
            # Why it switched, without the caller's words (privacy): explicit request, or STT detection
            # (or script) on a turn of enough words. Added after 6 switches in the 2026-10-16 browser call.
            logger.info(
                "session language %s -> %s (requested=%s detected=%s words=%d)",
                st.lang, new, requested_language(transcript, st.supported), st.detected,
                len(tokens(transcript)),
            )  # fmt: skip
            st.lang = new  # the TTS follows in tts_node, just before the reply's audio starts
            if st.on_language_switch is not None:
                st.on_language_switch(new)
        if st.metrics:
            st.metrics.record_language(st.lang, switched)
        st.detected = None

    def _count_caller_turn(self, msg_id: str, chat_ctx) -> None:
        """One count per caller turn. A preemptive draft that was discarded (its transcript changed) never
        reaches the history, so the message that replaces it is the same turn, not a new one. The booking
        guard relies on exact counts (a yes must answer the read-back directly)."""
        st = self.state
        previous = st.last_user_msg_id
        replaced_draft = previous is not None and not any(
            getattr(item, "id", None) == previous for item in chat_ctx.items
        )
        st.last_user_msg_id = msg_id
        if not replaced_draft:
            st.caller_turns += 1
            st.turn_start_lang = st.lang
        elif st.lang != st.turn_start_lang:
            # The discarded draft's language decision is undone; the final transcript decides afresh. Two
            # decisions in one turn flipped the language twice within a second (2026-10-16 browser call).
            logger.info("language from a discarded draft undone: %s -> %s", st.lang, st.turn_start_lang)
            st.lang = st.turn_start_lang

    # --- hooks for subclasses ------------------------------------------------------------------------
    def _turn_context(self, transcript: str) -> list[str]:
        """Extra system messages for this LLM call, placed before the turn instructions."""
        return []

    def _turn_instructions(self) -> str:
        raise NotImplementedError

    def _fixed_reply(self, transcript: str) -> str | None:
        """A reply decided in code and spoken without an LLM call, or None."""
        return None

    FIXED_SAY_TOOLS: tuple[str, ...] = ("capture_lead",)  # tools whose "say" is fixed text from code

    def _fixed_tool_reply(self, chat_ctx) -> str | None:
        """On the step after tool calls: a reply the tools already fixed (their "say" text), spoken without an
        LLM call; the second call only repeated it (~1 s; Phase 6 p90). Only when one such tool spoke in the
        latest tool step and no tool in that step failed."""
        outputs = []
        for item in reversed(chat_ctx.items):
            kind = getattr(item, "type", None)
            if kind == "function_call_output":
                outputs.append(item)
            elif kind != "function_call":
                break  # the tool step ends at the last message
        say = None
        for out in outputs:
            result = _parse_output(out)
            if out.is_error or result is None or "error" in result or result.get("valid") is False:
                return None  # something failed: let the LLM handle it
            if out.name in self.FIXED_SAY_TOOLS and isinstance(result.get("say"), str):
                if say is not None:
                    return None  # two fixed lines in one step: unusual, let the LLM word it
                say = result["say"]
        return say

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
        st = self.state
        if user_msg is not None and user_msg.id != st.last_user_msg_id:
            self._count_caller_turn(user_msg.id, chat_ctx)
            # Decided here, on the exact message the LLM answers: preemptive generation can start from a
            # preflight transcript before any final-transcript event (console runs 2 and 3), so event
            # timing can't be relied on. Detection comes from the latest transcript event, else the script.
            self._update_language(user_msg.text_content or "")
        transcript = (user_msg.text_content or "") if user_msg is not None else ""
        # A reply decided in code needs no LLM call (~1 s saved). Only when answering the caller's message
        # itself, never on a tool-reply step.
        answering_caller = user_msg is not None and chat_ctx.items and chat_ctx.items[-1] is user_msg
        if answering_caller and (fixed := self._fixed_reply(transcript)) is not None:
            logger.info("fixed reply from code, no LLM call")
            yield fixed
            return
        if not answering_caller and (fixed := self._fixed_tool_reply(chat_ctx)) is not None:
            logger.info("fixed tool reply spoken, no LLM call")
            yield fixed
            return
        ctx = chat_ctx.copy()
        for content in self._turn_context(transcript):
            ctx.add_message(role="system", content=content)
        # Last, so it outweighs long facts and earlier turns: English turns drifted into Hinglish when the
        # facts came after it (step 10 text check).
        ctx.add_message(role="system", content=self._turn_instructions())
        tags = InternalTagFilter()
        async for chunk in Agent.default.llm_node(self, ctx, tools, model_settings):
            if isinstance(chunk, str):
                chunk = tags.feed(chunk)
            elif isinstance(chunk, llm.ChatChunk) and chunk.delta and chunk.delta.content:
                chunk = chunk.model_copy(
                    update={
                        "delta": chunk.delta.model_copy(update={"content": tags.feed(chunk.delta.content)})
                    }
                )
            if chunk:
                yield chunk
        if rest := tags.flush():
            yield rest
        if tags.removed:
            logger.warning("LLM echoed internal lines: %s", tags.removed)

    # All speech cleanup happens here, streaming (CLAUDE.md rule 3).
    async def tts_node(self, text: AsyncIterable[str], model_settings: ModelSettings):
        # Preemptive TTS can call this before llm_node has decided the turn's language. The first text chunk
        # only exists after llm_node ran, so wait for it, then set the TTS language, then open the stream.
        chunks = aiter(text)
        try:
            first = await anext(chunks)
        except StopAsyncIteration:
            return
        st = self.state
        lang, stats, spoken = st.lang, TransformStats(), []
        if st.tts is not None and st.tts_lang != lang:
            st.tts.update_options(target_language_code=lang)
            st.tts_lang = lang
        turn_rec = st.metrics.start_reply(lang) if st.metrics else None

        async def rest() -> AsyncIterable[str]:
            yield first
            async for chunk in chunks:
                yield chunk

        async def cleaned() -> AsyncIterable[str]:
            async for piece in clean_text_stream(rest(), lang, stats):
                spoken.append(piece)
                yield piece

        first_frame = True
        async for frame in Agent.default.tts_node(self, cleaned(), model_settings):
            if first_frame and st.before_reply is not None:
                await st.before_reply()  # a started hold line finishes first; synthesis ran meanwhile
            first_frame = False
            yield frame
        mismatch = script_mismatch(lang, "".join(spoken))
        if mismatch:
            stats.violations.append(mismatch)
        if stats.violations:
            logger.warning("guardrail violations: %s", stats.violations)
        if st.metrics:
            st.metrics.finish_reply(turn_rec, stats)

    @function_tool
    async def capture_lead(
        self,
        reason: str,
        name: str | None = None,
        phone: str | None = None,
        interest: str | None = None,
        caller_confirmed: bool = False,
    ) -> dict[str, Any]:
        """Save the caller's details so the showroom calls them back. Use when booking can't complete, for an
        upcoming or pre-booking model, or when the caller asks for a callback. Ask for name and number first.
        The first call reads the number back and asks for consent (say its "say" text word for word); call
        again with the same details and caller_confirmed=true only after the caller says yes.

        Args:
            reason: One of: no_slot, phone_failed, no_licence, upcoming_model, caller_requested, out_of_scope,
                error.
            name: The caller's name, if given.
            phone: The mobile number as the caller said it, if given.
            interest: The model or topic they're interested in, if any.
            caller_confirmed: True only when the caller said yes to the read-back and callback question.
        """
        normalized = phone_mod.normalize(phone) if phone else None
        # A callback needs a number; without one only after the caller failed to give a valid one twice.
        if normalized is None and not self.state.booking.phone_tries_exhausted:
            note = "Ask for their mobile number (and name) first, then call capture_lead again."
            if phone:
                note = "That isn't a valid mobile number; ask for it again."
            return {"saved": False, "note": note}
        if self.state.pool is None:
            return {
                "saved": False,
                "note": "Saving failed: apologise and suggest calling the showroom directly.",
            }
        if interest and (ids := self.catalog.index.mentions(interest)):
            interest = ids[0]
        st = self.state
        if normalized is not None:  # consent before saving a number (SPEC §20.5): read back, then the yes
            pending = st.lead_pending
            agreed = (
                caller_confirmed
                and pending is not None
                and pending["phone"] == normalized
                and st.caller_turns > pending["turn"]
            )
            if not agreed:
                st.lead_pending = {"phone": normalized, "turn": st.caller_turns}
                return {
                    "saved": False,
                    "say": LEAD_CONSENT[self.lang].format(phone=phone_mod.speak(normalized)),
                    "note": "Say this word for word and wait. Only if the caller says yes, call capture_lead "
                    "again with the same details and caller_confirmed=true; if no, save nothing.",
                }
        async with st.pool.acquire() as conn:
            async with conn.transaction():
                customer = None
                if normalized is not None:
                    customer = await bookings.upsert_customer(
                        conn, normalized, name, self.lang, callback_consent=True
                    )
                await bookings.capture_lead(
                    conn, reason, self.lang, name=name, phone=normalized, interest=interest,
                    interest_bike_id=interest if interest in self.catalog.models else None,
                    call_id=st.call_id, customer_id=customer,
                )  # fmt: skip
        st.lead_pending = None
        self.state.outcomes.add("lead")
        logger.info(
            "lead captured: reason=%s phone=%s", reason, phone_mod.mask(normalized) if normalized else None
        )
        return {
            "saved": True,
            "note": "Say the showroom will call them back soon (the number was read back already).",
        }


def _parse_output(item) -> dict[str, Any] | None:
    """A tool output as stored in the chat history (the dict's text form), or None if it isn't one."""
    try:
        value = ast.literal_eval(item.output)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, dict) else None
