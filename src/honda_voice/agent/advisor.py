"""Sales advisor runtime (SPEC §20.1): after each caller turn, ask the signal provider in the background,
merge the answers into the caller profile and save them. Nothing here is awaited on the reply path: a slow or
failed signal changes nothing and the next turn asks again. In mode "on" (step 7.3) the profile also yields
one suggested move per reply (domain/advisor.py), which SalesAgent adds to its turn context; "shadow" records
only."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from honda_voice.agent.call_state import CallState
from honda_voice.db import calls
from honda_voice.decisions.base import SignalContext, SignalProvider
from honda_voice.decisions.profile import CallerProfile
from honda_voice.decisions.summary import scrub
from honda_voice.domain.advisor import CoachState, next_move
from honda_voice.domain.lead_score import score

logger = logging.getLogger("honda_voice.advisor")
CONTEXT_MESSAGES = 8  # the last 4 exchanges
SUMMARY_MESSAGES = 40  # the end of a long call is what matters for the callback
Summarizer = Callable[[list[tuple[str, str]]], Awaitable[str]]


class Advisor:
    def __init__(
        self,
        state: CallState,
        provider: SignalProvider,
        *,
        timeout_s: float = 8.0,
        mode: str = "shadow",
        summarizer: Summarizer | None = None,
    ) -> None:
        self.state = state
        self.mode = mode
        self.coach = CoachState()
        self.summarizer = summarizer
        self._history: list[Any] = []
        self._coach_turn: int | None = None
        self._coach_line: str | None = None
        self.provider = provider
        self.timeout_s = timeout_s
        self.profile = CallerProfile()
        self.turns = 0
        self.languages: list[str] = []
        self._tasks: set[asyncio.Task] = set()
        self._call: asyncio.Task | None = None

    def start(self) -> None:
        """Create the call's row in the background (no DB trip before the greeting)."""
        if self.state.pool is not None and self.state.room:
            self._call = asyncio.create_task(self._create_call())

    async def _create_call(self) -> int | None:
        try:
            async with self.state.pool.acquire() as conn:
                self.state.call_id = await calls.start(conn, self.state.room)
        except Exception:  # analytics only: never affects the call
            logger.warning("advisor: could not create the call row", exc_info=True)
        return self.state.call_id

    def on_item(self, item: Any, history: list[Any], active_agent: str = "sales") -> None:
        """Wire to the session's conversation_item_added. Only committed caller messages count (a discarded
        preemptive draft never reaches the history), so each caller turn is asked once."""
        if getattr(item, "role", None) != "user" or not (text := (item.text_content or "").strip()):
            return
        self.turns += 1
        self._history = history
        if self.state.lang not in self.languages:
            self.languages.append(self.state.lang)
        self.profile.add_models(self.state.catalog.mentioned(text))
        ctx = SignalContext(
            turns=recent_turns(history, item), active_agent=active_agent, language=self.state.lang
        )
        task = asyncio.create_task(self._ask(self.turns, ctx))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _ask(self, turn: int, ctx: SignalContext) -> None:
        try:
            result = await asyncio.wait_for(self.provider.ask(ctx), self.timeout_s)
        except Exception as e:  # timeout, HTTP error, bad output: skip this turn
            logger.warning("advisor: no signals for turn %d (%s: %s)", turn, type(e).__name__, e)
            return
        changed = self.profile.merge(result.answers)
        logger.info(
            "advisor: turn %d signals from %s in %d ms; profile changed: %s",
            turn, result.provider, result.latency_ms, ", ".join(changed) or "nothing",
        )  # fmt: skip
        call_id = await self._call if self._call is not None else None
        if call_id is None:
            return
        try:
            async with self.state.pool.acquire() as conn:
                await calls.save_turn(
                    conn, call_id, turn, result.answers, result.provider, result.latency_ms,
                    self.profile.as_row(), self.profile.max_purchase_intent,
                )  # fmt: skip
        except Exception:
            logger.warning("advisor: could not save turn %d", turn, exc_info=True)

    def coach_line(self, turn: int, *, booking_active: bool = False) -> str | None:
        """The suggested move for this caller turn (mode "on" only), from the profile as of the earlier turns.
        Computed once per turn: the LLM steps after a tool call get the same line, not the next move."""
        if self.mode != "on":
            return None
        if self._coach_turn != turn:
            move = next_move(self.profile, self.coach, booking_active=booking_active)
            if move is not None:
                logger.info("advisor: turn %d move %s", turn, move.kind)
            self._coach_turn, self._coach_line = turn, move.line() if move else None
        return self._coach_line

    async def settle(self) -> None:
        """Wait for in-flight signals (evals: the answers arrive before the next turn, every time)."""
        if self._tasks:
            await asyncio.wait(set(self._tasks))

    async def close(self, wait_s: float = 5.0) -> None:
        """At call end (the caller has gone): let in-flight signals finish (briefly), then write the summary,
        score this call's leads, link bookings and leads to the customer list, and close the call row."""
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=wait_s)
        call_id = await self._call if self._call is not None else None
        if call_id is None:
            return
        booked = bool(self.state.booking.references)
        outcomes = set(self.state.outcomes) | ({"booked"} if booked else set())
        summary = await self._summary()
        model = (self.profile.models_suggested or self.profile.models_discussed or [None])[-1]
        name = self.state.catalog.models.get(model, {}).get("name") if model else None
        lead = score(self.profile, booked=booked, model_name=name)
        logger.info("advisor: call scored %d (%s): %s", lead.value, lead.band, lead.next_action)
        try:
            async with self.state.pool.acquire() as conn:
                await calls.finish(
                    conn, call_id, self.state.room, self.languages, calls.outcome(outcomes, self.turns),
                    summary=summary, lead=lead,
                )  # fmt: skip
        except Exception:
            logger.warning("advisor: could not close the call row", exc_info=True)

    async def _summary(self) -> str | None:
        if self.summarizer is None or self.turns == 0:
            return None
        turns = [(r, t) for r, t in all_turns(self._history)][-SUMMARY_MESSAGES:]
        try:
            text = await asyncio.wait_for(self.summarizer(turns), 20)
        except Exception as e:
            logger.warning("advisor: no call summary (%s: %s)", type(e).__name__, e)
            return None
        return scrub(text, names=self._names()) or None

    def _names(self) -> list[str]:
        draft = self.state.booking
        names = [getattr(draft.pending, "name", None)] if getattr(draft, "pending", None) else []
        return [n for n in names if n]


def all_turns(history: list[Any]) -> list[tuple[str, str]]:
    out = []
    for it in history:
        role = getattr(it, "role", None)
        if role in ("user", "assistant") and (text := (getattr(it, "text_content", None) or "").strip()):
            out.append(("caller" if role == "user" else "agent", text))
    return out


def recent_turns(history: list[Any], latest: Any) -> list[tuple[str, str]]:
    """The last few caller/agent messages, oldest first, ending with the latest caller message."""
    turns: list[tuple[str, str]] = []
    for it in history:
        role = getattr(it, "role", None)
        if it is latest or role not in ("user", "assistant"):
            continue
        if text := (getattr(it, "text_content", None) or "").strip():
            turns.append(("caller" if role == "user" else "agent", text))
    turns = turns[-(CONTEXT_MESSAGES - 1) :]
    turns.append(("caller", latest.text_content.strip()))
    return turns


def build_provider(settings: Any) -> SignalProvider:
    """nano (default) or Laya, from SIGNAL_PROVIDER (SPEC §20, §23)."""
    if settings.signal_provider == "laya":
        from honda_voice.decisions.laya_client import LayaSignals

        return LayaSignals(settings.laya_base_url, settings.laya_model)
    from honda_voice.decisions.nano_signals import NanoSignals

    client, deployment = _nano_client(settings)
    return NanoSignals(client, deployment, reasoning_effort=settings.signal_reasoning_effort or None)


def build_summarizer(settings: Any) -> Summarizer:
    """The after-call summary on the same nano deployment (SPEC §20.5)."""
    from honda_voice.decisions.summary import summarize

    client, deployment = _nano_client(settings)

    async def run(turns: list[tuple[str, str]]) -> str:
        return await summarize(
            client, deployment, turns, reasoning_effort=settings.signal_reasoning_effort or None
        )

    return run


def _nano_client(settings: Any) -> tuple[Any, str]:
    from openai import AsyncAzureOpenAI

    deployment = settings.signal_deployment or settings.azure_openai_fallback_deployment
    if not deployment:
        raise ValueError("SIGNAL_DEPLOYMENT or AZURE_OPENAI_FALLBACK_DEPLOYMENT must be set for nano signals")
    client = AsyncAzureOpenAI(
        azure_endpoint=settings.azure_openai_fallback_endpoint or settings.azure_openai_endpoint,
        api_key=settings.azure_openai_fallback_api_key.get_secret_value()
        or settings.azure_openai_api_key.get_secret_value(),
        api_version=settings.azure_openai_api_version,
        max_retries=0,  # one attempt: a late signal is dropped, the next turn asks again
    )
    return client, deployment
