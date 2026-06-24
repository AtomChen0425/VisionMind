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

    def __init__(self, pipeline: PhotoProcessingPipeline, file_rows: Sequence[object]):
        super().__init__()
        self.pipeline = pipeline
        self.file_rows = list(file_rows)

    def run(self):
        try:
            total = len(self.file_rows)
            outcomes: list[ProcessingOutcome] = []
            for index, row in enumerate(self.file_rows, start=1):
                file_id = int(row["id"])
                image_path = str(row["file_path"])
                try:
                    outcome = self.pipeline.process_file(file_id, image_path)
                except Exception as exc:
                    self.pipeline.db.set_file_error(file_id, str(exc))
                else:
                    outcomes.append(outcome)
                self.progress_changed.emit(index, total, image_path)
            self.finished.emit(outcomes)
        except Exception as exc:
            self.failed.emit(str(exc))
