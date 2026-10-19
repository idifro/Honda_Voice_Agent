"""Hold line for slow turns: when the agent has been "thinking" (a tool, or an LLM call) for HOLD_AFTER_S, it
says a short fixed line in the caller's language ("One moment please, let me check.") and carries on; the
reply follows when it's ready. If the reply starts or the caller speaks first, the line is cut.

The line plays on a separate background audio track, not through the agent's speech queue, so it never
enters the chat history or changes turn-taking. It is synthesised once per language per call and cached.
Hold music was tried and removed after the 2026-10-16 browser test: it didn't work well on the call.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from honda_voice.agent.call_state import CallState
from honda_voice.agent.prompts import HOLD_LINE

logger = logging.getLogger("honda_voice.hold")

Synthesize = Callable[[str, str], Awaitable[list[Any]]]  # (text, lang) -> audio frames


class HoldAudio:
    def __init__(
        self,
        player: Any,  # livekit.agents.BackgroundAudioPlayer (started)
        state: CallState,
        synthesize: Synthesize,
        *,
        after_s: float = 2.0,
    ) -> None:
        self.player = player
        self.state = state
        self.synthesize = synthesize
        self.after_s = after_s
        self._task: asyncio.Task | None = None
        self._handle: Any = None
        self._filler: dict[str, asyncio.Task] = {}

    def on_agent_state(self, new_state: str) -> None:
        """Wire to the session's agent_state_changed: start the timer on "thinking", stop on anything else."""
        if new_state == "thinking":
            if self._task is None or self._task.done():
                self._task = asyncio.create_task(self._hold())
        else:
            self.stop()

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None
        if self._handle is not None:
            self._handle.stop()
            self._handle = None

    def prefetch(self, lang: str) -> None:
        """Synthesise the line for `lang` in the background (call start, and when the language changes)."""
        self._filler_frames(lang)

    def _filler_frames(self, lang: str) -> asyncio.Task:
        task = self._filler.get(lang)
        if task is None or (task.done() and (task.cancelled() or task.exception())):
            task = asyncio.create_task(self.synthesize(HOLD_LINE[lang], lang))
            self._filler[lang] = task
        return task

    async def _hold(self) -> None:
        await asyncio.sleep(self.after_s)
        lang = self.state.lang
        try:
            frames = await asyncio.shield(self._filler_frames(lang))
        except asyncio.CancelledError:
            raise
        except Exception:  # no line this time; the reply still comes
            logger.warning("hold: line synthesis failed", exc_info=True)
            return
        if frames:
            logger.info("hold: no reply after %.1f s, saying the hold line (%s)", self.after_s, lang)
            self._handle = self.player.play(_frames(frames))


async def _frames(frames: list[Any]) -> AsyncIterator[Any]:
    for frame in frames:
        yield frame
