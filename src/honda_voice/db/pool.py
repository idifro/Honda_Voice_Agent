"""The single asyncpg pool (CLAUDE.md: one DB client), connecting to DATABASE_URL through the Supabase session
pooler. All queries are parameterized; the LLM never writes SQL (rule 2).

TLS: asyncpg's default ("prefer") encrypts but doesn't verify the server's certificate. With DATABASE_CA_CERT
set (default: Supabase's root CA in certs/), connections verify the certificate chain and host name, so the
database can't be impersonated on the network (Phase 5 hardening)."""

import ssl
from functools import lru_cache
from pathlib import Path
from typing import Any

import asyncpg

from honda_voice.config import get_settings

REPO_ROOT = Path(__file__).resolve().parents[3]
_pool: asyncpg.Pool | None = None


@lru_cache
def ssl_context() -> ssl.SSLContext | None:
    """Verify-full TLS context from DATABASE_CA_CERT, or None (asyncpg's default) when it's empty."""
    path = get_settings().database_ca_cert
    if not path:
        return None
    cafile = Path(path) if Path(path).is_absolute() else REPO_ROOT / path
    return ssl.create_default_context(cafile=str(cafile))  # checks the chain and the host name


def _tls_kwargs() -> dict[str, Any]:
    ctx = ssl_context()
    return {"ssl": ctx} if ctx is not None else {}


async def connect() -> asyncpg.Connection:
    """A single connection (scripts, tests)."""
    return await asyncpg.connect(get_settings().database_url.get_secret_value(), **_tls_kwargs())


async def create_pool(**kwargs: Any) -> asyncpg.Pool:
    """A new pool with the same TLS settings (tests that need their own pool)."""
    return await asyncpg.create_pool(
        get_settings().database_url.get_secret_value(), **_tls_kwargs(), **kwargs
    )


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await create_pool(
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
