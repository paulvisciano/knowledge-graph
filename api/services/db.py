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
            # Docker networking silently drops idle TCP connections; without
            # these settings the pool retains dead sockets and every
            # acquire() on one blocks forever (the recurring "stale conn" bug).
            command_timeout=30,
            max_inactive_connection_lifetime=300,
            server_settings={
                "tcp_keepalives_idle": "30",
                "tcp_keepalives_interval": "10",
                "tcp_keepalives_count": "3",
            },
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
                note TEXT NOT NULL DEFAULT '',
                file_type TEXT NOT NULL DEFAULT 'image'
            );

            -- Idempotent backfill of the file_type column onto pre-existing
            -- rows. The table historically only held image jobs, so the
            -- default 'image' is correct for legacy rows; notes bypass the
            -- jobs table entirely (POST /notes goes straight to LightRAG).
            ALTER TABLE jobs ADD COLUMN IF NOT EXISTS note TEXT NOT NULL DEFAULT '';
            ALTER TABLE jobs ADD COLUMN IF NOT EXISTS file_type TEXT NOT NULL DEFAULT 'image';

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
            -- Partial index for the worker's pending-job poll query
            -- (SELECT ... WHERE status='pending' FOR UPDATE SKIP LOCKED) so
            -- the claim scan stays cheap as the jobs table grows.
            CREATE INDEX IF NOT EXISTS idx_jobs_status_pending ON jobs(status) WHERE status = 'pending';

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

            CREATE TABLE IF NOT EXISTS llm_jobs (
                id TEXT PRIMARY KEY,
                conv_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'pending',
                model TEXT,
                system_prompt TEXT,
                created_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
                updated_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
                error TEXT
            );

            CREATE TABLE IF NOT EXISTS llm_job_events (
                id BIGSERIAL PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES llm_jobs(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                event_data JSONB NOT NULL DEFAULT '{}',
                created_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
            );

            CREATE INDEX IF NOT EXISTS idx_llm_jobs_status ON llm_jobs(status);
            CREATE INDEX IF NOT EXISTS idx_llm_jobs_conv_id ON llm_jobs(conv_id);
            CREATE INDEX IF NOT EXISTS idx_llm_job_events_job_id ON llm_job_events(job_id);
            CREATE INDEX IF NOT EXISTS idx_llm_job_events_created_at ON llm_job_events(job_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_llm_jobs_status_pending ON llm_jobs(status) WHERE status = 'pending';

            CREATE TABLE IF NOT EXISTS photo_metadata (
                file_source TEXT PRIMARY KEY,
                exif_data JSONB NOT NULL DEFAULT '{}',
                date_taken TEXT,
                date_taken_friendly TEXT,
                image_width INTEGER,
                image_height INTEGER,
                metadata_text TEXT,
                created_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
                updated_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
            );

            -- Backfill the typed columns onto pre-existing rows. Idempotent so
            -- re-running init_db on an already-migrated DB is a no-op.
            ALTER TABLE photo_metadata
                ADD COLUMN IF NOT EXISTS date_taken TEXT,
                ADD COLUMN IF NOT EXISTS date_taken_friendly TEXT,
                ADD COLUMN IF NOT EXISTS image_width INTEGER,
                ADD COLUMN IF NOT EXISTS image_height INTEGER,
                ADD COLUMN IF NOT EXISTS metadata_text TEXT;

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
                pinch_zoom_sensitivity REAL NOT NULL DEFAULT 0.25,
                CONSTRAINT singleton CHECK (id = 0)
            );
            INSERT INTO app_settings (id, face_detection_enabled)
            VALUES (0, FALSE)
            ON CONFLICT (id) DO NOTHING;

            -- Backfill the sensitivity column onto pre-existing rows. Idempotent
            -- so re-running init_db on an already-migrated DB is a no-op.
            ALTER TABLE app_settings
                ADD COLUMN IF NOT EXISTS pinch_zoom_sensitivity REAL NOT NULL DEFAULT 0.25;
        """)
    logger.info("Database tables initialized")


async def close_db() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def save_photo_exif(
    file_source: str,
    exif_data: dict,
    metadata_text: str | None = None,
) -> None:
    """Persist EXIF metadata, writing the layout-critical fields to typed
    columns so the graph-load path can read them without parsing JSONB or
    touching image files.

    ``date_taken`` / ``date_taken_friendly`` / ``image_width`` / ``image_height``
    are pulled out of ``exif_data`` and stored as columns. When the extractor
    didn't emit pixel dimensions (older extractor path, or a file whose EXIF
    only sets ``ExifImageLength``), the dimensions are back-filled here from
    the actual image via PIL so the expensive I/O happens exactly once, at
    processing time — never on the per-load graph query path.

    ``metadata_text`` is the phase-1 output (EXIF + faces captions built by
    ``_build_metadata_text``) that phase 2 needs for VLM context and the
    combined LightRAG document. Stored alongside EXIF so phase 2 can resume
    from DB state without re-running EXIF.
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
                  image_width, image_height, metadata_text, created_at, updated_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, extract(epoch from now()), extract(epoch from now()))
               ON CONFLICT (file_source)
               DO UPDATE SET
                 exif_data = $2,
                 date_taken = COALESCE($3, photo_metadata.date_taken),
                 date_taken_friendly = COALESCE($4, photo_metadata.date_taken_friendly),
                 image_width = COALESCE($5, photo_metadata.image_width),
                 image_height = COALESCE($6, photo_metadata.image_height),
                 metadata_text = COALESCE($7, photo_metadata.metadata_text),
                 updated_at = extract(epoch from now())""",
            file_source, json.dumps(exif_data),
            str(date_taken) if date_taken is not None else None,
            str(friendly) if friendly is not None else None,
            int(width) if width is not None else None,
            int(height) if height is not None else None,
            metadata_text,
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


async def get_photo_metadata_text(file_source: str) -> str | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT metadata_text FROM photo_metadata WHERE file_source = $1",
            file_source,
        )
    if row is None:
        return None
    return row["metadata_text"]


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


async def get_file_types_for_sources(
    file_sources: list[str],
) -> dict[str, str]:
    """Resolve a batch of file_sources to their document file_type.

    Checks the `jobs` table first (file_type column), then falls back to
    `photo_metadata` (any row there is an 'image'). Returns {} for
    file_sources with no known type — the caller treats unknown as
    'unknown' and the UI defaults to a safe renderer.

    Mirrors `get_photo_dates_for_sources` in shape and batching.
    """
    if not file_sources:
        return {}
    pool = await get_pool()
    async with pool.acquire() as conn:
        # A file_source may have multiple job rows (re-uploads/retries);
        # take the most recent one per file_source via DISTINCT ON.
        job_rows = await conn.fetch(
            """SELECT DISTINCT ON (file_source) file_source, file_type
                 FROM jobs
                WHERE file_source = ANY($1)
                ORDER BY file_source, created_at DESC""",
            file_sources,
        )
        # Any file_source present in photo_metadata but not in jobs is an
        # image (legacy uploads that predate the jobs table, or photos
        # whose job row was pruned).
        photo_rows = await conn.fetch(
            """SELECT file_source FROM photo_metadata
                WHERE file_source = ANY($1)""",
            file_sources,
        )

    out: dict[str, str] = {}
    for row in job_rows:
        ft = row["file_type"]
        if ft:
            out[row["file_source"]] = ft
    for row in photo_rows:
        src = row["file_source"]
        # jobs is authoritative; don't override a known non-image type
        out.setdefault(src, "image")
    return out


async def get_app_settings() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT face_detection_enabled, pinch_zoom_sensitivity FROM app_settings WHERE id = 0"
        )
    if row is None:
        return {"face_detection_enabled": False, "pinch_zoom_sensitivity": 0.25}
    return {
        "face_detection_enabled": row["face_detection_enabled"],
        "pinch_zoom_sensitivity": float(row["pinch_zoom_sensitivity"]),
    }


async def update_app_settings(updates: dict) -> dict:
    allowed = {"face_detection_enabled", "pinch_zoom_sensitivity"}
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