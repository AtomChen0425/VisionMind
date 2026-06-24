from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from src.core.database import DatabaseManager
from src.core.pipeline import PhotoProcessingPipeline
from src.core.scanner import Scanner
from src.gui.workers import AnalysisWorker, ScanWorker


class AutoLibraryController(QObject):
    libraries_changed = Signal(object)
    active_library_changed = Signal(int, str)
    scan_started = Signal(int, str)
    scan_finished = Signal(object)
    analysis_started = Signal(int, str)
    analysis_finished = Signal(object)
    message = Signal(str)

    def __init__(self, db: DatabaseManager, scanner: Scanner, pipeline: PhotoProcessingPipeline, *, scan_interval_ms: int = 45000):
        super().__init__()
        self.db = db
        self.scanner = scanner
        self.pipeline = pipeline
        self.scan_interval_ms = scan_interval_ms
        self.scan_running = False
        self.analysis_running = False
        self._scan_queue: list[int] = []
        self._scan_index = 0
        self._active_library_id: int | None = None
        self._active_root_path: str = ""

        self.timer = QTimer(self)
        self.timer.setInterval(self.scan_interval_ms)
        self.timer.timeout.connect(self.request_scan)

        self.scan_thread: QThread | None = None
        self.scan_worker: ScanWorker | None = None
        self.analysis_thread: QThread | None = None
        self.analysis_worker: AnalysisWorker | None = None

    def refresh_libraries(self):
        libraries = self.db.list_libraries()
        self.libraries_changed.emit(libraries)
        if self._active_library_id is None and libraries:
            first = libraries[0]
            self._active_library_id = int(first["id"])
            self._active_root_path = str(first["root_path"])
            self.active_library_changed.emit(self._active_library_id, self._active_root_path)
        self._scan_queue = [int(row["id"]) for row in libraries]
        if not self.timer.isActive() and libraries:
            self.timer.start()

    def add_library(self, root_path: str) -> int:
        library_id = self.db.register_library(root_path)
        self.refresh_libraries()
        self.set_active_library(library_id)
        return library_id

    def remove_library(self, library_id: int):
        with self.db.get_connection() as conn:
            conn.execute("DELETE FROM libraries WHERE id = ?", (library_id,))
        self.refresh_libraries()

    def set_active_library(self, library_id: int):
        library = None
        for row in self.db.list_libraries():
            if int(row["id"]) == int(library_id):
                library = row
                break
        if library is None:
            return
        self._active_library_id = int(library["id"])
        self._active_root_path = str(library["root_path"])
        self.active_library_changed.emit(self._active_library_id, self._active_root_path)

    def active_library(self):
        if self._active_library_id is None:
            return None
        return self.db.get_library(self._active_root_path)

    def set_library_excludes(self, library_id: int, paths: list[str]):
        self.db.set_library_excludes(library_id, paths)
        self.message.emit("Exclude paths updated")

    def get_library_excludes(self, library_id: int):
        return self.db.get_library_excludes(library_id)

    def stop(self):
        self.timer.stop()

    def has_libraries(self) -> bool:
        return len(self._scan_queue) > 0

    def request_scan(self):
        if not self._scan_queue or self.scan_running or self.analysis_running:
            return

        if self._scan_index >= len(self._scan_queue):
            self._scan_index = 0

        library_id = self._scan_queue[self._scan_index]
        self._scan_index = (self._scan_index + 1) % len(self._scan_queue)

        library = None
        for row in self.db.list_libraries():
            if int(row["id"]) == library_id:
                library = row
                break
        if library is None:
            self.refresh_libraries()
            return

        self.scan_running = True
        root_path = str(library["root_path"])
        excludes = [str(row["path"]) for row in self.db.get_library_excludes(library_id)]
        self.scan_started.emit(library_id, root_path)
        self.message.emit(f"Watching {root_path} for changes...")

        self.scan_thread = QThread(self)
        self.scan_worker = ScanWorker(self.scanner, root_path, excludes)
        self.scan_worker.moveToThread(self.scan_thread)
        self.scan_thread.started.connect(self.scan_worker.run)
        self.scan_worker.finished.connect(self._on_scan_finished)
        self.scan_worker.failed.connect(self._on_scan_failed)
        self.scan_worker.finished.connect(self.scan_thread.quit)
        self.scan_worker.failed.connect(self.scan_thread.quit)
        self.scan_thread.finished.connect(self.scan_worker.deleteLater)
        self.scan_thread.finished.connect(self.scan_thread.deleteLater)
        self.scan_thread.start()

    @Slot(object)
    def _on_scan_finished(self, summary):
        self.scan_running = False
        self.scan_finished.emit(summary)
        self.message.emit(
            f"Scan complete: {summary.root_path} | {summary.files_seen} seen, {summary.files_added + summary.files_updated} changed, {summary.files_deleted} deleted"
        )
        self._maybe_start_analysis(summary.library_id)

    @Slot(str)
    def _on_scan_failed(self, message: str):
        self.scan_running = False
        self.message.emit(f"Scan failed: {message}")

    def _maybe_start_analysis(self, library_id: int):
        if self.analysis_running:
            return
        if not self.pipeline.analysis_service.analyzer.available():
            return

        pending_rows = self.db.list_pending_files(library_id)
        if not pending_rows:
            self.message.emit("Library is up to date")
            return

        library = None
        for row in self.db.list_libraries():
            if int(row["id"]) == library_id:
                library = row
                break
        if library is None:
            return

        self.analysis_running = True
        root_path = str(library["root_path"])
        self.analysis_started.emit(library_id, root_path)
        self.message.emit(f"Analyzing {len(pending_rows)} pending files in {root_path}...")

        self.analysis_thread = QThread(self)
        self.analysis_worker = AnalysisWorker(self.pipeline, pending_rows)
        self.analysis_worker.moveToThread(self.analysis_thread)
        self.analysis_thread.started.connect(self.analysis_worker.run)
        self.analysis_worker.finished.connect(self._on_analysis_finished)
        self.analysis_worker.failed.connect(self._on_analysis_failed)
        self.analysis_worker.finished.connect(self.analysis_thread.quit)
        self.analysis_worker.failed.connect(self.analysis_thread.quit)
        self.analysis_thread.finished.connect(self.analysis_worker.deleteLater)
        self.analysis_thread.finished.connect(self.analysis_thread.deleteLater)
        self.analysis_thread.start()

    @Slot(object)
    def _on_analysis_finished(self, outcomes):
        self.analysis_running = False
        self.analysis_finished.emit(outcomes)
        self.message.emit(f"Analysis complete: {len(outcomes)} files processed")

    @Slot(str)
    def _on_analysis_failed(self, message: str):
        self.analysis_running = False
        self.message.emit(f"Analysis failed: {message}")
