"""BookingAgent and the handoff, offline (no LLM, no database)."""

from datetime import datetime

from honda_voice.agent.booking_agent import BookingAgent
from honda_voice.agent.call_state import CallState
from honda_voice.agent.prompts import (
    BOOKED_LINE,
    BOOKING_INSTRUCTIONS,
    CONFIRM_SUMMARY,
    booking_turn_instructions,
)
from honda_voice.agent.sales_agent import SalesAgent
from honda_voice.domain.booking_guard import OfferedSlot
from honda_voice.domain.datetime_resolve import BUSINESS_TZ

ALL = ["hi-IN", "en-IN", "ta-IN", "te-IN", "ml-IN", "kn-IN"]
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=BUSINESS_TZ)  # Thursday
SLOT = OfferedSlot(7, "activa-125", "Honda Activa 125", datetime(2026, 10, 16, 17, 0, tzinfo=BUSINESS_TZ))


def state(catalog, lang="hi-IN") -> CallState:
    return CallState(catalog=catalog, lang=lang, supported=ALL, clock=lambda: NOW)


def tool_names(agent) -> set[str]:
    return {t.info.name for t in agent.tools}


def test_tools(catalog):
    st = state(catalog)
    assert {"start_booking", "capture_lead", "find_bike"} <= tool_names(SalesAgent(st, greet=False))
    assert tool_names(BookingAgent(st)) == {
        "check_slots", "record_licence", "validate_phone", "confirm_details", "book_test_ride",
        "cancel_test_ride",
        "capture_lead", "back_to_sales",
    }  # fmt: skip


def test_fixed_lines_every_language():
    assert set(CONFIRM_SUMMARY) == set(BOOKED_LINE) == set(ALL)
    text = (
        BOOKING_INSTRUCTIONS + " ".join(CONFIRM_SUMMARY.values()) + " ".join(BOOKED_LINE.values())
    ).lower()
    assert "consent" not in text and "recording" not in text


async def test_start_booking_hands_over_with_shared_state(catalog):
    st = state(catalog, "ta-IN")
    agent = await SalesAgent(st, greet=False).start_booking("Activa 125")
    assert isinstance(agent, BookingAgent) and agent.state is st and st.booking_model == "activa-125"
    st.lang = "en-IN"  # a language switch during booking is seen by every agent of the call
    back = await agent.back_to_sales()
    assert isinstance(back, SalesAgent) and back.lang == "en-IN"


async def test_start_booking_family_name_or_upcoming(catalog):
    st = state(catalog)
    assert isinstance(await SalesAgent(st, greet=False).start_booking("activa"), BookingAgent)
    assert st.booking_model is None  # "activa" could be several models: BookingAgent asks which
    out = await SalesAgent(st, greet=False).start_booking("XR300L")  # pre-booking
    assert out["handed_over"] is False and "upcoming_model" in out["note"]


async def test_check_slots_without_database_or_for_unrideable(catalog):
    agent = BookingAgent(state(catalog))
    assert (await agent.check_slots("tomorrow", "17:00", "Activa 125"))["error"] == "DB_UNAVAILABLE"
    assert (await agent.check_slots("tomorrow", "17:00", "ADV160"))["error"] == "NOT_RIDEABLE"
    agent.state.booking_model = None
    assert (await agent.check_slots("tomorrow"))["error"] == "MODEL_UNKNOWN"


async def test_read_back_then_book_needs_a_new_caller_turn(catalog):
    st = state(catalog, "ta-IN")
    st.caller_turns = 3
    st.booking.offer([SLOT])
    agent = BookingAgent(st)
    assert (await agent.confirm_details("tomorrow", "17:00", "Ravi", "9876543210"))["error"] == (
        "LICENCE_NOT_CONFIRMED"
    )
    assert (await agent.record_licence(True))["recorded"] is False  # same turn as the handoff: not asked yet
    st.caller_turns += 1  # the caller answered the licence question
    assert "no_licence" in (await agent.record_licence(False))["note"]
    assert (await agent.confirm_details("tomorrow", "17:00", "Ravi", "9876543210"))[
        "error"
    ] == "LICENCE_NOT_CONFIRMED"
    assert (await agent.record_licence(True))["recorded"]
    assert (await agent.confirm_details("tomorrow", "17:00", "Ravi", "9876543210"))[
        "error"
    ] == "PHONE_NOT_VALIDATED"
    assert (await agent.validate_phone("98765 43210"))["valid"] is True
    out = await agent.confirm_details("tomorrow", "17:00", "Ravi", "9876543210")
    assert out["say"] == (
        "Honda Activa 125, நாளை மாலை 5 மணிக்கு, பெயர் Ravi, நம்பர் 9 8 7 6 5, 4 3 2 1 0, "
        "valid driving licence உடன். "
        "இந்த test ride-ஐ confirm பண்ணட்டுமா?"
    )
    same_turn = await agent.book_test_ride("2026-10-16", "17:00", "Ravi", "9876543210", True)
    assert same_turn["error"] == "NO_CALLER_REPLY_SINCE_READ_BACK"
    st.caller_turns += 1  # the caller answered
    assert (await agent.book_test_ride("2026-10-16", "17:00", "Ravi", "9876543210", True))[
        "error"
    ] == "DB_UNAVAILABLE"


async def test_validate_phone_two_failures_offer_callback(catalog):
    agent = BookingAgent(state(catalog))
    assert "isn't a phone number" in (await agent.validate_phone("Priya"))["note"]  # not counted as a try
    assert "ask again" in (await agent.validate_phone("98765"))["note"]
    assert "phone_failed" in (await agent.validate_phone("12345 67890"))["note"]


def test_turn_instructions_and_state_line(catalog):
    st = state(catalog, "hi-IN")
    st.booking_model = "activa-125"
    st.booking.offer([SLOT])
    line = BookingAgent(st)._turn_instructions()
    assert line.startswith("Now: Thursday 15 October 2026, 12:00 IST.")
    assert (
        "model: Honda Activa 125" in line
        and "kal shaam 5 baje = day_ref 2026-10-16 time_24h 17:00" in line
        and "driving licence: not asked yet" in line
    )
    assert line.splitlines()[-1].startswith("[caller_language] Hindi")  # language tag last


def test_booking_turn_instructions_format():
    assert booking_turn_instructions("en-IN", NOW, "[booking_state] x").splitlines() == [
        "Now: Thursday 15 October 2026, 12:00 IST.",
        "[booking_state] x",
        "[caller_language] English. Reply only in English, written in Latin script.",
    ]


async def test_cancel_input_validation(catalog):
    agent = BookingAgent(state(catalog))
    assert "H N D" in (await agent.cancel_test_ride("HND 7K", "9876543210"))["note"]
    assert "mobile" in (await agent.cancel_test_ride("HND 7K3Q9", "123"))["note"]


async def test_capture_lead_needs_a_number(catalog):
    agent = BookingAgent(state(catalog))
    assert "mobile number" in (await agent.capture_lead("no_licence", name="Meena"))["note"]
    assert "isn't a valid" in (await agent.capture_lead("no_licence", phone="12345"))["note"]
    agent.state.booking.phone_failures = 2  # tries used up: saving without a number is allowed
    assert (
        "Saving failed" in (await agent.capture_lead("phone_failed", name="Meena"))["note"]
    )  # no DB offline


async def test_start_booking_passes_the_day_and_time_on(catalog):
    st = state(catalog)
    agent = await SalesAgent(st, greet=False).start_booking("Activa 125", "tomorrow", "17:00")
    assert agent._slot_request == ("tomorrow", "17:00")  # searched in code on entry
    unsure = await SalesAgent(state(catalog), greet=False).start_booking("activa", "tomorrow", "17:00")
    assert unsure._slot_request is None  # model not settled yet: BookingAgent asks first


async def test_find_one_full_name_vs_family_name(catalog):
    assert await catalog.find_one("Shine 100") == ("shine-100", [])  # Shine 100 DX also exists
    assert await catalog.find_one("Activa 125") == ("activa-125", [])
    bike_id, candidates = await catalog.find_one("activa")
    assert bike_id is None and {"activa-110", "activa-125"} <= set(candidates)


def test_both_prompts_carry_the_time_words():
    from honda_voice.agent.prompts import SALES_INSTRUCTIONS, TIME_WORDS

    assert TIME_WORDS in SALES_INSTRUCTIONS and TIME_WORDS in BOOKING_INSTRUCTIONS
    assert '"saade X" = X:30' in TIME_WORDS


async def test_slots_are_named_by_day_and_time_not_ids(catalog):
    st = state(catalog)
    st.booking.offer([SLOT])
    st.booking.licence = True
    st.booking.check_phone("9876543210")
    agent = BookingAgent(st)
    assert agent._offered_slot_id("tomorrow", "17:00") == 7 == agent._offered_slot_id("2026-10-16", "17:00")
    assert agent._offered_slot_id("tomorrow", "17:30") is None  # never offered
    assert (await agent.confirm_details("tomorrow", "17:30", "Ravi", "9876543210"))[
        "error"
    ] == "SLOT_NOT_OFFERED"
    assert (await agent.confirm_details("next month", "17:00", "Ravi", "9876543210"))["error"] == (
        "SLOT_NOT_OFFERED"
    )


async def test_chosen_time_hint_follows_the_read_back(catalog):
    st = state(catalog, "hi-IN")
    three = OfferedSlot(5, "sp-125", "Honda SP125", datetime(2026, 10, 16, 15, 0, tzinfo=BUSINESS_TZ))
    half_past = OfferedSlot(6, "sp-125", "Honda SP125", datetime(2026, 10, 16, 15, 30, tzinfo=BUSINESS_TZ))
    st.booking.offer([three, half_past])
    st.booking.requested_slot_id = 6  # "kal dopahar saade teen baje", free
    st.booking.licence = True
    st.booking.check_phone("9000066666")
    agent = BookingAgent(st)
    assert "caller's chosen time: kal dopahar saade 3 baje" in agent._state_line()
    assert (await agent.confirm_details("tomorrow", "15:00", "Anil", "9000066666"))["ok"]  # never refused
    assert "caller's chosen time: kal dopahar 3 baje" in agent._state_line()  # follows the read-back


def test_prompts_and_notes_never_say_ten_digit():
    import pathlib

    import honda_voice.agent as agent_pkg

    for path in pathlib.Path(agent_pkg.__file__).parent.glob("*.py"):
        assert "10-digit" not in path.read_text() or path.name == "text_transforms.py", path.name


def _tool_step(*results):
    """A chat context ending in one tool step: (name, output dict, is_error) per tool."""
    from livekit.agents import llm

    ctx = llm.ChatContext()
    ctx.add_message(role="user", content="9000066666")
    for i, (name, _output, _is_error) in enumerate(results):
        ctx.items.append(llm.FunctionCall(call_id=f"c{i}", name=name, arguments="{}"))
    for i, (name, output, is_error) in enumerate(results):
        ctx.items.append(
            llm.FunctionCallOutput(call_id=f"c{i}", name=name, output=str(output), is_error=is_error)
        )
    return ctx


def test_fixed_tool_reply_speaks_the_read_back_without_the_llm(catalog):
    agent = BookingAgent(state(catalog))
    say = "Honda SP125, tomorrow at 3:30 pm, name Anil, number 9 0 0 0 0, 6 6 6 6 6, ... Shall I confirm?"
    ok = _tool_step(
        ("validate_phone", {"valid": True, "phone": "9000066666"}, False),
        ("confirm_details", {"ok": True, "say": say}, False),
    )
    assert agent._fixed_tool_reply(ok) == say
    booked = _tool_step(("book_test_ride", {"booked": True, "say": "Done! ... H N D, ..."}, False))
    assert agent._fixed_tool_reply(booked) == "Done! ... H N D, ..."


def test_fixed_tool_reply_leaves_failures_and_other_tools_to_the_llm(catalog):
    agent = BookingAgent(state(catalog))
    say = {"ok": True, "say": "read-back"}
    for ctx in (
        _tool_step(
            ("validate_phone", {"valid": False, "note": "ask again"}, False), ("confirm_details", say, False)
        ),
        _tool_step(("confirm_details", {"ok": False, "error": "PHONE_NOT_VALIDATED"}, False)),
        _tool_step(("book_test_ride", {"booked": False, "error": "SLOT_TAKEN", "alternatives": []}, False)),
        _tool_step(("confirm_details", say, True)),
        _tool_step(("check_slots", {"slots": []}, False)),
    ):
        assert agent._fixed_tool_reply(ctx) is None
    from livekit.agents import llm

    caller_turn = llm.ChatContext()
    caller_turn.add_message(role="user", content="yes")
    assert agent._fixed_tool_reply(caller_turn) is None  # answering the caller: not a tool step
