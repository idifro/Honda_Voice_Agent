"""find/list/compare against the seeded database (SPEC §7). Read-only.

Run: `uv run pytest -q -m integration`.
"""

import time

import asyncpg
import pytest

from honda_voice.config import get_settings
from honda_voice.db.catalog import Catalog

pytestmark = pytest.mark.integration


@pytest.fixture
async def catalog():
    pool = await asyncpg.create_pool(get_settings().database_url.get_secret_value(), min_size=1, max_size=2)
    try:
        yield await Catalog.load(pool)
    finally:
        await pool.close()


async def test_loads_everything(catalog):
    assert len(catalog.models) == 33
    assert catalog.models["activa-e"]["price"]["min"] == 118000
    assert len(catalog.models["shine-100"]["on_road"]) == 1


@pytest.mark.parametrize(
    ("query", "expected", "method"),
    [
        ("Activa 125", ["activa-125"], "alias"),  # longest match, not Activa 110's "activa"
        ("Shine 100", ["shine-100"], "alias"),
        ("CB350HINCE", ["cb350-hness"], "alias"),  # STT spelling added as an alias
        ("cb350 hinees", ["cb350-hness"], "trigram"),  # partial alias "cb350": trigram clearly prefers H'ness
        ("hornit", ["hornet-2-0"], "trigram"),
        ("acteva 125", ["activa-125"], "trigram"),
        ("livvo", ["livo"], "trigram"),
        ("qc one", ["qc1"], "trigram"),
        ("Splendor", [], "none"),  # competitor: nothing, the agent pivots
        ("Pulsar", [], "none"),
    ],
)
async def test_find(catalog, query, expected, method):
    r = await catalog.find(query)
    assert (r.bike_ids, r.method) == (expected, method)


@pytest.mark.parametrize(
    ("query", "owner", "also"),
    [
        ("activa", "activa-110", {"activa-e", "activa-125"}),
        ("hornet", "hornet-2-0", {"cb125-hornet", "cb750-hornet"}),
        ("rebel", "rebel-300", {"rebel-500"}),
    ],
)
async def test_bare_family_names_offer_similar_models(catalog, query, owner, also):
    r = await catalog.find(query)
    assert r.bike_ids == [owner] and also <= set(r.similar)


async def test_specific_name_has_no_similar(catalog):
    assert (await catalog.find("Activa 125")).similar == []


async def test_trigram_lookup_is_fast(catalog):
    await catalog.find("hornit")  # warm the connection
    t = time.perf_counter()
    for q in ("hornit", "acteva 125", "livvo"):
        await catalog.find(q)
    assert (time.perf_counter() - t) / 3 < 0.3  # Mumbai: ~85 ms measured


async def test_background_load():
    pool = await asyncpg.create_pool(get_settings().database_url.get_secret_value(), min_size=1, max_size=2)
    try:
        cat = Catalog.load_in_background(pool)
        assert cat.mentioned("Activa 125") == []  # not loaded yet: pre-fetch simply skips
        assert (await cat.find("Activa 125")).bike_ids == ["activa-125"]  # find waits for the load
        assert cat.mentioned("Activa 125") == ["activa-125"]
    finally:
        await pool.close()


async def test_list_and_segments(catalog):
    under_1_lakh = catalog.list_models(max_price_inr=100_000)
    assert under_1_lakh and all(m["price"]["min"] <= 100_000 for m in under_1_lakh)
    prices = [m["price"]["min"] for m in under_1_lakh]
    assert prices == sorted(prices)
    assert len(catalog.segments()) == 17
