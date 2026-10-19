"""Hold line after HOLD_AFTER_S of thinking (no music); nothing on fast turns; cut when the reply starts."""

import asyncio

from honda_voice.agent.call_state import CallState
from honda_voice.agent.hold import HoldAudio
from honda_voice.agent.prompts import HOLD_LINE


class Handle:
    def __init__(self, log, name):
        self.log, self.name, self.stopped = log, name, False

    def stop(self):
        self.stopped = True
        self.log.append(("stop", self.name))

    def __await__(self):
        async def done():
            return self

        return done().__await__()


class Player:
    def __init__(self):
        self.log = []

    def play(self, audio, loop=False):
        assert not loop  # no music: only the line, played once
        self.log.append(("play", "line"))
        return Handle(self.log, "line")


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
    await asyncio.sleep(0.15)
    assert said == [(HOLD_LINE["ta-IN"], "ta-IN")]
    assert player.log == [("play", "line")]  # and nothing else: no music
    hold.on_agent_state("speaking")  # the reply starts: the line is cut if still playing
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
        await asyncio.sleep(0.12)
        hold.on_agent_state("listening")  # the caller spoke
    assert said == [(HOLD_LINE["ta-IN"], "ta-IN")]  # synthesised once


def test_hold_lines_every_language():
    assert set(HOLD_LINE) == {"hi-IN", "en-IN", "ta-IN", "te-IN", "ml-IN", "kn-IN"}
