from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from PySide6.QtCore import QObject, Signal

from src.core.analyzer import AnalysisService, OpenClipAnalyzer
from src.core.pipeline import PhotoProcessingPipeline, ProcessingOutcome
from src.core.scanner import ScanSummary, Scanner


class ScanWorker(QObject):
    progress_changed = Signal(int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, scanner: Scanner, root_dir: str, exclude_paths: Sequence[str] | None = None):
        super().__init__()
        self.scanner = scanner
        self.root_dir = root_dir
        self.exclude_paths = list(exclude_paths or [])

    def run(self):
        try:
            summary = self.scanner.scan(
                self.root_dir,
                exclude_paths=self.exclude_paths,
                progress_callback=self._emit_progress,
            )
            self.finished.emit(summary)
        except Exception as exc:
            self.failed.emit(str(exc))

    def _emit_progress(self, summary: ScanSummary):
        self.progress_changed.emit(summary.files_seen, summary.root_path)


class AnalysisWorker(QObject):
    progress_changed = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, pipeline: PhotoProcessingPipeline, file_rows: Sequence[object], *, batch_size: int = 8):
        super().__init__()
        self.pipeline = pipeline
        self.file_rows = list(file_rows)
        self.batch_size = max(1, int(batch_size))

    def run(self):
        try:
            total = len(self.file_rows)
            outcomes: list[ProcessingOutcome] = []
            processed = 0
            for batch_start in range(0, total, self.batch_size):
                batch_rows = self.file_rows[batch_start : batch_start + self.batch_size]
                batch_items = [
                    (int(row["id"]), str(row["file_path"]), int(row["mtime_ns"]), int(row["size"]))
                    for row in batch_rows
                ]
                try:
                    batch_outcomes = self.pipeline.process_files(batch_items)
                except Exception as exc:
                    for file_id, image_path, mtime_ns, size in batch_items:
                        try:
                            outcomes.append(self.pipeline.process_file(file_id, image_path, mtime_ns=mtime_ns, size=size))
                        except Exception as item_exc:
                            self.pipeline.db.set_file_error(file_id, str(item_exc or exc))
                else:
                    outcomes.extend(batch_outcomes)
                for _file_id, image_path, _mtime_ns, _size in batch_items:
                    processed += 1
                    self.progress_changed.emit(processed, total, image_path)
            self.finished.emit(outcomes)
        except Exception as exc:
            self.failed.emit(str(exc))
