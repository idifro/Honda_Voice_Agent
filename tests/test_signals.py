"""Caller signals (SPEC §20, step 7.2): questions, provider parsing, profile merge, background runtime."""

import asyncio
import json
from types import SimpleNamespace

from honda_voice.agent.advisor import Advisor, recent_turns
from honda_voice.agent.call_state import CallState
from honda_voice.db.calls import outcome
from honda_voice.decisions import laya_client, nano_signals
from honda_voice.decisions.base import Answer, SignalContext, SignalResult
from honda_voice.decisions.profile import CallerProfile
from honda_voice.decisions.questions import QUESTIONS, UNKNOWN, budget_band, json_schema


def msg(role, text):
    return SimpleNamespace(role=role, text_content=text)


def test_schema_requires_every_question_and_allows_unknown():
    schema = json_schema()
    assert set(schema["required"]) == {q.key for q in QUESTIONS} and not schema["additionalProperties"]
    for q in QUESTIONS:
        if q.kind == "choice":
            assert UNKNOWN in q.options or "none" in q.options, q.key
    assert schema["properties"]["frustration"]["type"] == ["integer", "null"]


def test_nano_parse_keeps_only_valid_values():
    raw = {
        "intent": "recommendation", "use_case": "teleport", "rider": "parent",
        "priorities": ["ease", "mileage", "speedboat"], "frustration": 9, "purchase_intent": 3,
        "wants_human": False, "made_up": "x",
    }  # fmt: skip
    out = nano_signals.parse(json.dumps(raw))
    assert out["intent"].value == "recommendation" and out["rider"].value == "parent"
    assert "use_case" not in out and "frustration" not in out and "made_up" not in out
    assert out["priorities"].value == ["ease", "mileage"] and out["purchase_intent"].value == 3


async def test_nano_provider_sends_strict_schema():
    seen = {}

    async def create(**kwargs):
        seen.update(kwargs)
        content = json.dumps({"intent": "price", "rider": "self"})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    provider = nano_signals.NanoSignals(client, "voice-gpt-5.4-nano")
    result = await provider.ask(SignalContext([("caller", "Activa 125 price?")]))
    assert result.provider == "gpt-5.4-nano" and result.answers["intent"].value == "price"
    assert seen["response_format"]["json_schema"]["strict"] is True
    assert seen["reasoning_effort"] == "minimal"
    assert seen["messages"][1]["content"] == "Latest caller message: Activa 125 price?"


def test_laya_questions_and_parse():
    qs = laya_client.laya_questions()
    assert qs["intent"]["type"] == "choice" and qs["priorities:ease"]["type"] == "noul"
    out = laya_client.parse({
        "intent": {"choice": "recommendation", "confidence": 0.7},
        "rider": {"choice": "parent", "confidence": 0.4},
        "priorities:ease": {"noul": 0.8}, "priorities:mileage": {"noul": 0.2},
        "purchase_intent": {"score": 3.6}, "wants_human": {"noul": 0.9},
    })  # fmt: skip
    assert out["intent"] == Answer("recommendation", 0.7) and out["priorities"].value == ["ease"]
    assert out["purchase_intent"].value == 4 and out["wants_human"].value is True


def test_profile_merge_rules():
    p = CallerProfile()
    changed = p.merge({
        "rider": Answer("parent"), "use_case": Answer(UNKNOWN), "budget_inr": Answer(90_000),
        "priorities": Answer(["ease", "mileage"]), "objection": Answer("none"),
        "purchase_intent": Answer(3), "frustration": Answer(1),
    })  # fmt: skip
    assert p.rider == "parent" and p.use_case is None and "budget_band" in changed
    p.merge({
        "budget_inr": Answer(150_000), "priorities": Answer(["mileage", "price"]),
        "objection": Answer("price_high"), "purchase_intent": Answer(2), "frustration": Answer(4),
    })  # fmt: skip
    assert p.budget_band == "1_2l_2l"  # a later clear answer replaces the earlier one
    assert p.priorities == ["ease", "mileage", "price"] and p.objections == ["price_high"]
    assert (
        p.max_purchase_intent == 3 and p.frustration == 4
    )  # intent keeps the highest, frustration the latest
    p.merge({"rider": Answer("child", 0.3)})  # below the confidence threshold: ignored
    assert p.rider == "parent"
    p.merge({"wants_human": Answer(True)})
    p.merge({"wants_human": Answer(False)})
    assert p.wants_human  # stays true once asked
    row = p.as_row()
    assert "frustration" not in row and row["priorities"] == ["ease", "mileage", "price"]


def test_context_text_and_recent_turns():
    history = [
        msg("system", "x"),
        msg("assistant", "Vanakkam!"),
        msg("user", "Tamil"),
        msg("assistant", "Sari"),
    ]
    latest = msg("user", "scooty for my mother")
    turns = recent_turns([*history, latest], latest)
    assert turns == [
        ("agent", "Vanakkam!"),
        ("caller", "Tamil"),
        ("agent", "Sari"),
        ("caller", "scooty for my mother"),
    ]
    text = SignalContext(turns).as_text()
    assert text.endswith("Latest caller message: scooty for my mother") and "Caller: Tamil" in text


def test_outcome_order():
    assert outcome({"lead", "booked"}, 5) == "booked" and outcome({"cancelled"}, 3) == "cancelled"
    assert outcome(set(), 4) == "info" and outcome({"lead"}, 0) == "dropped"


class FakeProvider:
    name = "fake"

    def __init__(self, answers=None, delay=0.0, fail=False):
        self.answers, self.delay, self.fail, self.calls = answers or {}, delay, fail, []

    async def ask(self, ctx):
        self.calls.append(ctx)
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("down")
        return SignalResult(self.answers, self.name, 5)


async def test_advisor_runs_in_background_per_caller_message(catalog):
    st = CallState(catalog=catalog, lang="ta-IN", supported=["ta-IN", "en-IN"])
    provider = FakeProvider({"rider": Answer("parent")}, delay=0.05)
    adv = Advisor(st, provider)
    user = msg("user", "Activa 125 for my mother")
    adv.on_item(msg("assistant", "Vanakkam"), [])  # agent messages don't count
    adv.on_item(user, [user], "sales")  # returns at once: the reply never waits
    assert adv.turns == 1 and adv.profile.rider is None
    await adv.close()  # waits for in-flight signals; no pool: nothing saved
    assert adv.profile.rider == "parent" and adv.profile.models_discussed == ["activa-125"]
    assert adv.languages == ["ta-IN"] and provider.calls[0].active_agent == "sales"


async def test_advisor_survives_slow_or_failing_provider(catalog):
    st = CallState(catalog=catalog, lang="en-IN", supported=["en-IN"])
    slow = Advisor(st, FakeProvider({"rider": Answer("self")}, delay=1.0), timeout_s=0.05)
    m = msg("user", "hello")
    slow.on_item(m, [m])
    await slow.close()
    assert slow.profile.rider is None  # dropped, not waited for
    broken = Advisor(st, FakeProvider(fail=True))
    broken.on_item(m, [m])
    await broken.close()
    assert broken.profile.rider is None


def test_budget_band_from_amount():
    assert [budget_band(x) for x in (None, 65_000, 80_000, 119_999, 180_000, 750_000)] == [
        None,
        "under_80k",
        "80k_1_2l",
        "80k_1_2l",
        "1_2l_2l",
        "over_2l",
    ]
    out = nano_signals.parse(json.dumps({"budget_inr": 90000, "rider": "self"}))
    assert out["budget_inr"].value == 90000
    assert "budget_inr" not in nano_signals.parse(json.dumps({"budget_inr": 5}))  # not a plausible budget
