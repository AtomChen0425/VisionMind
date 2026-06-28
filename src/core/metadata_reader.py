from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from fractions import Fraction
import logging
from pathlib import Path
from typing import Any
from .exiftool_manager import ExifToolManager
from exiftool import ExifToolHelper
from PIL import ExifTags, Image

logger = logging.getLogger(__name__)


def _parse_iso8601(value: str) -> datetime:
    cleaned = value.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return datetime.strptime(value, "%Y:%m:%d %H:%M:%S")


def _fraction_to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(Fraction(str(value)))
    except Exception:
        try:
            return float(value)
        except Exception:
            return None


def _seq_value(value: Any) -> Any:
    if isinstance(value, dict):
        seq = value.get("Seq") or value.get("seq")
        if isinstance(seq, dict):
            return seq.get("li") or seq.get("LI") or seq.get("Li")
        return seq
    return value


def _find_metadata_value(metadata: Any, keys: set[str]) -> Any:
    if isinstance(metadata, dict):
        for key, value in metadata.items():
            if key in keys and value not in (None, "", [], (), {}):
                return value
            found = _find_metadata_value(value, keys)
            if found not in (None, "", [], (), {}):
                return found
    elif isinstance(metadata, (list, tuple, set)):
        for item in metadata:
            found = _find_metadata_value(item, keys)
            if found not in (None, "", [], (), {}):
                return found
    return None


def _to_readable_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except Exception:
            pass
        delimiters = (";", ",")
        for delimiter in delimiters:
            if delimiter in text:
                return [part.strip() for part in text.split(delimiter) if part.strip()]
        return [text]
    if isinstance(value, (list, tuple, set)):
        if len(value) == 1:
            only_item = next(iter(value))
            if isinstance(only_item, str):
                return _to_readable_list(only_item)
        result: list[str] = []
        for item in value:
            decoded = decode_xp_text(item).strip()
            if decoded:
                result.append(decoded)
        return result
    decoded = decode_xp_text(value).strip()
    return [decoded] if decoded else []


def _to_display_value(value: Any, *, key: str | None = None) -> Any:
    if value is None:
        return None
    if key in {"Keywords", "XPKeywords", "Subject"}:
        return _to_readable_list(value)
    if key in {"FNumber", "FocalLength", "ExposureTime", "Megapixels", "GPSLatitude", "GPSLongitude", "GPSAltitude"}:
        return _fraction_to_float(value)
    if key in {"ImageWidth", "ImageHeight", "ISOSpeedRatings"}:
        try:
            return int(_seq_value(value))
        except Exception:
            try:
                return int(value)
            except Exception:
                return decode_xp_text(value).strip() or str(value)
    if key in {"DateTimeOriginal", "CreateDate", "ModifyDate", "GPSDateTime"}:
        try:
            return _parse_iso8601(str(value)).strftime("%Y:%m:%d %H:%M:%S")
        except Exception:
            return decode_xp_text(value).strip() or str(value)
    if isinstance(value, (list, tuple, set)):
        readable = _to_readable_list(value)
        return readable if readable else [decode_xp_text(item).strip() for item in value]
    if isinstance(value, dict):
        return {k: _to_display_value(v, key=k) for k, v in value.items()}
    if isinstance(value, str):
        return decode_xp_text(value).strip()
    return value


def decode_xp_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        if "\x00" in value:
            cleaned = value.replace("\x00", "").strip()
            if cleaned:
                return cleaned
        return value.strip()
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    elif isinstance(value, (list, tuple)):
        try:
            raw = bytes(value)
        except Exception:
            raw = str(value).encode("utf-8", errors="ignore")
    else:
        raw = str(value).encode("utf-8", errors="ignore")

    candidates = []
    if raw.startswith(b"\xff\xfe") or b"\x00" in raw:
        candidates.extend(
            [
                raw.decode("utf-16le", errors="ignore"),
                raw.decode("utf-16", errors="ignore"),
            ]
        )
    candidates.extend(
        [
            raw.decode("utf-8", errors="ignore"),
            raw.decode("latin1", errors="ignore"),
        ]
    )

    for candidate in candidates:
        cleaned = candidate.replace("\x00", "").strip()
        if cleaned:
            return cleaned

    return raw.decode("utf-8", errors="ignore").replace("\x00", "").strip()


def extract_keywords(metadata: dict[str, Any]) -> list[str]:
    value = _find_metadata_value(metadata, {"Keywords", "XPKeywords", "Subject", "dc:subject"})
    if value is None:
        return []
    return _to_readable_list(value)


def extract_title(metadata: dict[str, Any]) -> str | None:
    value = _find_metadata_value(metadata, {"ImageDescription", "Title", "XPTitle", "dc:title"})
    if value:
        if isinstance(value, (list, tuple, set)):
            readable = _to_readable_list(value)
            if readable:
                return readable[0]
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
        else:
            decoded = decode_xp_text(value).strip()
            if decoded:
                return decoded
    return None


def get_img_xmp(image: Image.Image) -> dict[str, Any]:
    img_xmp = image.getxmp() if hasattr(image, "getxmp") else {}
    parameter_dict: dict[str, Any] = {}

    try:
        xmp_data = img_xmp["xmpmeta"]["RDF"]["Description"]
    except Exception:
        return parameter_dict

    for key in ("LensModel", "Model", "Make"):
        value = xmp_data.get(key)
        if value is not None:
            parameter_dict[key] = value

    for key in ("FocalLength", "FNumber", "ExposureTime"):
        value = _fraction_to_float(xmp_data.get(key))
        if value is not None:
            parameter_dict[key] = value

    iso_value = _seq_value(xmp_data.get("ISOSpeedRatings"))
    if iso_value is not None:
        try:
            parameter_dict["ISOSpeedRatings"] = int(iso_value)
        except Exception:
            pass

    date_value = xmp_data.get("DateTimeOriginal")
    if date_value:
        try:
            date_obj = _parse_iso8601(str(date_value))
            parameter_dict["DateTimeOriginal"] = date_obj.strftime("%Y:%m:%d %H:%M:%S")
        except Exception:
            parameter_dict["DateTimeOriginal"] = str(date_value)

    return parameter_dict


def get_img_exif(image: Image.Image) -> dict[str, Any]:
    img_exif = image.getexif()
    # if not img_exif:
    #     return get_img_xmp(image)

    result_dict: dict[str, Any] = defaultdict(str)
    for key, val in img_exif.items():
        tag_name = ExifTags.TAGS.get(key)
        if tag_name:
            result_dict[tag_name] = val

    # xmp_data = get_img_xmp(image)
    # for key, value in xmp_data.items():
    #     result_dict.setdefault(key, value)

    return dict(result_dict)

def read_image_metadata_exiftool(image_path: str | Path) -> dict[str, Any]:
    image_path_str = str(image_path)
    sections: dict[str, dict[str, Any]] = {
        "file": {},
        "camera": {},
        "capture": {},
        "exposure": {},
        "lens": {},
        "location": {},
        "text": {},
        "technical": {},
    }

    file_fields = {
        "FileName": ("file", "FileName"),
        "Directory": ("file", "Directory"),
        "FileType": ("file", "FileType"),
        "FileTypeExtension": ("file", "FileTypeExtension"),
        "MIMEType": ("file", "MIMEType"),
        "ImageWidth": ("file", "Width"),
        "ImageHeight": ("file", "Height"),
        "ImageSize": ("file", "ImageSize"),
        "Megapixels": ("file", "Megapixels"),
        "Orientation": ("technical", "Orientation"),
        "Software": ("technical", "Software"),
        "Artist": ("technical", "Artist"),
        "Copyright": ("technical", "Copyright"),
    }
    camera_fields = {
        "Make": ("camera", "Make"),
        "Model": ("camera", "Model"),
    }
    lens_fields = {
        "LensModel": ("lens", "LensModel"),
        "LensInfo": ("lens", "LensInfo"),
    }
    capture_fields = {
        "DateTimeOriginal": ("capture", "DateTimeOriginal"),
        "CreateDate": ("capture", "CreateDate"),
        "ModifyDate": ("capture", "ModifyDate"),
    }
    exposure_fields = {
        "ExposureTime": ("exposure", "ExposureTime"),
        "FNumber": ("exposure", "FNumber"),
        "ISO": ("exposure", "ISO"),
        "ISOSpeedRatings": ("exposure", "ISOSpeedRatings"),
        "FocalLength": ("exposure", "FocalLength"),
        "ExposureProgram": ("exposure", "ExposureProgram"),
        "MeteringMode": ("exposure", "MeteringMode"),
        "WhiteBalance": ("exposure", "WhiteBalance"),
        "Flash": ("exposure", "Flash"),
    }
    location_fields = {
        "GPSLatitude": ("location", "Latitude"),
        "GPSLongitude": ("location", "Longitude"),
        "GPSAltitude": ("location", "Altitude"),
        "GPSPosition": ("location", "Position"),
        "GPSCoordinates": ("location", "Coordinates"),
        "GPSDateTime": ("location", "GPSDateTime"),
        "GPSLatitudeRef": ("location", "LatitudeRef"),
        "GPSLongitudeRef": ("location", "LongitudeRef"),
    }
    text_fields = {
        "Title": ("text", "Title"),
        "ImageDescription": ("text", "Description"),
        "XPTitle": ("text", "XPTitle"),
        "Keywords": ("text", "Keywords"),
        "Subject": ("text", "Subject"),
        "Comment": ("text", "Comment"),
        "UserComment": ("text", "UserComment"),
        "Description": ("text", "DescriptionText"),
    }

    mapped_fields = {
        **file_fields,
        **camera_fields,
        **lens_fields,
        **capture_fields,
        **exposure_fields,
        **location_fields,
        **text_fields,
    }

    with ExifToolHelper(executable=str(ExifToolManager().ensure_exiftool()), encoding="utf-8") as et:
        try:
            metadata_list = et.get_metadata(image_path_str)
            if not metadata_list:
                return {}

            raw_metadata = metadata_list[0]
            for key, value in raw_metadata.items():
                stripped_key = key.split(":", 1)[1] if ":" in key else key
                mapping = mapped_fields.get(stripped_key)
                if mapping is None:
                    continue
                section_name, target_key = mapping
                sections[section_name][target_key] = _to_display_value(value, key=stripped_key)

            keywords = extract_keywords(raw_metadata)
            if keywords:
                sections["text"]["Keywords"] = keywords

            title = extract_title(raw_metadata)
            if title:
                sections["text"]["Title"] = title

            structured = {name: data for name, data in sections.items() if data}
            return structured
        except Exception as e:
            print(f"Failed to read image metadata for {image_path}: {e}")
            logger.exception("Failed to read image metadata for %s", image_path)

    return {}
def read_image_metadata(image_path: str | Path) -> dict[str, Any]:
    try:
        logger.debug("Reading image metadata path=%s", image_path)
        exif_info = read_image_metadata_exiftool(image_path)
    except Exception:
        logger.exception("Failed to read image metadata path=%s", image_path)
        return {}
    return exif_info
