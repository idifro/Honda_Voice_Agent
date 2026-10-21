"""Leads and the customer list, step 7.4 (SPEC §20.5): callback consent and read-back, score, summary."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

from honda_voice.agent.call_state import CallState
from honda_voice.agent.prompts import LEAD_CONSENT
from honda_voice.agent.sales_agent import SalesAgent
from honda_voice.decisions.profile import CallerProfile
from honda_voice.decisions.summary import scrub
from honda_voice.domain.lead_score import score


class FakeConn:
    def __init__(self, log):
        self.log = log

    @asynccontextmanager
    async def transaction(self):
        yield

    async def fetchval(self, sql, *args):
        self.log.append((sql.split()[2], args))  # e.g. "public.customers"
        return "00000000-0000-0000-0000-000000000001" if "customers" in sql else 7


class FakePool:
    def __init__(self):
        self.log = []

    @asynccontextmanager
    async def acquire(self):
        yield FakeConn(self.log)


def agent(catalog, lang="en-IN"):
    st = CallState(catalog=catalog, lang=lang, supported=["en-IN", "hi-IN"], pool=FakePool())
    st.caller_turns = 3
    return SalesAgent(st, greet=False), st


async def test_callback_needs_read_back_and_a_later_yes(catalog):
    a, st = agent(catalog)
    first = await a.capture_lead("caller_requested", name="Ravi", phone="90000 16161", interest="Activa 125")
    assert first["saved"] is False and first["say"] == LEAD_CONSENT["en-IN"].format(
        phone="9 0 0 0 0, 1 6 1 6 1"
    )
    same_turn = await a.capture_lead("caller_requested", "Ravi", "9000016161", caller_confirmed=True)
    assert same_turn["saved"] is False and st.pool.log == []  # a yes in the read-back turn isn't a yes
    st.caller_turns += 1
    other = await a.capture_lead("caller_requested", "Ravi", "9000016162", caller_confirmed=True)
    assert other["saved"] is False and st.pool.log == []  # a different number needs its own read-back
    st.caller_turns += 1
    saved = await a.capture_lead("caller_requested", "Ravi", "9000016162", caller_confirmed=True)
    assert saved["saved"] is True and "lead" in st.outcomes and st.lead_pending is None
    tables = [t for t, _ in st.pool.log]
    assert tables == ["public.customers", "public.leads"]  # consent recorded with the customer
    assert st.pool.log[0][1][3] is True  # callback_consent


async def test_no_number_no_consent_question(catalog):
    a, st = agent(catalog)
    asked = await a.capture_lead("caller_requested", name="Ravi")
    assert asked["saved"] is False and "say" not in asked  # ask for the number first


def test_consent_line_every_language_has_the_number():
    assert set(LEAD_CONSENT) == {"hi-IN", "en-IN", "ta-IN", "te-IN", "ml-IN", "kn-IN"}
    assert all("{phone}" in line and "Honda" in line for line in LEAD_CONSENT.values())


def test_consent_line_is_spoken_from_code(catalog):
    a, _ = agent(catalog)
    out = SimpleNamespace(
        type="function_call_output", name="capture_lead", is_error=False,
        output=repr({"saved": False, "say": "Just to confirm: …", "note": "…"}),
    )  # fmt: skip
    ctx = SimpleNamespace(items=[SimpleNamespace(type="message"), SimpleNamespace(type="function_call"), out])
    assert a._fixed_tool_reply(ctx) == "Just to confirm: …"


def profile(**kw):
    p = CallerProfile()
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def test_lead_score_bands_and_actions():
    hot = score(profile(max_purchase_intent=5, timeline="this_week", models_discussed=["activa-125"],
                        payment_interest=["emi"]), booked=False, model_name="Honda Activa 125")  # fmt: skip
    assert (
        hot.band == "hot" and hot.value >= 70 and "EMI" in hot.next_action and "Activa 125" in hot.next_action
    )
    warm = score(
        profile(max_purchase_intent=3, timeline="1_3_months", models_discussed=["shine-100"]), booked=False
    )
    assert warm.band == "warm"
    cold = score(profile(max_purchase_intent=1, timeline="later_or_browsing"), booked=False)
    assert cold.band == "cold" and cold.value < 40
    booked = score(
        profile(max_purchase_intent=3, models_discussed=["sp-125"]), booked=True, model_name="SP125"
    )
    assert (
        booked.value > score(profile(max_purchase_intent=3, models_discussed=["sp-125"]), booked=False).value
    )
    assert "test ride" in booked.next_action
    angry = score(profile(frustration=5, wants_human=True), booked=False)
    assert "person" in angry.next_action


def test_summary_scrubs_numbers_codes_and_names():
    text = "Ravi wants an Activa 125 for his mother; call 90000 16161. Booked HND-7K3Q9 tomorrow."
    out = scrub(text, names=["Ravi"])
    assert "16161" not in out and "7K3Q9" not in out and "Ravi" not in out
    assert "Activa 125" in out  # a model number is not a phone number


async def test_summarize_sends_the_transcript_and_returns_text():
    from honda_voice.decisions.summary import SYSTEM, summarize

    seen = {}

    async def create(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=" Wants EMI. "))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    out = await summarize(client, "voice-gpt-5.4-nano", [("caller", "EMI?"), ("agent", "Example…")],
                          reasoning_effort="minimal")  # fmt: skip
    assert out == "Wants EMI." and seen["model"] == "voice-gpt-5.4-nano"
    assert seen["messages"][0]["content"] == SYSTEM and "the caller" in SYSTEM
    assert seen["messages"][1]["content"] == "Caller: EMI?\nAssistant: Example…"
