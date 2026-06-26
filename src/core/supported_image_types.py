from __future__ import annotations

RAW_SUPPORTED_IMAGE_EXTENSIONS = {
    ".arw",
    ".cr2",
    ".cr3",
    ".dng",
    ".erf",
    ".nef",
    ".orf",
    ".pef",
    ".raf",
    ".raw",
    ".rw2",
    ".srw",
    ".x3f",
}

SCAN_SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".heic", *RAW_SUPPORTED_IMAGE_EXTENSIONS}
METADATA_SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", *RAW_SUPPORTED_IMAGE_EXTENSIONS}
