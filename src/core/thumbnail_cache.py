from __future__ import annotations

import hashlib
from pathlib import Path


class ThumbnailCache:
    def __init__(self, cache_dir: str | Path = "data/cache/thumbnails"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def key(self, file_path: str, *, mtime_ns: int, size: int, thumb_size: int) -> str:
        digest = hashlib.sha1()
        digest.update(str(Path(file_path).resolve()).encode("utf-8"))
        digest.update(b"|")
        digest.update(str(mtime_ns).encode("utf-8"))
        digest.update(b"|")
        digest.update(str(size).encode("utf-8"))
        digest.update(b"|")
        digest.update(str(thumb_size).encode("utf-8"))
        return digest.hexdigest()

    def path_for(self, file_path: str, *, mtime_ns: int, size: int, thumb_size: int) -> Path:
        return self.cache_dir / f"{self.key(file_path, mtime_ns=mtime_ns, size=size, thumb_size=thumb_size)}.png"

    def has(self, file_path: str, *, mtime_ns: int, size: int, thumb_size: int) -> bool:
        return self.path_for(file_path, mtime_ns=mtime_ns, size=size, thumb_size=thumb_size).exists()
