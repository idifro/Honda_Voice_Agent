"""db/calls.py against the real database (migration 005): a call row, per-turn signals, the merged profile
and the outcome. Everything runs in a transaction that is rolled back."""

import pytest

from honda_voice.db import bookings, calls
from honda_voice.db.pool import connect
from honda_voice.decisions.base import Answer
from honda_voice.decisions.profile import CallerProfile
from honda_voice.domain.lead_score import score

pytestmark = pytest.mark.integration


@pytest.fixture
async def tx():
    conn = await connect()
    t = conn.transaction()
    await t.start()
    try:
        yield conn
    finally:
        await t.rollback()
        await conn.close()


async def test_call_signals_profile_and_outcome(tx):
    call_id = await calls.start(tx, "t-calls-room")
    assert await calls.start(tx, "t-calls-room") == call_id  # re-entrant for the same room
    profile = CallerProfile()
    turn1 = {
        "rider": Answer("parent"),
        "priorities": Answer(["ease", "mileage"]),
        "purchase_intent": Answer(3),
    }
    profile.merge(turn1)
    profile.add_models(["activa-110"])
    await calls.save_turn(tx, call_id, 1, turn1, "gpt-5.4-nano", 1200, profile.as_row(), 3)
    turn2 = {"budget_inr": Answer(90_000), "purchase_intent": Answer(4)}
    profile.merge(turn2)
    await calls.save_turn(tx, call_id, 2, turn2, "gpt-5.4-nano", 1100, profile.as_row(), 4)

    signals = await tx.fetch(
        "select turn_index, question, answer from public.call_signals where call_id = $1 order by id", call_id
    )
    assert [(s["turn_index"], s["question"]) for s in signals][:2] == [(1, "rider"), (1, "priorities")]
    assert signals[1]["answer"] == '["ease", "mileage"]'
    row = await tx.fetchrow("select * from public.caller_profiles where call_id = $1", call_id)
    assert row["rider"] == "parent" and row["budget_band"] == "80k_1_2l"
    assert row["priorities"] == ["ease", "mileage"] and row["models_discussed"] == ["activa-110"]

    await calls.finish(tx, call_id, "t-calls-room", ["ta-IN", "en-IN"], calls.outcome({"lead"}, 2))
    c = await tx.fetchrow("select * from public.calls where id = $1", call_id)
    assert c["outcome"] == "lead" and c["languages"] == ["ta-IN", "en-IN"] and c["max_purchase_intent"] == 4
    assert c["ended_at"] is not None


async def test_customer_upsert_keeps_consent_and_name(tx):
    first = await bookings.upsert_customer(tx, "9000054545", "Meena", "ta-IN", callback_consent=True)
    again = await bookings.upsert_customer(tx, "9000054545", None, "en-IN", callback_consent=False)
    assert first == again  # one row per number: the customer list
    row = await tx.fetchrow("select * from public.customers where id = $1::uuid", first)
    assert row["name"] == "Meena" and row["preferred_language"] == "en-IN"
    assert row["callback_consent"] and row["consent_text_version"] == bookings.CONSENT_TEXT_VERSION


async def test_finish_links_bookings_scores_leads_and_sets_customer(tx):
    call_id = await calls.start(tx, "t-finish-room")
    slot = await tx.fetchval("select id from public.slots order by starts_at desc limit 1")
    booking = await tx.fetchval(
        "insert into public.bookings (reference, slot_id, customer_name, customer_phone, language, "
        "room_name, licence_confirmed) "
        "values ('HND-TST01', $1, 'Arun', '9000054646', 'hi-IN', 't-finish-room', true) "
        "returning id",
        slot,
    )
    lead_customer = await bookings.upsert_customer(tx, "9000054747", "Arun", "hi-IN", callback_consent=True)
    lead = await bookings.capture_lead(
        tx, "caller_requested", "hi-IN", "Arun", "9000054747", "activa-125",
        interest_bike_id="activa-125", call_id=call_id, customer_id=lead_customer,
    )  # fmt: skip
    p = CallerProfile(max_purchase_intent=5, timeline="this_week", payment_interest=["emi"])
    await calls.finish(
        tx, call_id, "t-finish-room", ["hi-IN"], "booked",
        summary="Wants an Activa 125 this week; asked about EMI.", lead=score(p, booked=True),
    )  # fmt: skip
    b = await tx.fetchrow("select call_id, customer_id from public.bookings where id = $1", booking)
    assert b["call_id"] == call_id and b["customer_id"] is not None
    consent = await tx.fetchval(
        "select callback_consent from public.customers where id = $1", b["customer_id"]
    )
    assert consent is False  # a booking is not a callback consent
    row = await tx.fetchrow("select score, score_value, next_action from public.leads where id = $1", lead)
    assert row["score"] == "hot" and row["score_value"] >= 70 and row["next_action"]
    c = await tx.fetchrow("select summary, customer_id, outcome from public.calls where id = $1", call_id)
    assert (
        c["outcome"] == "booked"
        and c["summary"].startswith("Wants")
        and str(c["customer_id"]) == lead_customer
    )
