from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from fractions import Fraction
import json
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image


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


def decode_xp_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
    elif isinstance(value, (list, tuple)):
        try:
            raw = bytes(value)
        except Exception:
            raw = str(value).encode("utf-8", errors="ignore")
    else:
        raw = str(value).encode("utf-8", errors="ignore")

    try:
        return raw.decode("utf-16le", errors="ignore").rstrip("\x00")
    except Exception:
        return raw.decode("utf-8", errors="ignore").rstrip("\x00")


def extract_keywords(metadata: dict[str, Any]) -> list[str]:
    candidates = []
    for key in ("XPKeywords", "Keywords", "Subject", "dc:subject"):
        if key in metadata and metadata[key]:
            candidates.append(metadata[key])

    for value in candidates:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except Exception:
                pass
            if ";" in text:
                return [part.strip() for part in text.split(";") if part.strip()]
            if "," in text:
                return [part.strip() for part in text.split(",") if part.strip()]
            return [text]

        decoded = decode_xp_text(value).strip()
        if decoded:
            if ";" in decoded:
                return [part.strip() for part in decoded.split(";") if part.strip()]
            if "," in decoded:
                return [part.strip() for part in decoded.split(",") if part.strip()]
            return [decoded]

    return []


def extract_title(metadata: dict[str, Any]) -> str | None:
    for key in ("ImageDescription", "Title", "dc:title"):
        value = metadata.get(key)
        if value:
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
    if not img_exif:
        return get_img_xmp(image)

    result_dict: dict[str, Any] = defaultdict(str)
    for key, val in img_exif.items():
        tag_name = ExifTags.TAGS.get(key)
        if tag_name:
            result_dict[tag_name] = val

    xmp_data = get_img_xmp(image)
    for key, value in xmp_data.items():
        result_dict.setdefault(key, value)

    return dict(result_dict)


def read_image_metadata(image_path: str | Path) -> dict[str, Any]:
    with Image.open(image_path) as image:
        return get_img_exif(image)
if __name__ == "__main__":
    print('start')
    image_path=r"F:\相片\20251020湖边\枫叶和塔\DSC_7422_1.jpg"
    exif_info=read_image_metadata(image_path)
    print(exif_info)
