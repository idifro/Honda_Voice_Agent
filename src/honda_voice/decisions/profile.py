"""The caller profile (SPEC §20.2): merged turn by turn from signals. Pure. Rules:
- a choice is kept only when known (and, if the provider gives a confidence, ≥ MIN_CONFIDENCE); a later clear
  answer replaces an earlier one ("actually up to 1.5 lakh");
- lists (priorities, payment interest, objections, models) accumulate over the call;
- frustration is the latest score, purchase intent the highest; wanting a person stays true once said."""

from dataclasses import asdict, dataclass, field
from typing import Any

from honda_voice.decisions.base import Answer
from honda_voice.decisions.questions import UNKNOWN, budget_band

MIN_CONFIDENCE = 0.6
LATEST = ("intent", "use_case", "rider", "timeline", "experience")


@dataclass
class CallerProfile:
    intent: str | None = None
    use_case: str | None = None
    rider: str | None = None
    budget_band: str | None = None
    timeline: str | None = None
    experience: str | None = None
    priorities: list[str] = field(default_factory=list)
    payment_interest: list[str] = field(default_factory=list)
    objections: list[str] = field(default_factory=list)
    models_discussed: list[str] = field(default_factory=list)
    models_suggested: list[str] = field(default_factory=list)
    frustration: int | None = None
    max_purchase_intent: int | None = None
    wants_human: bool = False
    confidence: dict[str, float] = field(default_factory=dict)

    def merge(self, answers: dict[str, Answer]) -> list[str]:
        """Apply one turn's answers; returns the fields that changed."""
        changed: list[str] = []

        def ok(a: Answer) -> bool:
            return a.confidence is None or a.confidence >= MIN_CONFIDENCE

        for key in LATEST:
            a = answers.get(key)
            if a and a.value not in (None, UNKNOWN) and ok(a) and getattr(self, key) != a.value:
                setattr(self, key, a.value)
                if a.confidence is not None:
                    self.confidence[key] = a.confidence
                changed.append(key)
        if (a := answers.get("budget_inr")) and a.value is not None and ok(a):
            band = budget_band(a.value)
            if band != self.budget_band:
                self.budget_band = band
                changed.append("budget_band")
        for key in ("priorities", "payment_interest"):
            a = answers.get(key)
            if a and isinstance(a.value, list) and ok(a):
                changed += [key] if _add(getattr(self, key), a.value) else []
        if (a := answers.get("objection")) and a.value not in (None, "none", UNKNOWN) and ok(a):
            changed += ["objections"] if _add(self.objections, [a.value]) else []
        if (a := answers.get("frustration")) and a.value is not None and a.value != self.frustration:
            self.frustration = a.value
            changed.append("frustration")
        if (a := answers.get("purchase_intent")) and a.value is not None:
            if self.max_purchase_intent is None or a.value > self.max_purchase_intent:
                self.max_purchase_intent = a.value
                changed.append("max_purchase_intent")
        if (a := answers.get("wants_human")) and a.value is True and ok(a) and not self.wants_human:
            self.wants_human = True
            changed.append("wants_human")
        return changed

    def add_models(self, bike_ids: list[str]) -> bool:
        """Models the caller named (found by the catalog index in code, not by the signal model)."""
        return _add(self.models_discussed, bike_ids)

    def as_row(self) -> dict[str, Any]:
        row = asdict(self)
        row.pop("frustration"), row.pop("max_purchase_intent"), row.pop("wants_human")
        return row


def _add(target: list[str], values: list[str]) -> bool:
    new = [v for v in values if v not in target]
    target.extend(new)
    return bool(new)
