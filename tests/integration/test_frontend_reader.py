"""The frontend's read-only role (migrations 004, 005): sees only masked bookings and leads, caller
profiles and the catalog, can change nothing. Uses the role's URL from frontend/.env.local (skipped if it
isn't set up)."""

from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from honda_voice.db import bookings
from honda_voice.db.pool import ssl_context

from . import test_bookings as tb

pytestmark = tb.pytestmark
pool = tb.pool  # fixture: test showroom, everything deleted afterwards
ENV = Path(__file__).resolve().parents[2] / "frontend" / ".env.local"


@pytest.fixture
async def reader():
    url = dotenv_values(ENV).get("DATABASE_URL") if ENV.exists() else None
    if not url or "frontend_reader" not in url:
        pytest.skip("frontend/.env.local has no frontend_reader DATABASE_URL")
    conn = await asyncpg.connect(url, ssl=ssl_context())
    yield conn
    await conn.close()


async def test_bookings_are_masked(pool, reader):
    slot = await tb.make_slot(pool, tb.tomorrow_at(14))
    async with pool.acquire() as c:
        res = await bookings.reserve(
            c, slot, "  Ravi Kumar ", "9000012345", None, "ta-IN", licence_confirmed=True
        )
    row = await reader.fetchrow("select * from frontend.bookings where reference = $1", res.reference)
    assert row["customer_display"] == "Ravi K."
    assert row["phone_masked"] == "90xxx xx345"
    assert row["model"] == "Honda Shine 100" and row["language"] == "ta-IN"
    assert "customer_phone" not in row.keys() and "customer_name" not in row.keys()


async def test_catalog_readable(reader):
    assert await reader.fetchval("select count(*) from frontend.catalog") >= 30


@pytest.mark.parametrize(
    "sql",
    [
        "select customer_phone from public.bookings limit 1",
        "select * from public.leads limit 1",
        "select * from public.slots limit 1",
        "insert into public.leads (reason) values ('x')",
        "select public.cancel_booking('HND-AAAAA', '9000012345')",
        "select phone from public.customers limit 1",
        "select * from public.calls limit 1",
        "select * from public.caller_profiles limit 1",
        "select * from public.call_signals limit 1",
        "select * from public.finance_schemes limit 1",
    ],
)
async def test_everything_else_is_denied(reader, sql):
    with pytest.raises((asyncpg.InsufficientPrivilegeError, asyncpg.ReadOnlySQLTransactionError)):
        await reader.fetch(sql)


async def test_role_is_read_only_and_limited(reader):
    assert await reader.fetchval("show default_transaction_read_only") == "on"
    assert await reader.fetchval("select rolconnlimit from pg_roles where rolname = 'frontend_reader'") == 3


async def test_leads_and_profiles_are_masked(pool, reader):
    async with pool.acquire() as c:
        cust = await c.fetchval(
            "insert into public.customers (phone, name, preferred_language, callback_consent, consent_at, "
            "consent_text_version) values ('9000054321', 'Meena Raghavan', 'ta-IN', true, now(), 'v1') "
            "returning id"
        )
        call = await c.fetchval(
            "insert into public.calls (room_name, customer_id, summary) "
            "values ('t-reader-room', $1, 'Wants a light scooter for her mother.') returning id",
            cust,
        )
        await c.execute(
            "insert into public.caller_profiles (call_id, rider, priorities) "
            "values ($1, 'parent', '{ease,mileage}')",
            call,
        )
        lead = await c.fetchval(
            "insert into public.leads (reason, customer_id, call_id, score, interest_bike_id) "
            "values ('caller_requested', $1, $2, 'hot', 'activa-110') returning id",
            cust,
            call,
        )
    try:
        row = await reader.fetchrow("select * from frontend.leads where id = $1", lead)
        assert row["customer_display"] == "Meena" and row["phone_masked"] == "90xxx xx321"
        assert row["interest_model"] == "Honda Activa 110" and row["summary"].startswith("Wants")
        assert "phone" not in row.keys() and "customer_phone" not in row.keys()
        prof = await reader.fetchrow(
            "select * from frontend.caller_profiles where room_name = 't-reader-room'"
        )
        assert prof["rider"] == "parent" and prof["priorities"] == ["ease", "mileage"]
    finally:
        async with pool.acquire() as c:
            await c.execute("delete from public.leads where id = $1", lead)
            await c.execute("delete from public.calls where id = $1", call)  # cascades to caller_profiles
            await c.execute("delete from public.customers where id = $1", cust)
