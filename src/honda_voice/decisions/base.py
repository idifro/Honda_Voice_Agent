"""The provider interface for caller signals (SPEC §20.1). A provider answers every §20.2 question for one
caller turn; it never decides prices, EMIs, dates, slots or phone validity (rule 12)."""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Answer:
    value: Any  # str (choice), list[str] (multi), int | None (score), bool (flag)
    confidence: float | None = None  # None: the provider gives none (nano)


@dataclass(frozen=True)
class SignalContext:
    """The last few turns, oldest first, as (role, text) with role "caller" or "agent", plus agent state."""

    turns: list[tuple[str, str]]
    active_agent: str = "sales"
    language: str = "en-IN"

    def as_text(self) -> str:
        lines = [f"{'Caller' if role == 'caller' else 'Agent'}: {text}" for role, text in self.turns[:-1]]
        role, text = self.turns[-1] if self.turns else ("caller", "")
        lines.append(f"Latest caller message: {text}")
        return "\n".join(lines)


@dataclass(frozen=True)
class SignalResult:
    answers: dict[str, Answer]
    provider: str
    latency_ms: int
    extra: dict[str, Any] = field(default_factory=dict)


class SignalProvider(Protocol):
    name: str

    async def ask(self, ctx: SignalContext) -> SignalResult: ...
