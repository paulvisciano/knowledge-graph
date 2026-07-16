"""Facial recognition and analysis for images using DeepFace.

Detects faces, extracts 512-dimensional ArcFace embeddings, clusters faces
into identity groups, identifies known persons from a folder-based database,
and analyzes demographic attributes (age, gender, emotion, race).

All functions are optional — if deepface (or its dependencies) is not installed,
every function returns empty/None results with a logged warning.  The module
never crashes the calling pipeline.

Architecture mirrors ``exif_extractor.py``: lazy imports guarded by
module-level ``_checked`` flags, ``logger`` from Python's standard ``logging``
module, and graceful fallback on every code path.

Typical usage::

    from processors.face_recognizer import (
        detect_faces,
        extract_face_embeddings,
        cluster_faces,
        identify_person,
        analyze_attributes,
    )

    faces = detect_faces("photo.jpg")
    # [{face_id: "a3f2...", bbox: [x1,y1,x2,y2], confidence: 0.99}, ...]

    emb = extract_face_embeddings("photo.jpg")
    # [{face_id: "a3f2...", bbox: [...], confidence: 0.99, embedding: [...]}, ...]

    clusters = cluster_faces([e["embedding"] for e in emb])
    # [{"cluster_id": 0, "face_indices": [0, 3, 7]}, ...]

    person = identify_person("photo.jpg", db_path="known_faces/")
    # [{"identity": "Alice", "distance": 0.23, "face_id": "a3f2..."}, ...]

    attrs = analyze_attributes("photo.jpg")
    # [{"face_id": "a3f2...", age: 28, gender: "Woman", ...}, ...]
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger("processors.face_recognizer")

# ---------------------------------------------------------------------------
# Lazy-import guards (same pattern as exif_extractor.py)
# ---------------------------------------------------------------------------

_deepface: Any = None
_deepface_checked: bool = False

_sklearn_dbscan: Any = None
_sklearn_dbscan_checked: bool = False

# Default configuration
_DEFAULT_MODEL = "ArcFace"
_DEFAULT_DETECTOR = "retinaface"
_FALLBACK_DETECTOR = "mtcnn"
_FACE_DETECT_MAX_DIM = int(os.environ.get("FACE_DETECT_MAX_DIM", "1920"))


def _import_deepface() -> Any:  # noqa: ANN401 — module-level cache, type varies
    """Lazy-import DeepFace, caching the result.

    Returns the DeepFace module if available, otherwise ``None``.
    Warns once via ``logger`` on missing installation.
    """
    global _deepface, _deepface_checked
    if not _deepface_checked:
        try:
            from deepface import DeepFace as _mod  # type: ignore[import-untyped]

            _deepface = _mod
        except ImportError:
            _deepface = None
            logger.warning(
                "[FACE] DeepFace not installed — facial recognition will not be "
                "available. Install with: pip install deepface"
            )
        _deepface_checked = True
    return _deepface


def _import_dbscan() -> Any:  # noqa: ANN401 — module-level cache
    """Lazy-import sklearn.cluster.DBSCAN for face clustering.

    Returns the DBSCAN class if available, otherwise ``None``.
    """
    global _sklearn_dbscan, _sklearn_dbscan_checked
    if not _sklearn_dbscan_checked:
        try:
            from sklearn.cluster import DBSCAN as _mod  # type: ignore[import-untyped]

            _sklearn_dbscan = _mod
        except ImportError:
            _sklearn_dbscan = None
            logger.warning(
                "[FACE] scikit-learn not installed — face clustering will not be "
                "available. Install with: pip install scikit-learn"
            )
        _sklearn_dbscan_checked = True
    return _sklearn_dbscan


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _face_id(image_path: str | Path, face_index: int, facial_area: dict[str, int]) -> str:
    """Generate a deterministic hash-based ID for a detected face.

    Uses the image path, face index, and bounding-box coordinates so that
    the same face always gets the same ID across calls.

    Args:
        image_path: Path to the source image.
        face_index: Zero-based index of the face within the image.
        facial_area: Dict with ``x``, ``y``, ``w``, ``h`` keys from DeepFace.

    Returns:
        A hex string like ``"a3f2e1..."`` used as a stable face identifier.
    """
    src = str(image_path)
    x, y, w, h = facial_area.get("x", 0), facial_area.get("y", 0), facial_area.get("w", 0), facial_area.get("h", 0)
    raw = f"{src}:{face_index}:{x},{y},{w},{h}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _bbox_from_facial_area(facial_area: dict[str, int]) -> list[int]:
    """Convert DeepFace ``facial_area`` dict to ``[x1, y1, x2, y2]`` bbox.

    DeepFace returns ``{x, y, w, h}`` (top-left corner + dimensions).
    We normalise to ``[x1, y1, x2, y2]`` for consistency with common
    object-detection conventions.

    Args:
        facial_area: Dict with ``x``, ``y``, ``w``, ``h`` keys.

    Returns:
        List of four ints ``[x1, y1, x2, y2]``.
    """
    x = int(facial_area.get("x", 0))
    y = int(facial_area.get("y", 0))
    w = int(facial_area.get("w", 0))
    h = int(facial_area.get("h", 0))
    return [x, y, x + w, y + h]


@contextmanager
def _downscale_for_detection(image_path: str | Path, max_long: int = _FACE_DETECT_MAX_DIM):
    """Yield ``(effective_path, scale_x, scale_y)`` for face detection.

    If the image's longest edge exceeds ``max_long``, a downscaled JPEG copy
    is written to a temp file and yielded as ``effective_path``.  ``scale_x``
    and ``scale_y`` are the factors to convert coordinates from the downscaled
    image back to the original (``orig_coord = ds_coord * scale``).

    If the image is already small enough, the original path is yielded with
    scale factors of 1.0 and no temp file is created.
    """
    from PIL import Image

    image_path = Path(image_path)
    with Image.open(image_path) as img:
        w, h = img.size
    longest = max(w, h)
    if longest <= max_long:
        yield str(image_path), 1.0, 1.0
        return

    scale = max_long / longest
    ds_w, ds_h = int(w * scale), int(h * scale)

    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGB")
            img = img.resize((ds_w, ds_h), Image.Resampling.LANCZOS)
            img.save(tmp_path, "JPEG", quality=90)
        logger.info(
            f"[FACE] Downscaled {image_path.name} from {w}x{h} to {ds_w}x{ds_h} "
            f"for detection (scale={scale:.3f})"
        )
        yield tmp_path, w / ds_w, h / ds_h
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _detect_with_fallback(
    deepface: Any,
    image_path: str | Path,
    detector: str = _DEFAULT_DETECTOR,
) -> list[dict[str, Any]]:
    """Run face detection, falling back to opencv on detector failure.

    DeepFace's ``RetinaFace`` detector is the most accurate but may fail
    on images where the model cannot allocate memory or encounters corrupt
    data.  On failure we retry with ``opencv`` which is lighter.

    Args:
        deepface: The imported DeepFace module.
        image_path: Path to the image file.
        detector: Primary detector backend name.

    Returns:
        List of face dicts from DeepFace, or empty list on total failure.
    """
    str_path = str(image_path)
    for det in [detector, _FALLBACK_DETECTOR]:
        try:
            logger.debug(f"[FACE] Detecting faces in {image_path} with detector={det}")
            result = deepface.extract_faces(
                img_path=str_path,
                detector_backend=det,
                enforce_detection=False,
            )
            return result  # type: ignore[no-any-return]
        except Exception as exc:
            logger.debug(f"[FACE] Detector {det} failed on {image_path}: {exc}")
            if det == detector:
                logger.info(
                    f"[FACE] Primary detector '{detector}' failed on {image_path}, "
                    f"falling back to '{_FALLBACK_DETECTOR}'"
                )
                continue
            logger.warning(f"[FACE] All face detectors failed on {image_path}: {exc}")
            return []
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def detect_faces(image_path: str | Path) -> list[dict[str, Any]]:
    """Detect faces in an image and return bounding boxes with confidence.

    Uses DeepFace's ``extract_faces`` with RetinaFace as the primary detector
    (falls back to OpenCV on failure).

    Args:
        image_path: Path to the image file (supports large 5–18 MB JPGs).

    Returns:
        List of face dicts, each containing:

        - ``face_id`` (str): 16-char hex hash uniquely identifying the face.
        - ``bbox`` (list[int]): ``[x1, y1, x2, y2]`` bounding box.
        - ``confidence`` (float): Detection confidence from the model.

        Returns an empty list if DeepFace is not installed or no faces found.

    Example::

        >>> detect_faces("photo.jpg")
        [
            {
                "face_id": "a3f2e1b09c8d7f4a",
                "bbox": [120, 80, 340, 300],
                "confidence": 0.9987,
            }
        ]
    """
    deepface = _import_deepface()
    if deepface is None:
        return []

    image_path = Path(image_path)
    if not image_path.exists():
        logger.debug(f"[FACE] File not found: {image_path}")
        return []

    try:
        logger.info(f"[FACE] Detecting faces in {image_path.name} ({image_path.stat().st_size / 1_048_576:.1f} MB)")
        # Honor FACE_DETECTOR_BACKEND when set (e.g. the isolated detection subprocess
        # forces mtcnn); otherwise use the module default (retinaface).
        detector = os.environ.get("FACE_DETECTOR_BACKEND") or _DEFAULT_DETECTOR
        with _downscale_for_detection(image_path) as (eff_path, sx, sy):
            raw_faces = _detect_with_fallback(deepface, eff_path, detector)
    except Exception as exc:
        logger.warning(f"[FACE] Face detection failed on {image_path.name}: {exc}")
        return []

    if not raw_faces:
        logger.debug(f"[FACE] No faces detected in {image_path.name}")
        return []

    results: list[dict[str, Any]] = []
    for idx, face in enumerate(raw_faces):
        facial_area = face.get("facial_area", {})
        confidence = face.get("confidence", 0.0)
        if sx != 1.0 or sy != 1.0:
            facial_area = {
                "x": int(facial_area.get("x", 0) * sx),
                "y": int(facial_area.get("y", 0) * sy),
                "w": int(facial_area.get("w", 0) * sx),
                "h": int(facial_area.get("h", 0) * sy),
            }
        fw = facial_area.get("w", 0)
        fh = facial_area.get("h", 0)
        if fw < 50 or fh < 50:
            logger.info(f"[FACE] Skipping face {idx} ({fw}x{fh}px) — below 50px minimum")
            continue
        fid = _face_id(image_path, idx, facial_area)
        bbox = _bbox_from_facial_area(facial_area)
        results.append(
            {
                "face_id": fid,
                "bbox": bbox,
                "confidence": round(float(confidence), 4),
            }
        )

    logger.info(f"[FACE] Detected {len(results)} face(s) in {image_path.name}")
    return results


def extract_face_embeddings(
    image_path: str | Path,
    model_name: str = _DEFAULT_MODEL,
    detector_backend: str = _DEFAULT_DETECTOR,
) -> list[dict[str, Any]]:
    """Extract face embedding vectors from an image using ArcFace.

    For each detected face, returns a 512-dimensional embedding vector
    suitable for similarity search and clustering.

    Args:
        image_path: Path to the image file.
        model_name: DeepFace model to use for embeddings. Default ``"ArcFace"``.
        detector_backend: Face detector backend. Default ``"retinaface"``
            with automatic fallback to ``"opencv"``.

    Returns:
        List of face dicts, each containing:

        - ``face_id`` (str): 16-char hex hash.
        - ``bbox`` (list[int]): ``[x1, y1, x2, y2]`` bounding box.
        - ``confidence`` (float): Detection confidence.
        - ``embedding`` (list[float]): 512-dim ArcFace embedding.

        Returns an empty list if DeepFace is not installed or no faces found.

    Example::

        >>> extract_face_embeddings("photo.jpg")
        [
            {
                "face_id": "a3f2e1b09c8d7f4a",
                "bbox": [120, 80, 340, 300],
                "confidence": 0.9987,
                "embedding": [0.0123, -0.0456, ...],  # 512 floats
            }
        ]
    """
    deepface = _import_deepface()
    if deepface is None:
        return []

    image_path = Path(image_path)
    if not image_path.exists():
        logger.debug(f"[FACE] File not found: {image_path}")
        return []

    try:
        logger.info(
            f"[FACE] Extracting embeddings from {image_path.name} "
            f"({image_path.stat().st_size / 1_048_576:.1f} MB, model={model_name})"
        )
        with _downscale_for_detection(image_path) as (eff_path, sx, sy):
            try:
                representations = deepface.represent(
                    img_path=eff_path,
                    model_name=model_name,
                    detector_backend=detector_backend,
                    enforce_detection=True,
                )
            except ValueError as exc:
                if "Face could not be detected" in str(exc) or detector_backend != _FALLBACK_DETECTOR:
                    logger.info(
                        f"[FACE] {detector_backend} detector failed for embeddings on "
                        f"{image_path.name}, trying {_FALLBACK_DETECTOR}"
                    )
                    try:
                        representations = deepface.represent(
                            img_path=eff_path,
                            model_name=model_name,
                            detector_backend=_FALLBACK_DETECTOR,
                            enforce_detection=True,
                        )
                    except ValueError:
                        logger.debug(f"[FACE] No faces detected in {image_path.name}")
                        return []
                else:
                    logger.debug(f"[FACE] No faces detected in {image_path.name}")
                    return []
    except Exception as exc:
        logger.warning(f"[FACE] Embedding extraction failed on {image_path.name}: {exc}")
        return []

    if not representations:
        logger.debug(f"[FACE] No face embeddings returned for {image_path.name}")
        return []

    results: list[dict[str, Any]] = []
    for idx, rep in enumerate(representations):
        facial_area = rep.get("facial_area", {})
        confidence = rep.get("face_confidence", 0.0)
        embedding = rep.get("embedding", [])
        if sx != 1.0 or sy != 1.0:
            facial_area = {
                "x": int(facial_area.get("x", 0) * sx),
                "y": int(facial_area.get("y", 0) * sy),
                "w": int(facial_area.get("w", 0) * sx),
                "h": int(facial_area.get("h", 0) * sy),
            }
        fid = _face_id(image_path, idx, facial_area)
        bbox = _bbox_from_facial_area(facial_area)

        emb_list: list[float] = (
            embedding.tolist() if isinstance(embedding, np.ndarray) else [float(v) for v in embedding]
        )

        results.append(
            {
                "face_id": fid,
                "bbox": bbox,
                "confidence": round(float(confidence), 4),
                "embedding": emb_list,
            }
        )

    logger.info(f"[FACE] Extracted {len(results)} embedding(s) from {image_path.name}")
    return results


def cluster_faces(
    embeddings_list: list[list[float] | np.ndarray],
    threshold: float = 0.4,
) -> list[dict[str, Any]]:
    """Cluster face embeddings into identity groups using DBSCAN.

    Uses cosine distance on L2-normalised embeddings so that the DBSCAN
    ``eps`` parameter maps directly to a cosine-distance threshold (0.0 =
    identical, 2.0 = opposite).  An ``eps`` of ~0.4 groups faces that are
    cos-similar above ≈ 0.8 (arccos(0.4) ≈ 66°; cos(similarity > 0.92)
    at eps=0.4).

    Args:
        embeddings_list: List of 512-dim embedding vectors (numpy arrays or
            plain Python lists).
        threshold: Maximum cosine distance within a cluster. Default 0.4.

    Returns:
        List of cluster dicts, each containing:

        - ``cluster_id`` (int): Cluster label (``-1`` for noise / unclustered).
        - ``face_indices`` (list[int]): Indices into ``embeddings_list``.

        Unclustered faces (label ``-1``) are included so the caller can
        decide how to handle them.

    Example::

        >>> embeddings = [[0.1, 0.2, ...], [0.11, 0.19, ...], [0.9, 0.1, ...]]
        >>> cluster_faces(embeddings, threshold=0.4)
        [
            {"cluster_id": 0, "face_indices": [0, 1]},
            {"cluster_id": 1, "face_indices": [2]},
        ]
    """
    dbscan_cls = _import_dbscan()
    if dbscan_cls is None:
        return []

    if not embeddings_list:
        return []

    try:
        # Convert to numpy array and L2-normalise
        X = np.array(embeddings_list, dtype=np.float32)
        if X.ndim != 2:
            logger.warning(f"[FACE] Expected 2-D embeddings, got shape {X.shape}")
            return []

        norms = np.linalg.norm(X, axis=1, keepdims=True)
        # Avoid division by zero for zero-vectors
        norms = np.where(norms == 0, 1.0, norms)
        X_normed = X / norms

        # DBSCAN on cosine distance: eps = threshold, min_samples = 2
        # (a person must appear at least twice to form a cluster)
        clustering = dbscan_cls(eps=threshold, min_samples=2, metric="cosine")
        labels = clustering.fit_predict(X_normed)

        # Group indices by cluster label
        clusters: dict[int, list[int]] = {}
        for idx, label in enumerate(labels):
            clusters.setdefault(int(label), []).append(idx)

        results: list[dict[str, Any]] = []
        for cluster_id in sorted(clusters.keys()):
            results.append(
                {
                    "cluster_id": cluster_id,
                    "face_indices": clusters[cluster_id],
                }
            )

        n_clustered = sum(len(v) for k, v in clusters.items() if k != -1)
        n_noise = len(clusters.get(-1, []))
        logger.info(
            f"[FACE] Clustered {len(embeddings_list)} embeddings into "
            f"{len(results) - (1 if -1 in clusters else 0)} group(s), "
            f"{n_clustered} assigned, {n_noise} noise"
        )
        return results

    except Exception as exc:
        logger.warning(f"[FACE] Face clustering failed: {exc}")
        return []


def identify_person(
    image_path: str | Path,
    db_path: str | Path,
    model_name: str = _DEFAULT_MODEL,
    detector_backend: str = _DEFAULT_DETECTOR,
) -> list[dict[str, Any]]:
    """Identify known persons in an image against a folder-based face database.

    Uses DeepFace's ``find()`` to compare detected faces against reference
    photos stored in subfolders of ``db_path``, where each subfolder name is
    the person's identity (e.g. ``known_faces/Alice/``, ``known_faces/Bob/``).

    The ``db_path`` folder must be pre-indexed with ``DeepFace.build_index()``.
    If no index exists, ``find()`` will build one automatically on first call.

    Args:
        image_path: Path to the query image.
        db_path: Path to the folder of known faces (subfolders named after
            people, each containing one or more reference photos).
        model_name: DeepFace model for comparison. Default ``"ArcFace"``.
        detector_backend: Face detector backend. Default ``"retinaface"``.

    Returns:
        List of match dicts, each containing:

        - ``identity`` (str): Person name derived from the folder name.
        - ``distance`` (float): Cosine distance to the reference face.
        - ``confidence`` (float): Similarity confidence (1 - normalised distance).
        - ``face_id`` (str): Hash-based ID for this face.

        Returns an empty list if DeepFace is not installed or no matches found.

    Example::

        >>> identify_person("photo.jpg", "known_faces/")
        [
            {
                "identity": "Alice",
                "distance": 0.23,
                "confidence": 0.77,
                "face_id": "a3f2e1b09c8d7f4a",
            }
        ]
    """
    deepface = _import_deepface()
    if deepface is None:
        return []

    image_path = Path(image_path)
    if not image_path.exists():
        logger.debug(f"[FACE] File not found: {image_path}")
        return []

    db_path = Path(db_path)
    if not db_path.exists():
        logger.debug(f"[FACE] Face database not found: {db_path}")
        return []

    str_db = str(db_path)

    try:
        logger.info(
            f"[FACE] Identifying persons in {image_path.name} against "
            f"database at {db_path} (model={model_name})"
        )
        with _downscale_for_detection(image_path) as (eff_path, _sx, _sy):
            try:
                dfs = deepface.find(
                    img_path=eff_path,
                    db_path=str_db,
                    model_name=model_name,
                    detector_backend=detector_backend,
                    enforce_detection=False,
                )
            except Exception:
                logger.info(
                    f"[FACE] Retrying identification with {_FALLBACK_DETECTOR} detector"
                )
                dfs = deepface.find(
                    img_path=eff_path,
                    db_path=str_db,
                    model_name=model_name,
                    detector_backend=_FALLBACK_DETECTOR,
                    enforce_detection=False,
                )
    except Exception as exc:
        logger.warning(f"[FACE] Person identification failed on {image_path.name}: {exc}")
        return []

    # DeepFace.find() returns a list of DataFrames (one per detected face)
    if not dfs:
        logger.debug(f"[FACE] No matches found for {image_path.name}")
        return []

    results: list[dict[str, Any]] = []
    for df in dfs:
        if df is None or df.empty:
            continue
        for _, row in df.iterrows():
            # The 'identity' column contains the matched file path;
            # derive person name from the parent folder.
            identity_path = str(row.get("identity", ""))
            person_name = Path(identity_path).parent.name if identity_path else "Unknown"
            distance = float(row.get("distance", 0.0))
            # Cosine distance ranges 0-2; normalise to a 0-1 confidence.
            confidence = max(0.0, 1.0 - distance / 2.0)

            # Use identity_path + distance for a deterministic face_id
            face_hash = hashlib.sha256(f"{identity_path}:{distance:.6f}".encode()).hexdigest()[:16]

            results.append(
                {
                    "identity": person_name,
                    "distance": round(distance, 4),
                    "confidence": round(confidence, 4),
                    "face_id": face_hash,
                }
            )

    logger.info(f"[FACE] Identified {len(results)} match(es) in {image_path.name}")
    return results


def analyze_attributes(
    image_path: str | Path,
    actions: list[str] | None = None,
    detector_backend: str = _DEFAULT_DETECTOR,
) -> list[dict[str, Any]]:
    """Analyze demographic attributes of detected faces.

    For each face, returns age, gender, emotion, and race predictions.

    Args:
        image_path: Path to the image file.
        actions: List of attributes to analyze. Defaults to all four:
            ``["age", "gender", "emotion", "race"]``.
        detector_backend: Face detector backend. Default ``"retinaface"``.

    Returns:
        List of attribute dicts, each containing:

        - ``face_id`` (str): 16-char hex hash.
        - ``age`` (int | None): Predicted age.
        - ``gender`` (str | None): Predicted gender.
        - ``emotion`` (str | None): Dominant emotion.
        - ``race`` (str | None): Dominant race prediction.

        Returns an empty list if DeepFace is not installed or no faces detected.

    Example::

        >>> analyze_attributes("photo.jpg")
        [
            {
                "face_id": "a3f2e1b09c8d7f4a",
                "age": 28,
                "gender": "Woman",
                "emotion": "happy",
                "race": "asian",
            }
        ]
    """
    deepface = _import_deepface()
    if deepface is None:
        return []

    image_path = Path(image_path)
    if not image_path.exists():
        logger.debug(f"[FACE] File not found: {image_path}")
        return []

    if actions is None:
        actions = ["age", "gender", "emotion", "race"]

    try:
        logger.info(
            f"[FACE] Analyzing attributes in {image_path.name} "
            f"({image_path.stat().st_size / 1_048_576:.1f} MB, actions={actions})"
        )
        with _downscale_for_detection(image_path) as (eff_path, sx, sy):
            try:
                analysis = deepface.analyze(
                    img_path=eff_path,
                    actions=actions,
                    detector_backend=detector_backend,
                    enforce_detection=False,
                )
            except Exception:
                logger.info(
                    f"[FACE] Retrying attribute analysis with {_FALLBACK_DETECTOR} detector"
                )
                analysis = deepface.analyze(
                    img_path=eff_path,
                    actions=actions,
                    detector_backend=_FALLBACK_DETECTOR,
                    enforce_detection=False,
                )
    except Exception as exc:
        logger.warning(f"[FACE] Attribute analysis failed on {image_path.name}: {exc}")
        return []

    if isinstance(analysis, dict):
        analysis = [analysis]

    if not analysis:
        logger.debug(f"[FACE] No faces analyzed in {image_path.name}")
        return []

    results: list[dict[str, Any]] = []
    for idx, face_info in enumerate(analysis):
        facial_area = face_info.get("region", {})
        if sx != 1.0 or sy != 1.0:
            facial_area = {
                "x": int(facial_area.get("x", 0) * sx),
                "y": int(facial_area.get("y", 0) * sy),
                "w": int(facial_area.get("w", 0) * sx),
                "h": int(facial_area.get("h", 0) * sy),
            }
        fid = _face_id(image_path, idx, facial_area)

        age = face_info.get("age")
        gender = face_info.get("dominant_gender")
        emotion = face_info.get("dominant_emotion")
        race = face_info.get("dominant_race")

        if age is not None:
            try:
                age = int(age)
            except (ValueError, TypeError):
                pass

        results.append(
            {
                "face_id": fid,
                "age": age,
                "gender": gender,
                "emotion": emotion,
                "race": race,
            }
        )

    logger.info(f"[FACE] Analyzed {len(results)} face(s) in {image_path.name}")
    return results