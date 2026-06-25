from __future__ import annotations

from pathlib import Path
import json
import sys

from PySide6.QtCore import QModelIndex, QMimeData, QProcess, QRegularExpression, QSettings, Qt, QSortFilterProxyModel, QSize, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
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
from src.core.metadata_reader import read_image_metadata
from src.core.pipeline import PhotoProcessingPipeline
from src.core.exiftool_metadata import ExifToolTagWriter
from src.core.scanner import Scanner
from src.gui.automation import AutoLibraryController
from src.gui.gallery import GalleryModel


class LibraryFilterProxy(QSortFilterProxyModel):
    def filterAcceptsRow(self, source_row, source_parent):
        if not self.filterRegularExpression().pattern():
            return True
        index = self.sourceModel().index(source_row, 0, source_parent)
        text = self.sourceModel().data(index, Qt.DisplayRole) or ""
        return self.filterRegularExpression().match(str(text)).hasMatch()


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
            pixmap.fill(QColor("#111827"))
        self.preview.setText("")
        self.preview.set_source_pixmap(pixmap)
        self.path.setText(item.file_path)
        self.relative_path.setText(item.relative_path)
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
        self.setWindowTitle("PhotoManager")
        self.resize(1600, 960)

        self.db = DatabaseManager("data/photo_manager.db")
        self.scanner = Scanner(self.db)
        self.analyzer = OpenClipAnalyzer()
        self.analysis_service = AnalysisService(self.analyzer)
        self.pipeline = PhotoProcessingPipeline(self.db, self.analysis_service, ExifToolTagWriter())
        self.controller = AutoLibraryController(self.db, self.scanner, self.pipeline)

        self.settings = QSettings("PhotoManager", "PhotoManager")
        self.library_id: int | None = None
        self.root_path: str = ""
        self._updating_library_list = False

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
        header_layout.addLayout(title_block, 2)

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

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Filter by filename")
        self.search_box.textChanged.connect(self._update_filter)

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
        left_layout.addWidget(QLabel("Search"))
        left_layout.addWidget(self.search_box)
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
        palette.setColor(QPalette.Window, QColor("#0f172a"))
        palette.setColor(QPalette.WindowText, QColor("#e2e8f0"))
        palette.setColor(QPalette.Base, QColor("#111827"))
        palette.setColor(QPalette.AlternateBase, QColor("#1e293b"))
        palette.setColor(QPalette.Text, QColor("#e2e8f0"))
        palette.setColor(QPalette.Button, QColor("#1e293b"))
        palette.setColor(QPalette.ButtonText, QColor("#e2e8f0"))
        palette.setColor(QPalette.Highlight, QColor("#38bdf8"))
        palette.setColor(QPalette.HighlightedText, QColor("#020617"))
        QApplication.instance().setPalette(palette)

        self.setStyleSheet(
            """
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #0b1120, stop:1 #111827);
            }
            QFrame#HeaderCard, QFrame#SidePanel, QFrame#CenterPanel, QFrame#DetailsPanel, QFrame#StatCard, QFrame#DetailBlock {
                background: rgba(15, 23, 42, 210);
                border: 1px solid rgba(148, 163, 184, 40);
                border-radius: 18px;
            }
            QLabel#AppTitle {
                font-size: 26px;
                font-weight: 700;
                color: #f8fafc;
            }
            QLabel#AppSubtitle {
                color: #94a3b8;
                font-size: 12px;
            }
            QLabel#MonitoringTag {
                color: #7dd3fc;
                background: rgba(14, 165, 233, 26);
                border: 1px solid rgba(125, 211, 252, 90);
                border-radius: 999px;
                padding: 8px 14px;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel#ExifToolStatusTag {
                color: #a7f3d0;
                background: rgba(16, 185, 129, 22);
                border: 1px solid rgba(110, 231, 183, 80);
                border-radius: 999px;
                padding: 8px 14px;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel#ExifToolPathTag {
                color: #cbd5e1;
                font-size: 11px;
                font-family: Consolas, monospace;
                opacity: 0.95;
            }
            QLabel#StatTitle, QLabel#DetailHeading {
                color: #94a3b8;
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 1px;
            }
            QLabel#StatValue {
                color: #f8fafc;
                font-size: 28px;
                font-weight: 700;
            }
            QPushButton {
                background: #38bdf8;
                color: #081120;
                border: none;
                border-radius: 12px;
                padding: 10px 16px;
                font-weight: 600;
            }
            QPushButton:hover { background: #7dd3fc; }
            QPushButton:disabled { background: #334155; color: #64748b; }
            QPushButton#SecondaryButton {
                background: #1e293b;
                color: #e2e8f0;
                border: 1px solid rgba(148, 163, 184, 60);
            }
            QPushButton#SecondaryButton:hover { background: #334155; }
            QLineEdit, QTextEdit, QPlainTextEdit {
                background: #0b1220;
                color: #e2e8f0;
                border: 1px solid rgba(148, 163, 184, 60);
                border-radius: 12px;
                padding: 10px 12px;
            }
            QListView, QListWidget {
                background: transparent;
                border: none;
                outline: 0;
            }
            QListView::item, QListWidget::item {
                background: rgba(15, 23, 42, 180);
                border: 1px solid rgba(148, 163, 184, 30);
                border-radius: 16px;
                padding: 8px;
                margin: 4px;
                color: #e2e8f0;
            }
            QListView::item:selected, QListWidget::item:selected {
                border: 1px solid #38bdf8;
                background: rgba(56, 189, 248, 60);
            }
            """
        )

    def _set_status(self, text: str):
        self.status_label.setText(text)

    def _selected_gallery_index(self, view_index: QModelIndex | None = None):
        if view_index is not None and view_index.isValid():
            return view_index
        if not hasattr(self, "proxy_model") or self.proxy_model is None:
            return QModelIndex()
        return self.view.currentIndex()

    def _selected_gallery_item(self, view_index: QModelIndex | None = None):
        index = self._selected_gallery_index(view_index)
        if not index.isValid():
            return None
        source_index = self.proxy_model.mapToSource(index)
        return self.gallery_model.item(source_index.row())

    def _show_gallery_context_menu(self, position):
        if not hasattr(self, "proxy_model") or not hasattr(self, "gallery_model"):
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
            self._refresh_stats()
            self._update_library_action_state()
            return
        library_id = int(item.data(Qt.UserRole))
        self.controller.set_active_library(library_id)

    def choose_library(self):
        directory = QFileDialog.getExistingDirectory(self, "Select photo library")
        if directory:
            self._select_or_add_library(directory)

    def _update_filter(self, text: str):
        if hasattr(self, "proxy_model"):
            self.proxy_model.setFilterRegularExpression(QRegularExpression(QRegularExpression.escape(text)))

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
        self.proxy_model = LibraryFilterProxy(self)
        self.proxy_model.setSourceModel(self.gallery_model)
        self.proxy_model.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.view.setModel(self.proxy_model)
        self.view.setIconSize(self.gallery_model._placeholder.size())
        self.view.selectionModel().currentChanged.connect(self._on_current_changed)
        self._sync_library_excludes_box()
        self._refresh_stats()
        self._update_library_action_state()

    def _on_scan_finished(self, summary):
        if hasattr(self, "gallery_model") and self.library_id is not None:
            self.gallery_model.refresh(self.library_id)
        self._refresh_stats()
        self._update_library_action_state()
        self._set_status(
            f"Scan complete: {summary.root_path} | {summary.files_seen} seen, {summary.files_added + summary.files_updated} changed, {summary.files_deleted} deleted"
        )

    def _on_analysis_finished(self, outcomes):
        if hasattr(self, "gallery_model") and self.library_id is not None:
            self.gallery_model.refresh(self.library_id)
        self._refresh_stats()
        self._refresh_exiftool_status()
        self._update_library_action_state()
        self._set_status(f"Analysis complete: {len(outcomes)} files processed")

    def _on_current_changed(self, current: QModelIndex, previous: QModelIndex):
        if not current.isValid():
            self.details_panel.set_item(None, [])
            return
        source_index = self.proxy_model.mapToSource(current)
        item = self.gallery_model.item(source_index.row())
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
        self.controller.scan_library(self.library_id)
        self._update_library_action_state()
        self._set_status("Manual scan started")

    def _delete_current_library(self):
        if self.library_id is None:
            return
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
            self.controller.remove_library(self.library_id)
        except Exception as exc:
            QMessageBox.critical(self, "Delete Library Failed", str(exc))
            return
        self._set_status("Library deleted")
        self._update_library_action_state()

    def closeEvent(self, event):
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
