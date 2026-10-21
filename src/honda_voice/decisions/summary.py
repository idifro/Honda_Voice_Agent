"""After-call summary for whoever calls back (SPEC §20.5): 2–3 English sentences from nano, written after the
call ends (the caller never waits). No names, numbers or booking codes: asked for in the prompt, and scrubbed
in code afterwards because the frontend shows it."""

import re
from typing import Any

# Summary prompt: what the caller wants and what was agreed, for the salesperson; no personal details.
SYSTEM = (
    "Summarise this phone call between a Honda two-wheeler showroom's voice assistant and a caller, for "
    "the salesperson who will call them back. Write 2–3 short sentences in English: what the caller wants "
    "(model, use, rider, budget, timeline), any concern, and what was agreed (test ride, callback, EMI). "
    'Name the models discussed. Refer to the caller only as "the caller" (never he/she: their gender is '
    "unknown). Never include names, phone numbers or booking codes."
)
_DIGITS = re.compile(r"\d(?:[\s-]*\d){4,}")  # 5+ digits, however spaced: phone numbers, references
_REFERENCE = re.compile(r"\bH\s*N\s*D\b[\s,-]*(?:[A-Z0-9][\s,]*){5}", re.IGNORECASE)


def scrub(text: str, names: list[str] | None = None) -> str:
    text = _REFERENCE.sub("[code]", text)
    text = _DIGITS.sub("[number]", text)
    for name in names or []:
        if name and len(name) > 1:
            text = re.sub(rf"\b{re.escape(name)}\b", "the caller", text, flags=re.IGNORECASE)
    return text.strip()


def transcript(turns: list[tuple[str, str]]) -> str:
    return "\n".join(f"{'Caller' if role == 'caller' else 'Assistant'}: {text}" for role, text in turns)


async def summarize(
    client: Any, deployment: str, turns: list[tuple[str, str]], *, reasoning_effort: str | None
) -> str:
    kwargs: dict[str, Any] = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}
    resp = await client.chat.completions.create(
        model=deployment,
        messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": transcript(turns)}],
        **kwargs,
    )
    return (resp.choices[0].message.content or "").strip()
