"""Offline tests for SalesAgent logic that doesn't need the LLM or audio."""

from honda_voice.agent.call_state import CallState
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


def sales(catalog, lang: str, tts=None) -> SalesAgent:
    return SalesAgent(CallState(catalog=catalog, lang=lang, supported=ALL, tts=tts), greet=False)


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
    agent = sales(catalog, "ta-IN", tts)
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
    agent = sales(catalog, "hi-IN")
    found = await agent.find_bike("Shine 100")
    assert found["found"] and found["variants"][0]["spoken_price"] == "lagbhag 65 hazaar 6 sau rupaye"
    agent.lang = "te-IN"
    assert (await agent.find_bike("shine 100"))["variants"][0]["spoken_price"] == "సుమారు 65,600 రూపాయలు"
    rival = await agent.find_bike("Bajaj Pulsar")
    assert rival["found"] is False and rival["closest_honda"]["bike_id"] == "sp-160"
    assert "nothing at all about the other product" in rival["note"]
    assert "callback" in (await agent.find_bike("Zorblax 9000"))["note"]


def test_prefetch_pivots_from_a_competitor(catalog):
    facts = sales(catalog, "en-IN")._prefetched_facts("How much does the Royal Enfield Classic 350 cost?")
    assert "another brand" in facts and "cb350-hness" in facts


async def test_check_showroom_open_tool(catalog):
    agent = sales(catalog, "en-IN")
    out = await agent.check_showroom_open("tomorrow", "17:00")
    assert out["open_at_time_asked"] is True and out["date"] > now_ist().date().isoformat()
    bad = await agent.check_showroom_open("next month")
    assert "error" in bad


def test_prefetched_facts_in_session_language(catalog):
    agent = sales(catalog, "ta-IN")
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
    agent = sales(catalog, "ta-IN", tts)

    async def llm_text():
        agent.lang = "en-IN"  # llm_node decides after tts_node has already started waiting
        yield "The Shine 100 gives 77 kilometre per litre. "

    _ = [f async for f in agent.tts_node(llm_text(), None)]
    assert tts.calls == ["en-IN"] and opened_with == ["en-IN"]


def test_prefetch_asks_which_model_for_bare_family_names(catalog):
    agent = sales(catalog, "en-IN")
    bare = agent._prefetched_facts("How much is the activa?")
    assert "similar_models" in bare and "Honda Activa 125" in bare
    assert "similar_models" not in agent._prefetched_facts("How much is the Activa 125?")


def test_turn_context_out_of_scope_and_competitor_notes(catalog):
    agent = sales(catalog, "hi-IN")
    ctx = agent._turn_context("Meri Activa ki servicing kab karwani chahiye?")
    assert len(ctx) == 1 and "out of scope" in ctx[0] and "similar_models" not in ctx[0]  # no "which Activa?"
    both = agent._turn_context("Is the TVS Jupiter better than the Activa 125?")[0]
    assert "activa-125" in both and "another brand" in both


def test_caller_turns_ignore_a_discarded_preemptive_draft(catalog):
    class Item:
        def __init__(self, id):
            self.id = id

    class Ctx:
        def __init__(self, *ids):
            self.items = [Item(i) for i in ids]

    agent = sales(catalog, "en-IN")
    agent._count_caller_turn("u1", Ctx())  # first turn
    agent._count_caller_turn("draft", Ctx("u1"))  # turn 2, preemptive draft
    agent._count_caller_turn("u2", Ctx("u1"))  # transcript changed: the draft never reached history
    agent._count_caller_turn("u3", Ctx("u1", "u2"))  # turn 3
    assert agent.state.caller_turns == 3


def test_prefetched_comparison_leaves_out_unasked_prices(catalog):
    agent = sales(catalog, "en-IN")
    plain = agent._prefetched_facts("Compare the Unicorn and the SP160")
    assert "price_min_spoken" not in plain and "on_road" not in plain and "don't mention prices" in plain
    asked = agent._prefetched_facts("Compare the price of the Unicorn and the SP160")
    assert "price_min_spoken" in asked


async def test_call_limit_says_goodbye_in_session_language_then_ends(catalog):
    from honda_voice.agent.prompts import CALL_LIMIT_LINE
    from honda_voice.agent.worker import end_call_after

    events = []

    class Handle:
        async def wait_for_playout(self):
            events.append("played")

    class Session:
        def say(self, text, allow_interruptions):
            events.append(("say", text, allow_interruptions))
            return Handle()

    class Ctx:
        async def delete_room(self):
            events.append("room deleted")

        def shutdown(self, reason):
            events.append(("shutdown", reason))

    state = CallState(catalog=catalog, lang="kn-IN", supported=ALL)
    await end_call_after(0, Session(), Ctx(), state)
    assert events == [
        ("say", CALL_LIMIT_LINE["kn-IN"], False),
        "played",
        "room deleted",
        ("shutdown", "max_call_duration"),
    ]


def test_log_file_keeps_info_not_debug(tmp_path):
    import json
    import logging

    from honda_voice.agent.worker import add_log_file

    path = tmp_path / "worker.log"
    handler = add_log_file(str(path))
    log = logging.getLogger("honda_voice.test")
    log.setLevel(logging.DEBUG)
    try:
        log.debug("debug line")
        log.info("info line", extra={"room": "honda-test"})
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert [line["message"] for line in lines] == ["info line"]
    assert lines[0]["room"] == "honda-test" and lines[0]["level"] == "INFO"
    assert add_log_file("") is None


def test_fixed_intro_only_for_a_bare_language_pick(catalog):
    from honda_voice.agent.prompts import INTRO

    agent = sales(catalog, "ta-IN")
    agent.state.caller_turns = 1
    assert agent._fixed_reply("தமிழ்") == INTRO["ta-IN"]
    agent.lang = "en-IN"
    assert agent._fixed_reply("English please") == INTRO["en-IN"]
    for asked in ("English, what is the price of the Activa 125", "Tamil la Activa", "Hindi EMI kitna"):
        assert agent._fixed_reply(asked) is None  # a real question: the LLM answers it
    assert agent._fixed_reply("hello") is None  # no language picked: the LLM gives the intro itself
    agent.state.caller_turns = 2
    assert agent._fixed_reply("English") is None  # only on the first caller turn


async def test_fixed_intro_skips_the_llm(catalog, monkeypatch):
    from livekit.agents import Agent, llm

    from honda_voice.agent.prompts import INTRO

    async def no_llm(*args, **kwargs):
        raise AssertionError("the LLM must not be called")
        yield

    monkeypatch.setattr(Agent.default, "llm_node", no_llm)
    agent = sales(catalog, "ta-IN")
    ctx = llm.ChatContext()
    ctx.add_message(role="user", content="English")
    out = [c async for c in agent.llm_node(ctx, [], None)]
    assert out == [INTRO["en-IN"]] and agent.lang == "en-IN"


def test_build_llm_fallback_only_when_configured():
    from livekit.agents.llm import FallbackAdapter

    from honda_voice.config import Settings, build_llm, llm_conn_options

    base = dict(
        azure_openai_endpoint="https://example.openai.azure.com",
        azure_openai_api_key="test-key",
        azure_openai_deployment="primary",
        llm_model="gpt-5.4-mini",
        azure_openai_fallback_deployment="",  # explicit: the developer's .env may configure one
    )
    assert not isinstance(build_llm(Settings(**base)), FallbackAdapter)
    fb = build_llm(
        Settings(**{**base, "azure_openai_fallback_deployment": "backup"}, llm_attempt_timeout_s=3)
    )
    assert isinstance(fb, FallbackAdapter) and fb._attempt_timeout == 3 and len(fb._llm_instances) == 2
    opts = llm_conn_options(Settings(**base, llm_attempt_timeout_s=4, llm_max_retry=1))
    assert (opts.timeout, opts.max_retry) == (4, 2)


def test_call_load_caps_concurrent_calls(monkeypatch):
    from honda_voice.agent import worker

    class Server:
        active_jobs = [object(), object()]

    monkeypatch.setattr(worker.get_settings(), "max_concurrent_calls", 2)
    assert worker.call_load(Server()) == 1.0  # full: LiveKit stops dispatching here


def test_discarded_draft_language_decision_is_undone(catalog):
    class Item:
        def __init__(self, id):
            self.id = id

    class Ctx:
        def __init__(self, *ids):
            self.items = [Item(i) for i in ids]

    agent = sales(catalog, "ta-IN")
    agent._count_caller_turn("u1", Ctx())
    agent._update_language("தமிழ்ல சொல்லுங்க")
    agent._count_caller_turn("draft", Ctx("u1"))  # turn 2 starts in Tamil
    agent.note_detected_language("en-IN")
    agent._update_language("what is the price")  # the draft looked English
    assert agent.lang == "en-IN"
    agent._count_caller_turn("final", Ctx("u1"))  # transcript changed: the draft is discarded
    assert agent.lang == "ta-IN" and agent.state.caller_turns == 2  # back to the turn's start


def test_reply_metrics_filed_under_the_turn_the_reply_started_in(tmp_path):
    from types import SimpleNamespace

    from honda_voice.agent.metrics import TurnMetrics
    from honda_voice.agent.text_transforms import TransformStats

    m = TurnMetrics(room="r", log_path=tmp_path / "t.jsonl")
    m._on_user_state(SimpleNamespace(old_state="speaking", new_state="listening", created_at=1.0))  # turn 1
    rec = m.start_reply("ta-IN")
    m._on_user_state(SimpleNamespace(old_state="speaking", new_state="listening", created_at=5.0))  # turn 2
    m.finish_reply(rec, TransformStats(words=12))  # turn 1's reply ends after turn 2 began
    m.flush()
    import json

    turns = [json.loads(line) for line in (tmp_path / "t.jsonl").read_text().splitlines()]
    assert turns[0]["turn"] == 1 and turns[0]["reply_lang"] == "ta-IN" and turns[0]["words"] == 12
    assert "reply_lang" not in turns[1] and "words" not in turns[1]
