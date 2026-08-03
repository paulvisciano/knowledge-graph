"""Async event bus for real-time broadcasting to SSE clients.

Generalises the pg_notify LISTEN/NOTIFY pattern from the image-processing
SSE endpoint (images.py) into a reusable, channel-based event bus that any
number of SSE subscribers can consume.

Key design decisions
--------------------
* Each subscriber gets its own ``asyncio.Queue`` — queues are thread-safe and
  support async iteration without extra locking.
* A single dedicated asyncpg connection (NOT from the shared pool) is used for
  ``LISTEN`` because a listening connection is blocked and cannot be recycled.
* ``EventBus.start()`` / ``EventBus.stop()`` manage the listener lifecycle and
  are wired into the FastAPI lifespan in ``api/main.py``.
* ``get_missed_events(after_event_id)`` enables Last-Event-ID reconnection by
  replaying rows from ``llm_job_events`` that arrived while the client was
  disconnected.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from typing import AsyncIterator

import asyncpg

from api.services import db as db_module

logger = logging.getLogger(__name__)

PG_NOTIFY_CHANNEL = "llm_job_events"


class _Unsub:
    """Token returned by ``subscribe`` / ``subscribe_all`` so callers can
    remove their queue when they disconnect (e.g. in a ``finally`` block)."""

    __slots__ = ("_bus", "_channel", "_queue")

    def __init__(self, bus: EventBus, channel: str | None, queue: asyncio.Queue[dict]) -> None:
        self._bus = bus
        self._channel = channel
        self._queue = queue

    def remove(self) -> None:
        """Remove this subscriber's queue from the event bus."""
        if self._channel is None:
            # Global subscriber — stored in a separate set
            self._bus._global_subscribers.discard(self._queue)
        else:
            subs = self._bus._channel_subscribers.get(self._channel)
            if subs is not None:
                subs.discard(self._queue)
                # Clean up empty channel sets to avoid unbounded memory growth
                if not subs:
                    self._bus._channel_subscribers.pop(self._channel, None)


class EventBus:
    """In-process async event bus backed by PostgreSQL ``NOTIFY``.

    Usage::

        from api.services.event_bus import event_bus

        # Subscribe to a specific channel
        unsub = event_bus.subscribe("conv_abc123")
        try:
            async for event in event_bus.iterate(unsub):
                ...
        finally:
            unsub.remove()

        # Publish
        await event_bus.publish("conv_abc123", {"type": "token", "data": {...}})
    """

    def __init__(self) -> None:
        self._channel_subscribers: dict[str, set[asyncio.Queue[dict]]] = defaultdict(set)
        self._global_subscribers: set[asyncio.Queue[dict]] = set()

        # Dedicated asyncpg connection for LISTEN (not from the pool)
        self._listen_conn: asyncpg.Connection | None = None
        self._listen_task: asyncio.Task | None = None  # noqa: F841  # stored for lifecycle
        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Connect to PostgreSQL and start the ``LISTEN`` background task.

        Must be called during app startup (e.g. FastAPI lifespan).
        """
        if self._started:
            return
        self._started = True

        conn = await asyncpg.connect(db_module.DATABASE_URL)
        self._listen_conn = conn

        async def _listener_loop() -> None:
            """Background coroutine that reads from the pg_notify queue and
            fans out to all subscribers."""
            notify_queue: asyncio.Queue[str] = asyncio.Queue()

            def _on_notify(
                _conn: asyncpg.Connection,
                _pid: int,
                channel: str,
                payload: str,
            ) -> None:
                try:
                    notify_queue.put_nowait(payload)
                except asyncio.QueueFull:
                    logger.warning("EventBus notify queue full — dropping payload")

            await conn.add_listener(PG_NOTIFY_CHANNEL, _on_notify)
            logger.info("EventBus listening on pg_notify channel '%s'", PG_NOTIFY_CHANNEL)

            try:
                while True:
                    try:
                        payload = await asyncio.wait_for(notify_queue.get(), timeout=30.0)
                    except asyncio.TimeoutError:
                        # Heartbeat — just loop again so we stay alive
                        continue

                    try:
                        event = json.loads(payload)
                    except (json.JSONDecodeError, TypeError):
                        logger.warning("EventBus: invalid JSON payload: %s", payload)
                        continue

                    # Normalize pg_notify payloads: event_type -> type
                    if "event_type" in event and "type" not in event:
                        event["type"] = event.pop("event_type")

                    await self._fan_out(event)
            except asyncio.CancelledError:
                logger.info("EventBus listener task cancelled — shutting down")
            except Exception:
                logger.exception("EventBus listener task crashed")

        self._listen_task = asyncio.create_task(_listener_loop())

    async def stop(self) -> None:
        """Disconnect the listener and clean up.

        Must be called during app shutdown (e.g. FastAPI lifespan).
        """
        if not self._started:
            return
        self._started = False

        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None

        if self._listen_conn is not None:
            try:
                await self._listen_conn.close()
            except Exception:
                pass
            self._listen_conn = None

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    def subscribe(self, channel: str) -> _Unsub:
        """Subscribe to events on a specific channel.

        Returns an ``_Unsub`` token whose ``.remove()`` method cleans up the
        queue when the subscriber disconnects.
        """
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self._channel_subscribers[channel].add(queue)
        return _Unsub(self, channel, queue)

    def subscribe_all(self) -> _Unsub:
        """Subscribe to ALL events across every channel.

        Returns an ``_Unsub`` token whose ``.remove()`` method cleans up the
        queue when the subscriber disconnects.
        """
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self._global_subscribers.add(queue)
        return _Unsub(self, None, queue)

    async def iterate(self, unsub: _Unsub) -> AsyncIterator[dict]:
        """Convenience async iterator that yields events from a subscriber queue.

        Typically used as::

            unsub = event_bus.subscribe("conv_abc")
            try:
                async for event in event_bus.iterate(unsub):
                    ...
            finally:
                unsub.remove()
        """
        queue = unsub._queue
        while True:
            # Use a timeout so we yield control periodically and can be
            # cancelled cleanly.
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                continue
            yield event

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    async def publish(self, channel: str, event: dict) -> None:
        """Publish an event to all subscribers on *channel* AND all global
        subscribers.

        This is an in-process fan-out only — it does **not** write to the
        database or issue ``NOTIFY``. The worker (Task 5) is responsible for
        persisting events and triggering ``NOTIFY``.
        """
        # Channel-specific subscribers
        subs = self._channel_subscribers.get(channel)
        if subs:
            for q in list(subs):
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning("EventBus: dropping event for slow subscriber on '%s'", channel)

        # Global subscribers
        for q in list(self._global_subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("EventBus: dropping event for slow global subscriber")

    # ------------------------------------------------------------------
    # Last-Event-ID replay
    # ------------------------------------------------------------------

    async def get_missed_events(self, after_event_id: int) -> list[dict]:
        """Query ``llm_job_events`` for events with ``id > after_event_id``
        and return them ordered by ``id``.

        This enables SSE clients to reconnect using ``Last-Event-ID`` and
        receive any events they missed while disconnected.
        """
        pool = await db_module.get_pool()
        rows = await pool.fetch(
            """
            SELECT e.id, e.event_type, e.job_id, e.event_data, e.created_at,
                   j.conv_id
            FROM llm_job_events e
            JOIN llm_jobs j ON e.job_id = j.id
            WHERE e.id > $1
            ORDER BY e.id ASC
            """,
            after_event_id,
        )
        events: list[dict] = []
        for row in rows:
            event_data = row["event_data"]
            if isinstance(event_data, str):
                try:
                    event_data = json.loads(event_data)
                except (json.JSONDecodeError, TypeError):
                    event_data = {"raw": event_data}
            events.append(
                {
                    "id": row["id"],
                    "type": row["event_type"],
                    "conv_id": row.get("conv_id"),
                    "job_id": row.get("job_id"),
                    "data": event_data if isinstance(event_data, dict) else {"raw": event_data},
                    "created_at": row["created_at"],
                }
            )
        return events

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fan_out(self, event: dict) -> None:
        """Distribute a single event dict to the appropriate subscribers.

        The event dict is expected to have at minimum a ``type`` key.  If it
        has a ``conv_id`` key, it will be fanned out to that channel's
        subscribers as well as to all global subscribers.
        """
        conv_id = event.get("conv_id")
        job_id = event.get("job_id")

        # Fan out to channel-specific subscribers
        if conv_id:
            subs = self._channel_subscribers.get(conv_id)
            if subs:
                for q in list(subs):
                    try:
                        q.put_nowait(event)
                    except asyncio.QueueFull:
                        logger.warning(
                            "EventBus: dropping event for slow subscriber on conv '%s'",
                            conv_id,
                        )

        if job_id:
            channel = f"job_{job_id}"
            subs = self._channel_subscribers.get(channel)
            if subs:
                for q in list(subs):
                    try:
                        q.put_nowait(event)
                    except asyncio.QueueFull:
                        logger.warning(
                            "EventBus: dropping event for slow subscriber on job '%s'",
                            job_id,
                        )

        # Always fan out to global subscribers
        for q in list(self._global_subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("EventBus: dropping event for slow global subscriber")


# Module-level singleton
event_bus = EventBus()