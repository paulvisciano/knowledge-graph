from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import chat, graph, health, images, settings, sync
from api.services import config
from api.services import db as db_module
from api.services.event_bus import event_bus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def lifespan(app: FastAPI):
    logger.info("Knowledge Graph API starting up")
    await db_module.init_db()
    await event_bus.start()
    # The worker process (api.worker) owns all image processing: it polls the
    # jobs table, runs the two-phase pipeline, and runs the overnight VLM
    # batch scheduler. The API process only serves HTTP, so no
    # resume_pending_jobs / scheduler task is created here.
    yield
    await event_bus.stop()
    await db_module.close_db()
    logger.info("Knowledge Graph API shutting down")


app = FastAPI(title="Knowledge Graph API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(images.router)
app.include_router(graph.router)
app.include_router(settings.router)
app.include_router(sync.router)
app.include_router(chat.router, prefix="/api/chat")


if __name__ == "__main__":
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)