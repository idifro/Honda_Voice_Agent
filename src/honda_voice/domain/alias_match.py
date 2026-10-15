"""Model names in text by alias, longest match wins (SPEC §6, step 7 finding).

"Activa 125" contains the bare alias "activa" (Activa 110) and "Shine 100" contains "shine" (Shine 125), so a
shorter alias inside a longer matched one is dropped. Matching is whole-word for Latin aliases and substring
for native-script ones. Pure: no I/O.
"""

import re
from collections.abc import Mapping


def normalize(text: str) -> str:
    return " ".join(text.lower().replace("’", "'").split())


def _pattern(alias: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])")


class AliasIndex:
    def __init__(self, aliases: Mapping[str, str]) -> None:
        self._aliases = sorted(((normalize(a), b) for a, b in aliases.items()), key=lambda x: -len(x[0]))
        self._patterns = [(_pattern(a), b) for a, b in self._aliases]
        self.alias_to_bike = {a: b for a, b in self._aliases}

    def similar(self, name: str) -> list[str]:
        """Other models whose aliases contain `name` as whole words: "activa" → Activa 125, Activa e:.
        A specific name ("activa 125") is inside no other model's alias, so it has none."""
        q = normalize(name)
        owner = self.alias_to_bike.get(q)
        pattern = _pattern(q)
        out: list[str] = []
        for alias, bike_id in self._aliases:
            if bike_id != owner and bike_id not in out and alias != q and pattern.search(alias):
                out.append(bike_id)
        return sorted(out)

    def mentions(self, text: str) -> list[str]:
        """Bike ids named in `text`, in order of appearance, longest non-overlapping matches only."""
        return [bike_id for _, bike_id in self.matches(text)]

    def matches(self, text: str) -> list[tuple[str, str]]:
        """(matched alias, bike id) per model named in `text`, first mention of each, in order."""
        t = normalize(text)
        spans = [(m.start(), m.end(), b) for p, b in self._patterns for m in p.finditer(t)]
        # Drop a match that lies inside a longer match ("activa" inside "activa 125"); a greedy one-by-one
        # claim fails when two equally long aliases overlap ("activa electric" / "electric activa").
        kept = [
            (s, t[s:e], b)
            for s, e, b in spans
            if not any(s2 <= s and e <= e2 and (e2 - s2) > (e - s) for s2, e2, _ in spans)
        ]
        ordered: list[tuple[str, str]] = []
        for _, alias, bike_id in sorted(kept):
            if bike_id not in (b for _, b in ordered):
                ordered.append((alias, bike_id))
        return ordered
