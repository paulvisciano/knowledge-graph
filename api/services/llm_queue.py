from __future__ import annotations

import json
import logging
import time
import uuid

from api.services.db import get_pool

logger = logging.getLogger(__name__)

VALID_STATUSES = ("pending", "streaming", "complete", "error", "cancelled")


async def create_llm_job(
    conv_id: str,
    model: str | None = None,
    system_prompt: str | None = None,
) -> dict:
    """If conv_id already has a pending/streaming job, return it instead of creating a duplicate."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT * FROM llm_jobs WHERE conv_id = $1 AND status IN ('pending', 'streaming') ORDER BY created_at DESC LIMIT 1",
            conv_id,
        )
        if existing:
            return dict(existing)

        job_id = uuid.uuid4().hex[:12]
        now = time.time()
        await conn.execute(
            """INSERT INTO llm_jobs (id, conv_id, status, model, system_prompt, created_at, updated_at, error)
               VALUES ($1, $2, 'pending', $3, $4, $5, $6, NULL)""",
            job_id, conv_id, model, system_prompt, now, now,
        )
        row = await conn.fetchrow("SELECT * FROM llm_jobs WHERE id = $1", job_id)
    return dict(row)


async def claim_llm_job() -> dict | None:
    """SELECT ... FOR UPDATE SKIP LOCKED then UPDATE status='streaming' for safe concurrent claiming."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM llm_jobs WHERE status = 'pending' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1",
            )
            if not row:
                return None
            job_id = row["id"]
            now = time.time()
            await conn.execute(
                "UPDATE llm_jobs SET status = 'streaming', updated_at = $2 WHERE id = $1",
                job_id, now,
            )
            row = await conn.fetchrow("SELECT * FROM llm_jobs WHERE id = $1", job_id)
    return dict(row) if row else None


async def update_llm_job_status(
    job_id: str,
    status: str,
    error: str | None = None,
) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status '{status}'. Must be one of {VALID_STATUSES}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE llm_jobs SET status = $2, error = $3, updated_at = $4 WHERE id = $1",
            job_id, status, error, time.time(),
        )


async def append_llm_job_event(
    job_id: str,
    event_type: str,
    event_data: dict,
    conv_id: str | None = None,
) -> None:
    """Persist event to llm_job_events and pg_notify('llm_job_events') for EventBus pickup. Lookups conv_id from llm_jobs if not provided."""
    if isinstance(event_data, str):
        try:
            event_data = json.loads(event_data)
        except (json.JSONDecodeError, TypeError):
            event_data = {"raw": event_data}

    pool = await get_pool()
    async with pool.acquire() as conn:
        if conv_id is None:
            job_row = await conn.fetchrow(
                "SELECT conv_id FROM llm_jobs WHERE id = $1",
                job_id,
            )
            conv_id = job_row["conv_id"] if job_row else None

        row = await conn.fetchrow(
            """INSERT INTO llm_job_events (job_id, event_type, event_data, created_at)
               VALUES ($1, $2, $3::jsonb, $4) RETURNING id""",
            job_id, event_type, json.dumps(event_data), time.time(),
        )
        event_id = row["id"] if row else None

        payload = json.dumps({
            "id": event_id,
            "job_id": job_id,
            "conv_id": conv_id,
            "event_type": event_type,
            "data": event_data,
        })
        await conn.execute("SELECT pg_notify($1, $2)", "llm_job_events", payload)


async def get_llm_job_events(job_id: str, after: int = 0) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if after > 0:
            rows = await conn.fetch(
                "SELECT id, event_type, event_data, created_at FROM llm_job_events WHERE job_id = $1 AND id > $2 ORDER BY id",
                job_id, after,
            )
        else:
            rows = await conn.fetch(
                "SELECT id, event_type, event_data, created_at FROM llm_job_events WHERE job_id = $1 ORDER BY id",
                job_id,
            )
    return [dict(r) for r in rows]


async def get_pending_llm_jobs_count() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) AS cnt FROM llm_jobs WHERE status = 'pending'",
        )
    return row["cnt"] if row else 0


async def cancel_llm_job(job_id: str) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE llm_jobs SET status = 'cancelled', updated_at = $2 WHERE id = $1 AND status IN ('pending', 'streaming')",
            job_id, time.time(),
        )
    return result == "UPDATE 1"


async def get_job_statuses_for_conversations(conv_ids: list[str]) -> list[dict]:
    if not conv_ids:
        return []
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT conv_id, status, id AS job_id FROM llm_jobs WHERE conv_id = ANY($1)",
            conv_ids,
        )
    return [dict(r) for r in rows]