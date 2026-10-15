"""Load data/catalog/*.json (+ _price_overrides, _stt_aliases, _on_road_delhi) into the database (SPEC §6).

Run: `uv run python scripts/seed_catalog.py` (`--dry-run` validates and prints the report without writing).
"""

import argparse
import asyncio

import asyncpg

from honda_voice.config import get_settings
from honda_voice.db.catalog_seed import build_rows, seed


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="validate and report only")
    args = ap.parse_args()

    rows = build_rows()
    print(
        f"validated {len(rows.bikes)} models, {len(rows.variants)} variants, {len(rows.aliases)} aliases, "
        f"{len(rows.on_road)} on-road prices"
    )
    if not args.dry_run:
        conn = await asyncpg.connect(get_settings().database_url.get_secret_value())
        try:
            counts = await seed(conn, rows)
        finally:
            await conn.close()
        print("database now has:", ", ".join(f"{t}={n}" for t, n in counts.items()))
    print("\ndata-quality report:")
    for line in sorted(rows.report):
        print("  " + line)


if __name__ == "__main__":
    asyncio.run(main())
