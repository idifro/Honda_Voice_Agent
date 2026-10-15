"""Spoken ex-showroom prices in each supported language (SPEC §9).

`format_inr_spoken` is the only place a price is turned into words (CLAUDE.md rule 4). Tools call it
with the session language and the LLM repeats the result verbatim.
"""

from dataclasses import dataclass

LAKH = 100_000
CRORE = 10_000_000


@dataclass(frozen=True)
class _PriceWords:
    approx: str  # prefix used when rounding changed the value
    rupees: str
    lakh: str | None = None  # word-style languages only (hi, en)
    crore: str | None = None
    thousand: str | None = None
    hundred: str | None = None  # Hindi only; English keeps numerals below one lakh


_WORDS: dict[str, _PriceWords] = {
    "hi-IN": _PriceWords("lagbhag", "rupaye", "lakh", "crore", "hazaar", "sau"),
    "en-IN": _PriceWords("about", "rupees", "lakh", "crore", "thousand"),
    # Numeral style: Bulbul reads Indian-grouped numerals as native number words (tested 2026-10-15).
    "ta-IN": _PriceWords("சுமார்", "ரூபாய்"),
    "te-IN": _PriceWords("సుమారు", "రూపాయలు"),
    "ml-IN": _PriceWords("ഏകദേശം", "രൂപ"),
    "kn-IN": _PriceWords("ಸುಮಾರು", "ರೂಪಾಯಿ"),
}

PRICE_LANGUAGES = frozenset(_WORDS)


def round_inr(amount: int) -> int:
    """Round half-up: to the nearest 100 below one lakh, to the nearest 1,000 from one lakh up."""
    step = 100 if amount < LAKH else 1_000
    return (amount + step // 2) // step * step


def group_indian(n: int) -> str:
    """Indian digit grouping: 145000 -> '1,45,000', 4282000 -> '42,82,000'."""
    s = str(n)
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    pairs = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([head, *pairs, tail])


def _word_style(n: int, w: _PriceWords, lang: str) -> str:
    if lang == "en-IN" and n < LAKH:
        return group_indian(n)
    parts: list[str] = []
    for unit, word in ((CRORE, w.crore), (LAKH, w.lakh), (1_000, w.thousand), (100, w.hundred)):
        if word is None:
            continue
        count, n = divmod(n, unit)
        if count:
            parts.append(f"{count} {word}")
    if n:  # rounding guarantees no remainder; never speak a price with a part dropped
        raise ValueError(f"unexpected remainder {n} while wording {lang} price")
    return " ".join(parts)


def format_inr_spoken(amount: int, lang: str) -> str:
    """Return the spoken form of an ex-showroom price in `lang` (a BCP-47 code such as 'ta-IN').

    Examples: (74_640, 'hi-IN') -> 'lagbhag 74 hazaar 6 sau rupaye';
    (145_000, 'en-IN') -> '1 lakh 45 thousand rupees'; (80_600, 'ta-IN') -> '80,600 ரூபாய்'.
    """
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise TypeError("amount must be an int number of rupees")
    if amount < 100:
        raise ValueError(f"amount {amount} is not a plausible ex-showroom price")
    words = _WORDS.get(lang)
    if words is None:
        raise ValueError(f"unsupported language {lang!r}; expected one of {sorted(_WORDS)}")

    rounded = round_inr(amount)
    body = group_indian(rounded) if words.lakh is None else _word_style(rounded, words, lang)
    spoken = f"{body} {words.rupees}"
    return f"{words.approx} {spoken}" if rounded != amount else spoken
