"""LLM worker process entrypoint.

Runs as a separate process (``python -m api.llm_worker``) alongside the
FastAPI API and the image-processing worker.  The LLM worker polls the
``llm_jobs`` queue for pending jobs and processes them sequentially,
one at a time, to respect llama-server's limited GPU slots.

Responsibilities:
  * ``init_db`` — ensures the llm_jobs / llm_job_events tables exist
    (idempotent, same as the API process).
  * Poll loop — every ~1 s, atomically claim a ``status='pending'`` row
    with ``SELECT ... FOR UPDATE SKIP LOCKED``, flip it to ``streaming``,
    and drive the LLM through the multi-turn streaming + tool-call loop.
  * Graceful SIGTERM/SIGINT handling — stops the poll loop cleanly.

Message bus = Postgres.  ``llm_jobs`` is the durable work queue;
``llm_job_events`` + ``pg_notify`` (emitted by ``append_llm_job_event``)
is the live event stream the API's SSE endpoints LISTEN on.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Any

from api.services import db as db_module
from api.services.db import get_pool
from api.services.llm_queue import claim_llm_job
from api.services.llm_worker import poll_and_process

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("llm_worker")

POLL_INTERVAL = float(os.environ.get("LLM_WORKER_POLL_INTERVAL", "1.0"))


async def _poll_loop() -> None:
    """Continuously poll for LLM jobs and process them sequentially."""
    while True:
        try:
            await poll_and_process()
        except Exception:
            logger.exception("Poll iteration failed")
        await asyncio.sleep(POLL_INTERVAL)


async def main() -> None:
    logger.info("Knowledge Graph LLM worker starting up")
    await db_module.init_db()

    poll_task = asyncio.create_task(_poll_loop())

    stop_event = asyncio.Event()

    def _stop(*_: Any) -> None:
        logger.info("LLM worker received stop signal, shutting down")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            signal.signal(sig, _stop)

    await stop_event.wait()

    poll_task.cancel()
    try:
        await poll_task
    except asyncio.CancelledError:
        pass

    await db_module.close_db()
    logger.info("Knowledge Graph LLM worker shut down")


if __name__ == "__main__":
    asyncio.run(main())