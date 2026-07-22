"""EXIF metadata extraction and reverse geocoding for images.

Extracts camera, date, and location metadata from image files using ExifRead,
then reverse-geocodes GPS coordinates to human-readable place names using
reverse_geocoder (fully offline, no network required).

The output is a structured dict and a human-readable text summary designed to
be appended to image captions so that EXIF metadata flows into the Knowledge
Graph through the existing entity extraction pipeline.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("processors.exif_extractor")

_exifread: Any = None
_reverse_geocoder: Any = None
_exifread_checked: bool = False
_reverse_geocoder_checked: bool = False


def _import_exifread() -> Any:
    global _exifread, _exifread_checked
    if not _exifread_checked:
        try:
            import exifread as _mod  # type: ignore[import-untyped]
            _exifread = _mod
        except ImportError:
            _exifread = None
            logger.warning(
                "[EXIF] ExifRead not installed — image EXIF metadata will not be "
                "extracted. Install with: pip install ExifRead"
            )
        _exifread_checked = True
    return _exifread


def _import_reverse_geocoder() -> Any:
    global _reverse_geocoder, _reverse_geocoder_checked
    if not _reverse_geocoder_checked:
        try:
            import reverse_geocoder as _mod  # type: ignore[import-untyped]
            _reverse_geocoder = _mod
        except ImportError:
            _reverse_geocoder = None
            logger.warning(
                "[EXIF] reverse_geocoder not installed — GPS coordinates will not be "
                "resolved to place names. Install with: pip install reverse_geocoder"
            )
        _reverse_geocoder_checked = True
    return _reverse_geocoder


def _dms_to_decimal(dms: tuple[float, ...], ref: str) -> float | None:
    try:
        values = []
        for v in dms:
            if hasattr(v, "num") and hasattr(v, "den"):
                den = v.den
                if den == 0:
                    return None
                values.append(v.num / den)
            elif isinstance(v, (list, tuple)) and len(v) == 2:
                if v[1] == 0:
                    return None
                values.append(v[0] / v[1])
            else:
                values.append(float(v))

        decimal = values[0] + values[1] / 60.0 + values[2] / 3600.0
        if ref in ("S", "W"):
            decimal = -decimal
        return round(decimal, 6)
    except Exception:
        return None


def _get_gps_coords(tags: dict) -> tuple[float | None, float | None]:
    lat = lon = None

    lat_tag = tags.get("GPS GPSLatitude")
    lat_ref = tags.get("GPS GPSLatitudeRef")
    lon_tag = tags.get("GPS GPSLongitude")
    lon_ref = tags.get("GPS GPSLongitudeRef")

    if lat_tag and lat_ref:
        lat = _dms_to_decimal(lat_tag.values, str(lat_ref.values))
    if lon_tag and lon_ref:
        lon = _dms_to_decimal(lon_tag.values, str(lon_ref.values))

    if lat is not None and lon is not None and lat == 0.0 and lon == 0.0:
        return None, None

    return lat, lon


def _reverse_geocode(lat: float, lon: float) -> dict[str, str] | None:
    rg = _import_reverse_geocoder()
    if rg is None:
        return None

    try:
        results = rg.search((lat, lon), mode=1)
        if results:
            hit = results[0]
            return {
                "city": hit.get("name", ""),
                "admin1": hit.get("admin1", ""),
                "admin2": hit.get("admin2", ""),
                "country_code": hit.get("cc", ""),
            }
    except Exception as exc:
        logger.debug(f"[EXIF] Reverse geocode failed for ({lat}, {lon}): {exc}")

    return None


_CAMERA_MAKE_ALIASES: dict[str, str] = {
    "Canon": "Canon",
    "NIKON": "Nikon",
    "NIKON CORPORATION": "Nikon",
    "SONY": "Sony",
    "Apple": "Apple",
    "samsung": "Samsung",
    "SAMSUNG": "Samsung",
    "Google": "Google",
    "HUAWEI": "Huawei",
    "Xiaomi": "Xiaomi",
    "OnePlus": "OnePlus",
    "LG": "LG",
    "Panasonic": "Panasonic",
    "FUJIFILM": "Fujifilm",
    "OLYMPUS": "Olympus",
    "PENTAX": "Pentax",
    "Hasselblad": "Hasselblad",
    "DJI": "DJI",
}


def _friendly_camera(make: str | None, model: str | None) -> str | None:
    parts: list[str] = []
    if make:
        make_clean = _CAMERA_MAKE_ALIASES.get(make, make.strip().title())
        if model and model.strip().lower().startswith(make.strip().lower()):
            parts.append(model.strip())
        else:
            parts.append(make_clean)
            if model:
                parts.append(model.strip())
    elif model:
        parts.append(model.strip())

    return " ".join(parts) if parts else None


_COMMON_ABBREVIATIONS: dict[str, str] = {
    "st": "Saint",
    "st.": "Saint",
    "mt": "Mount",
    "mt.": "Mount",
    "ft": "Fort",
    "ft.": "Fort",
    "n": "North",
    "s": "South",
    "e": "East",
    "w": "West",
}


def _normalize_city_name(name: str) -> str:
    normalized = re.sub(r"\s+", " ", name.strip())
    normalized = re.sub(r"\.", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized.strip())

    words: list[str] = []
    for word in normalized.split(" "):
        lower = word.lower()
        if lower in _COMMON_ABBREVIATIONS:
            words.append(_COMMON_ABBREVIATIONS[lower])
        else:
            words.append(word.title() if word and not word[0].isupper() else word)

    return " ".join(words)


def _friendly_location(geo: dict[str, str]) -> str | None:
    city = geo.get("city", "").strip()
    admin1 = geo.get("admin1", "").strip()
    cc = geo.get("country_code", "").strip()

    parts = []
    if city:
        parts.append(_normalize_city_name(city))
    if admin1 and admin1 != city:
        parts.append(admin1)
    if cc:
        parts.append(cc)

    return ", ".join(parts) if parts else None


_EXIF_TAG_GROUPS = [
    "GPS",
    "EXIF",
    "Image",
]


def _extract_tag_value(tag) -> str | None:
    if tag is None:
        return None
    try:
        vals = tag.values
        if isinstance(vals, (list, tuple)):
            if len(vals) == 1:
                return str(vals[0])
            return ", ".join(str(v) for v in vals)
        return str(vals)
    except Exception:
        return str(tag)


def _extract_rational_value(tag) -> float | None:
    if tag is None:
        return None
    try:
        vals = tag.values
        if isinstance(vals, (list, tuple)) and len(vals) >= 1:
            v = vals[0]
            if hasattr(v, "num") and hasattr(v, "den"):
                if v.den == 0:
                    return None
                return v.num / v.den
            if isinstance(v, (list, tuple)) and len(v) == 2:
                if v[1] == 0:
                    return None
                return v[0] / v[1]
            return float(v)
    except Exception:
        pass
    return None


def extract_exif_metadata(image_path: str | Path) -> dict[str, Any]:
    """Extract EXIF metadata from an image file.

    Extracts date/time, camera info, exposure settings, GPS coordinates,
    and reverse-geocodes the location to a human-readable place name.

    Args:
        image_path: Path to the image file.

    Returns:
        Dict with structured EXIF data and a 'metadata_text' key containing
        a human-readable summary suitable for appending to image captions.

        Example::

            {
                "date_taken": "2024:06:05 14:30:22",
                "date_taken_friendly": "2024-06-05 at 14:30",
                "camera": "Google Pixel 10 Pro XL",
                "lens": "...",
                "f_number": 1.68,
                "iso": 109,
                "location": "Saint Petersburg, Florida, US",
                "metadata_text": "Photo taken on 2024-06-05 at 14:30 with Google Pixel 10 Pro XL at Saint Petersburg, Florida, US. f/1.68, 1/120s, ISO 109, 6.9mm."
            }
    """
    image_path = Path(image_path)
    result: dict[str, Any] = {"metadata_text": ""}

    if not image_path.exists():
        logger.debug(f"[EXIF] File not found: {image_path}")
        return result

    exifread = _import_exifread()
    if exifread is None:
        return result

    try:
        with open(image_path, "rb") as f:
            tags = exifread.process_file(f, details=False)
    except Exception as exc:
        logger.debug(f"[EXIF] Could not read EXIF from {image_path.name}: {exc}")
        return result

    if not tags:
        logger.debug(f"[EXIF] No EXIF data found in {image_path.name}")
        return result

    # --- Date/time ---
    date_str = (
        _extract_tag_value(tags.get("EXIF DateTimeOriginal"))
        or _extract_tag_value(tags.get("EXIF DateTimeDigitized"))
        or _extract_tag_value(tags.get("Image DateTime"))
    )
    if date_str:
        result["date_taken"] = date_str
        try:
            parts = date_str.split(" ")
            if len(parts) == 2:
                date_part = parts[0].replace(":", "-")
                time_part = parts[1].rstrip(":")
                time_short = ":".join(time_part.split(":")[:2])
                friendly = f"{date_part} at {time_short}"
            else:
                friendly = date_str
            result["date_taken_friendly"] = friendly
        except Exception:
            result["date_taken_friendly"] = date_str

    # --- Camera ---
    make = _extract_tag_value(tags.get("Image Make"))
    model = _extract_tag_value(tags.get("Image Model"))
    camera = _friendly_camera(make, model)
    if camera:
        result["camera"] = camera
        result["camera_make"] = make
        result["camera_model"] = model

    # --- Lens ---
    lens = _extract_tag_value(tags.get("EXIF LensModel"))
    if lens:
        result["lens"] = lens
    lens_make = _extract_tag_value(tags.get("EXIF LensMake"))
    if lens_make:
        result["lens_make"] = lens_make

    # --- Software ---
    software = _extract_tag_value(tags.get("EXIF Software"))
    if software:
        result["software"] = software

    # --- Exposure settings ---
    f_number = _extract_rational_value(tags.get("EXIF FNumber"))
    if f_number:
        result["f_number"] = round(f_number, 2)

    exposure_tag = tags.get("EXIF ExposureTime")
    if exposure_tag:
        result["exposure_time"] = _extract_tag_value(exposure_tag)

    iso_tag = tags.get("EXIF ISOSpeedRatings")
    if iso_tag:
        iso_val = _extract_tag_value(iso_tag)
        try:
            result["iso"] = int(float(iso_val)) if iso_val else None
        except (ValueError, TypeError):
            result["iso"] = iso_val

    focal_length = _extract_rational_value(tags.get("EXIF FocalLength"))
    focal_35mm = _extract_tag_value(tags.get("EXIF FocalLengthIn35mmFilm"))
    if focal_length:
        result["focal_length_mm"] = round(focal_length, 1)
    if focal_35mm:
        try:
            result["focal_length_35mm"] = int(float(focal_35mm))
        except (ValueError, TypeError):
            pass

    exposure_bias = _extract_tag_value(tags.get("EXIF ExposureBiasValue"))
    if exposure_bias:
        result["exposure_bias"] = exposure_bias

    flash = _extract_tag_value(tags.get("EXIF Flash"))
    if flash:
        result["flash"] = flash

    # --- Image dimensions ---
    # ExifRead exposes the EXIF spec field names: the "height" tag is
    # `ExifImageLength` (IFD0: `ImageLength`), NOT `ExifImageHeight`.
    # Pixel RAW JPEGs (and many other cameras) only set `ExifImageLength`, so
    # the previous lookup for `ExifImageHeight`/`ImageHeight` silently dropped
    # the height for every photo — leaving the UI to render portrait photos as
    # landscape planes. Check both the common alias and the spec name.
    width = _extract_tag_value(
        tags.get("EXIF ExifImageWidth")
        or tags.get("Image ImageWidth")
        or tags.get("Image ExifImageWidth")
    )
    height = _extract_tag_value(
        tags.get("EXIF ExifImageHeight")
        or tags.get("EXIF ExifImageLength")
        or tags.get("Image ImageHeight")
        or tags.get("Image ImageLength")
        or tags.get("Image ExifImageLength")
    )
    if width:
        try:
            result["image_width"] = int(float(width))
        except (ValueError, TypeError):
            pass
    if height:
        try:
            result["image_height"] = int(float(height))
        except (ValueError, TypeError):
            pass

    # --- GPS ---
    lat, lon = _get_gps_coords(tags)
    if lat is not None:
        result["gps_latitude"] = lat
    if lon is not None:
        result["gps_longitude"] = lon

    altitude_tag = tags.get("GPS GPSAltitude")
    if altitude_tag:
        alt_val = _extract_rational_value(altitude_tag)
        if alt_val is not None:
            alt_ref = _extract_tag_value(tags.get("GPS GPSAltitudeRef"))
            result["gps_altitude"] = f"{round(alt_val, 1)} m"
            if alt_ref == "1":
                result["gps_altitude"] = f"-{round(alt_val, 1)} m (below sea level)"

    # --- Reverse geocode GPS coordinates ---
    if lat is not None and lon is not None:
        geo = _reverse_geocode(lat, lon)
        if geo:
            location = _friendly_location(geo)
            if location:
                result["location"] = location
                result["location_city"] = geo.get("city", "")
                result["location_region"] = geo.get("admin1", "")
                result["location_country"] = geo.get("country_code", "")

    # --- Build human-readable summary ---
    summary_parts: list[str] = []

    if "date_taken_friendly" in result and "camera" in result:
        summary_parts.append(f"Photo taken on {result['date_taken_friendly']} with {result['camera']}")
    elif "date_taken_friendly" in result:
        summary_parts.append(f"Photo taken on {result['date_taken_friendly']}")
    elif "camera" in result:
        summary_parts.append(f"Photo taken with {result['camera']}")

    if "location" in result:
        summary_parts.append(f"at {result['location']}")

    shot_parts: list[str] = []
    if "f_number" in result:
        shot_parts.append(f"f/{result['f_number']}")
    if "exposure_time" in result:
        shot_parts.append(f"{result['exposure_time']}s")
    if "iso" in result and result["iso"]:
        shot_parts.append(f"ISO {result['iso']}")
    if "focal_length_mm" in result:
        fl = f"{int(result['focal_length_mm'])}mm" if result['focal_length_mm'] >= 10 else f"{result['focal_length_mm']}mm"
        if "focal_length_35mm" in result:
            fl += f" (equiv. {result['focal_length_35mm']}mm)"
        shot_parts.append(fl)

    if shot_parts:
        summary_parts.append(", ".join(shot_parts))

    if "lens" in result and result.get("lens") != result.get("camera_model"):
        summary_parts.append(f"lens: {result['lens']}")

    if summary_parts:
        result["metadata_text"] = ". ".join(summary_parts) + "."

    return result