"""Catalog lookups for the agent's tools (SPEC §7). Parameterized SQL only; the LLM never writes SQL (rule 2).

The catalog is small (33 models), so it is loaded once per call into a `Catalog` snapshot: alias matching,
pre-fetching, list and compare never cost a database round trip mid-turn. Only the trigram fallback for
misheard names queries Postgres. Catalog changes therefore take effect on the next call.
"""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from honda_voice.db.catalog_seed import SeedRows
from honda_voice.domain.alias_match import AliasIndex, normalize

TRIGRAM_MIN = 0.4  # SPEC §7
TRIGRAM_MARGIN = 0.1  # top match must beat the runner-up by this much, else ask "which one?"

_TRIGRAM_SQL = """
select bike_id,
       max(greatest(similarity(alias, $1),
                    similarity(replace(alias, ' ', ''), replace($1, ' ', '')))) as score
from public.bike_aliases
where alias % $1 or replace(alias, ' ', '') % replace($1, ' ', '')
group by bike_id
order by score desc
limit 3
"""


@dataclass
class FindResult:
    bike_ids: list[str]  # one: found; several: ambiguous, ask which; none: not found
    method: str  # "alias", "trigram" or "none"
    scores: list[float] = field(default_factory=list)
    similar: list[str] = field(default_factory=list)  # other models a bare name could mean


def _model(
    bike: dict[str, Any], variants: list[dict], on_road: list[dict], aliases: list[str]
) -> dict[str, Any]:
    """A bike in the catalog-file shape that `domain.catalog_view` formats."""
    specs = bike["specs"] if isinstance(bike["specs"], dict) else json.loads(bike["specs"])
    return {
        "id": bike["id"], "name": bike["name"], "segment": bike["segment"], "status": bike["status"],
        "fuel": bike["fuel"], "specs": specs, "features": list(bike["features"] or []),
        "colours": list(bike["colours"] or []), "unverified_fields": list(bike["unverified_fields"] or []),
        "pitch": bike["pitch"],
        "price": {
            "min": bike["price_min_inr"], "max": bike["price_max_inr"], "city": bike["price_city"],
            "as_of": str(bike["price_as_of"]) if bike["price_as_of"] else None,
        },
        "variants": [{"name": v["variant_name"], "ex_showroom_inr": v["ex_showroom_inr"]} for v in variants],
        "on_road": on_road,
        "aliases": aliases,
    }  # fmt: skip


class Catalog:
    def __init__(self, models: dict[str, dict[str, Any]], aliases: dict[str, str], pool: asyncpg.Pool | None):
        self.models = models
        self.index = AliasIndex(aliases)
        self.pool = pool  # None: offline (tests), no trigram fallback
        self._ready = asyncio.Event()
        self._ready.set()

    @classmethod
    def load_in_background(cls, pool: asyncpg.Pool) -> "Catalog":
        """An empty catalog that fills itself (~0.4 s) while the greeting plays. Until then `mentioned()`
        matches nothing (no pre-fetch) and `find()` waits for the load."""
        cat = cls({}, {}, pool)
        cat._ready.clear()

        async def _load() -> None:
            loaded = await cls.load(pool)
            cat.models, cat.index = loaded.models, loaded.index
            cat._ready.set()

        cat._load_task = asyncio.create_task(_load())
        return cat

    @classmethod
    async def load(cls, pool: asyncpg.Pool) -> "Catalog":
        async with pool.acquire() as conn:
            bikes = await conn.fetch("select * from public.bikes order by id")
            variants = await conn.fetch("select * from public.bike_variants order by bike_id, id")
            on_road = await conn.fetch(
                "select * from public.bike_on_road_prices order by bike_id, on_road_inr"
            )
            aliases = await conn.fetch("select alias, bike_id from public.bike_aliases")
        return cls._build([dict(b) for b in bikes], [dict(v) for v in variants], [dict(o) for o in on_road],
                          {a["alias"]: a["bike_id"] for a in aliases}, pool)  # fmt: skip

    @classmethod
    def from_seed_rows(cls, rows: SeedRows) -> "Catalog":
        """Offline catalog from the validated JSON (tests and evals without a database)."""
        on_road = [dict(o, other_charges=json.loads(o["other_charges"])) for o in rows.on_road]
        return cls._build(rows.bikes, rows.variants, sorted(on_road, key=lambda o: o["on_road_inr"]),
                          dict(rows.aliases), None)  # fmt: skip

    @classmethod
    def _build(cls, bikes, variants, on_road, aliases, pool) -> "Catalog":
        models = {}
        for b in bikes:
            ors = []
            for o in on_road:
                if o["bike_id"] == b["id"]:
                    other = o["other_charges"]
                    ors.append(dict(o, other_charges=json.loads(other) if isinstance(other, str) else other))
            models[b["id"]] = _model(
                b,
                [v for v in variants if v["bike_id"] == b["id"]],
                ors,
                sorted(a for a, bid in aliases.items() if bid == b["id"]),
            )
        return cls(models, aliases, pool)

    def mentioned(self, text: str) -> list[str]:
        return self.index.mentions(text)

    async def find(self, query: str) -> FindResult:
        """Alias match (longest wins); trigram fallback for misheard names. When an alias covers only part of
        the query ("cb350 hinees" hits "cb350"), a clearly better trigram match wins. A bare family name
        ("activa", "hornet") also returns the other models it could mean as `similar`."""
        await self._ready.wait()
        q = normalize(query)
        ids = self.mentioned(q)
        exact = q in self.index.alias_to_bike
        if ids and (exact or self.pool is None):
            return FindResult(ids[:3], "alias", similar=self.index.similar(q) if exact else [])
        if self.pool is None or not q:
            return FindResult([], "none")
        hits = await self._trigram(q)
        if ids:  # partial alias coverage: keep the alias match unless trigram clearly prefers another model
            alias_score = max((s for b, s in hits if b == ids[0]), default=0.0)
            if hits and hits[0][0] != ids[0] and hits[0][1] - alias_score >= TRIGRAM_MARGIN:
                return FindResult([hits[0][0]], "trigram", [hits[0][1]])
            return FindResult(ids[:3], "alias")
        hits = [(b, s) for b, s in hits if s >= TRIGRAM_MIN]
        if not hits:
            return FindResult([], "none")
        if len(hits) == 1 or hits[0][1] - hits[1][1] >= TRIGRAM_MARGIN:
            return FindResult([hits[0][0]], "trigram", [hits[0][1]])
        return FindResult([h[0] for h in hits], "trigram", [h[1] for h in hits])

    async def _trigram(self, q: str) -> list[tuple[str, float]]:
        assert self.pool is not None
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(_TRIGRAM_SQL, q)
        return [(r["bike_id"], float(r["score"])) for r in rows]

    def list_models(
        self, segment: str | None = None, max_price_inr: int | None = None, limit: int = 4
    ) -> list[dict]:
        """Cheapest first; models without a price are left out."""
        out = [
            m for m in self.models.values()
            if m["price"]["min"] and (segment is None or m["segment"] == segment)
            and (max_price_inr is None or m["price"]["min"] <= max_price_inr)
        ]  # fmt: skip
        return sorted(out, key=lambda m: m["price"]["min"])[:limit]

    def segments(self) -> list[str]:
        return sorted({m["segment"] for m in self.models.values()})
