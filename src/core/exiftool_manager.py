from __future__ import annotations

import platform
import re
import shutil
import stat
import tarfile
import urllib.request
import zipfile
from pathlib import Path

from .app_paths import get_exiftool_dir


EXIFTOOL_HOME_URL = "https://exiftool.org/"


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
    def _download_text(url: str) -> str:
        with urllib.request.urlopen(url) as response:
            return response.read().decode("utf-8", errors="replace")

    @staticmethod
    def _download_file(url: str, target_path: Path):
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url) as response, open(target_path, "wb") as target_file:
            shutil.copyfileobj(response, target_file)

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

    def download_exiftool(self) -> Path:
        if not self.is_supported_platform():
            raise RuntimeError(f"Unsupported platform: {platform.system()}")

        self.tool_dir.mkdir(parents=True, exist_ok=True)
        html = self._download_text(EXIFTOOL_HOME_URL)
        version = self._home_page_version(html)
        archive_url = self._archive_url(version)

        archive_name = archive_url.rsplit("/", 2)[-2]
        archive_path = self.tool_dir / archive_name

        self._download_file(archive_url, archive_path)

        try:
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
        return exiftool

    def ensure_exiftool(self) -> Path:
        existing = self.find_exiftool()
        if existing is not None:
            return existing
        return self.download_exiftool()
