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
                updated_at DOUBLE PRECISION NOT NULL DEFAULT extract(epoch from now())
            );

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
        """)
    logger.info("Database tables initialized")


async def close_db() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None