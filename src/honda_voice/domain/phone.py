"""Indian mobile numbers: normalize what the caller said, read it back, mask it (SPEC §7, §10, §16).

`normalize` is deliberately strict. It accepts digits in any Indian script and single-digit number words
in the six supported languages (STT sometimes writes "नौ आठ सात…" instead of "987…"), plus
"double"/"triple". Anything else, including compound numbers like "ninety eight", returns None so the
agent asks again digit by digit instead of guessing.
"""

import re
import unicodedata

_MOBILE = re.compile(r"[6-9]\d{9}")
_SEPARATORS = re.compile(r"[\s\-.,()/+।]+")

# Single-digit words. Native-script lists need a native-speaker check before the demo (SPEC §8).
_DIGIT_WORDS: dict[int, tuple[str, ...]] = {
    0: (
        "zero",
        "oh",
        "shunya",
        "शून्य",
        "ज़ीरो",
        "जीरो",
        "பூஜ்ஜியம்",
        "பூஜ்யம்",
        "சைபர்",
        "సున్నా",
        "ಸೊನ್ನೆ",
        "ಶೂನ್ಯ",
        "പൂജ്യം",
    ),
    1: ("one", "ek", "एक", "ஒன்று", "ఒకటి", "ಒಂದು", "ഒന്ന്"),
    2: ("two", "do", "दो", "இரண்டு", "రెండు", "ಎರಡು", "രണ്ട്"),
    3: ("three", "teen", "तीन", "மூன்று", "మూడు", "ಮೂರು", "മൂന്ന്"),
    4: ("four", "char", "chaar", "चार", "நான்கு", "నాలుగు", "ನಾಲ್ಕು", "നാല്"),
    5: ("five", "paanch", "panch", "पाँच", "पांच", "ஐந்து", "ఐదు", "ಐದು", "അഞ്ച്"),
    6: ("six", "chhe", "chhah", "chah", "छह", "छः", "छे", "ஆறு", "ఆరు", "ಆರು", "ആറ്"),
    7: ("seven", "saat", "सात", "ஏழு", "ఏడు", "ಏಳು", "ഏഴ്"),
    8: ("eight", "aath", "आठ", "எட்டு", "ఎనిమిది", "ಎಂಟು", "എട്ട്"),
    9: ("nine", "nau", "नौ", "ஒன்பது", "తొమ్మిది", "ಒಂಬತ್ತು", "ഒമ്പത്"),
}
_WORD_TO_DIGIT = {w: str(d) for d, words in _DIGIT_WORDS.items() for w in words}
_REPEAT_WORDS = {"double": 2, "triple": 3}


def _to_ascii_digits(token: str) -> str | None:
    """'९८७' or '987' -> '987'; 'नौ' -> '9'; anything else -> None."""
    if all(unicodedata.decimal(ch, None) is not None for ch in token):
        return "".join(str(unicodedata.decimal(ch)) for ch in token)
    return _WORD_TO_DIGIT.get(token.lower())


def _digits_from_text(raw: str) -> str | None:
    out: list[str] = []
    repeat = 1
    for token in _SEPARATORS.split(raw.strip()):
        if not token:
            continue
        if token.lower() in _REPEAT_WORDS:
            if repeat != 1:
                return None  # "double triple" makes no sense
            repeat = _REPEAT_WORDS[token.lower()]
            continue
        digits = _to_ascii_digits(token)
        if digits is None:
            return None
        if repeat != 1:
            if len(digits) != 1:
                return None  # "double 98" is ambiguous
            digits *= repeat
            repeat = 1
        out.append(digits)
    if repeat != 1:
        return None  # trailing "double"
    return "".join(out) or None


def has_number(raw: str) -> bool:
    """Whether `raw` contains any digit or number word: '98765' or 'nau shunya' yes, 'Priya' no."""
    return any(
        _to_ascii_digits(t) is not None or t.lower() in _REPEAT_WORDS for t in _SEPARATORS.split(raw) if t
    )


def normalize(raw: str) -> str | None:
    """Return a 10-digit Indian mobile number, or None if `raw` isn't clearly one.

    Strips spaces and punctuation, a +91 / 91 / 0091 country code, or a leading 0.
    """
    digits = _digits_from_text(raw)
    if digits is None:
        return None
    if len(digits) == 14 and digits.startswith("0091"):
        digits = digits[4:]
    elif len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if _MOBILE.fullmatch(digits) else None


def readback(phone: str) -> str:
    """'9876543210' -> '98765 43210' (5+5 grouping, read back before booking; CLAUDE.md rule 6)."""
    _require_normalized(phone)
    return f"{phone[:5]} {phone[5:]}"


def speak(phone: str) -> str:
    """'9876543210' -> '9 8 7 6 5, 4 3 2 1 0': the 5+5 read-back as TTS should say it, digit by digit
    (written as '98765' it may be read as a single number)."""
    _require_normalized(phone)
    return f"{' '.join(phone[:5])}, {' '.join(phone[5:])}"


def mask(phone: str) -> str:
    """For logs: keep only the last 3 digits (SPEC §16). '9876543210' -> 'xxxxxxx210'."""
    _require_normalized(phone)
    return "x" * 7 + phone[-3:]


def _require_normalized(phone: str) -> None:
    if not _MOBILE.fullmatch(phone):
        raise ValueError("expected a normalized 10-digit mobile number")
