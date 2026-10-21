"""Calls, caller profiles and per-turn signals (SPEC §20.5, §20.6; migration 005). Parameterized queries
only."""

import json
from typing import Any

import asyncpg

from honda_voice.db.bookings import upsert_customer
from honda_voice.decisions.base import Answer
from honda_voice.domain.lead_score import LeadScore

OUTCOME_ORDER = ("booked", "lead", "cancelled")  # the strongest outcome of the call wins; else info / dropped


async def start(conn: asyncpg.Connection, room: str) -> int:
    """One row per call (re-entrant for the same room, e.g. a worker restart)."""
    return await conn.fetchval(
        "insert into public.calls (room_name) values ($1) "
        "on conflict (room_name) do update set room_name = excluded.room_name returning id",
        room,
    )


async def save_turn(
    conn: asyncpg.Connection,
    call_id: int,
    turn: int,
    answers: dict[str, Answer],
    provider: str,
    latency_ms: int,
    profile: dict[str, Any],
    max_purchase_intent: int | None,
) -> None:
    """The turn's raw answers plus the merged profile, in one transaction."""
    rows = [
        (call_id, turn, key, json.dumps(a.value), a.confidence, provider, latency_ms)
        for key, a in answers.items()
    ]
    async with conn.transaction():
        await conn.executemany(
            "insert into public.call_signals (call_id, turn_index, question, answer, confidence, provider, "
            "latency_ms) values ($1, $2, $3, $4, $5, $6, $7)",
            rows,
        )
        cols = list(profile)
        params = ", ".join(f"${i}" + ("::jsonb" if c == "confidence" else "") for i, c in enumerate(cols, 2))
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols)
        values = [json.dumps(v) if c == "confidence" else v for c, v in profile.items()]
        await conn.execute(
            f"insert into public.caller_profiles (call_id, {', '.join(cols)}) values ($1, {params}) "
            f"on conflict (call_id) do update set {updates}, updated_at = now()",
            call_id,
            *values,
        )
        await conn.execute(
            "update public.calls set max_purchase_intent = $2 where id = $1", call_id, max_purchase_intent
        )


def outcome(outcomes: set[str], caller_turns: int) -> str:
    if caller_turns == 0:
        return "dropped"
    return next((o for o in OUTCOME_ORDER if o in outcomes), "info")


async def finish(
    conn: asyncpg.Connection,
    call_id: int,
    room: str,
    languages: list[str],
    result: str,
    *,
    summary: str | None = None,
    lead: LeadScore | None = None,
) -> None:
    """Close the call: outcome and summary; this call's bookings join the customer list (a booking is not a
    callback consent) and its leads get the score; the call points at its customer."""
    async with conn.transaction():
        await conn.execute(
            "update public.calls set ended_at = now(), languages = $2, outcome = $3, summary = $4 "
            "where id = $1",
            call_id, languages, result, summary,
        )  # fmt: skip
        await conn.execute(
            "update public.bookings set call_id = $1 where room_name = $2 and call_id is null", call_id, room
        )
        for b in await conn.fetch(
            "select id, customer_phone, customer_name, language from public.bookings "
            "where call_id = $1 and customer_id is null",
            call_id,
        ):
            customer = await upsert_customer(
                conn, b["customer_phone"], b["customer_name"], b["language"], callback_consent=False
            )
            await conn.execute(
                "update public.bookings set customer_id = $2::uuid where id = $1", b["id"], customer
            )
        if lead is not None:
            await conn.execute(
                "update public.leads set score = $2, score_value = $3, next_action = $4 where call_id = $1",
                call_id, lead.band, lead.value, lead.next_action,
            )  # fmt: skip
        await conn.execute(
            "update public.calls set customer_id = coalesce("
            "(select customer_id from public.leads where call_id = $1 and customer_id is not null limit 1),"
            " (select customer_id from public.bookings where call_id = $1 and customer_id is not null "
            "limit 1)) "
            "where id = $1",
            call_id,
        )
