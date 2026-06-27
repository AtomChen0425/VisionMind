from __future__ import annotations

from pathlib import Path
import json
import logging
import sys

from PySide6.QtCore import QModelIndex, QMimeData, QProcess, QSettings, Qt, QSize, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QMenu,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.analyzer import AnalysisService, OpenClipAnalyzer
from src.core.database import DatabaseManager
from src.core.logging_utils import setup_logging
from src.core.metadata_reader import read_image_metadata
from src.core.semantic_search import SemanticSearchService
from src.core.pipeline import PhotoProcessingPipeline
from src.core.exiftool_metadata import ExifToolTagWriter
from src.core.scanner import Scanner
from src.core.vector_index import VectorIndexManager
from src.gui.automation import AutoLibraryController
from src.gui.gallery import GalleryModel


class StatCard(QFrame):
    def __init__(self, title: str, value: str = "0"):
        super().__init__()
        self.setObjectName("StatCard")
        self.title = QLabel(title)
        self.title.setObjectName("StatTitle")
        self.value = QLabel(value)
        self.value.setObjectName("StatValue")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)
        layout.addWidget(self.title)
        layout.addWidget(self.value)

    def set_value(self, value: str):
        self.value.setText(value)


class AspectPreviewLabel(QLabel):
    def __init__(self):
        super().__init__("Select a photo")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(320)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._source_pixmap: QPixmap | None = None

    def set_source_pixmap(self, pixmap: QPixmap | None):
        self._source_pixmap = pixmap
        self._refresh_pixmap()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_pixmap()

    def _refresh_pixmap(self):
        if self._source_pixmap is None or self._source_pixmap.isNull():
            self.setPixmap(QPixmap())
            return
        scaled = self._source_pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.setPixmap(scaled)


class DetailsPanel(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("DetailsPanel")
        self.preview = AspectPreviewLabel()

        self.path = QLabel("-")
        self.relative_path = QLabel("-")
        self.status = QLabel("-")
        self.metadata_state = QLabel("-")
        self.tags = QTextEdit()
        self.tags.setReadOnly(True)
        self.tags.setMinimumHeight(180)
        self.metadata_details = QTextEdit()
        self.metadata_details.setReadOnly(True)
        self.metadata_details.setMinimumHeight(220)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(self.preview)
        layout.addWidget(self._label_block("File", self.path))
        layout.addWidget(self._label_block("Relative", self.relative_path))
        layout.addWidget(self._label_block("Status", self.status))
        layout.addWidget(self._label_block("Metadata", self.metadata_state))
        layout.addWidget(self._label_block("Tags", self.tags))
        layout.addWidget(self._label_block("Image Metadata", self.metadata_details))

    def _label_block(self, title: str, widget: QWidget):
        container = QFrame()
        container.setObjectName("DetailBlock")
        block_layout = QVBoxLayout(container)
        block_layout.setContentsMargins(0, 0, 0, 0)
        block_layout.setSpacing(4)
        heading = QLabel(title)
        heading.setObjectName("DetailHeading")
        block_layout.addWidget(heading)
        block_layout.addWidget(widget)
        return container

    def set_item(self, item, tags):
        if item is None:
            self.preview.setText("Select a photo")
            self.preview.set_source_pixmap(None)
            self.path.setText("-")
            self.relative_path.setText("-")
            self.status.setText("-")
            self.metadata_state.setText("-")
            self.tags.setPlainText("")
            self.metadata_details.setPlainText("")
            return

        pixmap = item.thumbnail or QPixmap(320, 320)
        if item.thumbnail is None:
            pixmap.fill(QColor("#f3f0e8"))
        self.preview.setText("")
        self.preview.set_source_pixmap(pixmap)
        self.path.setText(item.file_path)
        self.relative_path.setText(item.relative_path)
        if item.status == "error" and item.last_error:
            self.status.setText(f"error: {item.last_error}")
        else:
            self.status.setText(item.status)
        self.metadata_state.setText(item.xmp_state)
        if tags:
            text = "\n".join(f"{row['tag_name']}  ({row['confidence']:.2f})" for row in tags)
        else:
            text = "No tags yet"
        self.tags.setPlainText(text)
        try:
            metadata = read_image_metadata(item.file_path)
            self.metadata_details.setPlainText(json.dumps(metadata, indent=2, ensure_ascii=False, default=str))
        except Exception as exc:
            self.metadata_details.setPlainText(f"Failed to read metadata: {exc}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.setWindowTitle("PhotoManager")
        self.resize(1600, 960)

        self.log_path = setup_logging()
        self.logger.info("Application starting")
        self.db = DatabaseManager("data/photo_manager.db")
        self.scanner = Scanner(self.db)
        self.analyzer = OpenClipAnalyzer()
        self.analysis_service = AnalysisService(self.analyzer)
        self.vector_index = VectorIndexManager(self.db)
        self.search_service = SemanticSearchService(self.db, self.analysis_service, self.vector_index)
        self.pipeline = PhotoProcessingPipeline(self.db, self.analysis_service, ExifToolTagWriter(), self.vector_index)
        self.controller = AutoLibraryController(self.db, self.scanner, self.pipeline)

        self.settings = QSettings("PhotoManager", "PhotoManager")
        self.library_id: int | None = None
        self.root_path: str = ""
        self._updating_library_list = False
        self._search_mode = "Mixed"

        self._build_ui()
        self._bind_signals()
        self._apply_style()
        self._refresh_exiftool_status()
        self.controller.refresh_libraries()

        last_library = self.settings.value("lastLibraryPath", "", str)
        if last_library and Path(last_library).exists():
            self._select_or_add_library(last_library, from_startup=True)
        elif self.library_list.count() > 0:
            self.library_list.setCurrentRow(0)
            self._select_library_by_row(0)
        else:
            self.status_label.setText("Choose a library to start automatic import monitoring")

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(14)

        self.search_mode = QComboBox()
        self.search_mode.addItems(["Mixed", "Filename", "Semantic"])
        self.search_mode.currentTextChanged.connect(self._on_search_mode_changed)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search by filename or meaning")
        self.search_box.returnPressed.connect(self._execute_search)
        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self._execute_search)
        self.search_btn.setObjectName("SecondaryButton")

        header = QFrame()
        header.setObjectName("HeaderCard")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 16, 18, 16)
        header_layout.setSpacing(14)

        title_block = QVBoxLayout()
        self.title_label = QLabel("PhotoManager")
        self.title_label.setObjectName("AppTitle")
        self.subtitle_label = QLabel("Automatic library ingestion, thumbnails, ExifTool metadata tags, and AI keywording")
        self.subtitle_label.setObjectName("AppSubtitle")
        title_block.addWidget(self.title_label)
        title_block.addWidget(self.subtitle_label)
        header_layout.addLayout(title_block, 1)

        search_panel = QFrame()
        search_panel.setObjectName("SearchPanel")
        search_layout = QHBoxLayout(search_panel)
        search_layout.setContentsMargins(10, 8, 10, 8)
        search_layout.setSpacing(8)
        search_layout.addWidget(self.search_box, 1)
        search_layout.addWidget(self.search_mode)
        search_layout.addWidget(self.search_btn)
        header_layout.addWidget(search_panel, 2)

        action_block = QHBoxLayout()
        self.choose_btn = QPushButton("Add Library")
        self.choose_btn.clicked.connect(self.choose_library)
        self.monitoring_tag = QLabel("Auto monitoring")
        self.monitoring_tag.setObjectName("MonitoringTag")
        action_block.addWidget(self.choose_btn)
        action_block.addWidget(self.monitoring_tag)

        exiftool_block = QVBoxLayout()
        exiftool_block.setSpacing(4)
        self.exiftool_status_label = QLabel("ExifTool: checking...")
        self.exiftool_status_label.setObjectName("ExifToolStatusTag")
        self.exiftool_status_label.setWordWrap(True)
        self.exiftool_path_label = QLabel("-")
        self.exiftool_path_label.setObjectName("ExifToolPathTag")
        self.exiftool_path_label.setWordWrap(True)
        exiftool_block.addWidget(self.exiftool_status_label)
        exiftool_block.addWidget(self.exiftool_path_label)
        action_block.addLayout(exiftool_block)
        header_layout.addLayout(action_block, 1)

        outer.addWidget(header)

        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        self.total_card = StatCard("Total")
        self.pending_card = StatCard("Pending")
        self.analyzed_card = StatCard("Analyzed")
        self.error_card = StatCard("Errors")
        for card in (self.total_card, self.pending_card, self.analyzed_card, self.error_card):
            stats_row.addWidget(card)
        outer.addLayout(stats_row)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        left_panel = QFrame()
        left_panel.setObjectName("SidePanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(16, 16, 16, 16)
        left_layout.setSpacing(12)

        self.library_list = QListWidget()
        self.library_list.currentRowChanged.connect(self._select_library_by_row)

        self.library_label = QLabel("No library selected")
        self.library_label.setWordWrap(True)
        self.status_label = QLabel("Idle")
        self.status_label.setWordWrap(True)

        self.excludes_box = QPlainTextEdit()
        self.excludes_box.setPlaceholderText("One exclude path per line")
        self.excludes_box.setMinimumHeight(120)

        self.save_excludes_btn = QPushButton("Save Excludes")
        self.save_excludes_btn.clicked.connect(self._save_excludes)
        self.save_excludes_btn.setObjectName("SecondaryButton")

        self.scan_now_btn = QPushButton("Scan Now")
        self.scan_now_btn.clicked.connect(self._manual_scan_current_library)
        self.scan_now_btn.setObjectName("SecondaryButton")

        self.delete_library_btn = QPushButton("Delete Library")
        self.delete_library_btn.clicked.connect(self._delete_current_library)
        self.delete_library_btn.setObjectName("SecondaryButton")

        left_layout.addWidget(QLabel("Libraries"))
        left_layout.addWidget(self.library_list, 1)
        left_layout.addWidget(QLabel("Current Library"))
        left_layout.addWidget(self.library_label)
        left_layout.addWidget(QLabel("Status"))
        left_layout.addWidget(self.status_label)
        left_layout.addWidget(QLabel("Exclude Paths"))
        left_layout.addWidget(self.excludes_box)
        left_layout.addWidget(self.save_excludes_btn)
        action_row = QHBoxLayout()
        action_row.addWidget(self.scan_now_btn)
        action_row.addWidget(self.delete_library_btn)
        left_layout.addLayout(action_row)

        center_panel = QFrame()
        center_panel.setObjectName("CenterPanel")
        center_layout = QVBoxLayout(center_panel)
        center_layout.setContentsMargins(16, 16, 16, 16)
        center_layout.setSpacing(12)

        self.view = QListView()
        self.view.setViewMode(QListView.IconMode)
        self.view.setResizeMode(QListView.Adjust)
        self.view.setMovement(QListView.Static)
        self.view.setSpacing(14)
        self.view.setWrapping(True)
        self.view.setIconSize(QSize(220, 220))
        self.view.setUniformItemSizes(True)
        self.view.setWordWrap(True)
        self.view.setSelectionMode(QListView.SingleSelection)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._show_gallery_context_menu)
        center_layout.addWidget(self.view)

        right_panel = DetailsPanel()
        right_panel.setMinimumWidth(380)
        right_panel.setMaximumWidth(460)

        splitter.addWidget(left_panel)
        splitter.addWidget(center_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)

        outer.addWidget(splitter, 1)

        self.details_panel = right_panel

    def _bind_signals(self):
        self.controller.libraries_changed.connect(self._on_libraries_changed)
        self.controller.active_library_changed.connect(self._on_active_library_changed)
        self.controller.scan_started.connect(lambda library_id, root_path: self._set_status(f"Scanning {root_path} in the background..."))
        self.controller.scan_started.connect(lambda *_: self._update_library_action_state())
        self.controller.scan_finished.connect(self._on_scan_finished)
        self.controller.analysis_started.connect(lambda library_id, root_path: self._set_status(f"Analyzing new and changed photos in {root_path}..."))
        self.controller.analysis_started.connect(lambda *_: self._update_library_action_state())
        self.controller.analysis_finished.connect(self._on_analysis_finished)
        self.controller.message.connect(self._set_status)

    def _apply_style(self):
        QApplication.setStyle("Fusion")
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor("#ede7dc"))
        palette.setColor(QPalette.WindowText, QColor("#2d2924"))
        palette.setColor(QPalette.Base, QColor("#f6f1e8"))
        palette.setColor(QPalette.AlternateBase, QColor("#e3dccf"))
        palette.setColor(QPalette.Text, QColor("#2d2924"))
        palette.setColor(QPalette.Button, QColor("#efe5d2"))
        palette.setColor(QPalette.ButtonText, QColor("#2d2924"))
        palette.setColor(QPalette.Highlight, QColor("#357e72"))
        palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        QApplication.instance().setPalette(palette)

        self.setStyleSheet(
            """
            QWidget {
                color: #2d2924;
                font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
                font-size: 12px;
            }
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #e8dfd2, stop:0.52 #f1eadf, stop:1 #dbe7df);
            }
            QFrame#HeaderCard, QFrame#SidePanel, QFrame#CenterPanel, QFrame#DetailsPanel, QFrame#StatCard {
                background: #f4eee3;
                border: 1px solid #cfc5b6;
                border-radius: 10px;
            }
            QFrame#HeaderCard {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #efe4d4, stop:0.45 #f8f1e6, stop:1 #d9e5dd);
                border: 1px solid #c8baa6;
            }
            QFrame#SearchPanel,
            QFrame#CenterPanel {
                background: #ebe3d7;
                border: 1px solid #cbbfad;
            }
            QFrame#SidePanel {
                background: #e5ddd0;
            }
            QFrame#DetailsPanel {
                background: #eee7db;
            }
            QFrame#StatCard {
                background: #e9e0d2;
            }
            QFrame#DetailBlock {
                background: #e9e1d5;
                border: 1px solid #d1c6b8;
                border-radius: 8px;
                padding: 8px;
            }
            QLabel#AppTitle {
                font-size: 26px;
                font-weight: 750;
                color: #29231d;
            }
            QLabel#AppSubtitle {
                color: #6f6558;
                font-size: 12px;
            }
            QLabel#MonitoringTag {
                color: #214f49;
                background: #d6e8df;
                border: 1px solid #a9cbbd;
                border-radius: 12px;
                padding: 8px 14px;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel#ExifToolStatusTag {
                color: #59401a;
                background: #ead8b7;
                border: 1px solid #c9ac72;
                border-radius: 12px;
                padding: 8px 14px;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel#ExifToolPathTag {
                color: #6f6558;
                font-size: 11px;
                font-family: Consolas, monospace;
                opacity: 0.95;
            }
            QLabel#StatTitle, QLabel#DetailHeading {
                color: #716658;
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 1px;
            }
            QLabel#StatValue {
                color: #2b251f;
                font-size: 28px;
                font-weight: 700;
            }
            QPushButton {
                background: #357e72;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                padding: 10px 16px;
                font-weight: 600;
            }
            QPushButton:hover { background: #2b6c62; }
            QPushButton:pressed { background: #23584f; }
            QPushButton:disabled { background: #cfc5b6; color: #8b8175; }
            QPushButton#SecondaryButton {
                background: #e7d8bc;
                color: #342d25;
                border: 1px solid #c5af82;
            }
            QPushButton#SecondaryButton:hover { background: #dec999; }
            QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {
                background: #f7f1e7;
                color: #2d2924;
                border: 1px solid #c8bbab;
                border-radius: 8px;
                padding: 10px 12px;
                selection-background-color: #357e72;
                selection-color: #ffffff;
            }
            QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
                border: 1px solid #357e72;
                background: #fbf5ea;
            }
            QComboBox::drop-down {
                border: none;
                width: 28px;
            }
            QListView, QListWidget {
                background: transparent;
                border: none;
                outline: 0;
            }
            QListView::item, QListWidget::item {
                background: #f2eadf;
                border: 1px solid #d5c8b8;
                border-radius: 8px;
                padding: 8px;
                margin: 4px;
                color: #302a24;
            }
            QListView::item:hover, QListWidget::item:hover {
                background: #eadcc7;
                border: 1px solid #bd9f66;
            }
            QListView::item:selected, QListWidget::item:selected {
                border: 1px solid #357e72;
                background: #cfe2d9;
                color: #1f4640;
            }
            QMenu {
                background: #f4eee3;
                color: #2d2924;
                border: 1px solid #c8bbab;
                border-radius: 8px;
                padding: 6px;
            }
            QMenu::item {
                padding: 8px 24px;
                border-radius: 6px;
            }
            QMenu::item:selected {
                background: #cfe2d9;
                color: #1f4640;
            }
            QSplitter::handle {
                background: #cbc0b1;
            }
            """
        )

    def _set_status(self, text: str):
        self.status_label.setText(text)

    def _selected_gallery_index(self, view_index: QModelIndex | None = None):
        if view_index is not None and view_index.isValid():
            return view_index
        return self.view.currentIndex()

    def _selected_gallery_item(self, view_index: QModelIndex | None = None):
        index = self._selected_gallery_index(view_index)
        if not index.isValid() or not hasattr(self, "gallery_model"):
            return None
        return self.gallery_model.item(index.row())

    def _show_gallery_context_menu(self, position):
        if not hasattr(self, "gallery_model"):
            return
        view_index = self.view.indexAt(position)
        if not view_index.isValid():
            return
        self.view.setCurrentIndex(view_index)
        item = self._selected_gallery_item(view_index)
        if item is None:
            return

        menu = QMenu(self)
        open_action = menu.addAction("Open")
        reveal_action = menu.addAction("Show in Folder")
        copy_file_action = menu.addAction("Copy File")
        copy_path_action = menu.addAction("Copy Path")
        chosen = menu.exec(self.view.viewport().mapToGlobal(position))
        if chosen == open_action:
            self._open_file(item.file_path)
        elif chosen == reveal_action:
            self._show_in_folder(item.file_path)
        elif chosen == copy_file_action:
            self._copy_file_to_clipboard(item.file_path)
        elif chosen == copy_path_action:
            self._copy_path_to_clipboard(item.file_path)

    def _open_file(self, file_path: str):
        QDesktopServices.openUrl(QUrl.fromLocalFile(file_path))

    def _show_in_folder(self, file_path: str):
        path = Path(file_path)
        if sys.platform.startswith("win"):
            QProcess.startDetached("explorer.exe", [f"/select,{str(path)}"])
            return
        if sys.platform == "darwin":
            QProcess.startDetached("open", ["-R", str(path)])
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    def _copy_file_to_clipboard(self, file_path: str):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(file_path)])
        mime.setText(file_path)
        QApplication.clipboard().setMimeData(mime)

    def _copy_path_to_clipboard(self, file_path: str):
        QApplication.clipboard().setText(file_path)

    def _update_library_action_state(self):
        has_library = self.library_id is not None
        busy = self.controller.scan_running or self.controller.analysis_running
        self.scan_now_btn.setEnabled(has_library and not busy)
        self.delete_library_btn.setEnabled(has_library and not busy)
        self.save_excludes_btn.setEnabled(has_library and not busy)

    def _refresh_exiftool_status(self):
        writer = getattr(self.pipeline, "metadata_writer", None)
        exiftool_path = None
        if writer is not None:
            exiftool_path = getattr(writer, "exiftool_path", None)
            if exiftool_path is None:
                manager = getattr(writer, "manager", None)
                if manager is not None:
                    exiftool_path = manager.find_exiftool()
            elif not exiftool_path.exists():
                exiftool_path = None

        if exiftool_path is None:
            self.exiftool_status_label.setText("ExifTool: not ready")
            self.exiftool_path_label.setText("Will download on first write into data/tools/exiftool")
            return

        resolved_path = str(Path(exiftool_path).resolve())
        self.exiftool_status_label.setText("ExifTool: ready")
        self.exiftool_path_label.setText(resolved_path)

    def _set_gallery_library_view(self):
        if self.library_id is None:
            return
        self.gallery_model.refresh(self.library_id)
        self.view.setModel(self.gallery_model)
        self._sync_library_excludes_box()
        self._refresh_stats()
        self._update_library_action_state()

    def _on_search_mode_changed(self, mode: str):
        self._search_mode = mode

    def _execute_search(self):
        if self.library_id is None or not hasattr(self, "gallery_model"):
            return
        query = self.search_box.text().strip()
        if not query:
            self._set_gallery_library_view()
            return

        try:
            if self._search_mode == "Filename":
                rows = self.db.search_files_by_name(self.library_id, query, limit=200)
                self.gallery_model.set_search_results(self.library_id, rows)
            else:
                mode = "mixed" if self._search_mode == "Mixed" else "semantic"
                file_ids, score_map, _source_map = self.search_service.search(self.library_id, query, mode=mode, limit=200)
                rows = self.db.list_files_by_ids(self.library_id, file_ids)
                self.gallery_model.set_search_results(self.library_id, rows, score_map=score_map)
            self.view.setModel(self.gallery_model)
        except Exception as exc:
            self._set_status(f"Search failed: {exc}")

    def _refresh_stats(self):
        if self.library_id is None:
            self.total_card.set_value("0")
            self.pending_card.set_value("0")
            self.analyzed_card.set_value("0")
            self.error_card.set_value("0")
            return
        stats = self.db.get_library_stats(self.library_id)
        self.total_card.set_value(str(stats["total_files"] or 0))
        self.pending_card.set_value(str(stats["pending_files"] or 0))
        self.analyzed_card.set_value(str(stats["analyzed_files"] or 0))
        self.error_card.set_value(str(stats["error_files"] or 0))

    def _selected_library_id(self):
        item = self.library_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def _sync_library_excludes_box(self):
        if self.library_id is None:
            self.excludes_box.setPlainText("")
            return
        excludes = self.controller.get_library_excludes(self.library_id)
        paths = [str(row["path"]) for row in excludes]
        self.excludes_box.blockSignals(True)
        self.excludes_box.setPlainText("\n".join(paths))
        self.excludes_box.blockSignals(False)

    def _select_or_add_library(self, root_path: str, *, from_startup: bool = False):
        library_id = self.controller.add_library(root_path)
        self.settings.setValue("lastLibraryPath", str(Path(root_path).resolve()))
        if not from_startup:
            self._set_status(f"Library added: {root_path}")
        self._select_library(library_id)

    def _select_library(self, library_id: int):
        for row in range(self.library_list.count()):
            item = self.library_list.item(row)
            if item.data(Qt.UserRole) == library_id:
                self.library_list.setCurrentRow(row)
                return
        self.controller.set_active_library(library_id)

    def _select_library_by_row(self, row: int):
        if self._updating_library_list:
            return
        item = self.library_list.item(row)
        if item is None:
            self.library_id = None
            self.root_path = ""
            self.library_label.setText("No library selected")
            self.view.setModel(None)
            self.details_panel.set_item(None, [])
            return
        self._refresh_stats()
        self._update_library_action_state()
        library_id = int(item.data(Qt.UserRole))
        self.controller.set_active_library(library_id)

    def choose_library(self):
        directory = QFileDialog.getExistingDirectory(self, "Select photo library")
        if directory:
            self._select_or_add_library(directory)

    def _on_libraries_changed(self, libraries):
        self._updating_library_list = True
        current_id = self.library_id
        self.library_list.clear()
        for row in libraries:
            label = str(row["root_path"])
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, int(row["id"]))
            self.library_list.addItem(item)
        if current_id is not None:
            for row_index in range(self.library_list.count()):
                if int(self.library_list.item(row_index).data(Qt.UserRole)) == current_id:
                    self.library_list.setCurrentRow(row_index)
                    break
            else:
                if self.library_list.count() == 0:
                    self.library_id = None
                    self.root_path = ""
                    self.library_label.setText("No library selected")
                    self.view.setModel(None)
                    self.details_panel.set_item(None, [])
                    self._refresh_stats()
        self._updating_library_list = False
        if not libraries:
            self.library_id = None
            self.root_path = ""
            self.library_label.setText("No library selected")
            self.view.setModel(None)
            self.details_panel.set_item(None, [])
            self._refresh_stats()
        self._update_library_action_state()

    def _on_active_library_changed(self, library_id: int, root_path: str):
        self.library_id = library_id
        self.root_path = root_path
        self.library_label.setText(root_path)
        if hasattr(self, "gallery_model"):
            self.gallery_model.shutdown()
        self.gallery_model = GalleryModel(self.db, library_id)
        self.view.setModel(self.gallery_model)
        self.view.setIconSize(self.gallery_model._placeholder.size())
        self.view.selectionModel().currentChanged.connect(self._on_current_changed)
        self.search_box.blockSignals(True)
        self.search_box.clear()
        self.search_box.blockSignals(False)
        self._set_gallery_library_view()

    def _on_scan_finished(self, summary):
        if self.library_id is not None:
            self._set_gallery_library_view()
            if self.search_box.text().strip():
                self._execute_search()
        self._set_status(
            f"Scan complete: {summary.root_path} | {summary.files_seen} seen, {summary.files_added + summary.files_updated} changed, {summary.files_deleted} deleted"
        )

    def _on_analysis_finished(self, outcomes):
        if self.library_id is not None:
            self._set_gallery_library_view()
            if self.search_box.text().strip():
                self._execute_search()
        self._refresh_exiftool_status()
        self._set_status(f"Analysis complete: {len(outcomes)} files processed")

    def _on_current_changed(self, current: QModelIndex, previous: QModelIndex):
        if not current.isValid():
            self.details_panel.set_item(None, [])
            return
        item = self.gallery_model.item(current.row())
        if item is None:
            self.details_panel.set_item(None, [])
            return
        tags = self.db.list_tags_for_file(item.file_id)
        self.details_panel.set_item(item, tags)

    def _save_excludes(self):
        if self.library_id is None:
            return
        paths = [line.strip() for line in self.excludes_box.toPlainText().splitlines() if line.strip()]
        self.controller.set_library_excludes(self.library_id, paths)
        self._set_status("Exclude paths saved")

    def _manual_scan_current_library(self):
        if self.library_id is None:
            return
        self.logger.info("Manual scan requested for library_id=%s root_path=%s", self.library_id, self.root_path)
        self.controller.scan_library(self.library_id)
        self._update_library_action_state()
        self._set_status("Manual scan started")

    def _delete_current_library(self):
        if self.library_id is None:
            return
        library_id = self.library_id
        response = QMessageBox.question(
            self,
            "Delete Library",
            f"Delete the selected library?\n\n{self.root_path}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if response != QMessageBox.Yes:
            return
        try:
            self.logger.info("Deleting library_id=%s root_path=%s", library_id, self.root_path)
            self.controller.remove_library(library_id)
            self.vector_index.delete_library_indexes(library_id)
        except Exception as exc:
            self.logger.exception("Failed to delete library_id=%s", library_id)
            QMessageBox.critical(self, "Delete Library Failed", str(exc))
            return
        self._set_status("Library deleted")
        self._update_library_action_state()

    def closeEvent(self, event):
        self.logger.info("Application closing")
        self.controller.stop()
        if hasattr(self, "gallery_model"):
            self.gallery_model.shutdown()
        super().closeEvent(event)


def main():
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
