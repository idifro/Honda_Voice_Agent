"""Streaming cleanup of LLM text before TTS (SPEC §9; CLAUDE.md rule 3: never buffer the full reply).

Text is processed in whitespace-terminated segments as it streams. Only the trailing partial word is
held back, plus a pending currency amount ("₹ 80,600", "₹1.5 lakh") so it can be rewritten whole.
Because the cut points depend only on the text, the output is identical however the stream is chunked.
"""

import re
import unicodedata
from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from honda_voice.domain.pricing import format_inr_spoken

_RUPEE_WORDS = r"(?:rupees?|rupaye|rupaiye|रुपये|रुपए|ரூபாய்|రూపాయలు|రూపాయి|രൂപ|ರೂಪಾಯಿ)"
_MULTIPLIERS = {
    "lakh": 100_000,
    "lac": 100_000,
    "lakhs": 100_000,
    "crore": 10_000_000,
    "cr": 10_000_000,
    "k": 1_000,
    "thousand": 1_000,
}
_CURRENCY = re.compile(
    r"(?:₹|\bRs\.?|\bINR)\s?(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"(?:\s?(?P<mult>lakhs?|lac|crore|cr|k|thousand)\b)?"
    rf"(?:\s?{_RUPEE_WORDS})?",
    re.IGNORECASE,
)
# A segment ending like this may continue in the next chunk ("₹", "₹ 80,600", "₹1.5 lakh"): hold it.
_CURRENCY_TAIL = re.compile(
    r"(?:₹|\bRs\.?|\bINR)\s?(?:\d[\d,]*(?:\.\d+)?)?(?:\s?(?:lakhs?|lac|crore|cr|k|thousand))?\s*$",
    re.IGNORECASE,
)
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_KMPL = re.compile(r"\bkm/l(?:itre|iter)?\b|\bkmpl\b", re.IGNORECASE)  # no inner spaces: must fit one segment
_CC_AFTER_NUMBER = re.compile(r"(?<=\d)\s?cc\b", re.IGNORECASE)
# A booking code the LLM wrote raw ("HND-W8X8D", seen with gpt-5.4-nano) instead of the tool's spelled form:
# spelled here the same way as bookings.speak_reference, so TTS reads it character by character.
_REFERENCE = re.compile(r"\bHND-([A-Z2-9]{5})\b")
# "10-digit": TTS read the hyphenated token letter by letter ("1 0 d i g i t", 2026-10-16 call).
_NUMBER_HYPHEN_WORD = re.compile(r"(?<=\d)-(?=[^\W\d_])")
_CC_ALONE = re.compile(r"\bcc\b", re.IGNORECASE)
_BULLET = re.compile(r"(?m)^[ \t]*(?:[-*•]|\d+[.)])[ \t]+")
_MARKUP_CHARS = str.maketrans("", "", "*_`#")
_SPACES = re.compile(r"[ \t]{2,}")
_EMOJI_JOINERS = {"‍", "️", "︎"}
# Internal per-turn lines the LLM sometimes echoes ("... [caller_language] English"): dropped to the line end.
_INTERNAL_TAG = re.compile(r"\[(?:caller_language|booking_state)\]", re.IGNORECASE)


@dataclass
class TransformStats:
    """Per-reply counters for metrics (SPEC §16)."""

    words: int = 0
    violations: list[str] = field(default_factory=list)


def _is_emoji(ch: str) -> bool:
    return unicodedata.category(ch) in ("So", "Sk") or 0x1F000 <= ord(ch) <= 0x1FAFF


def _strip_emoji(text: str) -> str:
    out: list[str] = []
    prev_emoji = False
    for i, ch in enumerate(text):
        if _is_emoji(ch):
            prev_emoji = True
            continue
        # ZWJ/variation selectors belong to emoji sequences here; inside Indic words they're kept.
        if ch in _EMOJI_JOINERS and (prev_emoji or (i + 1 < len(text) and _is_emoji(text[i + 1]))):
            continue
        prev_emoji = False
        out.append(ch)
    return "".join(out)


class _TagDropper:
    """Drops echoed internal lines ('[caller_language] English …') from whole segments, to the line end."""

    def __init__(self) -> None:
        self._in_tag = False  # inside an echoed internal line: drop text until the next newline
        self._removed: list[str] = []

    def take_removed(self) -> list[str]:
        out, self._removed = self._removed, []
        return out

    def drop(self, segment: str) -> str:
        out = []
        while segment:
            if self._in_tag:
                newline = segment.find("\n")
                if newline < 0:
                    return "".join(out)
                self._in_tag, segment = False, segment[newline:]
                continue
            m = _INTERNAL_TAG.search(segment)
            if m is None:
                out.append(segment)
                break
            self._removed.append(f"internal tag spoken: {m.group(0)}")
            out.append(segment[: m.start()].rstrip(" \t"))
            self._in_tag, segment = True, segment[m.end() :]
        return "".join(out)


class InternalTagFilter:
    """Streaming filter for the LLM's own output (in llm_node), so an echoed tag never reaches the chat
    history: once it's there, the LLM copies it every turn (step 14 eval). Holds back only a partial word."""

    def __init__(self) -> None:
        self._buf = ""
        self._tags = _TagDropper()
        self.removed: list[str] = []

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        cut = max(self._buf.rfind(c) for c in (" ", "\n", "\t")) + 1
        segment, self._buf = self._buf[:cut], self._buf[cut:]
        return self._drop(segment)

    def flush(self) -> str:
        segment, self._buf = self._buf, ""
        return self._drop(segment)

    def _drop(self, segment: str) -> str:
        out = self._tags.drop(segment) if segment else ""
        self.removed += self._tags.take_removed()
        return out


class SpeechCleaner:
    """Feed LLM text chunks, get speakable text back. Call `flush()` at the end of the reply."""

    def __init__(self, lang: str, stats: TransformStats | None = None) -> None:
        self.lang = lang
        self.stats = stats or TransformStats()
        self._buf = ""
        self._line_start = True  # only whitespace since the last newline: a bullet may start here
        self._prev_out = ""  # last char emitted: avoids double spaces across segments
        self._tags = _TagDropper()

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        cut = max(self._buf.rfind(c) for c in (" ", "\n", "\t")) + 1
        if cut == 0:
            return ""
        tail = _CURRENCY_TAIL.search(self._buf[:cut])
        if tail:
            cut = tail.start()
        segment, self._buf = self._buf[:cut], self._buf[cut:]
        return self._process(segment)

    def flush(self) -> str:
        segment, self._buf = self._buf, ""
        return self._process(segment)

    def _process(self, segment: str) -> str:
        segment = self._tags.drop(segment)
        self.stats.violations += self._tags.take_removed()
        if not segment:
            return ""
        text = _URL.sub("", segment)
        text = _CURRENCY.sub(self._spoken_price, text)
        text = _KMPL.sub("kilometre per litre", text)
        text = _REFERENCE.sub(lambda m: f"H N D, {' '.join(m.group(1))}", text)
        text = _NUMBER_HYPHEN_WORD.sub(" ", text)
        text = _CC_AFTER_NUMBER.sub(" C C", text)
        text = _CC_ALONE.sub("C C", text)
        # Bullets only count at a line start. Prefix one context char so `^` sees whether earlier
        # segments left us at a line start ("\n") or not ("x" can never start a bullet).
        ctx = "\n" if self._line_start else "x"
        text = _BULLET.sub("", ctx + text)[1:]
        text = _strip_emoji(text.translate(_MARKUP_CHARS))
        text = _SPACES.sub(" ", text)
        if self._prev_out in (" ", "\t"):
            text = text.lstrip(" \t")  # a removal at a segment start must not leave a double space
        if "\n" in segment:
            self._line_start = segment[segment.rfind("\n") + 1 :].strip(" \t") == ""
        else:
            self._line_start = self._line_start and segment.strip(" \t") == ""
        if text:
            self._prev_out = text[-1]
        self.stats.words += len(text.split())
        return text

    def _spoken_price(self, m: re.Match[str]) -> str:
        self.stats.violations.append(f"raw currency in LLM output: {m.group(0).strip()!r}")
        try:
            value = Decimal(m.group("num").replace(",", ""))
        except InvalidOperation:
            return m.group("num")
        mult = m.group("mult")
        if mult:
            value *= _MULTIPLIERS[mult.lower()]
        try:
            spoken = format_inr_spoken(int(value.to_integral_value()), self.lang)
        except ValueError:
            return m.group("num")
        trailing = m.group(0)[len(m.group(0).rstrip()) :]
        return spoken + trailing


async def clean_text_stream(
    chunks: AsyncIterable[str], lang: str, stats: TransformStats | None = None
) -> AsyncIterator[str]:
    """Async adapter for the agent's `tts_node` (wired in step 6)."""
    cleaner = SpeechCleaner(lang, stats)
    async for chunk in chunks:
        out = cleaner.feed(chunk)
        if out:
            yield out
    out = cleaner.flush()
    if out:
        yield out
