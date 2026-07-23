from __future__ import annotations

import logging
import os
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    f"postgresql://{os.environ.get('POSTGRES_USER', 'lightrag')}:"
    f"{os.environ.get('POSTGRES_PASSWORD', 'lightrag')}"
    f"@{os.environ.get('POSTGRES_HOST', 'postgres')}:"
    f"{os.environ.get('POSTGRES_PORT', '5432')}"
    f"/{os.environ.get('POSTGRES_DATABASE', 'lightrag')}",
)

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=2,
            max_size=10,
        )
    return _pool


async def init_db() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                file_source TEXT NOT NULL,
                file_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                stage TEXT,
                error TEXT,
                skip_exif BOOLEAN NOT NULL DEFAULT FALSE,
                skip_faces BOOLEAN NOT NULL DEFAULT FALSE,
                insert BOOLEAN NOT NULL DEFAULT TRUE,
                created_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
                updated_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
                note TEXT NOT NULL DEFAULT ''
            );

            ALTER TABLE jobs ADD COLUMN IF NOT EXISTS note TEXT NOT NULL DEFAULT '';

            CREATE TABLE IF NOT EXISTS job_events (
                id BIGSERIAL PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                event_data JSONB NOT NULL DEFAULT '{}',
                created_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
            );

            CREATE INDEX IF NOT EXISTS idx_job_events_job_id ON job_events(job_id);
            CREATE INDEX IF NOT EXISTS idx_job_events_created_at ON job_events(job_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                last_modified DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
                curr_node TEXT,
                mcp_server_overrides JSONB,
                thinking_enabled BOOLEAN,
                reasoning_effort TEXT,
                forked_from_conversation_id TEXT,
                pinned BOOLEAN
            );

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conv_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                type TEXT NOT NULL DEFAULT 'message',
                timestamp DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
                role TEXT NOT NULL,
                content TEXT NOT NULL DEFAULT '',
                parent TEXT,
                children JSONB NOT NULL DEFAULT '[]',
                extra JSONB,
                reasoning_content TEXT,
                tool_calls TEXT,
                completion_id TEXT,
                tool_call_id TEXT,
                timings JSONB,
                model TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_messages_conv_id ON messages(conv_id);
            CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(conv_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_conversations_last_modified ON conversations(last_modified);

            CREATE TABLE IF NOT EXISTS photo_metadata (
                file_source TEXT PRIMARY KEY,
                exif_data JSONB NOT NULL DEFAULT '{}',
                date_taken TEXT,
                date_taken_friendly TEXT,
                image_width INTEGER,
                image_height INTEGER,
                created_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
                updated_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
            );

            -- Backfill the typed columns onto pre-existing rows. Idempotent so
            -- re-running init_db on an already-migrated DB is a no-op.
            ALTER TABLE photo_metadata
                ADD COLUMN IF NOT EXISTS date_taken TEXT,
                ADD COLUMN IF NOT EXISTS date_taken_friendly TEXT,
                ADD COLUMN IF NOT EXISTS image_width INTEGER,
                ADD COLUMN IF NOT EXISTS image_height INTEGER;

            -- Populate the typed columns from the JSONB blob for any row that
            -- was written before the columns existed. Runs once per startup;
            -- the WHERE clause skips rows already populated.
            UPDATE photo_metadata
               SET date_taken = (exif_data->>'date_taken'),
                   date_taken_friendly = (exif_data->>'date_taken_friendly'),
                   image_width = NULLIF(exif_data->>'image_width','')::int,
                   image_height = NULLIF(exif_data->>'image_height','')::int
             WHERE date_taken IS NULL
               AND date_taken_friendly IS NULL
               AND image_width IS NULL
               AND image_height IS NULL;

            CREATE INDEX IF NOT EXISTS idx_photo_metadata_dates
                ON photo_metadata(file_source);

            -- Single-row app settings. id is fixed at 0; the CHECK constraint
            -- rejects any other id, so reads/writes don't need a WHERE clause.
            CREATE TABLE IF NOT EXISTS app_settings (
                id INTEGER PRIMARY KEY DEFAULT 0,
                face_detection_enabled BOOLEAN NOT NULL DEFAULT FALSE,
                CONSTRAINT singleton CHECK (id = 0)
            );
            INSERT INTO app_settings (id, face_detection_enabled)
            VALUES (0, FALSE)
            ON CONFLICT (id) DO NOTHING;
        """)
    logger.info("Database tables initialized")


async def close_db() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def save_photo_exif(file_source: str, exif_data: dict) -> None:
    """Persist EXIF metadata, writing the layout-critical fields to typed
    columns so the graph-load path can read them without parsing JSONB or
    touching image files.

    ``date_taken`` / ``date_taken_friendly`` / ``image_width`` / ``image_height``
    are pulled out of ``exif_data`` and stored as columns. When the extractor
    didn't emit pixel dimensions (older extractor path, or a file whose EXIF
    only sets ``ExifImageLength``), the dimensions are back-filled here from
    the actual image via PIL so the expensive I/O happens exactly once, at
    processing time — never on the per-load graph query path.
    """
    import json

    date_taken = exif_data.get("date_taken")
    friendly = exif_data.get("date_taken_friendly")
    width = exif_data.get("image_width")
    height = exif_data.get("image_height")

    # Backfill pixel dimensions from the file when EXIF omitted them, so the
    # graph query never has to open an image. Best-effort; failures leave the
    # columns NULL and the layout falls back to a default aspect ratio.
    if (width is None or height is None):
        dims = _image_dims_from_file(file_source)
        if dims:
            if width is None:
                width = dims[0]
            if height is None:
                height = dims[1]

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO photo_metadata
                 (file_source, exif_data, date_taken, date_taken_friendly,
                  image_width, image_height, created_at, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, extract(epoch from now()), extract(epoch from now()))
               ON CONFLICT (file_source)
               DO UPDATE SET
                 exif_data = $2,
                 date_taken = COALESCE($3, photo_metadata.date_taken),
                 date_taken_friendly = COALESCE($4, photo_metadata.date_taken_friendly),
                 image_width = COALESCE($5, photo_metadata.image_width),
                 image_height = COALESCE($6, photo_metadata.image_height),
                 updated_at = extract(epoch from now())""",
            file_source, json.dumps(exif_data),
            str(date_taken) if date_taken is not None else None,
            str(friendly) if friendly is not None else None,
            int(width) if width is not None else None,
            int(height) if height is not None else None,
        )


def _image_dims_from_file(file_source: str) -> tuple[int, int] | None:
    """Return (width, height) for the image at INPUT_DIR/file_source, or None
    on failure. Honours EXIF orientation so portrait photos keep correct
    proportions. Synchronous (call from a threadpool)."""
    import os
    from pathlib import Path

    input_dir = Path(os.environ.get(
        "INPUT_DIR",
        str(Path(__file__).resolve().parent.parent.parent / "inputs"),
    ))
    file_path = input_dir / file_source
    if not file_path.is_file():
        return None
    try:
        from PIL import Image, ImageOps

        with Image.open(str(file_path)) as img:
            img = ImageOps.exif_transpose(img)
            return img.size  # (width, height) post-orientation
    except Exception:
        return None


async def get_photo_exif(file_source: str) -> dict | None:
    import json

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT exif_data FROM photo_metadata WHERE file_source = $1",
            file_source,
        )
    if row and row["exif_data"]:
        return json.loads(row["exif_data"])
    return None


async def get_photo_dates_for_sources(
    file_sources: list[str],
) -> dict[str, dict[str, object]]:
    """Return {file_source: {date_taken?, date_taken_friendly?, width?, height?}}
    scoped to a set of file_sources.

    Used by the /graphs proxy to enrich only the Photo nodes present in the
    current graph view instead of scanning every photo in the DB.
    """
    if not file_sources:
        return {}
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT file_source, date_taken, date_taken_friendly,
                      image_width, image_height
                 FROM photo_metadata
                WHERE file_source = ANY($1)
                  AND (date_taken IS NOT NULL
                       OR date_taken_friendly IS NOT NULL
                       OR image_width IS NOT NULL
                       OR image_height IS NOT NULL)""",
            file_sources,
        )

    out: dict[str, dict[str, object]] = {}
    for row in rows:
        entry: dict[str, object] = {}
        if row["date_taken"] is not None:
            entry["date_taken"] = str(row["date_taken"])
        if row["date_taken_friendly"] is not None:
            entry["date_taken_friendly"] = str(row["date_taken_friendly"])
        if row["image_width"] is not None:
            entry["width"] = int(row["image_width"])
        if row["image_height"] is not None:
            entry["height"] = int(row["image_height"])
        if entry:
            out[row["file_source"]] = entry
    return out


async def get_app_settings() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT face_detection_enabled FROM app_settings WHERE id = 0")
    if row is None:
        return {"face_detection_enabled": False}
    return {"face_detection_enabled": row["face_detection_enabled"]}


async def update_app_settings(updates: dict) -> dict:
    allowed = {"face_detection_enabled"}
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return await get_app_settings()

    set_clauses = []
    args: list = []
    for i, (col, val) in enumerate(filtered.items(), start=1):
        set_clauses.append(f"{col} = ${i}")
        args.append(val)
    sql = f"UPDATE app_settings SET {', '.join(set_clauses)} WHERE id = 0"

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(sql, *args)
    return await get_app_settings()