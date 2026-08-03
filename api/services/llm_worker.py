"""Server-side LLM worker that processes jobs sequentially.

Runs as a separate process (``python -m api.llm_worker``) alongside the
FastAPI API and the image-processing worker.  The worker polls
``claim_llm_job()`` for pending jobs and processes them one at a time,
respecting llama-server's limited slots.

The streaming / tool-call logic faithfully replicates the client-side
``streamAssistantResponse`` function in ``+page.svelte`` (lines 762-1147):
  * Turn 1:  tool_choice='required' with ALL enabled MCP tools
  * Turn 2+: tool_choice='auto' with only query tools (strip save/query KG tools)
  * Max 3 turns
  * SSE token streaming with content + reasoning_content deltas
  * Tool call accumulation from delta fragments
  * MCP tool calls via JSON-RPC over HTTP

Event flow:
  llama-server SSE  →  parse tokens  →  append_llm_job_event()
                                           ↓
                                    pg_notify('llm_job_events')
                                           ↓
                                    EventBus → SSE clients
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx

from api.services import config
from api.services.db import get_pool
from api.services.llm_queue import (
    append_llm_job_event,
    claim_llm_job,
    update_llm_job_status,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LLAMA_API = os.environ.get("LLAMA_API", config.vlm_url()).rstrip("/")
MCP_API = os.environ.get("MCP_API", "http://localhost:3001").rstrip("/")

# Default model when the job doesn't specify one
DEFAULT_MODEL = os.environ.get("LLM_DEFAULT_MODEL", "Bonsai-27B-Q1_0")

# System prompt used when the job has no system_prompt field
DEFAULT_SYSTEM_PROMPT = os.environ.get(
    "LLM_DEFAULT_SYSTEM_PROMPT",
    "You are a helpful assistant with access to a knowledge graph. "
    "Use the available tools to save and retrieve information as needed.",
)

# LLM generation parameters (matching the client)
MAX_TOKENS = 2048
TEMPERATURE = 0.7
FREQUENCY_PENALTY = 0.3
PRESENCE_PENALTY = 0.2
REPEAT_PENALTY = 1.15
MAX_TURNS = 3

# Tool names to strip after turn 1
SAVE_AND_QUERY_TOOLS = frozenset({
    "save_to_knowledge_graph",
    "query_knowledge_graph",
    "query_knowledge_graph_stream",
})

# Poll interval when no jobs are pending
POLL_INTERVAL = float(os.environ.get("LLM_WORKER_POLL_INTERVAL", "1.0"))

# MCP JSON-RPC request ID counter
_mcp_request_id = 0


# ---------------------------------------------------------------------------
# MCP helpers
# ---------------------------------------------------------------------------

async def _mcp_initialize(client: httpx.AsyncClient) -> dict[str, Any] | None:
    """Send MCP ``initialize`` + ``notifications/initialized`` handshake.

    Returns the server info dict on success, None on failure.
    """
    global _mcp_request_id
    _mcp_request_id += 1
    try:
        resp = await client.post(
            f"{MCP_API}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": _mcp_request_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "nexus-llm-worker", "version": "0.1.0"},
                },
            },
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        if resp.status_code != 200:
            logger.warning("MCP initialize failed: HTTP %d", resp.status_code)
            return None
        data = resp.json()
        if data.get("error"):
            logger.warning("MCP initialize error: %s", data["error"])
            return None
        # Send initialized notification (fire-and-forget)
        _mcp_request_id += 1
        await client.post(
            f"{MCP_API}/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        return data.get("result", {}).get("serverInfo")
    except Exception:
        logger.exception("MCP initialize failed")
        return None


async def _mcp_list_tools(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """Fetch the list of available MCP tools as OpenAI-compatible tool definitions."""
    global _mcp_request_id
    _mcp_request_id += 1
    try:
        resp = await client.post(
            f"{MCP_API}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": _mcp_request_id,
                "method": "tools/list",
            },
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        if resp.status_code != 200:
            logger.warning("MCP tools/list failed: HTTP %d", resp.status_code)
            return []
        data = resp.json()
        if data.get("error"):
            logger.warning("MCP tools/list error: %s", data["error"])
            return []

        raw_tools = data.get("result", {}).get("tools", [])
        openai_tools: list[dict[str, Any]] = []
        for tool in raw_tools:
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("inputSchema"),
                },
            })
        return openai_tools
    except Exception:
        logger.exception("MCP tools/list failed")
        return []


async def _mcp_call_tool(
    client: httpx.AsyncClient,
    name: str,
    arguments: dict[str, Any],
) -> str:
    """Call an MCP tool via JSON-RPC and return the result string."""
    global _mcp_request_id
    _mcp_request_id += 1
    try:
        resp = await client.post(
            f"{MCP_API}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": _mcp_request_id,
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": arguments,
                },
            },
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        if resp.status_code != 200:
            return f"Tool call failed: HTTP {resp.status_code}"

        data = resp.json()
        if data.get("error"):
            return data["error"].get("message", "Tool call failed")

        result = data.get("result", {})
        # MCP results have a "content" array of content blocks
        content = result.get("content")
        if content and isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
                else:
                    parts.append(json.dumps(block))
            return "\n".join(parts)

        return json.dumps(result)
    except Exception as exc:
        logger.exception("MCP tool call '%s' failed", name)
        return f"Tool call error: {exc}"


# ---------------------------------------------------------------------------
# Message loading from DB
# ---------------------------------------------------------------------------

async def _load_conversation_messages(conv_id: str) -> list[dict[str, Any]]:
    """Load all messages for a conversation from the DB, ordered by timestamp."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM messages WHERE conv_id = $1 ORDER BY timestamp ASC",
            conv_id,
        )
    return [dict(r) for r in rows]


async def _save_assistant_message(
    conv_id: str,
    content: str,
    reasoning_content: str | None = None,
    tool_calls: str | None = None,
    model: str | None = None,
    timings: dict | None = None,
) -> str:
    """Save a final assistant message to the messages table and update conversation."""
    import uuid
    msg_id = uuid.uuid4().hex[:12]
    now = time.time()
    extra = json.dumps(timings) if timings else None

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO messages (id, conv_id, role, content, timestamp, reasoning_content, tool_calls, extra, model)
               VALUES ($1, $2, 'assistant', $3, $4, $5, $6, $7, $8)""",
            msg_id, conv_id, content, now, reasoning_content, tool_calls, extra, model,
        )
        # Update conversation's last_modified timestamp
        await conn.execute(
            "UPDATE conversations SET last_modified = $1 WHERE id = $2",
            now, conv_id,
        )
    return msg_id


# ---------------------------------------------------------------------------
# SSE parsing
# ---------------------------------------------------------------------------

def _parse_sse_lines(buffer: str) -> tuple[list[dict[str, Any]], str]:
    """Parse SSE data from a buffer. Returns (parsed_events, remaining_buffer).

    SSE format: lines separated by double-newline. Each event has:
      data: <json>\n\n
    The sentinel ``data: [DONE]`` is skipped.
    """
    events: list[dict[str, Any]] = []
    remaining = buffer

    # Split on double newline to get SSE event boundaries
    while "\n\n" in remaining:
        event_str, remaining = remaining.split("\n\n", 1)
        for line in event_str.split("\n"):
            line = line.strip()
            if not line or not line.startswith("data: "):
                continue
            data = line[6:]  # strip "data: " prefix
            if data == "[DONE]":
                continue
            try:
                events.append(json.loads(data))
            except (json.JSONDecodeError, TypeError):
                logger.warning("Failed to parse SSE data: %s", data[:100])

    return events, remaining


# ---------------------------------------------------------------------------
# Core LLM job processing
# ---------------------------------------------------------------------------

async def process_llm_job(job: dict[str, Any]) -> None:
    """Process a single LLM job: load messages, stream from llama-server,
    handle tool calls, persist results.

    This is the server-side equivalent of ``streamAssistantResponse`` in
    ``+page.svelte``.
    """
    job_id: str = job["id"]
    conv_id: str = job["conv_id"]
    model: str = job.get("model") or DEFAULT_MODEL
    system_prompt: str = job.get("system_prompt") or DEFAULT_SYSTEM_PROMPT

    # Replace date placeholder like the client does
    from datetime import date
    system_prompt = system_prompt.replace("{{CURRENT_DATE}}", date.today().isoformat())

    logger.info("Processing LLM job %s for conv %s", job_id, conv_id)

    # Mark job as streaming
    await update_llm_job_status(job_id, "streaming")

    # Publish "start" event so clients know processing has begun
    await append_llm_job_event(
        job_id, "start",
        {"conv_id": conv_id, "model": model},
        conv_id=conv_id,
    )

    # Load conversation messages from DB
    db_messages = await _load_conversation_messages(conv_id)

    # Build the messages array for the API
    api_messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
    ]
    for msg in db_messages:
        if msg.get("role") == "user":
            api_messages.append({"role": "user", "content": msg.get("content", "")})
        elif msg.get("role") == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": msg.get("content", "") or None}
            # Include tool_calls if present
            if msg.get("tool_calls"):
                try:
                    entry["tool_calls"] = json.loads(msg["tool_calls"]) if isinstance(msg["tool_calls"], str) else msg["tool_calls"]
                except (json.JSONDecodeError, TypeError):
                    pass
            api_messages.append(entry)
        elif msg.get("role") == "tool":
            api_messages.append({
                "role": "tool",
                "tool_call_id": msg.get("tool_call_id", ""),
                "content": msg.get("content", ""),
            })

    # Fetch MCP tools
    async with httpx.AsyncClient(timeout=30.0) as http_client:
        server_info = await _mcp_initialize(http_client)
        if server_info:
            logger.info("MCP server: %s v%s", server_info.get("name"), server_info.get("version"))

        all_tools = await _mcp_list_tools(http_client)
        if not all_tools:
            logger.warning("No MCP tools available — proceeding without tools")

        # Multi-turn loop (matching client's maxTurns = 3)
        for turn in range(1, MAX_TURNS + 1):
            logger.info("Job %s turn %d/%d", job_id, turn, MAX_TURNS)

            # Determine tools for this turn
            # Turn 1: all tools, tool_choice='required'
            # Turn 2+: only query tools (strip save + KG query tools), tool_choice='auto'
            if turn == 1:
                turn_tools = all_tools
                tool_choice = "required" if all_tools else None
            else:
                turn_tools = [
                    t for t in all_tools
                    if t.get("function", {}).get("name") not in SAVE_AND_QUERY_TOOLS
                ]
                tool_choice = "auto" if turn_tools else None

            # Build request body
            request_body: dict[str, Any] = {
                "model": model,
                "messages": api_messages,
                "stream": True,
                "reasoning_format": "deepseek",
                "max_tokens": MAX_TOKENS,
                "stop": ["<|im_end|>"],
                "temperature": TEMPERATURE,
                "frequency_penalty": FREQUENCY_PENALTY,
                "presence_penalty": PRESENCE_PENALTY,
                "repeat_penalty": REPEAT_PENALTY,
            }
            if turn_tools:
                request_body["tools"] = turn_tools
                request_body["tool_choice"] = tool_choice

            # Stream from llama-server
            accumulated_content = ""
            accumulated_thinking = ""
            tool_calls: list[dict[str, str]] = []
            finish_reason = ""
            timings: dict[str, Any] = {}

            try:
                async with httpx.AsyncClient(timeout=None) as stream_client:
                    async with stream_client.stream(
                        "POST",
                        f"{LLAMA_API}/v1/chat/completions",
                        json=request_body,
                        headers={"Content-Type": "application/json"},
                    ) as response:
                        if response.status_code != 200:
                            err_body = await response.aread()
                            err_text = err_body.decode("utf-8", errors="replace")[:500]
                            raise RuntimeError(f"llama-server HTTP {response.status_code}: {err_text}")

                        buffer = ""
                        async for chunk in response.aiter_text():
                            buffer += chunk

                            # Parse any complete SSE events from the buffer
                            events, buffer = _parse_sse_lines(buffer)

                            for parsed in events:
                                choices = parsed.get("choices", [])
                                if not choices:
                                    continue
                                choice = choices[0]
                                delta = choice.get("delta", {})
                                if not delta:
                                    # Some chunks have no delta (e.g. role-only)
                                    if choice.get("finish_reason"):
                                        finish_reason = choice["finish_reason"]
                                    continue

                                # Content delta
                                if delta.get("content"):
                                    accumulated_content += delta["content"]
                                    await append_llm_job_event(
                                        job_id, "token",
                                        {"content": delta["content"]},
                                        conv_id=conv_id,
                                    )

                                # Reasoning/thinking delta
                                if delta.get("reasoning_content"):
                                    accumulated_thinking += delta["reasoning_content"]
                                    await append_llm_job_event(
                                        job_id, "token",
                                        {"reasoning_content": delta["reasoning_content"]},
                                        conv_id=conv_id,
                                    )

                                # Tool call delta — accumulate by index
                                if delta.get("tool_calls"):
                                    for tc in delta["tool_calls"]:
                                        idx = tc.get("index", len(tool_calls))
                                        while len(tool_calls) <= idx:
                                            tool_calls.append({"id": f"call_{idx}", "name": "", "arguments": ""})
                                        if tc.get("id"):
                                            tool_calls[idx]["id"] = tc["id"]
                                        if tc.get("function", {}).get("name"):
                                            tool_calls[idx]["name"] = tc["function"]["name"]
                                        if tc.get("function", {}).get("arguments"):
                                            tool_calls[idx]["arguments"] += tc["function"]["arguments"]

                                # Finish reason
                                if choice.get("finish_reason"):
                                    finish_reason = choice["finish_reason"]

                                # Timings (llama-server adds these)
                                if parsed.get("timings"):
                                    t = parsed["timings"]
                                    if t.get("predicted_per_second"):
                                        timings["predicted_per_second"] = t["predicted_per_second"]
                                    if t.get("prompt_n"):
                                        timings["prompt_n"] = t["prompt_n"]
                                    if t.get("predicted_n"):
                                        timings["predicted_n"] = t["predicted_n"]
                                    if t.get("prompt_ms"):
                                        timings["prompt_ms"] = t["prompt_ms"]
                                    if t.get("predicted_ms"):
                                        timings["predicted_ms"] = t["predicted_ms"]

                        # Process any remaining buffer (shouldn't be any complete events left,
                        # but parse just in case)
                        if buffer.strip():
                            events, _ = _parse_sse_lines(buffer + "\n\n")
                            for parsed in events:
                                choices = parsed.get("choices", [])
                                if not choices:
                                    continue
                                choice = choices[0]
                                delta = choice.get("delta", {})
                                if delta.get("content"):
                                    accumulated_content += delta["content"]
                                    await append_llm_job_event(
                                        job_id, "token",
                                        {"content": delta["content"]},
                                        conv_id=conv_id,
                                    )
                                if delta.get("reasoning_content"):
                                    accumulated_thinking += delta["reasoning_content"]
                                    await append_llm_job_event(
                                        job_id, "token",
                                        {"reasoning_content": delta["reasoning_content"]},
                                        conv_id=conv_id,
                                    )
                                if choice.get("finish_reason"):
                                    finish_reason = choice["finish_reason"]

            except httpx.ReadTimeout:
                logger.error("Job %s: llama-server read timeout", job_id)
                await update_llm_job_status(job_id, "error", "llama-server read timeout")
                await append_llm_job_event(
                    job_id, "error",
                    {"error": "llama-server read timeout"},
                    conv_id=conv_id,
                )
                return
            except httpx.ConnectError:
                logger.error("Job %s: cannot connect to llama-server at %s", job_id, LLAMA_API)
                await update_llm_job_status(job_id, "error", f"Cannot connect to llama-server at {LLAMA_API}")
                await append_llm_job_event(
                    job_id, "error",
                    {"error": f"Cannot connect to llama-server at {LLAMA_API}"},
                    conv_id=conv_id,
                )
                return

            # Publish timing info if we got any
            if timings:
                await append_llm_job_event(
                    job_id, "timings",
                    timings,
                    conv_id=conv_id,
                )

            # If no tool calls or finish reason is not "tool_calls", we're done
            if finish_reason != "tool_calls" or not tool_calls:
                # Save the final assistant message
                tool_calls_json = json.dumps(tool_calls) if tool_calls else None
                msg_id = await _save_assistant_message(
                    conv_id, accumulated_content,
                    reasoning_content=accumulated_thinking or None,
                    tool_calls=tool_calls_json,
                    model=model,
                    timings=timings if timings else None,
                )

                # Mark job as complete
                await update_llm_job_status(job_id, "complete")
                await append_llm_job_event(
                    job_id, "complete",
                    {
                        "content": accumulated_content,
                        "thinking": accumulated_thinking,
                        "message_id": msg_id,
                        "model": model,
                        "timings": timings,
                    },
                    conv_id=conv_id,
                )
                logger.info("Job %s complete (%d chars)", job_id, len(accumulated_content))
                return

            # We have tool calls — process them via MCP
            logger.info("Job %s turn %d: %d tool call(s)", job_id, turn, len(tool_calls))

            # Publish tool_call event so clients can show progress
            await append_llm_job_event(
                job_id, "tool_calls",
                {
                    "tool_calls": [
                        {"id": tc["id"], "name": tc["name"], "arguments": tc["arguments"]}
                        for tc in tool_calls
                    ],
                },
                conv_id=conv_id,
            )

            # Add assistant message with tool_calls to api_messages
            openai_tool_calls = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {"name": tc["name"], "arguments": tc["arguments"]},
                }
                for tc in tool_calls
            ]
            api_messages.append({
                "role": "assistant",
                "content": accumulated_content or None,
                "tool_calls": openai_tool_calls,
            })

            # Execute each tool call via MCP
            for tc in tool_calls:
                tool_name = tc["name"]
                try:
                    tool_args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                except (json.JSONDecodeError, TypeError):
                    tool_args = {}

                # Publish tool_call_start event
                await append_llm_job_event(
                    job_id, "tool_call_start",
                    {"tool_name": tool_name, "tool_call_id": tc["id"]},
                    conv_id=conv_id,
                )

                tool_result = await _mcp_call_tool(http_client, tool_name, tool_args)

                # Publish tool_call_result event
                # Truncate very long results in the event to avoid huge payloads
                display_result = tool_result[:50000] if len(tool_result) > 50000 else tool_result
                await append_llm_job_event(
                    job_id, "tool_call_result",
                    {
                        "tool_name": tool_name,
                        "tool_call_id": tc["id"],
                        "result": display_result,
                    },
                    conv_id=conv_id,
                )

                # Feed tool result back as a tool role message
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result,
                })

            # Save the intermediate assistant message (with tool calls) to DB
            # This ensures the conversation history in DB is complete for
            # potential next-turn context (and for the client to display)
            tool_calls_json = json.dumps(openai_tool_calls)
            await _save_assistant_message(
                conv_id, accumulated_content,
                reasoning_content=accumulated_thinking or None,
                tool_calls=tool_calls_json,
                model=model,
            )

            # Loop continues — next turn will use tool results + query-only tools

        # If we exhausted max_turns, still finalize with what we have
        logger.warning("Job %s: exhausted %d turns, finalizing", job_id, MAX_TURNS)
        msg_id = await _save_assistant_message(
            conv_id, accumulated_content,
            reasoning_content=accumulated_thinking or None,
            model=model,
        )
        await update_llm_job_status(job_id, "complete")
        await append_llm_job_event(
            job_id, "complete",
            {
                "content": accumulated_content,
                "thinking": accumulated_thinking,
                "message_id": msg_id,
                "model": model,
                "max_turns_reached": True,
            },
            conv_id=conv_id,
        )


# ---------------------------------------------------------------------------
# Worker poll loop
# ---------------------------------------------------------------------------

async def poll_and_process() -> None:
    """Poll for a pending LLM job, process it, and repeat.

    Processes one job at a time (sequential) to respect llama-server's
    limited slots.  When no job is pending, sleeps for POLL_INTERVAL seconds.
    """
    job = await claim_llm_job()
    if job is None:
        return

    job_id = job["id"]
    conv_id = job["conv_id"]
    logger.info("Claimed LLM job %s for conv %s", job_id, conv_id)

    try:
        await process_llm_job(job)
    except Exception:
        logger.exception("Job %s failed with unhandled exception", job_id)
        try:
            await update_llm_job_status(job_id, "error", "Unhandled exception during processing")
            await append_llm_job_event(
                job_id, "error",
                {"error": "Unhandled exception during processing"},
                conv_id=conv_id,
            )
        except Exception:
            logger.exception("Failed to mark job %s as error", job_id)