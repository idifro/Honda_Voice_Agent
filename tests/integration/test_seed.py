"""Seeding the real database (SPEC §6). The catalog seed is idempotent, so running it here is safe.

Run: `uv run pytest -q -m integration`.
"""

import pytest

from honda_voice.db.catalog_seed import build_rows, seed
from honda_voice.db.pool import connect

pytestmark = pytest.mark.integration

FINGERPRINT = """
select md5(string_agg(t, '|' order by t)) from (
  select row(b.*)::text as t from public.bikes b
  union all select row(v.*)::text from public.bike_variants v
  union all select row(a.*)::text from public.bike_aliases a
  union all select row(o.bike_id, o.variant_label, o.city, o.on_road_inr, o.ex_showroom_basis_inr, o.rto_inr,
                       o.insurance_inr, o.other_charges, o.source, o.as_of, o.needs_review)::text
            from public.bike_on_road_prices o
) x
"""


@pytest.fixture
async def conn():
    c = await connect()
    try:
        yield c
    finally:
        await c.close()


async def test_seed_counts_and_idempotent(conn):
    rows = build_rows()
    counts = await seed(conn, rows)
    assert counts == {
        "showrooms": 1, "bikes": 33, "bike_variants": 57,
        "bike_aliases": len(rows.aliases), "bike_on_road_prices": 60, "bike_fit": 33, "finance_schemes": 3,
    }  # fmt: skip
    before = await conn.fetchval(FINGERPRINT)
    await seed(conn, build_rows())
    assert await conn.fetchval(FINGERPRINT) == before  # identical data, not just identical counts


async def test_seeded_values(conn):
    assert (
        await conn.fetchval("select count(*) from public.bikes where fuel = 'petrol' and engine_cc is null")
        == 0
    )
    ev = await conn.fetchrow(
        "select engine_cc, range_km, price_min_inr from public.bikes where id = 'activa-e'"
    )
    assert ev["engine_cc"] is None and ev["range_km"] == 102 and ev["price_min_inr"] == 120133
    assert await conn.fetchval("select count(*) from public.bikes where not price_available") == 0
    assert (
        await conn.fetchval("select count(*) from public.bike_on_road_prices where needs_review is not null")
        == 1  # QC3's "+30,000 subsidy" row; the CB350 H'ness one was replaced by a sourced row (2026-10-20)
    )
    show = await conn.fetchrow(
        "select city, open_time, close_time from public.showrooms where id = 'demo-showroom'"
    )
    assert show["city"] == "Delhi" and str(show["open_time"]) == "10:00:00"


async def test_trigram_lookup_finds_misheard_name(conn):
    # What Phase 2's find_bike will do for STT spellings that aren't stored verbatim.
    row = await conn.fetchrow(
        "select bike_id, similarity(alias, $1) as sim from public.bike_aliases "
        "order by similarity(alias, $1) desc limit 1",
        "cb350 hinees",
    )
    assert row["bike_id"] == "cb350-hness" and row["sim"] >= 0.4
