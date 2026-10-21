"""Profile questions (SPEC §20.2), one definition for every provider and the labelled eval. Categories only:
nothing here asks for transcript text or sensitive inferences (income, caste, religion, health)."""

from dataclasses import dataclass, field
from typing import Any, Literal

UNKNOWN = "unknown"
Kind = Literal["choice", "multi", "score", "flag", "amount"]


@dataclass(frozen=True)
class Question:
    key: str
    kind: Kind  # choice: one option or "unknown"; multi: any subset; score: 1–5 or null; flag: true/false;
    # amount: whole rupees or null
    text: str
    options: dict[str, str] = field(default_factory=dict)  # value -> meaning (choice, multi)
    scale: tuple[str, ...] = ()  # score: meaning of 1 … 5


QUESTIONS: tuple[Question, ...] = (
    Question("intent", "choice", "What does the caller want in their latest message?", {
        "specific_model": "specs, mileage or features of a Honda model they named",
        "price": "the price of a model",
        "compare": "a comparison of two Honda models",
        "recommendation": "help choosing a two-wheeler for a need, without settling on a model",
        "finance": "EMI, loan, down payment or exchange",
        "booking": "book a test ride",
        "cancel": "cancel a test ride",
        "competitor": "mentions or compares with another brand's model (TVS, Bajaj, Hero, Yamaha…)",
        "out_of_scope": "service, insurance, spare parts or anything a sales call doesn't cover",
        "smalltalk": "greeting, thanks, yes/no, choosing a language",
        "wants_human": "asks for a person or a call back",
        UNKNOWN: "none of the above",
    }),
    Question("use_case", "choice", "What will the two-wheeler mainly be used for?", {
        "commute": "daily travel to office or work",
        "family_errands": "local errands, shopping, dropping children, family use",
        "long_rides": "highway, touring or long trips",
        "college": "a student going to college",
        "delivery": "delivery or gig work",
        "weekend_fun": "leisure or weekend riding",
        UNKNOWN: "not said",
    }),
    Question("rider", "choice", "Who will ride it?", {
        "self": "the caller (also when they describe their own riding without naming anyone else)",
        "parent": "the caller's mother or father",
        "child": "the caller's son or daughter",
        "spouse": "the caller's wife or husband",
        "other": "someone else",
        UNKNOWN: "not said",
    }),
    Question("priorities", "multi", "What does the caller say matters to them? Pick every one that fits.", {
        "price": "a low price or a tight budget",
        "mileage": "fuel efficiency or low running cost",
        "power": "performance, speed or power",
        "ease": "easy handling, light weight, low seat",
        "comfort": "comfort, smooth ride, pillion comfort",
        "style": "looks or style",
        "electric": "an electric vehicle",
        "safety": "safety or brakes",
    }),
    # The LLM reports the amount; code picks the band (it put "ninety thousand" under 80,000 when asked for a
    # band, 2026-10-20 labelled run).
    Question("budget_inr", "amount", "The most the caller wants to spend, in whole rupees (\"ninety "
             "thousand\" = 90000, \"1.5 lakh\" = 150000); null if they gave no budget."),
    Question("timeline", "choice", "When does the caller plan to buy?", {
        "this_week": "within a few days",
        "this_month": "within a month",
        "1_3_months": "in one to three months",
        "later_or_browsing": "later, next year, or just looking",
        UNKNOWN: "not said",
    }),
    Question("experience", "choice", "What is the rider's experience with two-wheelers?", {
        "first_two_wheeler": "first two-wheeler or a learner",
        "upgrading": "has one and wants a better one",
        "replacing": "replacing an old or broken one",
        UNKNOWN: "not said",
    }),
    Question("payment_interest", "multi", "Which payment topics does the caller raise?", {
        "emi": "EMI, loan, finance or down payment",
        "exchange": "exchanging their old vehicle",
    }),
    Question("objection", "choice", "Does the caller raise a concern about buying?", {
        "price_high": "the price is too high",
        "waiting_time": "delivery or waiting time",
        "comparing_brand": "they are considering another brand",
        "mileage_doubt": "doubts about mileage or running cost",
        "none": "no concern",
    }),
    Question("frustration", "score", "How frustrated does the caller sound?",
             scale=("calm", "slightly impatient", "annoyed", "very annoyed", "angry or about to hang up")),
    Question("purchase_intent", "score", "How ready to buy is the caller?",
             scale=("just browsing", "curious", "considering", "likely to buy soon",
                    "ready to buy this month")),
    Question("wants_human", "flag", "Does the caller ask to speak to a person or to be called back?"),
)  # fmt: skip
BY_KEY = {q.key: q for q in QUESTIONS}
BUDGET_BANDS = ((80_000, "under_80k"), (120_000, "80k_1_2l"), (200_000, "1_2l_2l"))


def budget_band(inr: int | None) -> str | None:
    """Ex-showroom budget -> band (SPEC §20.2); a budget of exactly 80,000 is in 80k–1.2 lakh."""
    if inr is None:
        return None
    return next((band for limit, band in BUDGET_BANDS if inr < limit), "over_2l")


def json_schema() -> dict[str, Any]:
    """Strict JSON schema for structured output: every key required; unknown/null/empty when not said."""
    props: dict[str, Any] = {}
    for q in QUESTIONS:
        if q.kind == "choice":
            props[q.key] = {"type": "string", "enum": list(q.options)}
        elif q.kind == "multi":
            props[q.key] = {"type": "array", "items": {"type": "string", "enum": list(q.options)}}
        elif q.kind == "score":
            props[q.key] = {"type": ["integer", "null"], "minimum": 1, "maximum": 5}
        elif q.kind == "amount":
            props[q.key] = {"type": ["integer", "null"]}
        else:
            props[q.key] = {"type": "boolean"}
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def describe() -> str:
    """The questions as plain text for an LLM prompt."""
    lines = []
    for q in QUESTIONS:
        if q.options:
            opts = "; ".join(f"{k} = {v}" for k, v in q.options.items())
            lines.append(f"- {q.key} ({q.kind}): {q.text} Options: {opts}.")
        elif q.kind == "amount":
            lines.append(f"- {q.key} (integer or null): {q.text}")
        elif q.scale:
            scale = "; ".join(f"{i} = {v}" for i, v in enumerate(q.scale, 1))
            lines.append(f"- {q.key} (1–5, null if no evidence): {q.text} {scale}.")
        else:
            lines.append(f"- {q.key} (true/false): {q.text}")
    return "\n".join(lines)
