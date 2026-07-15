from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.services import db as db_module

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["sync"])


class MCPOverride(BaseModel):
    serverId: str
    enabled: bool


class DBConversation(BaseModel):
    id: str
    name: str = ""
    lastModified: float | None = None
    currNode: str | None = None
    mcpServerOverrides: list[MCPOverride] | None = None
    thinkingEnabled: bool | None = None
    reasoningEffort: str | None = None
    forkedFromConversationId: str | None = None
    pinned: bool | None = None


class DBMessage(BaseModel):
    id: str
    convId: str
    type: str = "message"
    timestamp: float
    role: str
    content: str = ""
    parent: str | None = None
    children: list[str] | None = []
    extra: list[Any] | None = None
    reasoningContent: str | None = None
    toolCalls: str | None = None
    completionId: str | None = None
    toolCallId: str | None = None
    timings: dict[str, Any] | None = None
    model: str | None = None


class ExportedConversation(BaseModel):
    conv: DBConversation
    messages: list[DBMessage] = []


@router.get("")
async def list_conversations():
    pool = await db_module.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, last_modified, curr_node, pinned FROM conversations ORDER BY last_modified DESC"
        )
    return [
        {
            "id": r["id"],
            "name": r["name"],
            "lastModified": r["last_modified"] * 1000 if r["last_modified"] and r["last_modified"] < 1e12 else r["last_modified"],
            "currNode": r["curr_node"],
            "pinned": r["pinned"],
        }
        for r in rows
    ]


@router.get("/since")
async def conversations_since(timestamp: float):
    pool = await db_module.get_pool()
    async with pool.acquire() as conn:
        convs = await conn.fetch(
            "SELECT id, name, last_modified, curr_node, mcp_server_overrides, "
            "thinking_enabled, reasoning_effort, forked_from_conversation_id, pinned "
            "FROM conversations WHERE last_modified > $1 ORDER BY last_modified DESC",
            timestamp / 1000 if timestamp > 1e12 else timestamp,
        )
        results = []
        for conv in convs:
            messages = await conn.fetch(
                "SELECT id, conv_id, type, timestamp, role, content, parent, children, "
                "extra, reasoning_content, tool_calls, completion_id, tool_call_id, timings, model "
                "FROM messages WHERE conv_id = $1 ORDER BY timestamp ASC",
                conv["id"],
            )
            results.append(_row_to_exported(conv, messages))
        return results


@router.get("/{conv_id}")
async def get_conversation(conv_id: str):
    pool = await db_module.get_pool()
    async with pool.acquire() as conn:
        conv = await conn.fetchrow(
            "SELECT id, name, last_modified, curr_node, mcp_server_overrides, "
            "thinking_enabled, reasoning_effort, forked_from_conversation_id, pinned "
            "FROM conversations WHERE id = $1",
            conv_id,
        )
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        messages = await conn.fetch(
            "SELECT id, conv_id, type, timestamp, role, content, parent, children, "
            "extra, reasoning_content, tool_calls, completion_id, tool_call_id, timings, model "
            "FROM messages WHERE conv_id = $1 ORDER BY timestamp ASC",
            conv_id,
        )
    return _row_to_exported(conv, messages)


@router.put("")
async def save_conversation(payload: ExportedConversation):
    pool = await db_module.get_pool()
    conv = payload.conv
    now = time.time()

    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT id, last_modified FROM conversations WHERE id = $1", conv.id)
        if existing:
            if conv.lastModified and existing["last_modified"] is not None:
                existing_ts = existing["last_modified"]
                incoming_ts = conv.lastModified / 1000 if conv.lastModified > 1e12 else conv.lastModified
                if incoming_ts <= existing_ts:
                    raise HTTPException(status_code=409, detail="Server version is newer")

        last_mod = conv.lastModified if conv.lastModified else now
        if last_mod > 1e12:
            last_mod = last_mod / 1000

        mcp_overrides = json.dumps([o.model_dump() for o in conv.mcpServerOverrides]) if conv.mcpServerOverrides else None

        await conn.execute(
            """INSERT INTO conversations (id, name, last_modified, curr_node, mcp_server_overrides,
                   thinking_enabled, reasoning_effort, forked_from_conversation_id, pinned)
               VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
               ON CONFLICT (id) DO UPDATE SET
                   name = EXCLUDED.name,
                   last_modified = EXCLUDED.last_modified,
                   curr_node = EXCLUDED.curr_node,
                   mcp_server_overrides = EXCLUDED.mcp_server_overrides,
                   thinking_enabled = EXCLUDED.thinking_enabled,
                   reasoning_effort = EXCLUDED.reasoning_effort,
                   forked_from_conversation_id = EXCLUDED.forked_from_conversation_id,
                   pinned = EXCLUDED.pinned""",
            conv.id, conv.name or "", last_mod,
            conv.currNode, mcp_overrides,
            conv.thinkingEnabled, conv.reasoningEffort,
            conv.forkedFromConversationId, conv.pinned,
        )

        await conn.execute("DELETE FROM messages WHERE conv_id = $1", conv.id)
        for msg in payload.messages:
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
                msg.id, conv.id, msg.type, msg_ts, msg.role, msg.content,
                msg.parent, children_json, extra_json,
                msg.reasoningContent, msg.toolCalls, msg.completionId,
                msg.toolCallId, json.dumps(msg.timings) if msg.timings else None,
                msg.model,
            )

    return {"status": "ok"}


@router.delete("/{conv_id}")
async def delete_conversation(conv_id: str):
    pool = await db_module.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM conversations WHERE id = $1", conv_id)
        if result == "DELETE 0":
            raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "ok"}


def _row_to_exported(conv_row, message_rows) -> dict:
    return {
        "conv": {
            "id": conv_row["id"],
            "name": conv_row["name"],
            "lastModified": conv_row["last_modified"] * 1000 if conv_row["last_modified"] and conv_row["last_modified"] < 1e12 else conv_row["last_modified"],
            "currNode": conv_row["curr_node"],
            "mcpServerOverrides": json.loads(conv_row["mcp_server_overrides"]) if conv_row["mcp_server_overrides"] else None,
            "thinkingEnabled": conv_row["thinking_enabled"],
            "reasoningEffort": conv_row["reasoning_effort"],
            "forkedFromConversationId": conv_row["forked_from_conversation_id"],
            "pinned": conv_row["pinned"],
        },
        "messages": [
            {
                "id": m["id"],
                "convId": m["conv_id"],
                "type": m["type"],
                "timestamp": m["timestamp"] * 1000 if m["timestamp"] and m["timestamp"] < 1e12 else m["timestamp"],
                "role": m["role"],
                "content": m["content"],
                "parent": m["parent"],
                "children": json.loads(m["children"]) if isinstance(m["children"], str) else (m["children"] or []),
                "extra": json.loads(m["extra"]) if isinstance(m["extra"], str) else m["extra"],
                "reasoningContent": m["reasoning_content"],
                "toolCalls": m["tool_calls"],
                "completionId": m["completion_id"],
                "toolCallId": m["tool_call_id"],
                "timings": json.loads(m["timings"]) if isinstance(m["timings"], str) else m["timings"],
                "model": m["model"],
            }
            for m in message_rows
        ],
    }