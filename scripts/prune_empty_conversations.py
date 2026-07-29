#!/usr/bin/env python3
"""Delete conversations with fewer than 2 non-empty messages.

Usage:
  # Dry run — show what would be deleted
  python scripts/prune_empty_conversations.py --dry-run

  # Apply
  python scripts/prune_empty_conversations.py

  # Custom DB URL
  python scripts/prune_empty_conversations.py --db-url "postgresql://lightrag:lightrag@localhost:5432/lightrag"
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

import asyncpg


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without deleting")
    parser.add_argument(
        "--db-url",
        default=os.environ.get(
            "DATABASE_URL",
            f"postgresql://{os.environ.get('POSTGRES_USER', 'lightrag')}:"
            f"{os.environ.get('POSTGRES_PASSWORD', 'lightrag')}"
            f"@{os.environ.get('POSTGRES_HOST', 'localhost')}:"
            f"{os.environ.get('POSTGRES_PORT', '5432')}"
            f"/{os.environ.get('POSTGRES_DATABASE', 'lightrag')}",
        ),
        help="PostgreSQL connection URL",
    )
    args = parser.parse_args()

    conn = await asyncpg.connect(args.db_url)
    try:
        rows = await conn.fetch("SELECT id, name FROM conversations ORDER BY name")
        if not rows:
            print("No conversations found.")
            return 0

        to_delete: list[str] = []
        for r in rows:
            conv_id = r["id"]
            name = r["name"] or "(untitled)"
            count = await conn.fetchval(
                "SELECT count(*) FROM messages WHERE conv_id = $1 AND trim(content) <> ''",
                conv_id,
            )
            if count < 2:
                to_delete.append(conv_id)
                print(f"  {'DRY' if args.dry_run else 'DEL'} {conv_id}  msgs={count}  name={name!r}")

        print(f"\n{len(to_delete)} conversation(s) {'would be' if args.dry_run else 'were'} deleted.")

        if args.dry_run or not to_delete:
            return 0

        async with conn.transaction():
            await conn.executemany("DELETE FROM messages WHERE conv_id = $1", [(cid,) for cid in to_delete])
            await conn.executemany("DELETE FROM conversations WHERE id = $1", [(cid,) for cid in to_delete])

        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))