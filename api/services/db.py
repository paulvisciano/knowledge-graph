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
                created_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now()),
                updated_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
            );

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
    import json

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO photo_metadata (file_source, exif_data, created_at, updated_at)
               VALUES ($1, $2, extract(epoch from now()), extract(epoch from now()))
               ON CONFLICT (file_source)
               DO UPDATE SET exif_data = $2, updated_at = extract(epoch from now())""",
            file_source, json.dumps(exif_data),
        )


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


async def get_bulk_photo_dates() -> dict[str, dict[str, str]]:
    """Return {file_source: {date_taken, date_taken_friendly}} for every photo
    that has EXIF date data. Used by the UI to position Photo nodes on the
    canvas by the date the photo was taken (from EXIF) rather than the
    LightRAG node-creation/upload timestamp (created_at).
    """
    import json

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT file_source, exif_data FROM photo_metadata")
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        try:
            data = json.loads(row["exif_data"]) if row["exif_data"] else {}
        except (json.JSONDecodeError, TypeError):
            continue
        date_taken = data.get("date_taken")
        friendly = data.get("date_taken_friendly")
        if not (date_taken or friendly):
            continue
        entry: dict[str, str] = {}
        if date_taken:
            entry["date_taken"] = str(date_taken)
        if friendly:
            entry["date_taken_friendly"] = str(friendly)
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