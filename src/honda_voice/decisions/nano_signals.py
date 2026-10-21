"""Caller signals from Azure gpt-5.4-nano with strict structured output (SPEC §20, §23.2b: 96–100 % per
language on the probe set). One request per caller turn, in parallel with the reply."""

import json
import time
from typing import Any

from honda_voice.decisions.base import Answer, SignalContext, SignalResult
from honda_voice.decisions.questions import BY_KEY, describe, json_schema

# Signal prompt: judge the caller from the conversation; categories only, no free text.
SYSTEM = (
    "You classify a caller on a voice call with a Honda two-wheeler showroom in India. The caller may speak "
    "Hindi/Hinglish, English, Tamil, Telugu, Malayalam or Kannada, often mixed with English. Answer every "
    "question about the caller as of their latest message, using earlier turns for context. Use only what "
    "the caller said or clearly implied; when they haven't said it, answer unknown, an empty list or null.\n"
    "Questions:\n" + describe()
)


class NanoSignals:
    def __init__(self, client: Any, deployment: str, *, reasoning_effort: str | None = "minimal") -> None:
        """client: an openai.AsyncAzureOpenAI (or anything with the same chat.completions.create)."""
        self.client = client
        self.deployment = deployment
        self.reasoning_effort = reasoning_effort
        self.name = deployment.removeprefix("voice-") or "nano"

    async def ask(self, ctx: SignalContext) -> SignalResult:
        kwargs: dict[str, Any] = {}
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        t0 = time.perf_counter()
        resp = await self.client.chat.completions.create(
            model=self.deployment,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": ctx.as_text()}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "caller_signals", "schema": json_schema(), "strict": True},
            },
            **kwargs,
        )
        latency = int((time.perf_counter() - t0) * 1000)
        return SignalResult(parse(resp.choices[0].message.content or "{}"), self.name, latency)


def parse(content: str) -> dict[str, Answer]:
    """Structured output -> answers; anything outside the schema is dropped, not trusted."""
    raw = json.loads(content)
    out: dict[str, Answer] = {}
    for key, value in raw.items():
        q = BY_KEY.get(key)
        if q is None:
            continue
        if q.kind == "choice" and value in q.options:
            out[key] = Answer(value)
        elif q.kind == "multi" and isinstance(value, list):
            out[key] = Answer([v for v in value if v in q.options])
        elif q.kind == "score" and (value is None or (isinstance(value, int) and 1 <= value <= 5)):
            out[key] = Answer(value)
        elif q.kind == "flag" and isinstance(value, bool):
            out[key] = Answer(value)
        elif q.kind == "amount" and (
            value is None or (isinstance(value, int) and 1_000 <= value <= 10_000_000)
        ):
            out[key] = Answer(value)
    return out
