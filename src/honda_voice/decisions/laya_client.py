"""Optional local Laya provider (SPEC §23.3): the same questions over laya-serve's POST /v1/systemone.
Measured weaker than nano on Indic turns (§23.2b); kept for comparison and a later trained checkpoint."""

import time
from typing import Any

import aiohttp

from honda_voice.decisions.base import Answer, SignalContext, SignalResult
from honda_voice.decisions.questions import QUESTIONS, UNKNOWN


def laya_questions() -> dict[str, Any]:
    """Laya has choice, score and noul (P(true)); a multi-choice question becomes one noul per option."""
    out: dict[str, Any] = {}
    for q in QUESTIONS:
        instr = f"{q.text} Judge the caller in `conversation` as of their latest message."
        if q.kind == "choice":
            out[q.key] = {"type": "choice", "instructions": instr, "criteria": dict(q.options)}
        elif q.kind == "multi":
            for opt, meaning in q.options.items():
                out[f"{q.key}:{opt}"] = {
                    "type": "noul",
                    "instructions": f"Does the caller mention: {meaning}?",
                }
        elif q.kind == "score":
            out[q.key] = {"type": "score", "instructions": instr, "criteria": list(q.scale)}
        elif q.kind == "flag":
            out[q.key] = {"type": "noul", "instructions": instr}
    return out  # amounts (budget) have no Laya primitive: Laya never reports a budget


def parse(answers: dict[str, Any]) -> dict[str, Answer]:
    out: dict[str, Answer] = {}
    for q in QUESTIONS:
        if q.kind == "multi":
            picked = [o for o in q.options if (answers.get(f"{q.key}:{o}") or {}).get("noul", 0) >= 0.5]
            out[q.key] = Answer(picked)
            continue
        a = answers.get(q.key)
        if not a:
            continue
        conf = a.get("confidence")
        if q.kind == "choice" and a.get("choice") in q.options:
            out[q.key] = Answer(a["choice"], conf)
        elif q.kind == "score" and isinstance(a.get("score"), int | float):
            out[q.key] = Answer(max(1, min(5, round(a["score"]))), conf)  # expected value on a 1-5 scale
        elif q.kind == "flag" and isinstance(a.get("noul"), int | float):
            out[q.key] = Answer(a["noul"] >= 0.5, a["noul"])
    if "intent" not in out:
        out["intent"] = Answer(UNKNOWN)
    return out


class LayaSignals:
    def __init__(
        self, base_url: str, model: str = "multilingual", *, session: aiohttp.ClientSession | None = None
    ):
        self.url = base_url.rstrip("/") + "/v1/systemone"
        self.model = model
        self.session = session
        self.name = f"laya-{model}"

    async def ask(self, ctx: SignalContext) -> SignalResult:
        body = {"state": {"conversation": ctx.as_text()}, "questions": laya_questions(), "model": self.model}
        own = self.session is None
        session = self.session or aiohttp.ClientSession()
        t0 = time.perf_counter()
        try:
            async with session.post(self.url, json=body) as resp:
                resp.raise_for_status()
                data = await resp.json()
        finally:
            if own:
                await session.close()
        return SignalResult(parse(data.get("answers", {})), self.name, int((time.perf_counter() - t0) * 1000))
