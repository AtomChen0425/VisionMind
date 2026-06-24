from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .database import DatabaseManager
from .supported_image_types import SCAN_SUPPORTED_IMAGE_EXTENSIONS


@dataclass(slots=True)
class ScanSummary:
    library_id: int
    root_path: str
    files_seen: int = 0
    files_added: int = 0
    files_updated: int = 0
    files_unchanged: int = 0
    files_skipped: int = 0
    files_deleted: int = 0
    errors: int = 0


class Scanner:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.progress = 0
        self.current_file = ""
        self.last_summary: ScanSummary | None = None

    def _calculate_hash(self, file_path: str, block_size: int = 65536) -> str | None:
        sha256 = hashlib.sha256()
        try:
            with open(file_path, "rb") as file_handle:
                for block in iter(lambda: file_handle.read(block_size), b""):
                    sha256.update(block)
            return sha256.hexdigest()
        except OSError:
            return None

    def _normalize_excludes(self, exclude_paths: Iterable[str] | None) -> list[Path]:
        normalized = []
        if not exclude_paths:
            return normalized
        for path in exclude_paths:
            try:
                normalized.append(Path(path).resolve())
            except OSError:
                continue
        return normalized

    def _is_excluded(self, path: Path, excludes: list[Path]) -> bool:
        for excluded in excludes:
            try:
                path.relative_to(excluded)
                return True
            except ValueError:
                continue
        return False

    def _iter_image_files(self, root_dir: str, *, exclude_paths: Iterable[str] | None = None) -> Iterable[str]:
        root_path = Path(root_dir).resolve()
        excludes = self._normalize_excludes(exclude_paths)
        stack = [root_path]

        while stack:
            current_dir = stack.pop()
            if self._is_excluded(current_dir, excludes):
                continue
            try:
                with os.scandir(current_dir) as entries:
                    subdirs: list[Path] = []
                    for entry in entries:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                dir_path = Path(entry.path)
                                if self._is_excluded(dir_path, excludes):
                                    continue
                                subdirs.append(dir_path)
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            if Path(entry.name).suffix.lower() in SCAN_SUPPORTED_IMAGE_EXTENSIONS:
                                yield entry.path
                        except OSError:
                            continue
                    stack.extend(reversed(subdirs))
            except OSError:
                continue

    def scan(
        self,
        root_dir: str,
        *,
        exclude_paths: Iterable[str] | None = None,
        compute_hash_for_changed_files: bool = True,
        progress_callback: Callable[[ScanSummary], None] | None = None,
    ) -> ScanSummary:
        normalized_root = str(Path(root_dir).resolve())
        library_id = self.db.register_library(normalized_root)
        scan_run_id = self.db.create_scan_run(library_id, normalized_root)
        summary = ScanSummary(library_id=library_id, root_path=normalized_root)
        seen_paths: set[str] = set()
        files_to_scan = list(self._iter_image_files(normalized_root, exclude_paths=exclude_paths))
        total_files = len(files_to_scan)

        try:
            if total_files == 0:
                self.progress = 100
                self.db.update_scan_run_progress(scan_run_id, files_seen=0, files_changed=0, files_deleted=0)
                self.db.finish_scan_run(scan_run_id, "completed")
                self.last_summary = summary
                return summary

            for index, file_path in enumerate(files_to_scan, start=1):
                summary.files_seen += 1
                self.current_file = file_path

                try:
                    stat_result = os.stat(file_path, follow_symlinks=False)
                except OSError:
                    summary.files_skipped += 1
                    summary.errors += 1
                    continue

                normalized_file_path = str(Path(file_path).resolve())
                seen_paths.add(normalized_file_path)
                relative_path = os.path.relpath(normalized_file_path, normalized_root)

                existing = self.db.get_file_by_path(normalized_file_path)
                needs_hash = existing is None or int(existing["mtime_ns"]) != int(stat_result.st_mtime_ns) or int(existing["size"]) != int(stat_result.st_size)
                file_hash = None
                if compute_hash_for_changed_files and needs_hash:
                    file_hash = self._calculate_hash(normalized_file_path)

                try:
                    file_id, changed = self.db.upsert_file_record(
                        library_id=library_id,
                        file_path=normalized_file_path,
                        relative_path=relative_path,
                        file_hash=file_hash if file_hash is not None else (existing["file_hash"] if existing is not None else None),
                        mtime=stat_result.st_mtime,
                        mtime_ns=stat_result.st_mtime_ns,
                        size=stat_result.st_size,
                        status="pending",
                        last_scan_run_id=scan_run_id,
                    )
                except OSError:
                    summary.files_skipped += 1
                    summary.errors += 1
                    continue

                if existing is None:
                    summary.files_added += 1
                elif changed:
                    summary.files_updated += 1
                else:
                    summary.files_unchanged += 1

                self.progress = min(99, int((index / total_files) * 100))
                if progress_callback is not None:
                    progress_callback(summary)

            summary.files_deleted = self.db.mark_missing_files(library_id, seen_paths)
            self.progress = 100
            self.last_summary = summary
            self.db.update_scan_run_progress(
                scan_run_id,
                files_seen=summary.files_seen,
                files_changed=summary.files_added + summary.files_updated,
                files_deleted=summary.files_deleted,
            )
            self.db.finish_scan_run(scan_run_id, "completed")
            return summary
        except Exception as exc:
            self.db.finish_scan_run(scan_run_id, "failed", str(exc))
            raise

    def get_scan_progress(self):
        return {"progress": self.progress, "current_file": self.current_file}
