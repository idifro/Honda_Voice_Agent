"""Session language: which language the agent replies in (SPEC §4.1). Code decides; the LLM never guesses.

Sarvam reports language_probability 1.0 even for one-word answers, so probability alone can't stop
"ok" from flipping a Tamil call to English. The rule therefore needs enough words *and* a confident
detection, while an explicit request (or a bare language name, e.g. answering the greeting) always wins.
"""

import re
from dataclasses import dataclass

SWITCH_MIN_WORDS = 3
SWITCH_MIN_PROBABILITY = 0.6


@dataclass(frozen=True)
class LanguageInfo:
    code: str
    name: str  # used in the LLM tag: "[caller_language] Tamil"
    reply_script: str  # what the LLM must write: "Latin" or the native script name
    native_block: tuple[int, int] | None  # Unicode block of the native script, None for Latin replies
    name_stems: tuple[str, ...]  # how callers name the language (prefix match, any script)


LANGUAGES: dict[str, LanguageInfo] = {
    info.code: info
    for info in (
        LanguageInfo(
            "hi-IN",
            "Hindi",
            "Latin",
            None,
            ("hindi", "हिंदी", "हिन्दी", "ஹிந்தி", "இந்தி", "హిందీ", "ಹಿಂದಿ", "ഹിന്ദി"),
        ),
        LanguageInfo(
            "en-IN",
            "English",
            "Latin",
            None,
            (
                "english",
                "angrezi",
                "अंग्रेज़ी",
                "अंग्रेजी",
                "इंग्लिश",
                "ஆங்கில",
                "இங்கிலீஷ்",
                "ఇంగ్లీష్",
                "ఇంగ్లిష్",
                "ಇಂಗ್ಲಿಷ್",
                "ಇಂಗ್ಲೀಷ್",
                "ഇംഗ്ലീഷ്",
            ),
        ),
        LanguageInfo(
            "ta-IN",
            "Tamil",
            "Tamil",
            (0x0B80, 0x0BFF),
            ("tamil", "தமிழ்", "तमिल", "తమిళ", "ತಮಿಳು", "തമിഴ്"),
        ),
        LanguageInfo(
            "te-IN",
            "Telugu",
            "Telugu",
            (0x0C00, 0x0C7F),
            ("telugu", "తెలుగు", "तेलुगु", "தெலுங்கு", "ತೆಲುಗು", "തെലുങ്ക്"),
        ),
        LanguageInfo(
            "ml-IN",
            "Malayalam",
            "Malayalam",
            (0x0D00, 0x0D7F),
            ("malayalam", "മലയാള", "मलयालम", "மலையாள", "మలయాళ", "ಮಲಯಾಳ"),
        ),
        LanguageInfo(
            "kn-IN",
            "Kannada",
            "Kannada",
            (0x0C80, 0x0CFF),
            ("kannada", "ಕನ್ನಡ", "कन्नड़", "कन्नड", "கன்னட", "కన్నడ", "കന്നഡ"),
        ),
    )
}

_INDIC_BLOCK = (0x0900, 0x0D7F)  # Devanagari … Malayalam
# Hindi function words absent from English: two or more in an English reply means it drifted to Hinglish.
_HINGLISH_MARKERS = frozenset(
    "hai hain ka ki ke mein aap kya bataun chahenge chahiye nahi kar karna sakti sakta raha rahi aur "
    "bhi toh tak liye wala wali".split()
)
_TOKEN_SPLIT = re.compile(r"[\s,.!?;:।\"'()\-–—]+")  # not \w: Indic vowel signs aren't alphanumeric


def to_supported(code: str | None, supported: tuple[str, ...] | list[str] = tuple(LANGUAGES)) -> str | None:
    """Map an STT language code ('ta', 'ta-IN', 'TA-in') to one of ours, or None."""
    if not code:
        return None
    base = str(code).split("-")[0].lower()
    return next((c for c in supported if c.split("-")[0] == base), None)


def script_language(text: str, supported: tuple[str, ...] | list[str] = tuple(LANGUAGES)) -> str | None:
    """Language implied by a transcript's native script (Tamil, Telugu, Kannada, Malayalam, Devanagari→Hindi).
    Latin script is ambiguous (English or Hinglish), so it returns None. Used when STT's detection is late."""
    counts: dict[str, int] = {}
    for ch in text:
        o = ord(ch)
        for code, info in LANGUAGES.items():
            block = info.native_block or ((0x0900, 0x097F) if code == "hi-IN" else None)
            if block and block[0] <= o <= block[1] and code in supported:
                counts[code] = counts.get(code, 0) + 1
    return max(counts, key=counts.get) if counts else None


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_SPLIT.split(text) if t]


def requested_language(text: str, supported: tuple[str, ...] | list[str] = tuple(LANGUAGES)) -> str | None:
    """A language the caller asked for by name ("Tamil", "English mein bolo", "తెలుగులో"), else None.

    If more than one language is named (e.g. the caller repeats the list), nothing is inferred.
    """
    named = set()
    for tok in tokens(text.lower()):
        for code in supported:
            if any(tok.startswith(stem) for stem in LANGUAGES[code].name_stems):
                named.add(code)
    return named.pop() if len(named) == 1 else None


def is_language_pick(text: str, supported: tuple[str, ...] | list[str] = tuple(LANGUAGES)) -> bool:
    """The caller only named a language ("Tamil", "English please", "Hindi mein baat karo"): a short turn
    naming exactly one language. Callers check separately that nothing else (a model, a price) was asked."""
    return requested_language(text, supported) is not None and 0 < len(tokens(text)) <= 4


def next_language(
    current: str,
    transcript: str,
    detected: str | None,
    probability: float | None,
    supported: tuple[str, ...] | list[str] = tuple(LANGUAGES),
) -> str:
    """Language for the next reply, given the caller's final transcript and STT's detection."""
    asked = requested_language(transcript, supported)
    if asked:
        return asked
    det = to_supported(detected, supported) or script_language(transcript, supported)
    if det is None or det == current:
        return current
    confident = probability is None or probability >= SWITCH_MIN_PROBABILITY
    return det if confident and len(tokens(transcript)) >= SWITCH_MIN_WORDS else current


def llm_language_tag(code: str) -> str:
    """Per-turn instruction line for the LLM (SPEC §4.1)."""
    info = LANGUAGES[code]
    script = "Roman script (Hinglish)" if code == "hi-IN" else f"{info.reply_script} script"
    return f"[caller_language] {info.name}. Reply only in {info.name}, written in {script}."


def script_mismatch(code: str, reply: str) -> str | None:
    """Why `reply` isn't in the expected script for `code`, or None. Logged as a guardrail violation."""
    info = LANGUAGES[code]
    indic = [ch for ch in reply if _INDIC_BLOCK[0] <= ord(ch) <= _INDIC_BLOCK[1]]
    if info.native_block is None:
        if indic:
            return f"{info.name} reply contains Indic script"
        if code == "en-IN" and sum(t.lower() in _HINGLISH_MARKERS for t in tokens(reply)) >= 2:
            return "English reply drifted into Hinglish"
        return None
    lo, hi = info.native_block
    native = sum(lo <= ord(ch) <= hi for ch in indic)
    if native == 0 and sum(ch.isalpha() for ch in reply) >= 10:
        return f"{info.name} reply has no {info.reply_script} script"
    if native < len(indic):
        return f"{info.name} reply mixes in another Indic script"
    return None
