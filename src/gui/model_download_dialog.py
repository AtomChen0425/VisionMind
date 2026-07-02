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

from src.core.model_bootstrap import ensure_pretrained_weights, is_pretrained_cached


class _DownloadWorker(QObject):
    stage_changed = Signal(str)
    progress_changed = Signal(int, int)
    log_message = Signal(str)
    finished = Signal(bool, str)
    failed = Signal(str)

    def __init__(self, model_name: str, pretrained: str, cache_dir: str):
        super().__init__()
        self.model_name = model_name
        self.pretrained = pretrained
        self.cache_dir = cache_dir
        self.logger = logging.getLogger(__name__)

    def run(self):
        try:
            self.log_message.emit(f"Checking cache for {self.model_name}:{self.pretrained}")

            def _progress(stage: str, current: int, total: int | None):
                self.stage_changed.emit(stage)
                if total is None:
                    self.progress_changed.emit(max(0, current), 0)
                else:
                    self.progress_changed.emit(max(0, current), max(1, total))

            cached_before = is_pretrained_cached(self.model_name, self.pretrained, cache_dir=self.cache_dir)
            if cached_before:
                self.log_message.emit("Model assets already cached")
                self.finished.emit(False, "already cached")
                return

            self.log_message.emit("Downloading model assets")
            ensure_pretrained_weights(
                self.model_name,
                self.pretrained,
                cache_dir=self.cache_dir,
                progress_callback=_progress,
            )
            self.log_message.emit("Model assets ready")
            self.finished.emit(True, "downloaded")
        except Exception as exc:
            self.logger.exception("Model bootstrap failed")
            self.failed.emit(str(exc))


class ModelDownloadDialog(QDialog):
    def __init__(self, model_name: str, pretrained: str, cache_dir: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preparing AI model")
        self.setModal(True)
        self.resize(560, 260)
        self.setObjectName("ModelDownloadDialog")

        self._model_name = model_name
        self._pretrained = pretrained
        self._cache_dir = cache_dir
        self._thread: QThread | None = None
        self._worker: _DownloadWorker | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(14)

        title = QLabel("Preparing AI model")
        title.setObjectName("DialogTitle")
        subtitle = QLabel(f"{model_name} / {pretrained}")
        subtitle.setObjectName("DialogSubtitle")

        header = QVBoxLayout()
        header.addWidget(title)
        header.addWidget(subtitle)
        outer.addLayout(header)

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
        self.log_box.setMaximumBlockCount(200)
        outer.addWidget(self.log_box, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Close).setEnabled(False)
        buttons.button(QDialogButtonBox.Close).setObjectName("SecondaryButton")
        self._close_button = buttons.button(QDialogButtonBox.Close)
        outer.addWidget(buttons)

    def start(self) -> bool:
        self._thread = QThread(self)
        self._worker = _DownloadWorker(self._model_name, self._pretrained, self._cache_dir)
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
            self.status_label.setText("Checking local cache...")
            self.progress_bar.setRange(0, 0)
        elif stage == "download":
            self.status_label.setText("Downloading model weights...")
        elif stage == "ready":
            self.status_label.setText("Model ready")
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(1)

    def _on_progress_changed(self, current: int, total: int):
        if total <= 0:
            self.progress_bar.setRange(0, 0)
            return
        self.progress_bar.setRange(0, total)
        self.progress_bar.setValue(min(current, total))
        self.status_label.setText(f"Downloading... {min(current, total)}/{total}")

    def _on_finished(self, downloaded: bool, reason: str):
        self._append_log(reason)
        self.status_label.setText("Model ready")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self._close_button.setEnabled(True)
        self.accept()

    def _on_failed(self, message: str):
        self._append_log(message)
        self.status_label.setText("Model download failed")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self._close_button.setEnabled(True)
        self.reject()
