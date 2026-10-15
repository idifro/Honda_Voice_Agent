# CLAUDE.md — Honda 2-Wheeler Multilingual Voice Sales Agent (POC)

Real-time voice agent for a Honda (HMSI) showroom. It answers model and price questions and books test rides in Hindi/Hinglish, English, Tamil, Telugu, Malayalam and Kannada, replying in the caller's language (code picks the language, SPEC §4.1). Built with LiveKit Agents (Python) + Sarvam STT/TTS + Supabase Postgres, with a Next.js frontend. Uses LiveKit Cloud and hosted Supabase until Phases 10–11 move both to local Docker (SPEC §24).

**The full specification is in `docs/SPEC.md`. Read it before starting any phase.** This file holds only the rules and commands that apply to every session.

---

## Hard rules (never break these)

1. **No secrets in the repo.** All credentials come from `.env` / `frontend/.env.local`, both git-ignored. Only placeholders go in `.env.example`, docs, tests, and commit messages. If you see a real key or password in a tracked file, stop and flag it.
2. **The LLM never writes SQL.** Tools take typed parameters and call parameterized queries or Postgres functions in `src/honda_voice/db/`.
3. **Never break streaming.** Do not buffer a full LLM response before TTS. All speech cleanup happens as a streaming transform in the agent's `tts_node` (`agent/text_transforms.py`).
4. **Prices are spoken only from tool output.** `domain/pricing.format_inr_spoken()` is the single source of spoken prices. The prompt tells the LLM to repeat `spoken_price` verbatim and never compute or round prices itself.
5. **The LLM never does date arithmetic.** It extracts `day_ref` + `time_24h`; `domain/datetime_resolve.py` resolves those to an IST datetime and snaps to the slot grid.
6. **Read back before writing.** Phone (grouped 5+5), name, model, and slot are confirmed aloud, and the caller agrees, before `book_test_ride` runs.
7. **All times are `TIMESTAMPTZ`, and the business timezone is `Asia/Kolkata`.** Never use naive datetimes.
8. **Supabase/Postgres is the source of truth for bookings.** Google Calendar is a write-only mirror. A calendar failure never fails or rolls back a customer booking.
9. **The service-role key and DB URL never reach the browser.** The frontend only reads data through Next.js server routes.
10. **Every public table has RLS enabled.** New migrations must enable it.
11. **Never disparage competitors or state facts about competitor products.** Follow the competitor policy in SPEC §8.
12. **The decision model gives judgments, code applies rules (SPEC §20).** The decision model is Laya, self-hosted (`services/laya/run.sh`, SPEC §23); Jev is not used. It never decides prices, dates, slots, or phone validity. Only the booking-confirmation gate may call it in the serial voice path; every other call runs in parallel and must not delay audio. On timeout or error, fail safe (re-confirm), never fail open.
13. **LangGraph (Phase 8) stays behind `ORCHESTRATOR=langgraph`, and the native path is never removed.** No full-reply post-processing nodes; route with `decision` or `rules` unless the A/B in SPEC §21.3 justifies `llm`.

## Commands

```bash
# Python (always through uv; never activate venvs manually in scripts)
uv sync                                              # install deps + project
uv run python -m honda_voice.agent.worker console    # talk to the agent in the terminal (no frontend)
uv run python -m honda_voice.agent.worker dev        # run the worker against LiveKit Cloud
uv run python scripts/seed_catalog.py                # load data/catalog/*.json into the database
uv run python scripts/generate_slots.py --days 14    # create test-ride slots
uv run python scripts/retry_calendar_sync.py         # re-push bookings whose calendar sync failed

# Quality
uv run ruff check . && uv run ruff format --check .
uv run pytest -q                                     # unit tests (no network)
uv run pytest -q -m integration                      # needs a Supabase test DB (local Postgres from Phase 11)
uv run python -m evals.run_text_evals                # scripted conversation evals
services/laya/run.sh                                 # Phase 7: local Laya decision server on 127.0.0.1:8100
uv run python -m evals.decision_labelled             # Phase 7: Laya vs LLM-fallback on labelled turns
uv run python -m evals.latency_ab --configs native,lg_rules,lg_decision,lg_llm   # Phase 8 A/B

# Frontend
cd frontend && pnpm install && pnpm dev              # http://localhost:3000
```

DB migrations are in `data/migrations/`, applied in numeric order with `uv run python scripts/apply_migrations.py` (records each file in `schema_migrations`; auto-applied by Docker in Phase 11).

## Code conventions

- Python 3.11, fully async, type hints everywhere, `pydantic` models for tool I/O.
- Package is `src/honda_voice/`. Run modules with `python -m`, never by file path.
- One DB client: `asyncpg` pool in `db/pool.py`, connecting to `DATABASE_URL` (Supabase session pooler; local Postgres from Phase 11). Do not add `supabase-py` or SQLAlchemy.
- Pure logic (pricing, date resolution, phone validation, text transforms) lives in `domain/`, has no I/O, and has unit tests.
- Agent prompts live in `agent/prompts.py`; keep each one short and give it a one-line comment explaining its purpose.
- Log per-turn latency metrics (STT, LLM TTFT, TTS TTFB, end-to-end) using LiveKit's metrics events.

## Working rules for Claude

- LiveKit Agents and Sarvam APIs change often. **Check the current docs before writing integration code** (docs.livekit.io, docs.sarvam.ai), and note the verified version in `pyproject.toml` comments. Code snippets in SPEC.md are illustrative, not authoritative.
- Work one phase at a time (SPEC §14). A phase is done only when its exit criteria are met and tests pass.
- When a spec decision turns out wrong in practice (e.g. STT mode, LLM choice), update SPEC.md in the same change and say why.
- Keep this file short. Detail belongs in SPEC.md.