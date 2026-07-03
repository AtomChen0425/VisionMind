from __future__ import annotations

import ssl
import platform
import re
import stat
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

from .app_paths import get_exiftool_dir


EXIFTOOL_HOME_URL = "https://exiftool.org/"
ProgressCallback = Callable[[str, int, int | None], None]

try:
    import certifi
except Exception:  # pragma: no cover - optional dependency
    certifi = None


def _emit(progress_callback: ProgressCallback | None, stage: str, current: int, total: int | None) -> None:
    if progress_callback is not None:
        progress_callback(stage, current, total)


def _build_ssl_context() -> ssl.SSLContext:
    if certifi is not None:
        try:
            return ssl.create_default_context(cafile=certifi.where())
        except Exception:
            pass
    return ssl.create_default_context()


def _open_url(url: str):
    try:
        return urllib.request.urlopen(url, context=_build_ssl_context())
    except ssl.SSLError:
        return urllib.request.urlopen(url, context=ssl._create_unverified_context())


class ExifToolManager:
    def __init__(self, tool_dir: str | Path = get_exiftool_dir()):
        self.tool_dir = Path(tool_dir)

    @staticmethod
    def system_name() -> str:
        return platform.system().lower()

    @classmethod
    def is_windows(cls) -> bool:
        return cls.system_name() == "windows"

    @classmethod
    def is_macos(cls) -> bool:
        return cls.system_name() == "darwin"

    @classmethod
    def is_supported_platform(cls) -> bool:
        return cls.is_windows() or cls.is_macos()

    @staticmethod
    def _normalize_version(version: str) -> str:
        return version.strip()

    @staticmethod
    def _home_page_version(html: str) -> str:
        match = re.search(r"Download Version\s+([0-9]+\.[0-9]+)", html)
        if match:
            return match.group(1)
        match = re.search(r"Version\s+([0-9]+\.[0-9]+)", html)
        if match:
            return match.group(1)
        raise RuntimeError("Unable to determine the current ExifTool version from the official page")

    @classmethod
    def _archive_url(cls, version: str) -> str:
        version = cls._normalize_version(version)
        if cls.is_windows():
            bits = "64" if platform.architecture()[0] == "64bit" else "32"
            return f"https://sourceforge.net/projects/exiftool/files/exiftool-{version}_{bits}.zip/download"
        if cls.is_macos():
            return f"https://sourceforge.net/projects/exiftool/files/Image-ExifTool-{version}.tar.gz/download"
        raise RuntimeError(f"Unsupported platform: {platform.system()}")

    @staticmethod
    def _download_text(url: str, progress_callback: ProgressCallback | None = None, stage: str = "fetch") -> str:
        with _open_url(url) as response:
            total = response.headers.get("Content-Length")
            total_int = int(total) if total and total.isdigit() else None
            chunks: list[bytes] = []
            received = 0
            _emit(progress_callback, stage, 0, total_int)
            while True:
                buffer = response.read(8192)
                if not buffer:
                    break
                chunks.append(buffer)
                received += len(buffer)
                _emit(progress_callback, stage, received, total_int)
            return b"".join(chunks).decode("utf-8", errors="replace")

    @staticmethod
    def _download_file(url: str, target_path: Path, progress_callback: ProgressCallback | None = None, stage: str = "download"):
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with _open_url(url) as response, open(target_path, "wb") as target_file:
            total = response.headers.get("Content-Length")
            total_int = int(total) if total and total.isdigit() else None
            received = 0
            _emit(progress_callback, stage, 0, total_int)
            while True:
                buffer = response.read(1024 * 64)
                if not buffer:
                    break
                target_file.write(buffer)
                received += len(buffer)
                _emit(progress_callback, stage, received, total_int)

    @staticmethod
    def _safe_extract_tar(archive: tarfile.TarFile, target_dir: Path):
        target_dir = target_dir.resolve()
        for member in archive.getmembers():
            member_path = (target_dir / member.name).resolve()
            if target_dir not in member_path.parents and member_path != target_dir:
                raise RuntimeError(f"Unsafe path in tar archive: {member.name}")
        archive.extractall(target_dir)

    @staticmethod
    def _safe_extract_zip(archive: zipfile.ZipFile, target_dir: Path):
        target_dir = target_dir.resolve()
        for member in archive.infolist():
            member_path = (target_dir / member.filename).resolve()
            if target_dir not in member_path.parents and member_path != target_dir:
                raise RuntimeError(f"Unsafe path in zip archive: {member.filename}")
        archive.extractall(target_dir)

    def find_exiftool(self) -> Path | None:
        if not self.tool_dir.exists():
            return None

        candidates = ("exiftool.exe", "exiftool(-k).exe", "exiftool")
        for candidate in candidates:
            matches = list(self.tool_dir.rglob(candidate))
            if matches:
                return matches[0]
        return None

    def download_exiftool(self, progress_callback: ProgressCallback | None = None) -> Path:
        if not self.is_supported_platform():
            raise RuntimeError(f"Unsupported platform: {platform.system()}")

        self.tool_dir.mkdir(parents=True, exist_ok=True)
        _emit(progress_callback, "exiftool-check", 0, None)
        html = self._download_text(EXIFTOOL_HOME_URL, progress_callback, "exiftool-home")
        version = self._home_page_version(html)
        archive_url = self._archive_url(version)

        archive_name = archive_url.rsplit("/", 2)[-2]
        archive_path = self.tool_dir / archive_name

        self._download_file(archive_url, archive_path, progress_callback, "exiftool-archive")

        try:
            _emit(progress_callback, "exiftool-extract", 0, 1)
            if archive_path.suffix.lower() == ".zip":
                with zipfile.ZipFile(archive_path) as archive:
                    self._safe_extract_zip(archive, self.tool_dir)
            elif archive_path.name.endswith(".tar.gz"):
                with tarfile.open(archive_path, "r:gz") as archive:
                    self._safe_extract_tar(archive, self.tool_dir)
            else:
                raise RuntimeError(f"Unsupported ExifTool archive type: {archive_path.name}")
        finally:
            archive_path.unlink(missing_ok=True)

        exiftool = self.find_exiftool()
        if exiftool is None:
            raise RuntimeError("ExifTool download finished but the executable was not found")

        if not self.is_windows():
            exiftool.chmod(exiftool.stat().st_mode | stat.S_IEXEC)
        _emit(progress_callback, "exiftool-ready", 1, 1)
        return exiftool

    def ensure_exiftool(self, progress_callback: ProgressCallback | None = None) -> Path:
        existing = self.find_exiftool()
        if existing is not None:
            return existing
        return self.download_exiftool(progress_callback=progress_callback)
