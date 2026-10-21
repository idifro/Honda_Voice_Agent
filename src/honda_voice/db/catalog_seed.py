"""Catalog → database (SPEC §5, §6, §20.7). Validates data/catalog/*.json with pydantic, applies the
sourced overlay (_enrichment.json) and product-owner price overrides, builds table rows (pure, unit tested),
and upserts them in one transaction. Run through `scripts/seed_catalog.py`."""

import json
import re
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, ConfigDict, model_validator

from honda_voice.domain.recommend import ease_score

CATALOG_DIR = Path(__file__).resolve().parents[3] / "data" / "catalog"
SEGMENTS = Literal[
    "adventure", "adventure-crossover", "adventure-scooter", "adventure-tourer", "commuter",
    "cruiser", "dual-sport", "electric-scooter", "heritage-roadster", "luxury-tourer",
    "premium-commuter", "retro-classic", "retro-scrambler", "scooter", "sporty-commuter",
    "street", "superbike",
]  # fmt: skip
DEMO_SHOWROOM = {
    "id": "demo-showroom", "name": "Honda Demo Showroom", "city": "Delhi", "open_time": time(10, 0),
    "close_time": time(19, 0), "closed_isodow": [], "slot_minutes": 30, "tz": "Asia/Kolkata",
}  # fmt: skip


class PriceBlock(BaseModel):
    model_config = ConfigDict(extra="allow")
    city: str = "Delhi"
    min: int | None
    max: int | None
    as_of: date | None = None
    precision: Literal["exact", "approx", "missing"]
    source: str | None = None


class Variant(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    ex_showroom_inr: int | None = None
    inferred: bool = False
    notes: str | None = None


class CatalogModel(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str
    segment: SEGMENTS
    division: str
    fuel: Literal["petrol", "electric"]
    status: Literal["on_sale", "pre_booking", "upcoming"]
    official_url: str | None = None
    price: PriceBlock
    variants: list[Variant]
    specs: dict[str, Any] = {}
    features: list[str] = []
    colours: list[str] = []
    aliases: list[str] = []
    pitch: str
    pitch_status: str = "draft"
    unverified_fields: list[str] = []
    snapshot_date: date

    @model_validator(mode="after")
    def _consistent(self) -> "CatalogModel":
        if not self.variants:
            raise ValueError(f"{self.id}: no variants")
        lo, hi = self.price.min, self.price.max
        if (lo is None) != (hi is None) or (lo is not None and lo > hi):
            raise ValueError(f"{self.id}: bad price range {lo}..{hi}")
        if self.fuel == "petrol" and not self.specs.get("displacement_cc"):
            raise ValueError(f"{self.id}: petrol model without displacement_cc")
        return self


@dataclass
class SeedRows:
    showroom: dict[str, Any]
    bikes: list[dict[str, Any]] = field(default_factory=list)
    variants: list[dict[str, Any]] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)  # alias -> bike_id
    on_road: list[dict[str, Any]] = field(default_factory=list)
    fit: list[dict[str, Any]] = field(default_factory=list)
    finance: list[dict[str, Any]] = field(default_factory=list)
    report: list[str] = field(default_factory=list)


def _number(value: Any) -> float | None:
    if isinstance(value, dict):
        value = value.get("value")
    return float(value) if isinstance(value, int | float) else None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def normalize_alias(text: str) -> str:
    return " ".join(text.lower().replace("’", "'").split())


def load_models(catalog_dir: Path = CATALOG_DIR) -> list[CatalogModel]:
    files = sorted(p for p in catalog_dir.glob("*.json") if not p.name.startswith("_"))
    return [CatalogModel.model_validate_json(p.read_text(encoding="utf-8")) for p in files]


def _load_aux(catalog_dir: Path, name: str) -> dict[str, Any]:
    path = catalog_dir / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def apply_price_overrides(models: list[CatalogModel], overrides: dict[str, Any], report: list[str]) -> None:
    by_id = {m.id: m for m in models}
    for o in overrides.get("overrides", []):
        m = by_id.get(o["bike_id"])
        if m is None:
            raise ValueError(f"price override for unknown model {o['bike_id']!r}")
        # Re-validate (model_copy(update=...) skips validation): parses dates and rejects bad overrides.
        m.price = PriceBlock.model_validate({**m.price.model_dump(), **o["price"]})
        names = {v.name for v in m.variants}
        for vname, inr in o.get("variants", {}).items():
            if vname not in names:
                raise ValueError(f"price override for unknown variant {o['bike_id']}/{vname}")
            m.variants = [
                Variant.model_validate({**v.model_dump(), "ex_showroom_inr": inr}) if v.name == vname else v
                for v in m.variants
            ]
        report.append(f"override  {m.id}: price {m.price.min}..{m.price.max} ({o['decided']})")


def apply_enrichment(models: list[CatalogModel], enrichment: dict[str, Any], report: list[str]) -> None:
    """Sourced values (SPEC §20.7) on top of the generated catalog. Fills gaps only, except variant prices: a
    model whose variants were priced from one source gets its range from those prices (they replace older
    figures). Estimates are added to unverified_fields, so they're used for ranking but never spoken."""
    by_id = {m.id: m for m in models}
    as_of = enrichment.get("as_of")
    for bike_id, e in enrichment.get("models", {}).items():
        m = by_id.get(bike_id)
        if m is None:
            raise ValueError(f"enrichment for unknown model {bike_id!r}")
        if st := e.get("status"):  # product-owner decision, e.g. a launched model still marked upcoming
            report.append(f"status    {m.id}: {m.status} -> {st['value']} ({st['decided']})")
            m.status = st["value"]
        if (vp := e.get("variant_prices")) and vp["values"]:
            names = {v.name for v in m.variants}
            unknown = set(vp["values"]) - names
            if unknown:
                raise ValueError(f"enrichment for unknown variants {bike_id}/{sorted(unknown)}")
            m.variants = [
                Variant.model_validate({
                    **v.model_dump(), "ex_showroom_inr": vp["values"][v.name], "inferred": False,
                    "notes": "; ".join(x for x in (v.notes, vp["notes"].get(v.name)) if x) or None,
                }) if v.name in vp["values"] else v
                for v in m.variants
            ]  # fmt: skip
            priced = [v.ex_showroom_inr for v in m.variants if v.ex_showroom_inr is not None]
            complete = len(priced) == len(m.variants)
            old = (m.price.min, m.price.max)
            m.price = PriceBlock.model_validate({
                **m.price.model_dump(), "min": min(priced), "max": max(priced), "as_of": as_of,
                "precision": "exact" if complete else "approx", "source": vp["source"],
                "city": vp.get("city", m.price.city),
            })  # fmt: skip
            if old != (m.price.min, m.price.max):
                report.append(
                    f"price     {m.id}: {old[0]}..{old[1]} -> {m.price.min}..{m.price.max} (sourced)"
                )
        specs = dict(m.specs)
        unverified = list(m.unverified_fields)
        if (
            (mil := e.get("mileage_kmpl"))
            and m.fuel == "petrol"
            and _number(specs.get("mileage_kmpl")) is None
        ):
            specs["mileage_kmpl"] = {k: v for k, v in mil.items() if k != "estimate"}
            if mil.get("estimate") and "mileage_kmpl" not in unverified:
                unverified.append("mileage_kmpl")
                report.append(f"estimate  {m.id}: mileage {mil['value']} kmpl (ranking only, never spoken)")
        for key in ("kerb_weight_kg", "seat_height_mm"):
            if specs.get(key) is None and (e.get("size") or {}).get(key) is not None:
                specs[key] = e["size"][key]
        m.specs, m.unverified_fields = specs, unverified
        if not m.official_url and e.get("official_url"):
            m.official_url = e["official_url"]
        if not m.colours and e.get("colours"):
            m.colours = e["colours"]["values"]
        if not m.features and e.get("features"):
            m.features = e["features"]["values"]
    for what, why in enrichment.get("left_empty", {}).items():
        report.append(f"empty     {what}: {why}")


def _kerb(value: Any) -> float | None:
    """Specs hold a number or {variant: kg}; the lightest variant counts for handling."""
    if isinstance(value, dict):
        nums = [v for v in value.values() if isinstance(v, int | float)]
        return float(min(nums)) if nums else None
    return float(value) if isinstance(value, int | float) else None


def build_fit_rows(
    models: list[CatalogModel], enrichment: dict[str, Any], report: list[str]
) -> list[dict[str, Any]]:
    rows = []
    for m in models:
        e = enrichment.get("models", {}).get(m.id, {})
        fit, est = e.get("fit"), e.get("size_estimate") or {}
        if fit is None:
            report.append(f"no fit    {m.id}: not offered by recommend_bikes")
            continue
        kerb, seat = _kerb(m.specs.get("kerb_weight_kg")), _number(m.specs.get("seat_height_mm"))
        estimated = False
        if kerb is None and est.get("kerb_weight_kg") is not None:
            kerb, estimated = float(est["kerb_weight_kg"]), True
        if seat is None and est.get("seat_height_mm") is not None:
            seat, estimated = float(est["seat_height_mm"]), True
        if estimated:
            report.append(f"estimate  {m.id}: fit size ({est.get('why')})")
        rows.append({
            "bike_id": m.id, "kerb_weight_kg": kerb, "seat_height_mm": int(seat) if seat else None,
            "ease_score": ease_score(kerb, seat), "use_cases": fit["use_cases"],
            "rider_fit": fit["rider_fit"], "source": fit["source"],
            "is_estimate": estimated or not fit.get("approved", False),
        })  # fmt: skip
    return rows


def build_finance_rows(schemes: dict[str, Any]) -> list[dict[str, Any]]:
    as_of = date.fromisoformat(schemes["as_of"]) if schemes else None
    return [
        {
            "id": s["id"],
            "name": s["name"],
            "interest_rate_pct": s["interest_rate_pct"],
            "tenure_months": s["tenure_months"],
            "min_down_payment_pct": s["min_down_payment_pct"],
            "processing_fee_inr": s.get("processing_fee_inr", 0),
            "is_demo": True,
            "as_of": as_of,
        }  # fmt: skip
        for s in schemes.get("schemes", [])
    ]


def build_rows(catalog_dir: Path = CATALOG_DIR) -> SeedRows:
    models = load_models(catalog_dir)
    rows = SeedRows(showroom=dict(DEMO_SHOWROOM))
    enrichment = _load_aux(catalog_dir, "_enrichment.json")
    apply_enrichment(models, enrichment, rows.report)
    apply_price_overrides(models, _load_aux(catalog_dir, "_price_overrides.json"), rows.report)
    rows.fit = build_fit_rows(models, enrichment, rows.report)
    rows.finance = build_finance_rows(_load_aux(catalog_dir, "_finance_schemes.json"))
    stt_aliases = _load_aux(catalog_dir, "_stt_aliases.json").get("aliases", {})
    ids = {m.id for m in models}

    for m in models:
        specs, p = m.specs, m.price
        mileage = specs.get("mileage_kmpl") if m.fuel == "petrol" else None
        rows.bikes.append({
            "id": m.id, "name": m.name, "segment": m.segment, "division": m.division, "fuel": m.fuel,
            "status": m.status,
            "engine_cc": _number(specs.get("displacement_cc")),
            "range_km": _number(specs.get("range_km")) if m.fuel == "electric" else None,
            "mileage_kmpl": _number(mileage),
            "mileage_basis": mileage.get("basis") if isinstance(mileage, dict) else None,
            "specs": json.dumps(specs, ensure_ascii=False),
            "features": m.features, "colours": m.colours, "unverified_fields": m.unverified_fields,
            "pitch": m.pitch, "pitch_status": m.pitch_status, "official_url": m.official_url,
            "price_available": p.min is not None, "price_min_inr": p.min, "price_max_inr": p.max,
            "price_city": p.city, "price_as_of": p.as_of, "price_precision": p.precision,
            "price_source": p.source,
            "snapshot_date": m.snapshot_date,
        })  # fmt: skip
        for v in m.variants:
            rows.variants.append({
                "id": f"{m.id}-{_slug(v.name)}", "bike_id": m.id, "variant_name": v.name,
                "ex_showroom_inr": v.ex_showroom_inr, "inferred": v.inferred, "notes": v.notes,
            })  # fmt: skip
        names = [m.name, m.name.removeprefix("Honda "), *m.aliases, *stt_aliases.get(m.id, [])]
        for alias in {normalize_alias(n) for n in names if n.strip()}:
            owner = rows.aliases.setdefault(alias, m.id)
            if owner != m.id:
                raise ValueError(f"alias {alias!r} claimed by both {owner} and {m.id}")

        # Data-quality report (SPEC §6).
        if p.min is None:
            rows.report.append(f"no price  {m.id}: agent gives specs and offers a callback for price")
        if m.status != "on_sale":
            rows.report.append(f"status    {m.id}: {m.status}")
        if m.unverified_fields:
            rows.report.append(f"unverified {m.id}: {', '.join(m.unverified_fields)} (never spoken)")
        priced = [v.ex_showroom_inr for v in m.variants if v.ex_showroom_inr]
        if priced and p.min is not None and not (min(priced) >= p.min and max(priced) <= p.max):
            rows.report.append(
                f"mismatch  {m.id}: variant prices {sorted(priced)} outside range {p.min}..{p.max}"
            )
        if any(v.inferred for v in m.variants):
            rows.report.append(f"inferred  {m.id}: some variant prices mapped from the range")
    for bike_id in stt_aliases:
        if bike_id not in ids:
            raise ValueError(f"_stt_aliases.json names unknown model {bike_id!r}")

    # On-road rows: the overlay's (complete breakdowns) replace the older file's rows for its models.
    base, newer = _load_aux(catalog_dir, "_on_road_delhi.json"), enrichment.get("on_road", {})
    replaced = set(newer.get("replaces_models", []))
    kept = [(base, e) for e in base.get("entries", []) if e["bike_id"] not in replaced]
    for block, e in kept + [(newer, e) for e in newer.get("entries", [])]:
        if e["bike_id"] not in ids:
            raise ValueError(f"on-road price for unknown model {e['bike_id']!r}")
        rows.on_road.append({
            "bike_id": e["bike_id"], "variant_label": e["variant_label"], "city": block["city"],
            "on_road_inr": e["on_road_inr"], "ex_showroom_basis_inr": e.get("ex_showroom_basis_inr"),
            "rto_inr": e.get("rto_inr"), "insurance_inr": e.get("insurance_inr"),
            "other_charges": json.dumps(e.get("other_charges", []), ensure_ascii=False),
            "source": e["source"], "as_of": date.fromisoformat(block["as_of"]),
            "needs_review": e.get("needs_review"),
        })  # fmt: skip
        if e.get("needs_review"):
            rows.report.append(
                f"review    on-road {e['bike_id']} / {e['variant_label']}: {e['needs_review']}"
            )
    return rows


def _upsert_sql(table: str, cols: list[str], key: str) -> str:
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != key)
    params = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
    return (
        f"insert into public.{table} ({', '.join(cols)}) values ({params}) "
        f"on conflict ({key}) do update set {updates}"
    )


async def _insert_all(conn: asyncpg.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    if rows:
        cols = list(rows[0])
        params = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
        await conn.executemany(
            f"insert into public.{table} ({', '.join(cols)}) values ({params})",
            [tuple(r.values()) for r in rows],
        )


async def seed(conn: asyncpg.Connection, rows: SeedRows) -> dict[str, int]:
    """Upsert everything in one transaction. Bikes are upserted (slots may reference them); their variants,
    aliases and on-road rows are replaced, so re-running converges on the catalog exactly."""
    bike_ids = [b["id"] for b in rows.bikes]
    async with conn.transaction():
        s = rows.showroom
        await conn.execute(_upsert_sql("showrooms", list(s), "id"), *s.values())
        cols = list(rows.bikes[0])
        sql = _upsert_sql("bikes", cols, "id").replace(
            f"${cols.index('specs') + 1}", f"${cols.index('specs') + 1}::jsonb"
        )
        await conn.executemany(sql, [tuple(b.values()) for b in rows.bikes])
        await conn.execute("delete from public.bike_variants where bike_id = any($1::text[])", bike_ids)
        await conn.execute("delete from public.bike_aliases where bike_id = any($1::text[])", bike_ids)
        await conn.execute("delete from public.bike_on_road_prices where bike_id = any($1::text[])", bike_ids)
        vcols = list(rows.variants[0])
        await conn.executemany(
            f"insert into public.bike_variants ({', '.join(vcols)}) values "
            f"({', '.join(f'${i}' for i in range(1, len(vcols) + 1))})",
            [tuple(v.values()) for v in rows.variants],
        )
        await conn.executemany(
            "insert into public.bike_aliases (alias, bike_id) values ($1, $2)", list(rows.aliases.items())
        )
        if rows.on_road:
            ocols = list(rows.on_road[0])
            params = [f"${i}" + ("::jsonb" if c == "other_charges" else "") for i, c in enumerate(ocols, 1)]
            await conn.executemany(
                f"insert into public.bike_on_road_prices ({', '.join(ocols)}) values ({', '.join(params)})",
                [tuple(o.values()) for o in rows.on_road],
            )
        await conn.execute("delete from public.bike_fit where bike_id = any($1::text[])", bike_ids)
        await _insert_all(conn, "bike_fit", rows.fit)
        await conn.execute("delete from public.finance_schemes")
        await _insert_all(conn, "finance_schemes", rows.finance)
        stale = await conn.fetch("select id from public.bikes where not (id = any($1::text[]))", bike_ids)
        for r in stale:
            rows.report.append(f"stale     {r['id']}: in the database but not in the catalog (left in place)")
    return {
        t: await conn.fetchval(f"select count(*) from public.{t}")
        for t in (
            "showrooms",
            "bikes",
            "bike_variants",
            "bike_aliases",
            "bike_on_road_prices",
            "bike_fit",
            "finance_schemes",
        )
    }
