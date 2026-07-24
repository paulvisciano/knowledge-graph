"""Centralized configuration for the Knowledge Graph backend.

Single source of truth for env-var-driven settings.  Other modules read runtime
config from here instead of calling ``os.environ.get`` at import time, so:

* the settings page can influence backend behavior without a process restart,
* there is one place to audit/change LightRAG / VLM / CORS / timeout knobs, and
* duplicate definitions (e.g. ``LIGHTRAG_URL`` in both ``processor.py`` and
  ``images.py``) are eliminated.

Values are read lazily via accessor functions so test environments can
monkeypatch the environment and re-read, and so a future runtime settings
store can override these without code changes to call sites.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def lightrag_url() -> str:
    """Base URL of the LightRAG server (no trailing slash)."""
    return _env("LIGHTRAG_URL", "http://localhost:9621").rstrip("/")


def vlm_url() -> str:
    """Base URL of the VLM OpenAI-compatible endpoint."""
    return _env("VLM_LLM_BINDING_HOST", "http://host.docker.internal:8080")


def vlm_model() -> str:
    return _env("VLM_LLM_MODEL", "Gemma-4-12B-OBLITERATED-Q4_K_M")


def vlm_api_key() -> str:
    return _env("VLM_LLM_BINDING_API_KEY", "llama-server")


def vlm_max_concurrent() -> int:
    """Max in-flight VLM chat-completion requests.

    Default 2 — the expensive AI description step (~88s/call) was serialized by
    a hardcoded ``Semaphore(1)``, making a 5-image upload take ~7 minutes.  2
    parallelizes the slow step while keeping GPU memory pressure reasonable on
    a single GPU.  Operators with more GPU headroom can raise this.
    """
    return max(1, _env_int("VLM_MAX_CONCURRENT", 2))


def vlm_timeout() -> int:
    """Per-VLM-request timeout in seconds."""
    return _env_int("VLM_TIMEOUT", 300)


def vlm_image_max_dim() -> int:
    """Max dimension (px) for images sent to the VLM.

    Full-resolution photos (10–12 MP) are needlessly expensive: the Gemma-4-12B
    mmproj caps image tokens at --image-max-tokens (280), so detail beyond ~768px
    is discarded by the projector anyway.  Resizing to 768px max-dim before
    base64-encoding cuts the VLM payload ~50× (4 MB → ~80 KB) and the GPU wired
    memory spike proportionally, while producing identical descriptions.

    The original full-resolution file is never modified — only the in-memory
    copy sent to the VLM is resized.  Set to 0 to disable resizing.
    """
    return max(0, _env_int("VLM_IMAGE_MAX_DIM", 768))


def max_concurrent_jobs() -> int:
    return max(1, _env_int("MAX_CONCURRENT_JOBS", 3))


@dataclass(frozen=True)
class HttpTimeouts:
    long: float = 60.0
    short: float = 15.0
    vlm: float = 300.0


@lru_cache(maxsize=1)
def http_timeouts() -> HttpTimeouts:
    """Cached HTTP timeout bundle.

    Replaces ~13 scattered ``timeout=30``/``timeout=15`` literals across
    processor.py.  ``long`` is for LightRAG document inserts + pipeline-busy
    retries; ``short`` is for entity/relation creates + existence verifies.
    Override via ``HTTP_TIMEOUT_LONG``, ``HTTP_TIMEOUT_SHORT``, ``VLM_TIMEOUT``.
    """
    return HttpTimeouts(
        long=_env_float("HTTP_TIMEOUT_LONG", 60.0),
        short=_env_float("HTTP_TIMEOUT_SHORT", 15.0),
        vlm=_env_float("VLM_TIMEOUT", 300.0),
    )


def max_upload_bytes() -> int:
    """Max bytes for a single image upload.  Default 50MB.

    Guards ``await file.read()`` against OOM from a huge upload.  Override via
    ``MAX_UPLOAD_MB`` (megabytes).
    """
    return max(1, _env_int("MAX_UPLOAD_MB", 50)) * 1024 * 1024


def allowed_upload_extensions() -> frozenset[str]:
    """Filename extensions (lowercase, dot-prefixed) accepted for image jobs."""
    raw = _env(
        "ALLOWED_UPLOAD_EXTENSIONS",
        ".jpg,.jpeg,.png,.gif,.webp,.bmp,.svg,.tif,.tiff,.heic,.heif",
    )
    return frozenset(
        ext.strip().lower() if ext.startswith(".") else f".{ext.strip().lower()}"
        for ext in raw.split(",")
        if ext.strip()
    )


def cors_allowed_origins() -> list[str]:
    """Allowed CORS origins.

    The previous config was ``allow_origins=["*"]`` with
    ``allow_credentials=True`` — both insecure AND invalid per the Fetch spec
    (browsers reject credentialed requests when the origin is the wildcard).
    Default to the Nexus UI origin so dev works out of the box; override via
    ``CORS_ALLOWED_ORIGINS`` (comma-separated).
    """
    raw = _env("CORS_ALLOWED_ORIGINS", "http://localhost:3000")
    return [o.strip() for o in raw.split(",") if o.strip()]