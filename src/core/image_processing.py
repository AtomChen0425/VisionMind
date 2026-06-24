from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps


def create_thumbnail(image_path: str | Path, thumb_size: int = 320) -> Image.Image:
    with Image.open(image_path) as image:
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
