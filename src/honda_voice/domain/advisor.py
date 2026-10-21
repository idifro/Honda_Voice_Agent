"""Next-move rules (SPEC §20.3). Pure: the caller profile -> at most one suggested move for the next reply,
which the reply LLM phrases or skips. Each move is suggested once per call (discovery questions: at most
MAX_QUESTIONS); nothing during booking. The caller's own question always comes first (the line says so)."""

from dataclasses import dataclass, field

from honda_voice.decisions.profile import CallerProfile

MAX_QUESTIONS = 4
SOON = ("this_week", "this_month")
FAMILY = ("parent", "child", "spouse")
PREFIX = "Advisor suggestion (only after answering what the caller just asked; skip it if it doesn't fit): "


@dataclass(frozen=True)
class Move:
    kind: str
    text: str

    def line(self) -> str:
        return PREFIX + self.text


@dataclass
class CoachState:
    done: set[str] = field(default_factory=set)
    questions: int = 0


def _need_summary(p: CallerProfile) -> str:
    parts = [
        f"use={p.use_case}" if p.use_case else "",
        f"rider={p.rider}" if p.rider else "",
        f"priorities={','.join(p.priorities)}" if p.priorities else "",
        f"budget band={p.budget_band}" if p.budget_band else "",
        f"experience={p.experience}" if p.experience else "",
    ]
    return " ".join(x for x in parts if x) or "nothing yet"


def _has_need(p: CallerProfile) -> bool:
    return bool(p.use_case or p.rider in FAMILY or p.priorities or p.budget_band or p.experience)


def next_move(p: CallerProfile, coach: CoachState, *, booking_active: bool = False) -> Move | None:
    """The one move for the next reply, or None. Marks it done in `coach`."""
    if booking_active:
        return None
    move = _pick(p, coach)
    if move is not None:
        coach.done.add(move.kind)
        if move.kind.startswith("ask_"):
            coach.questions += 1
    return move


def _pick(p: CallerProfile, coach: CoachState) -> Move | None:
    done = coach.done

    def fresh(kind: str) -> bool:
        return kind not in done

    if (p.wants_human or (p.frustration or 0) >= 4) and fresh("callback"):
        return Move(
            "callback",
            "the caller is frustrated or wants a person: be brief, ask no questions, and "
            "offer a showroom callback (capture_lead, reason caller_requested).",
        )
    if "callback" in done:
        return None  # after offering a callback, no more selling
    if "price_high" in p.objections and fresh("price_objection"):
        return Move(
            "price_objection",
            "the caller finds the price high: don't argue; mention the lowest "
            "variant price (find_bike) and one EMI example (emi_examples), and say the showroom confirms "
            "actual offers.",
        )
    if "emi" in p.payment_interest and fresh("emi"):
        return Move(
            "emi",
            "the caller is interested in EMI: give one example from emi_examples for the model "
            "they're discussing and say the showroom confirms the actual terms.",
        )
    if "exchange" in p.payment_interest and fresh("exchange"):
        return Move(
            "exchange",
            "the caller wants to exchange their old vehicle: don't quote exchange values; "
            "offer a showroom callback for an exchange quote.",
        )
    if p.timeline == "later_or_browsing":
        if fresh("browsing"):
            return Move(
                "browsing",
                "the caller is just looking: don't push a test ride or a purchase; answer, "
                "and offer once to note their number for updates.",
            )
        return None
    if _has_need(p) and not p.models_suggested and not p.models_discussed and fresh("suggest"):
        return Move(
            "suggest",
            f"suggest at most 2 models with recommend_bikes ({_need_summary(p)}), one reason "
            "each, then ask which one they'd like to know more about.",
        )
    ready = (p.max_purchase_intent or 0) >= 4 or p.timeline in SOON
    model = (p.models_suggested or p.models_discussed or [None])[-1]
    if ready and model and fresh("test_ride"):
        return Move(
            "test_ride",
            f"the caller seems ready: offer a test ride of {model}; if they decline, "
            "offer a showroom callback.",
        )
    if coach.questions < MAX_QUESTIONS and (
        p.models_discussed or p.models_suggested or p.intent == "recommendation"
    ):
        if p.use_case is None and fresh("ask_use"):
            return Move(
                "ask_use",
                "ask one short question: what they'll mostly use it for (office, family, "
                "college, long rides).",
            )
        if p.rider is None and p.intent == "recommendation" and fresh("ask_rider"):
            return Move("ask_rider", "ask one short question: who will mostly ride it.")
        if p.budget_band is None and p.intent == "recommendation" and fresh("ask_budget"):
            return Move("ask_budget", "ask one short question: roughly what budget they have in mind.")
        if p.timeline is None and fresh("ask_timeline") and (p.models_discussed or p.models_suggested):
            return Move("ask_timeline", "ask one short question: when they're planning to buy.")
    if (
        (p.models_suggested or p.models_discussed)
        and fresh("test_ride")
        and p.timeline in (*SOON, "1_3_months")
    ):
        return Move("test_ride", f"offer a test ride of {model}.")
    return None
