from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.services import db as db_module

router = APIRouter(prefix="/settings", tags=["settings"])


class AppSettings(BaseModel):
    face_detection_enabled: bool = Field(default=False)
    pinch_zoom_sensitivity: float = Field(default=0.5, ge=0.1, le=3.0)


@router.get("", response_model=AppSettings)
async def get_settings() -> AppSettings:
    data = await db_module.get_app_settings()
    return AppSettings(
        face_detection_enabled=bool(data.get("face_detection_enabled", False)),
        pinch_zoom_sensitivity=float(data.get("pinch_zoom_sensitivity", 1.0)),
    )


@router.put("", response_model=AppSettings)
async def update_settings(payload: AppSettings) -> AppSettings:
    updated = await db_module.update_app_settings({
        "face_detection_enabled": payload.face_detection_enabled,
        "pinch_zoom_sensitivity": payload.pinch_zoom_sensitivity,
    })
    return AppSettings(
        face_detection_enabled=bool(updated.get("face_detection_enabled", False)),
        pinch_zoom_sensitivity=float(updated.get("pinch_zoom_sensitivity", 1.0)),
    )