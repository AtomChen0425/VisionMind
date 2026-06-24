from __future__ import annotations

from pathlib import Path
from typing import Sequence

try:
    from exiftool import ExifToolHelper
except ImportError:  # pragma: no cover - only hit when dependency is missing
    ExifToolHelper = None

from .exiftool_manager import ExifToolManager
from .metadata_reader import extract_keywords, extract_title, read_image_metadata
from .supported_image_types import METADATA_SUPPORTED_IMAGE_EXTENSIONS


class ExifToolTagWriter:
    def __init__(
        self,
        tool_dir: str | Path = "data/tools/exiftool",
        exiftool_path: str | Path | None = None,
    ):
        self.manager = ExifToolManager(tool_dir)
        self.exiftool_path = Path(exiftool_path) if exiftool_path is not None else None

    @staticmethod
    def _unique_tags(tags: Sequence[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for tag in tags:
            cleaned = str(tag).strip()
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(cleaned)
        return result

    def _resolve_exiftool(self) -> Path:
        if self.exiftool_path is not None and self.exiftool_path.exists():
            return self.exiftool_path
        return self.manager.ensure_exiftool()

    def write(self, image_path: str | Path, tags: Sequence[str], title: str | None = None) -> Path:
        image_path = Path(image_path)
        if image_path.suffix.lower() not in METADATA_SUPPORTED_IMAGE_EXTENSIONS:
            raise ValueError(f"Unsupported image type for metadata writing: {image_path.suffix}")

        tags = self._unique_tags(tags)
        current_metadata = read_image_metadata(image_path)
        current_tags = self._unique_tags(extract_keywords(current_metadata))
        current_title = extract_title(current_metadata)
        resolved_title = title if title is not None else current_title

        if current_tags == tags and resolved_title == current_title:
            return image_path

        if ExifToolHelper is None:
            raise RuntimeError("pyexiftool is not installed")

        tag_payload = {
            "XMP-dc:Subject": tags,
            "IPTC:Keywords": tags,
            "Keywords": tags,
        }
        if resolved_title is not None and str(resolved_title).strip():
            title_value = str(resolved_title).strip()
            tag_payload["XMP-dc:Title"] = title_value
            tag_payload["EXIF:ImageDescription"] = title_value
            tag_payload["IPTC:Caption-Abstract"] = title_value

        with ExifToolHelper(executable=str(self._resolve_exiftool()), encoding="utf-8") as helper:
            helper.set_tags(
                [str(image_path)],
                tag_payload,
                params=["-overwrite_original", "-P", "-m", "-charset", "filename=UTF8", "-charset", "exif=UTF8"],
            )
        return image_path
