"""Out-of-scope topics detected in code (SPEC §8 "Out of scope"). Pure: no I/O.

Added after the step 14 eval: for "when should my Activa be serviced?" the pre-fetched model facts (and
their "ask which Activa" note) pulled the LLM into answering. A match skips the pre-fetch and tells the LLM to
use the out-of-scope line. Plain "insurance" isn't listed: it's part of the on-road price breakdown.
"""

import re

TOPICS: dict[str, tuple[str, ...]] = {
    "servicing or repairs": ("service", "servicing", "serviced", "repair", "repairs", "mechanic",
                             "spare parts", "sarvice", "சர்வீஸ்", "సర్వీస్", "സർവീസ്", "ಸರ್ವಿಸ್", "सर्विस"),
    "insurance renewal or claims": ("insurance renewal", "renew", "renewal", "claim", "insurance claim"),
}  # fmt: skip
# Finance/EMI left the list in Phase 7 step 3: demo EMI examples come from emi_examples (SPEC §20.4).
_NON_WORD = re.compile(r"[^\w]+")


def out_of_scope_topic(text: str) -> str | None:
    """The out-of-scope topic a caller's turn is about, or None."""
    padded = f" {_NON_WORD.sub(' ', text.lower()).strip()} "
    for topic, words in TOPICS.items():
        if any(f" {w} " in padded for w in words):
            return topic
    return None


# Words that make a turn about price, in the six languages (romanised Hindi as STT writes it).
PRICE_WORDS = ("price", "prices", "cost", "costs", "how much", "rate", "rupees", "lakh", "budget", "on-road",
               "on road", "ex-showroom", "emi", "kitne", "kitna", "kitni", "keemat", "kimat", "daam", "விலை",
               "எவ்வளவு", "ధర", "ఎంత", "വില", "എത്ര", "ಬೆಲೆ", "ಎಷ್ಟು", "कीमत", "दाम")  # fmt: skip


def asks_about_price(text: str) -> bool:
    lowered = text.lower()
    return any(w in lowered for w in PRICE_WORDS)
