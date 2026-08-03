"""FastAPI router for chat message submission, SSE event streaming,
conversation status polling, and LLM job cancellation.

Mirrors the transaction/conversation-save patterns from sync.py and the
SSE streaming patterns from images.py.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse, ServerSentEvent

from api.routes.sync import DBConversation, DBMessage
from api.services import db as db_module
from api.services.event_bus import event_bus
from api.services.llm_queue import (
    cancel_llm_job,
    create_llm_job,
    get_job_statuses_for_conversations,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class AttachmentPayload(BaseModel):
    name: str
    mimeType: str
    dataUrl: str


class SubmitMessageRequest(BaseModel):
    content: str = ""
    attachments: list[AttachmentPayload] | None = None
    audioUrl: str | None = None
    audioData: str | None = None
    audioFormat: str | None = None
    message: DBMessage | None = None
    conversation: DBConversation | None = None
    model: str | None = None
    system_prompt: str | None = None


class SubmitMessageResponse(BaseModel):
    job_id: str
    conv_id: str


class ConversationStatus(BaseModel):
    conv_id: str
    status: str
    job_id: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/conversations/{conv_id}/messages")
async def submit_message(conv_id: str, payload: SubmitMessageRequest):
    """Save a user message, upsert the conversation, create an LLM job, and
    publish a ``new_message`` event to the event bus."""

    # Build the DBMessage from the simple payload if not provided directly
    if payload.message:
        msg = payload.message
        if msg.convId != conv_id:
            msg.convId = conv_id
    else:
        extra = None
        if payload.attachments:
            extra = [{"name": a.name, "mimeType": a.mimeType, "dataUrl": a.dataUrl} for a in payload.attachments]
        if payload.audioUrl:
            extra = (extra or []) + [{"audioUrl": payload.audioUrl}]
        if payload.audioData:
            extra = (extra or []) + [{"audioData": payload.audioData, "audioFormat": payload.audioFormat or "wav"}]
        msg = DBMessage(
            id=str(uuid.uuid4()),
            convId=conv_id,
            type="message",
            timestamp=time.time(),
            role="user",
            content=payload.content,
            extra=extra or None,
        )

    pool = await db_module.get_pool()

    conv = payload.conversation or DBConversation(id=conv_id)
    if conv.id != conv_id:
        conv = conv.model_copy(update={"id": conv_id})

    now = time.time()

    async with pool.acquire() as conn:
        async with conn.transaction():
            # Lock conversation row to prevent concurrent DELETE
            existing = await conn.fetchrow(
                "SELECT id, last_modified FROM conversations WHERE id = $1 FOR UPDATE",
                conv.id,
            )

            last_mod = conv.lastModified if conv.lastModified else now
            if last_mod and last_mod > 1e12:
                last_mod = last_mod / 1000

            mcp_overrides = (
                json.dumps([o.model_dump() for o in conv.mcpServerOverrides])
                if conv.mcpServerOverrides
                else None
            )

            await conn.execute(
                """INSERT INTO conversations (id, name, last_modified, curr_node, mcp_server_overrides,
                       thinking_enabled, reasoning_effort, forked_from_conversation_id, pinned)
               VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
               ON CONFLICT (id) DO UPDATE SET
                   name = CASE WHEN conversations.name <> '' AND (EXCLUDED.name = '' OR EXCLUDED.name IS NULL) THEN conversations.name ELSE EXCLUDED.name END,
                   last_modified = EXCLUDED.last_modified,
                   curr_node = EXCLUDED.curr_node,
                   mcp_server_overrides = EXCLUDED.mcp_server_overrides,
                   thinking_enabled = EXCLUDED.thinking_enabled,
                   reasoning_effort = EXCLUDED.reasoning_effort,
                   forked_from_conversation_id = EXCLUDED.forked_from_conversation_id,
                   pinned = EXCLUDED.pinned""",
                conv.id,
                conv.name or "",
                last_mod,
                conv.currNode,
                mcp_overrides,
                conv.thinkingEnabled,
                conv.reasoningEffort,
                conv.forkedFromConversationId,
                conv.pinned,
            )

            # Save the message
            extra_json = json.dumps(msg.extra) if msg.extra else None
            children_json = json.dumps(msg.children) if msg.children else "[]"
            msg_ts = msg.timestamp if msg.timestamp < 1e12 else msg.timestamp / 1000

            await conn.execute(
                """INSERT INTO messages (id, conv_id, type, timestamp, role, content, parent,
                       children, extra, reasoning_content, tool_calls, completion_id,
                       tool_call_id, timings, model)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10, $11, $12, $13, $14::jsonb, $15)
               ON CONFLICT (id) DO UPDATE SET
                   type = EXCLUDED.type, timestamp = EXCLUDED.timestamp,
                   role = EXCLUDED.role, content = EXCLUDED.content,
                   parent = EXCLUDED.parent, children = EXCLUDED.children,
                   extra = EXCLUDED.extra, reasoning_content = EXCLUDED.reasoning_content,
                   tool_calls = EXCLUDED.tool_calls, completion_id = EXCLUDED.completion_id,
                   tool_call_id = EXCLUDED.tool_call_id, timings = EXCLUDED.timings,
                   model = EXCLUDED.model""",
                msg.id,
                conv.id,
                msg.type,
                msg_ts,
                msg.role,
                msg.content,
                msg.parent,
                children_json,
                extra_json,
                msg.reasoningContent,
                msg.toolCalls,
                msg.completionId,
                msg.toolCallId,
                json.dumps(msg.timings) if msg.timings else None,
                msg.model,
            )

    # --- Create LLM job ---
    job = await create_llm_job(
        conv_id=conv_id,
        model=payload.model,
        system_prompt=payload.system_prompt,
    )

    if not existing:
        await event_bus.publish(
            conv_id,
            {
                "type": "new_conversation",
                "conv_id": conv_id,
                "name": conv.name or "",
                "created_at": last_mod,
            },
        )

    await event_bus.publish(
        conv_id,
        {
            "type": "new_message",
            "conv_id": conv_id,
            "job_id": job["id"],
            "message_id": msg.id,
            "role": msg.role,
            "content": msg.content,
        },
    )

    await event_bus.publish(
        conv_id,
        {
            "type": "status_change",
            "conv_id": conv_id,
            "status": "pending" if job["status"] == "pending" else "streaming",
            "job_id": job["id"],
        },
    )

    return SubmitMessageResponse(job_id=job["id"], conv_id=conv_id)


@router.get("/events")
async def stream_events(request: Request):
    """SSE endpoint that streams all LLM job events to connected clients.

    Supports ``Last-Event-ID`` header for reconnection — replays missed events
    via ``event_bus.get_missed_events()`` before subscribing to live events.
    """

    async def event_stream():
        # --- Replay missed events if Last-Event-ID provided ---
        last_event_id_str = request.headers.get("Last-Event-ID")
        if last_event_id_str:
            try:
                after_id = int(last_event_id_str)
                missed = await event_bus.get_missed_events(after_id)
                for evt in missed:
                    yield ServerSentEvent(
                        data=json.dumps(evt),
                        id=str(evt["id"]),
                    )
            except (ValueError, TypeError):
                logger.warning("Invalid Last-Event-ID header: %s", last_event_id_str)

        # --- Subscribe to live events ---
        unsub = event_bus.subscribe_all()
        try:
            async for evt in event_bus.iterate(unsub):
                # Heartbeat / keep-alive events may not have an id
                evt_id = evt.get("id")
                yield ServerSentEvent(
                    data=json.dumps(evt),
                    id=str(evt_id) if evt_id is not None else None,
                )
        finally:
            unsub.remove()

    return EventSourceResponse(event_stream())


@router.get("/conversations/statuses")
async def conversation_statuses(conv_ids: str = ""):
    """Return status of LLM jobs for the given conversation IDs.

    ``conv_ids`` is a comma-separated list of conversation IDs.
    Only returns entries for conversations that have a pending or streaming job.
    """
    if not conv_ids:
        return []

    ids = [cid.strip() for cid in conv_ids.split(",") if cid.strip()]
    if not ids:
        return []

    rows = await get_job_statuses_for_conversations(ids)
    # Filter to only pending/streaming jobs
    result = [
        ConversationStatus(
            conv_id=r["conv_id"],
            status=r["status"],
            job_id=r["job_id"],
        )
        for r in rows
        if r["status"] in ("pending", "streaming")
    ]
    return result


@router.post("/conversations/{conv_id}/cancel")
async def cancel_conversation(conv_id: str):
    """Cancel a pending LLM job for a conversation.

    Finds the most recent pending/streaming job for the conversation and
    cancels it, publishing a ``status_change`` event.
    """
    pool = await db_module.get_pool()
    async with pool.acquire() as conn:
        job = await conn.fetchrow(
            "SELECT id, status FROM llm_jobs WHERE conv_id = $1 AND status IN ('pending', 'streaming') ORDER BY created_at DESC LIMIT 1",
            conv_id,
        )

    if not job:
        raise HTTPException(status_code=404, detail="No pending or streaming job found for this conversation")

    job_id: str = job["id"]
    cancelled = await cancel_llm_job(job_id)

    if not cancelled:
        raise HTTPException(status_code=409, detail="Job could not be cancelled (may have already started)")

    await event_bus.publish(
        conv_id,
        {
            "type": "status_change",
            "conv_id": conv_id,
            "job_id": job_id,
            "status": "cancelled",
        },
    )

    return {"status": "ok", "job_id": job_id}