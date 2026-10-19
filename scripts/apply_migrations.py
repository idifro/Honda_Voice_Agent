"""Apply data/migrations/*.sql in numeric order, each once, each in its own transaction.

Run: `uv run python scripts/apply_migrations.py` (uses DATABASE_URL from .env). psql isn't required.
Applied files are recorded in public.schema_migrations (RLS enabled, like every public table).
"""

import asyncio
import hashlib
import sys
from pathlib import Path

from honda_voice.db.pool import connect

MIGRATIONS = Path(__file__).resolve().parents[1] / "data" / "migrations"
TRACKING = """
create table if not exists public.schema_migrations (
  filename    text primary key,
  sha256      text not null,
  applied_at  timestamptz not null default now()
);
alter table public.schema_migrations enable row level security;
"""


async def main() -> int:
    conn = await connect()
    try:
        await conn.execute(TRACKING)
        applied = {
            r["filename"]: r["sha256"] for r in await conn.fetch("select * from public.schema_migrations")
        }
        for path in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
            sql = path.read_text(encoding="utf-8")
            digest = hashlib.sha256(sql.encode()).hexdigest()
            if path.name in applied:
                if applied[path.name] != digest:
                    print(f"! {path.name} changed after it was applied; write a new migration instead")
                    return 1
                print(f"  {path.name} already applied")
                continue
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "insert into public.schema_migrations (filename, sha256) values ($1, $2)",
                    path.name,
                    digest,
                )
            print(f"✓ {path.name} applied")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
