"""Competitor mentions -> the closest Honda model (CLAUDE.md rule 11, SPEC §8). Pure: no I/O.

Only names live here, never facts about other products: the agent says nothing about the other model and
offers the Honda instead. Moved into code after the step 14 eval, where the LLM sometimes offered a callback
instead of pivoting. Pairings are by segment and price class; extend the table as callers name new models.
"""

import re

# Competitor model words -> closest Honda bike_id.
PIVOTS: dict[str, str] = {
    # scooters
    **dict.fromkeys(("jupiter", "ntorq", "access", "burgman", "fascino", "rayzr", "ray zr", "pleasure",
                     "destini", "maestro", "zest", "avenis"), "activa-125"),
    # 100-110 cc commuters
    **dict.fromkeys(("splendor", "passion", "platina", "hf deluxe", "radeon", "star city", "ct 100"),
                    "shine-100"),
    # 125 cc commuters
    **dict.fromkeys(("super splendor", "glamour", "raider"), "shine-125"),
    # 150-160 cc
    **dict.fromkeys(("pulsar", "apache", "xtreme", "gixxer"), "sp-160"),
    # 200 cc street
    **dict.fromkeys(("fz", "mt 15", "mt15", "r15", "duke 200"), "hornet-2-0"),
    # 350 cc retro
    **dict.fromkeys(("classic", "classic 350", "bullet", "meteor", "jawa 42", "yezdi roadster"),
                    "cb350-hness"),
    **dict.fromkeys(("hunter", "hunter 350"), "cb350rs"),
    # electric scooters
    **dict.fromkeys(("chetak", "iqube", "ola s1", "s1 pro", "ather", "rizta", "vida"), "qc1"),
    # adventure
    **dict.fromkeys(("xpulse",), "nx200"),
    **dict.fromkeys(("himalayan",), "nx500"),
}  # fmt: skip
BRANDS = ("bajaj", "hero", "tvs", "royal enfield", "enfield", "suzuki", "yamaha", "ktm", "jawa", "yezdi",
          "ola", "ather", "kawasaki", "triumph", "harley")  # fmt: skip
_NON_WORD = re.compile(r"[^0-9a-z]+")


def _words(text: str) -> str:
    return f" {_NON_WORD.sub(' ', text.lower()).strip()} "


def competitor_pivot(text: str) -> tuple[bool, str | None]:
    """(a competitor is mentioned, closest Honda bike_id or None). The longest matching model name wins."""
    padded = _words(text)
    hits = [name for name in PIVOTS if f" {name} " in padded]
    if hits:
        return True, PIVOTS[max(hits, key=len)]
    return any(f" {b} " in padded for b in BRANDS), None
