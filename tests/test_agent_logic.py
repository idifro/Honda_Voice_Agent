"""Offline tests for SalesAgent logic that doesn't need the LLM or audio."""

from honda_voice.agent.prompts import (
    GREETING_TEXT,
    INTRO,
    OUT_OF_SCOPE,
    SALES_INSTRUCTIONS,
    turn_instructions,
)
from honda_voice.agent.sales_agent import SalesAgent
from honda_voice.domain.datetime_resolve import now_ist

ALL = ["hi-IN", "en-IN", "ta-IN", "te-IN", "ml-IN", "kn-IN"]


class FakeTTS:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def update_options(self, *, target_language_code: str) -> None:
        self.calls.append(target_language_code)


def test_every_language_has_fixed_lines(catalog):
    assert set(INTRO) == set(OUT_OF_SCOPE) == set(ALL)


def test_no_recording_consent_anywhere(catalog):
    text = " ".join([GREETING_TEXT, SALES_INSTRUCTIONS, *INTRO.values(), *OUT_OF_SCOPE.values()]).lower()
    for word in ("consent", "record", "சம்மத", "సమ్మత", "ಒಪ್ಪಿಗೆ", "സമ്മത", "sahmati"):
        assert word not in text


def test_turn_instructions_intro_only_when_asked(catalog):
    first = turn_instructions("ta-IN", include_intro=True)
    assert first.startswith("[caller_language] Tamil.")
    assert INTRO["ta-IN"] in first and OUT_OF_SCOPE["ta-IN"] in first
    assert INTRO["ta-IN"] not in turn_instructions("ta-IN", include_intro=False)


def test_language_switch_rules_in_agent(catalog):
    tts = FakeTTS()
    agent = SalesAgent(
        catalog=catalog, initial_language="ta-IN", supported_languages=ALL, tts=tts, greet=False
    )
    agent.note_detected_language("en-IN")
    agent._update_language("ok")  # short answer: stays Tamil
    assert agent.lang == "ta-IN"
    agent.note_detected_language("en-IN")
    agent._update_language("What is the mileage of the Shine")
    assert agent.lang == "en-IN"
    agent._update_language("Telugu lo cheppandi")  # explicit request, no detection needed
    assert agent.lang == "te-IN"
    agent._update_language("ಹೌದು, Activa 125 ಬೆಲೆ ಎಷ್ಟು")  # no detection yet: inferred from the script
    assert agent.lang == "kn-IN"
    assert tts.calls == []  # the TTS follows in tts_node, not here


async def test_find_bike_outputs(catalog):
    agent = SalesAgent(catalog=catalog, initial_language="hi-IN", supported_languages=ALL, greet=False)
    found = await agent.find_bike("Shine 100")
    assert found["found"] and found["variants"][0]["spoken_price"] == "lagbhag 65 hazaar 6 sau rupaye"
    agent.lang = "te-IN"
    assert (await agent.find_bike("shine 100"))["variants"][0]["spoken_price"] == "సుమారు 65,600 రూపాయలు"
    missing = await agent.find_bike("Bajaj Pulsar")
    assert missing["found"] is False and "closest Honda" in missing["note"]


async def test_check_showroom_open_tool(catalog):
    agent = SalesAgent(catalog=catalog, initial_language="en-IN", supported_languages=ALL, greet=False)
    out = await agent.check_showroom_open("tomorrow", "17:00")
    assert out["open_at_time_asked"] is True and out["date"] > now_ist().date().isoformat()
    bad = await agent.check_showroom_open("next month")
    assert "error" in bad


def test_prefetched_facts_in_session_language(catalog):
    agent = SalesAgent(catalog=catalog, initial_language="ta-IN", supported_languages=ALL, greet=False)
    facts = agent._prefetched_facts("Activa 125 விலை என்ன?")
    assert facts and "activa-125" in facts and "சுமார் 95,300 ரூபாய்" in facts
    assert agent._prefetched_facts("insurance renewal ಬಗ್ಗೆ ಹೇಳಿ") is None


async def test_tts_language_set_from_first_chunk(monkeypatch, catalog):
    """Preemptive TTS may enter tts_node before llm_node decides the language (console runs 2-3)."""
    from livekit.agents import Agent

    opened_with: list[str] = []

    async def fake_default_tts(agent, text, model_settings):
        opened_with.append(agent._tts_lang)
        async for _ in text:
            pass
        if False:
            yield

    monkeypatch.setattr(Agent.default, "tts_node", fake_default_tts)
    tts = FakeTTS()
    agent = SalesAgent(
        catalog=catalog, initial_language="ta-IN", supported_languages=ALL, tts=tts, greet=False
    )

    async def llm_text():
        agent.lang = "en-IN"  # llm_node decides after tts_node has already started waiting
        yield "The Shine 100 gives 77 kilometre per litre. "

    _ = [f async for f in agent.tts_node(llm_text(), None)]
    assert tts.calls == ["en-IN"] and opened_with == ["en-IN"]


def test_prefetch_asks_which_model_for_bare_family_names(catalog):
    agent = SalesAgent(catalog=catalog, initial_language="en-IN", supported_languages=ALL, greet=False)
    bare = agent._prefetched_facts("How much is the activa?")
    assert "similar_models" in bare and "Honda Activa 125" in bare
    assert "similar_models" not in agent._prefetched_facts("How much is the Activa 125?")
