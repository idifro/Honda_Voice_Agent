"""Read-back-before-write, enforced in code (CLAUDE.md rule 6; SPEC §7, §8). Pure: no I/O.

The LLM can only book what the caller heard and agreed to: a slot that a tool actually offered, a confirmed
valid driving licence, a phone number that passed validation, and exactly the slot, name and phone in the last
confirmation summary, after the caller answered that summary directly (the very next caller turn) with a yes.
"""

from dataclasses import dataclass, field
from datetime import datetime

from honda_voice.domain import phone as phone_mod

MAX_PHONE_TRIES = 2  # then offer a callback lead instead (SPEC §8)


@dataclass(frozen=True)
class OfferedSlot:
    slot_id: int
    bike_id: str
    model_name: str
    starts_at: datetime


@dataclass(frozen=True)
class Confirmation:
    """What the caller heard in the confirmation summary."""

    slot_id: int
    name: str
    phone: str  # normalized
    caller_turn: int  # caller turns so far when the summary was given


def same_name(a: str, b: str) -> bool:
    return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()


@dataclass
class BookingDraft:
    offered: dict[int, OfferedSlot] = field(default_factory=dict)
    validated_phones: set[str] = field(default_factory=set)
    phone_failures: int = 0
    licence: bool | None = None  # caller's answer to "valid two-wheeler driving licence?"; None = not asked
    pending: Confirmation | None = None
    # The caller's current choice, shown to the LLM as a hint: the time they asked for when it was free, then
    # whatever was last read back; cleared when it's taken. The LLM twice confirmed a neighbouring slot
    # ("saade 3" read as "3", step 14+ evals). A hint, not a refusal: callers change their minds.
    requested_slot_id: int | None = None
    references: list[str] = field(default_factory=list)  # booked on this call

    def offer(self, slots: list[OfferedSlot]) -> None:
        for s in slots:
            self.offered[s.slot_id] = s

    def check_phone(self, raw: str) -> str | None:
        """Normalized number, remembered as validated; None counts a failed try."""
        normalized = phone_mod.normalize(raw)
        if normalized is None:
            self.phone_failures += 1
        else:
            self.validated_phones.add(normalized)
            self.phone_failures = 0
        return normalized

    @property
    def phone_tries_exhausted(self) -> bool:
        return self.phone_failures >= MAX_PHONE_TRIES

    def _check_details(self, slot_id: int, name: str, raw_phone: str) -> tuple[str | None, str | None]:
        """(error, normalized phone)."""
        if slot_id not in self.offered:
            return "SLOT_NOT_OFFERED", None
        if self.licence is not True:
            return "LICENCE_NOT_CONFIRMED", None
        if not name.strip():
            return "NAME_MISSING", None
        normalized = phone_mod.normalize(raw_phone)
        if normalized is None or normalized not in self.validated_phones:
            return "PHONE_NOT_VALIDATED", None
        return None, normalized

    def prepare(self, slot_id: int, name: str, raw_phone: str, caller_turn: int) -> Confirmation | str:
        """Record the summary about to be read back, or return why it can't be."""
        error, normalized = self._check_details(slot_id, name, raw_phone)
        if error:
            return error
        self.pending = Confirmation(slot_id, " ".join(name.split()), normalized, caller_turn)
        return self.pending

    def authorize(
        self, slot_id: int, name: str, raw_phone: str, caller_confirmed: bool, caller_turn: int
    ) -> Confirmation | str:
        """The confirmation to book, or the reason booking is refused."""
        if not caller_confirmed:
            return "NOT_CONFIRMED_BY_CALLER"
        pending = self.pending
        if pending is None:
            return "NO_READ_BACK"
        if caller_turn <= pending.caller_turn:
            return "NO_CALLER_REPLY_SINCE_READ_BACK"
        if caller_turn > pending.caller_turn + 1:  # e.g. "make it 11:30", answered without a new read-back
            return "READ_BACK_NOT_LAST"
        error, normalized = self._check_details(slot_id, name, raw_phone)
        if error:
            return error
        if (slot_id, normalized) != (pending.slot_id, pending.phone) or not same_name(name, pending.name):
            return "DETAILS_DIFFER_FROM_READ_BACK"
        return pending

    def booked(self, reference: str) -> None:
        self.references.append(reference)
        self.pending = None
