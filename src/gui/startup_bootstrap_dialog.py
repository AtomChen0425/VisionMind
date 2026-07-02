from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QVBoxLayout,
)

from src.core.exiftool_manager import ExifToolManager
from src.core.model_bootstrap import ensure_pretrained_weights
from src.core.model_bootstrap import is_pretrained_cached


class _BootstrapWorker(QObject):
    stage_changed = Signal(str)
    progress_changed = Signal(int, int)
    log_message = Signal(str)
    finished = Signal()
    failed = Signal(str)

    def __init__(self, model_name: str, pretrained: str, model_cache_dir: str, exiftool_dir: str):
        super().__init__()
        self.model_name = model_name
        self.pretrained = pretrained
        self.model_cache_dir = model_cache_dir
        self.exiftool_manager = ExifToolManager(exiftool_dir)
        self.logger = logging.getLogger(__name__)

    def run(self):
        try:
            if not is_pretrained_cached(self.model_name, self.pretrained, cache_dir=self.model_cache_dir):
                self.log_message.emit(f"Preparing AI model: {self.model_name} / {self.pretrained}")

                def _model_progress(stage: str, current: int, total: int | None):
                    self.stage_changed.emit(stage)
                    self.progress_changed.emit(max(0, current), max(1, total) if total else 0)

                ensure_pretrained_weights(
                    self.model_name,
                    self.pretrained,
                    cache_dir=self.model_cache_dir,
                    progress_callback=_model_progress,
                )
                self.log_message.emit("AI model ready")
            else:
                self.log_message.emit("AI model already cached")

            if self.exiftool_manager.find_exiftool() is None:
                self.log_message.emit("Preparing ExifTool")

                def _exiftool_progress(stage: str, current: int, total: int | None):
                    self.stage_changed.emit(stage)
                    self.progress_changed.emit(max(0, current), max(1, total) if total else 0)

                self.exiftool_manager.ensure_exiftool(progress_callback=_exiftool_progress)
                self.log_message.emit("ExifTool ready")
            else:
                self.log_message.emit("ExifTool already cached")

            self.finished.emit()
        except Exception as exc:
            self.logger.exception("Bootstrap worker failed")
            self.failed.emit(str(exc))


class StartupBootstrapDialog(QDialog):
    def __init__(self, model_name: str, pretrained: str, model_cache_dir: str, exiftool_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preparing application resources")
        self.setModal(True)
        self.resize(640, 320)
        self.setObjectName("StartupBootstrapDialog")

        self._model_name = model_name
        self._pretrained = pretrained
        self._model_cache_dir = model_cache_dir
        self._exiftool_dir = exiftool_dir
        self._thread: QThread | None = None
        self._worker: _BootstrapWorker | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(14)

        title = QLabel("Preparing application resources")
        title.setObjectName("DialogTitle")
        subtitle = QLabel("AI model and ExifTool are prepared on first run, then reused locally.")
        subtitle.setObjectName("DialogSubtitle")
        outer.addWidget(title)
        outer.addWidget(subtitle)

        self.status_label = QLabel("Starting...")
        self.status_label.setObjectName("DialogStatus")
        outer.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setTextVisible(True)
        outer.addWidget(self.progress_bar)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setObjectName("DialogLog")
        self.log_box.setMaximumBlockCount(300)
        outer.addWidget(self.log_box, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Close).setEnabled(False)
        buttons.button(QDialogButtonBox.Close).setObjectName("SecondaryButton")
        self._close_button = buttons.button(QDialogButtonBox.Close)
        outer.addWidget(buttons)

    def start(self) -> bool:
        self._thread = QThread(self)
        self._worker = _BootstrapWorker(
            self._model_name,
            self._pretrained,
            self._model_cache_dir,
            self._exiftool_dir,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.stage_changed.connect(self._on_stage_changed)
        self._worker.progress_changed.connect(self._on_progress_changed)
        self._worker.log_message.connect(self._append_log)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.failed.connect(self._thread.quit)
        self._worker.finished.connect(lambda *_: self._thread.quit())
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()
        return self.exec() == QDialog.Accepted

    def _append_log(self, text: str):
        self.log_box.appendPlainText(text)

    def _on_stage_changed(self, stage: str):
        if stage == "check":
            self.status_label.setText("Checking cache...")
            self.progress_bar.setRange(0, 0)
        elif stage == "download":
            self.status_label.setText("Downloading AI model...")
        elif stage == "fetch":
            self.status_label.setText("Fetching ExifTool metadata...")
        elif stage == "exiftool-home":
            self.status_label.setText("Checking ExifTool version...")
            self.progress_bar.setRange(0, 0)
        elif stage == "exiftool-archive":
            self.status_label.setText("Downloading ExifTool...")
        elif stage == "exiftool-extract":
            self.status_label.setText("Extracting ExifTool...")
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)
        elif stage in {"ready", "exiftool-ready"}:
            self.status_label.setText("Ready")
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(1)

    def _on_progress_changed(self, current: int, total: int):
        if total <= 0:
            self.progress_bar.setRange(0, 0)
            return
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(min(current, total))
        self.status_label.setText(f"Working... {min(current, total)}/{total}")

    def _on_finished(self):
        self._append_log("All startup resources are ready")
        self.status_label.setText("Ready")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self._close_button.setEnabled(True)
        self.accept()

    def _on_failed(self, message: str):
        self._append_log(message)
        self.status_label.setText("Startup resource preparation failed")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self._close_button.setEnabled(True)
        self.reject()
