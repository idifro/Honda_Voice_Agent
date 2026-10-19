"""Read-back-before-write rules (CLAUDE.md rule 6): every way the LLM could book something the caller
didn't hear and agree to is refused."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from honda_voice.db.bookings import normalize_reference
from honda_voice.domain import phone
from honda_voice.domain.booking_guard import BookingDraft, Confirmation, OfferedSlot

IST = ZoneInfo("Asia/Kolkata")
SLOT = OfferedSlot(101, "activa-125", "Honda Activa 125", datetime(2026, 10, 16, 17, 0, tzinfo=IST))
OTHER = OfferedSlot(102, "activa-125", "Honda Activa 125", datetime(2026, 10, 16, 17, 30, tzinfo=IST))


def ready_draft() -> BookingDraft:
    d = BookingDraft()
    d.offer([SLOT, OTHER])
    d.licence = True
    assert d.check_phone("+91 98765 43210") == "9876543210"
    assert isinstance(d.prepare(101, "Ravi  Kumar", "9876543210", caller_turn=4), Confirmation)
    return d


def test_happy_path_needs_a_later_caller_turn():
    d = ready_draft()
    assert (
        d.authorize(101, "Ravi Kumar", "98765 43210", True, caller_turn=4)
        == "NO_CALLER_REPLY_SINCE_READ_BACK"
    )
    ok = d.authorize(101, "ravi kumar", "098765-43210", True, caller_turn=5)  # same details, other spelling
    assert isinstance(ok, Confirmation) and ok.phone == "9876543210" and ok.name == "Ravi Kumar"
    d.booked("HND-7K3Q9")
    assert d.pending is None and d.references == ["HND-7K3Q9"]
    assert (
        d.authorize(101, "Ravi Kumar", "9876543210", True, caller_turn=6) == "NO_READ_BACK"
    )  # no re-booking


@pytest.mark.parametrize(
    ("slot_id", "name", "number", "confirmed", "error"),
    [
        (101, "Ravi Kumar", "9876543210", False, "NOT_CONFIRMED_BY_CALLER"),
        (102, "Ravi Kumar", "9876543210", True, "DETAILS_DIFFER_FROM_READ_BACK"),  # other offered slot
        (101, "Ravi", "9876543210", True, "DETAILS_DIFFER_FROM_READ_BACK"),
        (101, "Ravi Kumar", "9123456789", True, "PHONE_NOT_VALIDATED"),
        (999, "Ravi Kumar", "9876543210", True, "SLOT_NOT_OFFERED"),
        (101, "  ", "9876543210", True, "NAME_MISSING"),
    ],
)
def test_authorize_refusals(slot_id, name, number, confirmed, error):
    assert ready_draft().authorize(slot_id, name, number, confirmed, caller_turn=5) == error


def test_licence_withdrawn_after_read_back():
    d = ready_draft()
    d.licence = False  # "actually my licence expired"
    assert d.authorize(101, "Ravi Kumar", "9876543210", True, 5) == "LICENCE_NOT_CONFIRMED"


def test_changed_number_must_be_read_back_again():
    d = ready_draft()
    d.check_phone("9123456789")  # caller corrected the number: validated, but not yet read back
    assert d.authorize(101, "Ravi Kumar", "9123456789", True, 5) == "DETAILS_DIFFER_FROM_READ_BACK"
    d.prepare(101, "Ravi Kumar", "9123456789", caller_turn=5)
    assert isinstance(d.authorize(101, "Ravi Kumar", "9123456789", True, 6), Confirmation)


def test_prepare_refusals():
    d = BookingDraft()
    assert d.prepare(101, "Ravi", "9876543210", 1) == "SLOT_NOT_OFFERED"
    d.offer([SLOT])
    assert d.prepare(101, "Ravi", "9876543210", 1) == "LICENCE_NOT_CONFIRMED"  # not asked yet
    d.licence = False
    assert d.prepare(101, "Ravi", "9876543210", 1) == "LICENCE_NOT_CONFIRMED"  # no licence: no booking
    d.licence = True
    assert d.prepare(101, "Ravi", "9876543210", 1) == "PHONE_NOT_VALIDATED"  # never passed validate_phone
    assert d.pending is None


def test_phone_tries():
    d = BookingDraft()
    assert d.check_phone("12345") is None and not d.phone_tries_exhausted
    assert d.check_phone("ninety eight") is None and d.phone_tries_exhausted
    assert d.check_phone("9876543210") and not d.phone_tries_exhausted  # a valid number resets the count


def test_phone_spoken_readback():
    assert phone.speak("9876543210") == "9 8 7 6 5, 4 3 2 1 0"


@pytest.mark.parametrize(
    ("raw", "ref"),
    [("HND-7K3Q9", "HND-7K3Q9"), ("h n d 7 k 3 q 9", "HND-7K3Q9"), ("7k3q9", "HND-7K3Q9"),
     ("HND 7K3Q", None), ("HND-7K3O9", None), ("", None)],
)  # fmt: skip
def test_normalize_reference(raw, ref):
    assert normalize_reference(raw) == ref


def test_yes_must_answer_the_read_back_directly():
    """Step 14 eval: read-back 11:00, caller "make it 11:30", LLM says "done" without a tool, caller "yes"."""
    d = ready_draft()  # read back at caller turn 4
    assert d.authorize(101, "Ravi Kumar", "9876543210", True, caller_turn=6) == "READ_BACK_NOT_LAST"
    d.prepare(102, "Ravi Kumar", "9876543210", caller_turn=6)  # the new slot is read back
    assert isinstance(d.authorize(102, "Ravi Kumar", "9876543210", True, caller_turn=7), Confirmation)
