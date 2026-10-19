"""Migration 001 against the real database (SPEC §5). Every test runs in a transaction that is rolled back.

Run: `uv run pytest -q -m integration` (needs DATABASE_URL in .env).
"""

import asyncpg
import pytest

from honda_voice.db.pool import connect

pytestmark = pytest.mark.integration

TABLES = {
    "showrooms", "bikes", "bike_variants", "bike_on_road_prices", "bike_aliases",
    "slots", "bookings", "leads", "schema_migrations",
}  # fmt: skip
BIKE = dict(
    id="t-bike", name="Test", segment="commuter", division="RedWing", fuel="petrol", status="on_sale",
    engine_cc=99.9, pitch="p", price_available=True, price_min_inr=65640, snapshot_date="2026-10-14",
)  # fmt: skip


@pytest.fixture
async def tx():
    conn = await connect()
    t = conn.transaction()
    await t.start()
    try:
        yield conn
    finally:
        await t.rollback()
        await conn.close()


async def insert_bike(conn, **over):
    row = {**BIKE, **over}
    cols = ", ".join(row)
    vals = ", ".join(f"${i}" for i in range(1, len(row) + 1))
    args = [
        __import__("datetime").date.fromisoformat(v) if k == "snapshot_date" else v for k, v in row.items()
    ]
    await conn.execute(f"insert into public.bikes ({cols}) values ({vals})", *args)


async def test_all_tables_exist_with_rls(tx):
    rows = await tx.fetch(
        "select c.relname, c.relrowsecurity from pg_class c join pg_namespace n on n.oid = c.relnamespace "
        "where n.nspname = 'public' and c.relkind = 'r'"
    )
    found = {r["relname"]: r["relrowsecurity"] for r in rows}
    assert TABLES <= set(found)
    assert all(found[t] for t in TABLES), {t: found[t] for t in TABLES if not found[t]}


async def test_pg_trgm_installed(tx):
    assert await tx.fetchval("select count(*) from pg_extension where extname = 'pg_trgm'") == 1


async def test_anon_cannot_read_or_write(tx):
    await insert_bike(tx)
    await tx.execute("set local role anon")
    try:
        assert await tx.fetchval("select count(*) from public.bikes") == 0  # RLS, no policy: nothing visible
    except asyncpg.InsufficientPrivilegeError:
        pass  # no grant at all is fine too
    with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.exceptions.PostgresError)):
        async with tx.transaction():
            await tx.execute("insert into public.leads (reason) values ('x')")


async def test_segment_must_be_one_of_17(tx):
    await insert_bike(tx, id="ok", segment="heritage-roadster")
    with pytest.raises(asyncpg.CheckViolationError):
        async with tx.transaction():
            await insert_bike(tx, id="bad", segment="premium")  # one of the old 5 categories, no longer valid


async def test_ev_without_engine_cc_but_petrol_needs_it(tx):
    await insert_bike(tx, id="ev", fuel="electric", segment="electric-scooter", engine_cc=None, range_km=102)
    with pytest.raises(asyncpg.CheckViolationError):
        async with tx.transaction():
            await insert_bike(tx, id="petrol", engine_cc=None)


async def test_price_available_matches_price(tx):
    await insert_bike(tx, id="noprice", price_available=False, price_min_inr=None)
    with pytest.raises(asyncpg.CheckViolationError):
        async with tx.transaction():
            await insert_bike(tx, id="liar", price_available=True, price_min_inr=None)
    with pytest.raises(asyncpg.CheckViolationError):
        async with tx.transaction():
            await insert_bike(tx, id="range", price_min_inr=90000, price_max_inr=80000)


async def test_variant_price_optional(tx):
    await insert_bike(tx)
    await tx.execute(
        "insert into public.bike_variants (id, bike_id, variant_name) values ('v1', 't-bike', 'DLX')"
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        async with tx.transaction():
            await tx.execute(
                "insert into public.bike_variants (id, bike_id, variant_name) values ('v2', 't-bike', 'DLX')"
            )


async def test_on_road_breakdown_all_or_nothing(tx):
    await insert_bike(tx)
    q = (
        "insert into public.bike_on_road_prices (bike_id, variant_label, city, on_road_inr, "
        "ex_showroom_basis_inr, rto_inr, insurance_inr, other_charges, source, as_of) "
        "values ('t-bike', $1, 'Delhi', 77945, $2, $3, $4, '[]', 'test', '2026-10-15')"
    )
    await tx.execute(q, "full", 65642, 5781, 6522)
    await tx.execute(q, "total only", None, None, None)
    with pytest.raises(asyncpg.CheckViolationError):
        async with tx.transaction():
            await tx.execute(q, "partial", 65642, None, 6522)


async def test_booking_phone_and_language_checks(tx):
    await insert_bike(tx)
    await tx.execute("insert into public.showrooms (id, name, city) values ('s', 'S', 'Delhi')")
    slot = await tx.fetchval(
        "insert into public.slots (showroom_id, bike_id, starts_at) "
        "values ('s', 't-bike', now() + interval '1 day') returning id"
    )
    ins = (
        "insert into public.bookings (reference, slot_id, customer_name, customer_phone, language, "
        "licence_confirmed) values ($1, $2, 'Ravi', $3, $4, $5)"
    )
    await tx.execute(ins, "HND-OK001", slot, "9876543210", "ta-IN", True)
    with pytest.raises(asyncpg.CheckViolationError):
        async with tx.transaction():
            await tx.execute(ins, "HND-BAD01", slot, "5876543210", "ta-IN", True)  # invalid first digit
    with pytest.raises(asyncpg.CheckViolationError):
        async with tx.transaction():
            await tx.execute(ins, "HND-BAD02", slot, "9876543211", "fr-FR", True)  # unsupported language
    with pytest.raises(asyncpg.CheckViolationError):
        async with tx.transaction():
            await tx.execute(ins, "HND-BAD03", slot, "9876543212", "ta-IN", False)  # no driving licence
    cols = {r["column_name"] for r in await tx.fetch(
        "select column_name from information_schema.columns where table_name in ('bookings', 'leads')"
    )}  # fmt: skip
    assert "consent_given" not in cols and "language" in cols


async def test_slot_capacity_check(tx):
    await insert_bike(tx)
    await tx.execute("insert into public.showrooms (id, name, city) values ('s', 'S', 'Delhi')")
    with pytest.raises(asyncpg.CheckViolationError):
        async with tx.transaction():
            await tx.execute(
                "insert into public.slots (showroom_id, bike_id, starts_at, capacity, booked) "
                "values ('s', 't-bike', now(), 1, 2)"
            )
