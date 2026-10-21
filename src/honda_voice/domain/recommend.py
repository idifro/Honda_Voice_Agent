"""Advisor ranking (SPEC §20.4). Pure: catalog models + bike_fit facts + a caller's need -> the best 2–3
on-sale models, each with short reasons. Reasons use verified facts only (an estimate is used for ranking but
never given as a reason, so it is never spoken). The LLM never picks or invents a bike."""

from dataclasses import dataclass, field
from typing import Any

USE_CASES = ("commute", "family_errands", "long_rides", "college", "delivery", "weekend_fun")
RIDERS = ("self", "parent", "child", "spouse", "other")
PRIORITIES = ("price", "mileage", "power", "ease", "comfort", "style", "electric", "safety")
EXPERIENCE = ("first_two_wheeler", "upgrading", "replacing")
STYLE_SEGMENTS = {
    "retro-classic", "heritage-roadster", "cruiser", "retro-scrambler", "street", "sporty-commuter",
}  # fmt: skip
GEARLESS = {"scooter", "electric-scooter", "adventure-scooter"}
OVER_BUDGET_SLACK = 1.05  # a model up to 5 % over the stated budget still counts as within it
MIN_SHARE_OF_BEST = 0.6  # a pick must score at least this share of the best one


def ease_score(kerb_weight_kg: float | None, seat_height_mm: float | None) -> int:
    """1 (demanding) … 5 (very easy to handle), from weight, lowered by one for a tall seat (> 820 mm).
    Without a weight, the seat height alone decides (≤ 770 mm easy, ≤ 820 middling, else hard)."""
    if kerb_weight_kg is None:
        if seat_height_mm is None:
            raise ValueError("need a kerb weight or a seat height")
        return 4 if seat_height_mm <= 770 else 3 if seat_height_mm <= 820 else 2
    score = next(
        s
        for limit, s in ((110, 5), (130, 4), (160, 3), (200, 2), (float("inf"), 1))
        if kerb_weight_kg <= limit
    )
    if seat_height_mm is not None and seat_height_mm > 820:
        score -= 1
    return max(1, score)


@dataclass(frozen=True)
class Need:
    use_case: str | None = None
    rider: str | None = None
    priorities: tuple[str, ...] = ()
    budget_inr: int | None = None
    experience: str | None = None

    def is_empty(self) -> bool:
        return not (self.use_case or self.rider or self.priorities or self.budget_inr or self.experience)

    def wants_easy(self) -> bool:
        family = self.rider in ("parent", "spouse", "child")
        return family or self.experience == "first_two_wheeler" or "ease" in self.priorities


@dataclass
class Pick:
    bike_id: str
    score: float
    reasons: list[str] = field(default_factory=list)
    over_budget: bool = False


def _number(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")
    return float(value) if isinstance(value, int | float) else None


def _has_abs(model: dict[str, Any]) -> bool:
    specs = model.get("specs") or {}
    return "ABS" in " ".join(model.get("features") or []) + " " + str(specs.get("braking_system", ""))


def _score(m: dict[str, Any], f: dict[str, Any], need: Need, lo: int, hi: int) -> Pick:
    specs, unverified = m.get("specs") or {}, set(m.get("unverified_fields") or [])
    price = m["price"]["min"]
    p = Pick(m["id"], 0.0)
    if need.budget_inr:
        p.over_budget = price > need.budget_inr * OVER_BUDGET_SLACK
        if not p.over_budget:
            p.reasons.append("within budget")
    if need.use_case and need.use_case in f["use_cases"]:
        p.score += 3
        p.reasons.append(f"suits {need.use_case.replace('_', ' ')}")
    if need.wants_easy():
        p.score += (f["ease_score"] - 3) * 1.5
        if "experienced" in f["rider_fit"]:
            p.score -= 4
        if f["ease_score"] >= 4 and not f["is_estimate"] and f.get("kerb_weight_kg"):
            p.reasons.append(f"light ({f['kerb_weight_kg']:g} kg) and easy to handle")
        if m.get("segment") in GEARLESS:  # no clutch or gears: easier than a light motorcycle
            p.score += 1.5
            p.reasons.append("gearless")
    if need.experience == "first_two_wheeler" and "first_time" in f["rider_fit"]:
        p.score += 2
    if "mileage" in need.priorities:
        mileage = _number(specs.get("mileage_kmpl"))
        if m.get("fuel") == "electric":  # no fuel cost, but a mileage question isn't an EV request
            p.score += 1
        elif mileage:
            p.score += min((mileage - 40) / 15, 2)  # one factor among several, not the deciding one
            if "mileage_kmpl" not in unverified:
                p.reasons.append(f"about {mileage:g} km per litre")
    if "electric" in need.priorities:
        p.score += 5 if m.get("fuel") == "electric" else -3
        if m.get("fuel") == "electric" and (km := _number(specs.get("range_km"))):
            p.reasons.append(f"electric, about {km:g} km per charge")
    if "power" in need.priorities and (cc := _number(specs.get("displacement_cc"))):
        p.score += min(cc / 100, 5)
        p.reasons.append(f"{cc:g} cc")
    if ("price" in need.priorities or need.budget_inr) and hi > lo:
        p.score += 2 * (1 - (price - lo) / (hi - lo))
    if "comfort" in need.priorities and "pillion_comfort" in f["rider_fit"]:
        p.score += 2
        p.reasons.append("comfortable for two")
    if "style" in need.priorities and m.get("segment") in STYLE_SEGMENTS:
        p.score += 3
        p.reasons.append(f"{m['segment'].replace('-', ' ')} style")
    if "safety" in need.priorities and _has_abs(m):
        p.score += 2
        p.reasons.append("has ABS")
    return p


def rank(
    models: dict[str, dict[str, Any]], fit: dict[str, dict[str, Any]], need: Need, limit: int = 3
) -> list[Pick]:
    """Best on-sale models for the need, highest score first (ties: cheaper first). Over-budget models only
    fill in when fewer than two fit the budget, and are marked."""
    if need.is_empty():
        return []
    candidates = [
        m for m in models.values()
        if m.get("status") == "on_sale" and (m.get("price") or {}).get("min") and m["id"] in fit
    ]  # fmt: skip
    if not candidates:
        return []
    prices = [m["price"]["min"] for m in candidates]
    picks = [_score(m, fit[m["id"]], need, min(prices), max(prices)) for m in candidates]
    picks.sort(key=lambda p: (-p.score, models[p.bike_id]["price"]["min"]))
    if picks[0].score > 0:  # drop weak fillers: "within budget" alone isn't a recommendation
        picks = [p for p in picks if p.score >= picks[0].score * MIN_SHARE_OF_BEST]
    within = [p for p in picks if not p.over_budget]
    if len(within) >= 2 or not need.budget_inr:
        return within[:limit]
    over = sorted((p for p in picks if p.over_budget), key=lambda p: models[p.bike_id]["price"]["min"])
    return (within + over)[:limit]
