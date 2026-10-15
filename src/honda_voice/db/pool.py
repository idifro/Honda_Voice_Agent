"""The single asyncpg pool (CLAUDE.md: one DB client), connecting to DATABASE_URL through the Supabase session
pooler. All queries are parameterized; the LLM never writes SQL (rule 2)."""

import asyncpg

from honda_voice.config import get_settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            get_settings().database_url.get_secret_value(),
            min_size=1,
            max_size=5,  # one worker process; tools run a few queries per turn at most
            command_timeout=5,  # a voice turn can't wait longer than this for the database
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
