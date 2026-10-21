"""Hold line after HOLD_AFTER_S of thinking (no music); nothing on fast turns; a started line finishes
before the reply; the caller speaking stops it."""

import asyncio

from honda_voice.agent.call_state import CallState
from honda_voice.agent.hold import HoldAudio
from honda_voice.agent.prompts import HOLD_LINE


class Handle:
    def __init__(self, log, name, length_s=0.1):
        self.log, self.name, self.stopped = log, name, False
        self._finished = asyncio.Event()
        asyncio.get_running_loop().call_later(length_s, self._finish)

    def _finish(self):
        if not self._finished.is_set():
            self._finished.set()
            self.log.append(("finished", self.name))

    def done(self):
        return self._finished.is_set()

    def stop(self):
        self.stopped = True
        self.log.append(("stop", self.name))
        self._finished.set()

    async def wait_for_playout(self):
        await self._finished.wait()


class Player:
    def __init__(self, length_s=0.1):
        self.log, self.length_s = [], length_s

    def play(self, audio, loop=False):
        assert not loop  # no music: only the line, played once
        self.log.append(("play", "line"))
        return Handle(self.log, "line", self.length_s)


def make(catalog, lang="ta-IN", after=0.05, fail=False):
    said = []

    async def synth(text, lang):
        said.append((text, lang))
        if fail:
            raise RuntimeError("tts down")
        return ["frame"]

    player = Player()
    state = CallState(catalog=catalog, lang=lang, supported=[lang])
    return HoldAudio(player, state, synth, after_s=after), player, said


async def test_slow_turn_says_the_hold_line_only(catalog):
    hold, player, said = make(catalog)
    hold.on_agent_state("thinking")
    await asyncio.sleep(0.07)
    assert said == [(HOLD_LINE["ta-IN"], "ta-IN")]
    assert player.log == [("play", "line")]  # and nothing else: no music


async def test_reply_waits_for_a_started_line_to_finish(catalog):
    hold, player, _ = make(catalog)
    hold.on_agent_state("thinking")
    await asyncio.sleep(0.07)  # the line (0.1 s) is playing when the reply's first audio is ready
    await hold.before_reply()
    assert player.log == [("play", "line"), ("finished", "line")]  # finished, never cut
    hold.on_agent_state("speaking")
    assert ("stop", "line") not in player.log


async def test_reply_stops_waiting_after_max_wait(catalog):
    hold, player, _ = make(catalog)
    player.length_s = 5.0
    hold.on_agent_state("thinking")
    await asyncio.sleep(0.07)
    await hold.before_reply(max_wait_s=0.05)
    assert player.log[-1] == ("stop", "line")


async def test_reply_ready_before_the_line_cancels_it(catalog):
    hold, player, said = make(catalog, after=0.1)
    hold.on_agent_state("thinking")
    await asyncio.sleep(0.03)
    await hold.before_reply()  # immediate: nothing playing
    await asyncio.sleep(0.15)
    assert player.log == []


async def test_caller_speaking_stops_the_line(catalog):
    hold, player, _ = make(catalog)
    player.length_s = 5.0
    hold.on_agent_state("thinking")
    await asyncio.sleep(0.07)
    hold.on_agent_state("listening")
    assert player.log[-1] == ("stop", "line")


async def test_fast_turn_plays_nothing(catalog):
    hold, player, said = make(catalog, after=0.2)
    hold.on_agent_state("thinking")
    await asyncio.sleep(0.05)
    hold.on_agent_state("speaking")
    await asyncio.sleep(0.25)
    assert player.log == [] and said == []


async def test_synthesis_failure_plays_nothing(catalog):
    hold, player, _ = make(catalog, fail=True)
    hold.on_agent_state("thinking")
    await asyncio.sleep(0.15)
    assert player.log == []
    hold.stop()


async def test_line_cached_per_language(catalog):
    hold, player, said = make(catalog)
    hold.prefetch("ta-IN")
    for _ in range(2):
        hold.on_agent_state("thinking")
        await asyncio.sleep(0.07)
        hold.on_agent_state("listening")  # the caller spoke
    assert said == [(HOLD_LINE["ta-IN"], "ta-IN")]  # synthesised once


def test_hold_lines_every_language():
    assert set(HOLD_LINE) == {"hi-IN", "en-IN", "ta-IN", "te-IN", "ml-IN", "kn-IN"}
