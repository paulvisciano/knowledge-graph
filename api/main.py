from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import health, images, settings, sync
from api.services import db as db_module
from api.services.job_manager import resume_pending_jobs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def lifespan(app: FastAPI):
    logger.info("Knowledge Graph API starting up")
    await db_module.init_db()
    resumed = await resume_pending_jobs()
    if resumed:
        logger.info("Resumed %d pending/interrupted jobs", len(resumed))
    yield
    await db_module.close_db()
    logger.info("Knowledge Graph API shutting down")


app = FastAPI(title="Knowledge Graph API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(images.router)
app.include_router(settings.router)
app.include_router(sync.router)


if __name__ == "__main__":
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)