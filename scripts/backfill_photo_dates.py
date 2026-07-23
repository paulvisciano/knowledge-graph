#!/usr/bin/env python3
"""One-time backfill of the typed photo_metadata columns.

Populates ``date_taken``, ``date_taken_friendly``, ``image_width``,
``image_height`` for pre-existing rows that were written before the columns
existed. The init_db migration already copies these out of the JSONB blob, so
this script only needs to PIL-backfill pixel dimensions for rows whose EXIF
omitted them (older extractor that looked for ``ExifImageHeight`` instead of
``ExifImageLength``). Run once after deploying the schema change.

Usage:
    python scripts/backfill_photo_dates.py            # apply
    python scripts/backfill_photo_dates.py --dry-run  # report only
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.services import db as db_module

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_photo_dates")


async def backfill(dry_run: bool) -> None:
    await db_module.init_db()
    pool = await db_module.get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT file_source
                 FROM photo_metadata
                WHERE image_width IS NULL OR image_height IS NULL""",
        )

    logger.info("Found %d rows missing pixel dimensions", len(rows))
    if not rows:
        return

    input_dir = Path(os.environ.get(
        "INPUT_DIR",
        str(Path(__file__).resolve().parent.parent / "inputs"),
    ))

    fixed = 0
    skipped = 0
    for row in rows:
        file_source = row["file_source"]
        dims = db_module._image_dims_from_file(file_source)
        if dims is None:
            skipped += 1
            continue

        if dry_run:
            logger.info("[dry-run] %s -> %dx%d", file_source, dims[0], dims[1])
            fixed += 1
            continue

        async with pool.acquire() as conn:
            await conn.execute(
                """UPDATE photo_metadata
                      SET image_width = $2, image_height = $3,
                          updated_at = extract(epoch from now())
                    WHERE file_source = $1
                      AND (image_width IS NULL OR image_height IS NULL)""",
                file_source, dims[0], dims[1],
            )
        fixed += 1
        if fixed % 25 == 0:
            logger.info("Progress: %d/%d", fixed, len(rows))

    logger.info("Done. Fixed=%d skipped=%d total=%d (dry_run=%s)",
                fixed, skipped, len(rows), dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()
    asyncio.run(backfill(args.dry_run))


if __name__ == "__main__":
    main()