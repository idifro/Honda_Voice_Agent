"""Sales advisor, step 7.3 (SPEC §20.3, §20.4): ranking, next-move rules, recommend_bikes and emi_examples."""

from honda_voice.agent.advisor import Advisor
from honda_voice.agent.call_state import CallState
from honda_voice.agent.sales_agent import SalesAgent
from honda_voice.decisions.base import Answer
from honda_voice.decisions.profile import CallerProfile
from honda_voice.domain.advisor import MAX_QUESTIONS, PREFIX, CoachState, next_move
from honda_voice.domain.recommend import Need, rank


def ids(catalog, need):
    return [p.bike_id for p in rank(catalog.models, catalog.fit, need)]


def test_rank_mom_scooter_light_and_economical(catalog):
    picks = rank(catalog.models, catalog.fit, Need(rider="parent", priorities=("ease", "mileage")))
    assert picks[0].bike_id == "activa-110"
    assert "about 59.5 km per litre" in picks[0].reasons
    assert all(catalog.models[p.bike_id]["fuel"] == "petrol" or p.bike_id.startswith("qc") for p in picks)


def test_rank_only_on_sale_and_never_estimates_as_reasons(catalog):
    for need in (Need(priorities=("power",)), Need(use_case="long_rides"), Need(priorities=("mileage",))):
        for p in rank(catalog.models, catalog.fit, need):
            m = catalog.models[p.bike_id]
            assert m["status"] == "on_sale"
            if "mileage_kmpl" in m["unverified_fields"]:
                assert not any("km per litre" in r for r in p.reasons), p  # estimate: ranking only


def test_rank_budget_and_electric(catalog):
    within = rank(catalog.models, catalog.fit, Need(use_case="commute", budget_inr=90_000))
    assert within and all(not p.over_budget for p in within)
    assert all(catalog.models[p.bike_id]["price"]["min"] <= 94_500 for p in within)
    over = rank(catalog.models, catalog.fit, Need(use_case="commute", budget_inr=50_000))
    assert over and all(p.over_budget for p in over)  # nothing fits: the cheapest, marked
    ev = ids(catalog, Need(use_case="delivery", priorities=("electric",)))
    assert ev and all(catalog.models[i]["fuel"] == "electric" for i in ev)


def test_rank_needs_something():
    assert rank({}, {}, Need()) == []


def profile(**kw):
    p = CallerProfile()
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def test_move_frustration_first_then_nothing_more():
    coach = CoachState()
    p = profile(frustration=4, objections=["price_high"], intent="recommendation")
    m = next_move(p, coach)
    assert m.kind == "callback" and m.line().startswith(PREFIX) and "capture_lead" in m.text
    assert next_move(p, coach) is None  # after the callback offer, no more selling


def test_move_price_objection_and_emi_once_each():
    coach = CoachState()
    p = profile(objections=["price_high"], payment_interest=["emi"], models_discussed=["activa-125"])
    assert next_move(p, coach).kind == "price_objection"
    assert next_move(p, coach).kind == "emi"
    assert next_move(p, coach).kind != "emi"


def test_move_suggest_when_need_known_and_no_model():
    m = next_move(profile(rider="parent", priorities=["ease"]), CoachState())
    assert m.kind == "suggest" and "recommend_bikes" in m.text and "rider=parent" in m.text
    assert next_move(profile(rider="parent", models_discussed=["activa-110"]), CoachState()).kind != "suggest"


def test_move_test_ride_when_ready():
    m = next_move(profile(max_purchase_intent=4, models_discussed=["shine-100"]), CoachState())
    assert m.kind == "test_ride" and "shine-100" in m.text


def test_move_browsing_never_pushes():
    coach = CoachState()
    p = profile(timeline="later_or_browsing", max_purchase_intent=1, models_discussed=["cb350"])
    assert next_move(p, coach).kind == "browsing"
    assert next_move(p, coach) is None


def test_move_discovery_questions_capped_and_not_repeated():
    coach = CoachState()
    p = profile(intent="recommendation", models_suggested=["activa-110"])
    kinds = [m.kind for m in iter(lambda: next_move(p, coach), None)]
    asks = [k for k in kinds if k.startswith("ask_")]
    assert len(asks) == len(set(asks)) <= MAX_QUESTIONS and asks[0] == "ask_use"


def test_no_moves_during_booking():
    assert next_move(profile(frustration=5), CoachState(), booking_active=True) is None


class Quiet:
    name = "quiet"

    async def ask(self, ctx):  # never used: these tests set the profile directly
        raise AssertionError


def agent_with_advisor(catalog, mode="on", lang="en-IN"):
    st = CallState(catalog=catalog, lang=lang, supported=["en-IN", "hi-IN", "ta-IN"])
    st.advisor = Advisor(st, Quiet(), mode=mode)
    return SalesAgent(st, greet=False), st


def test_coach_line_once_per_turn_and_shadow_silent(catalog):
    agent, st = agent_with_advisor(catalog)
    st.advisor.profile.merge({"rider": Answer("parent"), "priorities": Answer(["ease"])})
    st.caller_turns = 3
    first = agent._turn_context("ok")
    assert first and first[-1].startswith(PREFIX) and "recommend_bikes" in first[-1]
    assert agent._turn_context("ok") == first  # a tool step in the same turn: same line, no new move
    shadow, st2 = agent_with_advisor(catalog, mode="shadow")
    st2.advisor.profile.merge({"rider": Answer("parent")})
    assert not any(PREFIX in c for c in shadow._turn_context("ok"))


async def test_recommend_bikes_tool_uses_profile_and_records_suggestions(catalog):
    agent, st = agent_with_advisor(catalog, lang="hi-IN")
    st.advisor.profile.merge({"rider": Answer("parent"), "priorities": Answer(["ease", "mileage"])})
    out = await agent.recommend_bikes(use_case="family_errands")
    assert out["found"] and out["picks"][0]["name"] == "Honda Activa 110"
    assert out["picks"][0]["ex_showroom_from_spoken"].endswith("rupaye")
    assert st.advisor.profile.models_suggested[0] == "activa-110"
    empty = await SalesAgent(
        CallState(catalog=catalog, lang="en-IN", supported=["en-IN"]), greet=False
    ).recommend_bikes()
    assert empty["found"] is False


async def test_emi_examples_tool_speaks_code_computed_amounts(catalog):
    agent, st = agent_with_advisor(catalog)
    out = await agent.emi_examples("Activa 125", "DLX")
    assert out["found"] and out["price_basis_spoken"] == "about 91,700 rupees"  # DLX ₹91,679
    first = out["examples"][0]
    assert first["plan"] == "Demo plan, 12 months" and first["tenure_months"] == 12
    assert first["emi_spoken"].endswith("rupees") and "showroom confirms" in out["note"]
    assert "emi" in st.advisor.profile.payment_interest
    assert (await agent.emi_examples("Splendor"))["found"] is False
