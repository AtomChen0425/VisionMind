from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps
import numpy as np

try:
    import rawpy
except Exception:  # pragma: no cover - rawpy is optional in some environments
    rawpy = None

from .supported_image_types import RAW_SUPPORTED_IMAGE_EXTENSIONS


def _is_raw_image_path(image_path: str | Path) -> bool:
    return Path(image_path).suffix.lower() in RAW_SUPPORTED_IMAGE_EXTENSIONS


def load_image_for_processing(image_path: str | Path) -> Image.Image:
    path = Path(image_path)
    if not _is_raw_image_path(path):
        with Image.open(path) as image:
            return image.copy()

    if rawpy is None:
        raise RuntimeError("rawpy is required to process raw image files")

    with rawpy.imread(str(path)) as raw:
        rgb = raw.postprocess(
            output_bps=8,
            use_camera_wb=True,
            no_auto_bright=True,
            output_color=rawpy.ColorSpace.sRGB,
        )
    return Image.fromarray(np.asarray(rgb))


def create_thumbnail(image_path: str | Path, thumb_size: int = 320) -> Image.Image:
    image = load_image_for_processing(image_path)
    image = ImageOps.exif_transpose(image)
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGB")
    else:
        image = image.copy()
    image.thumbnail((thumb_size, thumb_size), Image.Resampling.LANCZOS)
    return image


def save_thumbnail_png(image_path: str | Path, cache_path: str | Path, thumb_size: int = 320) -> Path:
    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    thumbnail = create_thumbnail(image_path, thumb_size=thumb_size)
    thumbnail.save(cache_path, format="PNG", optimize=True)
    return cache_path
