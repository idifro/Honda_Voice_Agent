"""Lead score and next action (SPEC §20.5). Pure: the caller profile and the call's outcome -> 0–100, a band
(hot ≥ 70, warm 40–69, cold < 40) and one line for whoever calls back. No model decides this (rule 12)."""

from dataclasses import dataclass

from honda_voice.decisions.profile import CallerProfile

TIMELINE_POINTS = {"this_week": 25, "this_month": 20, "1_3_months": 15, "later_or_browsing": 0}
HOT, WARM = 70, 40


@dataclass(frozen=True)
class LeadScore:
    value: int
    band: str
    next_action: str


def score(p: CallerProfile, *, booked: bool, model_name: str | None = None) -> LeadScore:
    points = 0
    if p.max_purchase_intent:
        points += (p.max_purchase_intent - 1) * 12  # 0–48
    points += TIMELINE_POINTS.get(p.timeline or "", 5)  # unknown timeline: a little
    points += min(5 * len({*p.models_discussed, *p.models_suggested}), 10)  # engagement
    if booked:
        points += 20
    if p.payment_interest:
        points += 5  # asked how to pay: a buying question
    if (p.frustration or 0) >= 4:
        points -= 10
    value = max(0, min(100, points))
    band = "hot" if value >= HOT else "warm" if value >= WARM else "cold"
    return LeadScore(value, band, next_action(p, booked=booked, model_name=model_name))


def next_action(p: CallerProfile, *, booked: bool, model_name: str | None = None) -> str:
    model = model_name or "the model they asked about"
    if p.wants_human or (p.frustration or 0) >= 4:
        return "Call back promptly: the caller wanted a person"
    if booked:
        return f"Confirm the test ride of {model} and bring up finance options"
    if "exchange" in p.payment_interest:
        return f"Give an exchange quote for their old vehicle against {model}"
    if "emi" in p.payment_interest or "price_high" in p.objections:
        return f"Share current EMI and offers for {model}"
    if p.timeline in ("this_week", "this_month"):
        return f"Invite for a test ride of {model} this week"
    return f"Follow up on {model}"
