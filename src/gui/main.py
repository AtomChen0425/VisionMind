from __future__ import annotations

from pathlib import Path
import logging
import sys
import os
from PySide6.QtCore import QEvent, QItemSelectionModel, QModelIndex, QMimeData, QProcess, QSettings, Qt, QSize, QUrl, QPoint, QRect
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
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
    QPushButton,
    QMenu,
    QSplitter,
    QVBoxLayout,
    QWidget,
    QSizePolicy
    
)

from src.core.analyzer import AnalysisService, OpenClipAnalyzer
from src.core.database import DatabaseManager
from src.core.logging_utils import setup_logging
from src.core.metadata_reader import read_image_metadata,extract_keywords
from src.core.semantic_search import SemanticSearchService
from src.core.pipeline import PhotoProcessingPipeline
from src.core.exiftool_metadata import ExifToolTagWriter
from src.core.scanner import Scanner
from src.core.vector_index import VectorIndexManager
from src.gui.automation import AutoLibraryController
from src.gui.i18n import normalize_language, tr
from src.gui.gallery import GalleryModel
from src.gui.settings_dialog import AppSettings, SettingsDialog


from src.gui.widgets import DetailsPanel, StatCard

current_dir = os.path.dirname(os.path.abspath(__file__))
qss_path = os.path.join(current_dir, "style.qss")


class LibraryRowWidget(QWidget):
    def __init__(self, display_name: str, root_path: str, delete_callback, parent=None):
        super().__init__(parent)
        self._delete_callback = delete_callback
        self._root_path = root_path
        self._full_display_name = display_name
        self.setObjectName("LibraryRowWidget")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 10, 0)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignVCenter)
        self.accent_bar = QFrame()
        self.accent_bar.setObjectName("LibraryRowAccent")
        self.accent_bar.setFixedWidth(4)
        self.accent_bar.hide()

        self.name_label = QLabel(self._full_display_name)
        self.name_label.setObjectName("LibraryRowName")
        self.name_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.delete_btn = QPushButton("🗑")
        self.delete_btn.setObjectName("LibraryDeleteButton")
        self.delete_btn.clicked.connect(self._on_delete_clicked)
        self.delete_btn.hide()

        layout.addWidget(self.accent_bar, 0, Qt.AlignVCenter)
        layout.addWidget(self.name_label, 1,Qt.AlignVCenter)
        layout.addWidget(self.delete_btn, 0,Qt.AlignVCenter)
        self.setMinimumHeight(40)
        self._update_elided_text()

    def _on_delete_clicked(self):
        if callable(self._delete_callback):
            self._delete_callback()

    def set_delete_tooltip(self, text: str):
        self.delete_btn.setToolTip(text)

    def set_selected(self, selected: bool):
        self.setProperty("selected", selected)
        self.accent_bar.setVisible(selected)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def enterEvent(self, event):
        self.delete_btn.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.delete_btn.hide()
        super().leaveEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_elided_text()

    def _update_elided_text(self):
        metrics = self.name_label.fontMetrics()
        available = max(80, self.name_label.width() or self.width() - 78)
        elided = metrics.elidedText(self._full_display_name, Qt.ElideRight, available)
        self.name_label.setText(elided)
        # self.name_label.setText(metrics.elidedText(self.name_label.toolTip(), Qt.ElideRight, available))


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

        self.settings = QSettings("PhotoManager", "PhotoManager")
        self.ui_language = normalize_language(self.settings.value("ui/language", "en", str))
        self.analyzer_model_name = self.settings.value("analyzer/model_name", "ViT-B-32", str)
        self.analyzer_pretrained = self.settings.value("analyzer/pretrained", "laion2b_s34b_b79k", str)
        self.analyzer_probability_threshold = float(self.settings.value("analyzer/probability_threshold", 0.2))

        self.analyzer = OpenClipAnalyzer(
            model_name=self.analyzer_model_name,
            pretrained=self.analyzer_pretrained,
            probability_threshold=self.analyzer_probability_threshold,
        )
        self.logger.info("open_clip cache root=%s", self.analyzer.model_cache_root)
        self.analysis_service = AnalysisService(self.analyzer)
        self.vector_index = VectorIndexManager(self.db)
        self.search_service = SemanticSearchService(self.db, self.analysis_service, self.vector_index)
        self.pipeline = PhotoProcessingPipeline(self.db, self.analysis_service, ExifToolTagWriter(), self.vector_index)
        self.controller = AutoLibraryController(self.db, self.scanner, self.pipeline)

        self.library_id: int | None = None
        self.root_path: str = ""
        self._updating_library_list = False
        self._search_mode_key = "mixed"
        self._details_panel_width = 380

        self._build_ui()
        self._bind_signals()
        self._apply_style()
        self._apply_language()
        self._refresh_exiftool_status()
        self.controller.refresh_libraries()

        last_library = self.settings.value("lastLibraryPath", "", str)
        if last_library and Path(last_library).exists():
            existing_library = self.db.get_library(last_library)
            if existing_library is not None:
                self._select_library(int(existing_library["id"]))
        elif self.library_list.count() > 0:
            self.library_list.setCurrentRow(0)
            self._select_library_by_row(0)
        else:
            self.status_label.setText(self._ui_text("choose_library_prompt"))

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.search_mode = QComboBox()
        self._populate_search_mode_combo()
        self.search_mode.currentTextChanged.connect(self._on_search_mode_changed)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText('Search photos, e.g. "sunset by the sea" or "group portrait"')
        self.search_box.returnPressed.connect(self._execute_search)
        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self._execute_search)
        self.search_btn.setObjectName("PrimaryButton")

        left_panel = QFrame()
        left_panel.setObjectName("Sidebar")
        left_panel.setFixedWidth(280)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(16, 14, 16, 16)
        left_layout.setSpacing(12)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        self.title_label = QLabel("AI Gallery")
        self.title_label.setObjectName("AppTitle")
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("IconButton")
        self.settings_btn.clicked.connect(self._open_settings_dialog)
        self.settings_btn.setEnabled(True)
        brand_row.addWidget(self.title_label, 3)
        brand_row.addWidget(self.settings_btn)
        left_layout.addLayout(brand_row)

        self.people_row = QFrame()
        self.people_row.setObjectName("NavRow")
        people_layout = QHBoxLayout(self.people_row)
        people_layout.setContentsMargins(10, 8, 10, 8)
        self.people_label = QLabel("People")
        self.people_count = QLabel("0")
        self.people_count.setObjectName("MutedCount")
        people_layout.addWidget(self.people_label, 1)
        people_layout.addWidget(self.people_count)
        left_layout.addWidget(self.people_row)

        divider = QFrame()
        divider.setObjectName("SidebarDivider")
        divider.setFixedHeight(1)
        left_layout.addWidget(divider)

        group_header = QHBoxLayout()
        self.group_title = QLabel("Libraries")
        self.group_title.setObjectName("SectionLabel")
        self.choose_btn = QPushButton("➕")
        self.choose_btn.clicked.connect(self.choose_library)
        self.choose_btn.setObjectName("SmallIconButton")
        group_header.addWidget(self.group_title, 1)
        group_header.addWidget(self.choose_btn)
        left_layout.addLayout(group_header)

        self.library_list = QListWidget()
        self.library_list.setObjectName("LibraryList")
        self.library_list.currentRowChanged.connect(self._select_library_by_row)
        left_layout.addWidget(self.library_list, 1)

        left_layout.addStretch(1)

        sidebar_footer = QFrame()
        sidebar_footer.setObjectName("SidebarFooter")
        footer_layout = QVBoxLayout(sidebar_footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(8)

        self.status_label = QLabel("Idle")
        self.status_label.setObjectName("StatusLabel")
        self.status_label.setWordWrap(True)
        self.exiftool_status_label = QLabel("ExifTool: checking...")
        self.exiftool_status_label.setObjectName("ExifToolStatusTag")
        self.exiftool_status_label.setWordWrap(True)
        self.exiftool_path_label = QLabel("-")
        self.exiftool_path_label.setObjectName("ExifToolPathTag")
        self.exiftool_path_label.setWordWrap(True)
        footer_layout.addWidget(self.status_label)
        footer_layout.addWidget(self.exiftool_status_label)
        footer_layout.addWidget(self.exiftool_path_label)
        left_layout.addWidget(sidebar_footer)

        self.library_label = QLabel("No library selected")
        self.library_label.setObjectName("LibraryPathLabel")
        self.library_label.setWordWrap(True)
        self.library_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.scan_now_btn = QPushButton("Scan Now")
        self.scan_now_btn.clicked.connect(self._manual_scan_current_library)
        self.scan_now_btn.setObjectName("HeaderActionButton")

        main_panel = QFrame()
        main_panel.setObjectName("MainPanel")
        main_layout = QVBoxLayout(main_panel)
        main_layout.setContentsMargins(26, 6, 22, 22)
        main_layout.setSpacing(16)

        top_bar = QFrame()
        top_bar.setObjectName("TopBar")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(10)
        top_layout.addWidget(self.search_box, 1)
        top_layout.addWidget(self.search_mode)
        top_layout.addWidget(self.search_btn)
        main_layout.addWidget(top_bar)

        content_header = QHBoxLayout()
        title_column = QVBoxLayout()
        title_column.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(0)
        self.album_title = QLabel("Default Album")
        self.album_title.setObjectName("AlbumTitle")
        self.album_subtitle = QLabel("0 photos")
        self.album_subtitle.setObjectName("AlbumSubtitle")
        title_row.addWidget(self.album_title, 0)
        title_row.addWidget(self.library_label, 1)
        title_column.addLayout(title_row)
        title_column.addWidget(self.album_subtitle)
        action_row.addWidget(self.scan_now_btn, 0)
        action_row.addStretch(1)
        title_column.addLayout(action_row)
        content_header.addLayout(title_column, 1)

        self.total_card = StatCard("Total")
        self.pending_card = StatCard("Pending")
        self.analyzed_card = StatCard("Analyzed")
        self.error_card = StatCard("Errors")
        for card in (self.total_card, self.pending_card, self.analyzed_card, self.error_card):
            content_header.addWidget(card)
        main_layout.addLayout(content_header)

        center_panel = QFrame()
        center_panel.setObjectName("CenterPanel")
        center_layout = QVBoxLayout(center_panel)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)

        self.view = QListView()
        self.view.setViewMode(QListView.IconMode)
        self.view.setResizeMode(QListView.Adjust)
        self.view.setMovement(QListView.Static)
        self.view.setSpacing(16)
        self.view.setWrapping(True)
        self.view.setIconSize(QSize(220, 220))
        self.view.setUniformItemSizes(True)
        self.view.setWordWrap(True)
        self.view.setSelectionMode(QListView.ExtendedSelection)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._show_gallery_context_menu)
        self.view.viewport().installEventFilter(self)
        center_layout.addWidget(self.view)
        main_layout.addWidget(center_panel, 1)

        right_panel = DetailsPanel()
        right_panel.setMinimumWidth(320)
        right_panel.setMaximumWidth(720)
        right_panel.hide()
        self.details_panel = right_panel

        self.content_splitter = QSplitter(Qt.Horizontal)
        self.content_splitter.setChildrenCollapsible(False)
        self.content_splitter.setHandleWidth(8)
        self.content_splitter.setOpaqueResize(True)
        self.content_splitter.addWidget(main_panel)
        self.content_splitter.addWidget(right_panel)
        self.content_splitter.setStretchFactor(0, 1)
        self.content_splitter.setStretchFactor(1, 0)
        self.content_splitter.setSizes([1, 0])

        root.addWidget(left_panel)
        root.addWidget(self.content_splitter, 1)

    def _bind_signals(self):
        self.controller.libraries_changed.connect(self._on_libraries_changed)
        self.controller.active_library_changed.connect(self._on_active_library_changed)
        self.controller.scan_started.connect(lambda library_id, root_path: self._set_status(self._ui_text("scanning").format(root_path=root_path)))
        self.controller.scan_started.connect(lambda *_: self._update_library_action_state())
        self.controller.scan_finished.connect(self._on_scan_finished)
        self.controller.analysis_started.connect(lambda library_id, root_path: self._set_status(self._ui_text("analyzing").format(root_path=root_path)))
        self.controller.analysis_started.connect(lambda *_: self._update_library_action_state())
        self.controller.analysis_finished.connect(self._on_analysis_finished)
        self.controller.message.connect(self._set_status)

    def _apply_style(self):
        QApplication.setStyle("Fusion")
        palette = QPalette()
        palette.setColor(QPalette.Window, QColor("#f4f7fb"))
        palette.setColor(QPalette.WindowText, QColor("#172033"))
        palette.setColor(QPalette.Base, QColor("#ffffff"))
        palette.setColor(QPalette.AlternateBase, QColor("#eef3fa"))
        palette.setColor(QPalette.Text, QColor("#172033"))
        palette.setColor(QPalette.Button, QColor("#edf3fb"))
        palette.setColor(QPalette.ButtonText, QColor("#172033"))
        palette.setColor(QPalette.Highlight, QColor("#2f73d9"))
        palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        QApplication.instance().setPalette(palette)
        with open(qss_path, "r", encoding="utf-8") as f:
            self.setStyleSheet(f.read())

    def _set_status(self, text: str):
        self.status_label.setText(text)

    def _ui_text(self, key: str) -> str:
        return tr(self.ui_language, key)

    def _apply_language(self):
        current_search_mode = self._search_mode_key
        self.setWindowTitle(self._ui_text("title"))
        self.title_label.setText(self._ui_text("title"))
        self.people_label.setText(self._ui_text("people"))
        self.group_title.setText(self._ui_text("group_title"))
        self.choose_btn.setText(self._ui_text("add"))
        self.scan_now_btn.setText(self._ui_text("scan_now"))
        self.search_btn.setText(self._ui_text("search"))
        self.search_box.setPlaceholderText(self._ui_text("search_placeholder"))
        self.total_card.title.setText(self._ui_text("total"))
        self.pending_card.title.setText(self._ui_text("pending"))
        self.analyzed_card.title.setText(self._ui_text("analyzed"))
        self.error_card.title.setText(self._ui_text("errors"))
        self._populate_search_mode_combo(current_search_mode)
        if self.library_id is None:
            self.library_label.setText(self._ui_text("no_library_selected"))
            self.album_title.setText(self._ui_text("default_album"))
            self.album_subtitle.setText("0 photos")
            self.status_label.setText(self._ui_text("idle"))
        self.details_panel.set_language(self.ui_language)

    def _populate_search_mode_combo(self, selected_key: str | None = None):
        selected_key = selected_key or self._search_mode_key
        labels = {
            "mixed": self._ui_text("search_mode_mixed"),
            "filename": self._ui_text("search_mode_filename"),
            "semantic": self._ui_text("search_mode_semantic"),
        }
        self.search_mode.blockSignals(True)
        self.search_mode.clear()
        self.search_mode.addItem(labels["mixed"], "mixed")
        self.search_mode.addItem(labels["filename"], "filename")
        self.search_mode.addItem(labels["semantic"], "semantic")
        index = self.search_mode.findData(selected_key)
        if index >= 0:
            self.search_mode.setCurrentIndex(index)
        self.search_mode.blockSignals(False)
        self._search_mode_key = str(self.search_mode.currentData() or "mixed")

    def _rebuild_runtime_stack(self):
        self.analyzer = OpenClipAnalyzer(
            model_name=self.analyzer_model_name,
            pretrained=self.analyzer_pretrained,
            probability_threshold=self.analyzer_probability_threshold,
        )
        self.logger.info("open_clip cache root=%s", self.analyzer.model_cache_root)
        self.analysis_service = AnalysisService(self.analyzer)
        self.search_service = SemanticSearchService(self.db, self.analysis_service, self.vector_index)
        self.pipeline = PhotoProcessingPipeline(self.db, self.analysis_service, ExifToolTagWriter(), self.vector_index)
        self.controller.pipeline = self.pipeline

    def _open_settings_dialog(self):
        if self.controller.scan_running or self.controller.analysis_running:
            QMessageBox.information(self, self._ui_text("settings_title"), self._ui_text("settings_busy"))
            return

        dialog = SettingsDialog(
            self,
            settings=AppSettings(
                probability_threshold=self.analyzer_probability_threshold,
                model_name=self.analyzer_model_name,
                pretrained=self.analyzer_pretrained,
                language=self.ui_language,
            ),
        )
        if dialog.exec() != QDialog.Accepted:
            return

        values = dialog.values()
        changed_analyzer = (
            values.probability_threshold != self.analyzer_probability_threshold
            or values.model_name != self.analyzer_model_name
            or values.pretrained != self.analyzer_pretrained
        )
        changed_language = values.language != self.ui_language

        self.analyzer_probability_threshold = values.probability_threshold
        self.analyzer_model_name = values.model_name
        self.analyzer_pretrained = values.pretrained
        self.ui_language = values.language

        self.settings.setValue("analyzer/probability_threshold", self.analyzer_probability_threshold)
        self.settings.setValue("analyzer/model_name", self.analyzer_model_name)
        self.settings.setValue("analyzer/pretrained", self.analyzer_pretrained)
        self.settings.setValue("ui/language", self.ui_language)

        if changed_analyzer:
            self._rebuild_runtime_stack()
            self._refresh_exiftool_status()
        if changed_language:
            self._apply_language()
        self._set_status(self._ui_text("settings_saved"))

    def _set_details_visible(self, visible: bool):
        if not hasattr(self, "content_splitter"):
            return
        if not visible:
            sizes = self.content_splitter.sizes()
            if len(sizes) >= 2 and sizes[1] > 0:
                self._details_panel_width = max(320, sizes[1])
            self.details_panel.setVisible(False)
            sizes = self.content_splitter.sizes()
            if len(sizes) >= 2:
                total = max(1, sum(sizes))
                self.content_splitter.setSizes([total, 0])
            return
        self.details_panel.setVisible(True)
        sizes = self.content_splitter.sizes()
        if len(sizes) < 2 or sizes[1] > 0:
            return
        total = max(1, sum(sizes) or self.content_splitter.width() or self.width() or 1)
        desired_right = min(560, max(320, self._details_panel_width))
        if total > 2:
            desired_right = min(desired_right, total - 1)
        if desired_right <= 0:
            return
        left_size = max(1, total - desired_right)
        self.content_splitter.setSizes([left_size, desired_right])

    def eventFilter(self, watched, event):
        if hasattr(self, "view") and watched is self.view.viewport() and event.type() == QEvent.MouseButtonPress:
            if self.view.indexAt(event.position().toPoint()).isValid():
                return super().eventFilter(watched, event)
            if event.button() in (Qt.LeftButton, Qt.RightButton):
                self.view.clearSelection()
                self.view.setCurrentIndex(QModelIndex())
                self.details_panel.set_item(None, [])
                self._set_details_visible(False)
        return super().eventFilter(watched, event)

    def _selected_gallery_index(self, view_index: QModelIndex | None = None):
        if view_index is not None and view_index.isValid():
            return view_index
        return self.view.currentIndex()

    def _selected_gallery_item(self, view_index: QModelIndex | None = None):
        index = self._selected_gallery_index(view_index)
        if not index.isValid() or not hasattr(self, "gallery_model"):
            return None
        return self.gallery_model.item(index.row())

    def _selected_gallery_items(self) -> list:
        if not hasattr(self, "gallery_model") or self.view.selectionModel() is None:
            return []
        rows = sorted({index.row() for index in self.view.selectionModel().selectedIndexes() if index.isValid()})
        return [item for row in rows if (item := self.gallery_model.item(row)) is not None]

    def _show_gallery_context_menu(self, position):
        if not hasattr(self, "gallery_model"):
            return
        view_index = self.view.indexAt(position)
        if not view_index.isValid():
            return

        selection = self.view.selectionModel()
        if selection is not None and not selection.isSelected(view_index):
            selection.select(view_index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
            self.view.setCurrentIndex(view_index)
        elif self.view.currentIndex() != view_index:
            self.view.setCurrentIndex(view_index)

        items = self._selected_gallery_items()
        if not items:
            return
        primary_item = self._selected_gallery_item(view_index) or items[0]
        multi = len(items) > 1

        menu = QMenu(self)
        open_action = menu.addAction(self._ui_text("open_selected") if multi else self._ui_text("open"))
        reveal_action = menu.addAction(self._ui_text("show_in_folder"))
        copy_file_action = menu.addAction(self._ui_text("copy_files").format(count=len(items)) if multi else self._ui_text("copy_file"))
        copy_path_action = menu.addAction(self._ui_text("copy_paths").format(count=len(items)) if multi else self._ui_text("copy_path"))
        chosen = menu.exec(self.view.viewport().mapToGlobal(position))
        if chosen == open_action:
            self._open_files([item.file_path for item in items])
        elif chosen == reveal_action:
            self._show_in_folder(primary_item.file_path)
        elif chosen == copy_file_action:
            self._copy_files_to_clipboard([item.file_path for item in items])
        elif chosen == copy_path_action:
            self._copy_paths_to_clipboard([item.file_path for item in items])

    def _open_file(self, file_path: str):
        QDesktopServices.openUrl(QUrl.fromLocalFile(file_path))

    def _open_files(self, file_paths: list[str]):
        for file_path in file_paths:
            self._open_file(file_path)

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
        self._copy_files_to_clipboard([file_path])

    def _copy_files_to_clipboard(self, file_paths: list[str]):
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(file_path) for file_path in file_paths])
        mime.setText("\n".join(file_paths))
        QApplication.clipboard().setMimeData(mime)

    def _copy_path_to_clipboard(self, file_path: str):
        self._copy_paths_to_clipboard([file_path])

    def _copy_paths_to_clipboard(self, file_paths: list[str]):
        QApplication.clipboard().setText("\n".join(file_paths))

    def _update_library_action_state(self):
        has_library = self.library_id is not None
        busy = self.controller.scan_running or self.controller.analysis_running
        self.scan_now_btn.setEnabled(has_library and not busy)
        self.settings_btn.setEnabled(not busy)

    def _refresh_library_row_selection(self):
        current_item = self.library_list.currentItem()
        current_row = self.library_list.row(current_item) if current_item is not None else -1
        for row in range(self.library_list.count()):
            item = self.library_list.item(row)
            widget = self.library_list.itemWidget(item)
            if widget is not None and hasattr(widget, "set_selected"):
                widget.set_selected(row == current_row)

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
        self._refresh_stats()
        self._update_library_action_state()

    def _on_search_mode_changed(self, mode: str):
        selected = self.search_mode.currentData()
        self._search_mode_key = str(selected or "mixed")

    def _execute_search(self):
        if self.library_id is None or not hasattr(self, "gallery_model"):
            return
        query = self.search_box.text().strip()
        if not query:
            self._set_gallery_library_view()
            return

        try:
            if self._search_mode_key == "filename":
                rows = self.db.search_files_by_name(self.library_id, query, limit=200)
                self.gallery_model.set_search_results(self.library_id, rows)
            else:
                mode = self._search_mode_key if self._search_mode_key in ("mixed", "semantic") else "mixed"
                file_ids, score_map, _source_map = self.search_service.search(self.library_id, query, mode=mode, limit=200)
                rows = self.db.list_files_by_ids(self.library_id, file_ids)
                self.gallery_model.set_search_results(self.library_id, rows, score_map=score_map)
            self.view.setModel(self.gallery_model)
        except Exception as exc:
            self._set_status(self._ui_text("search_failed").format(error=exc))

    def _refresh_stats(self):
        if self.library_id is None:
            self.total_card.set_value("0")
            self.pending_card.set_value("0")
            self.analyzed_card.set_value("0")
            self.error_card.set_value("0")
            self.people_count.setText("0")
            self.album_subtitle.setText("0 photos")
            return
        stats = self.db.get_library_stats(self.library_id)
        self.total_card.set_value(str(stats["total_files"] or 0))
        self.pending_card.set_value(str(stats["pending_files"] or 0))
        self.analyzed_card.set_value(str(stats["analyzed_files"] or 0))
        self.error_card.set_value(str(stats["error_files"] or 0))
        total_files = int(stats["total_files"] or 0)
        self.people_count.setText(str(total_files))
        self.album_subtitle.setText(f"{total_files} photos")

    def _selected_library_id(self):
        item = self.library_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def _select_or_add_library(self, root_path: str, *, from_startup: bool = False):
        library_id = self.controller.add_library(root_path)
        self.settings.setValue("lastLibraryPath", str(Path(root_path).resolve()))
        if not from_startup:
            self._set_status(f"{self._ui_text('library_added')}: {root_path}")
        self._select_library(library_id)

    def _select_library(self, library_id: int):
        for row in range(self.library_list.count()):
            item = self.library_list.item(row)
            if item.data(Qt.UserRole) == library_id:
                self.library_list.setCurrentRow(row)
                self._refresh_library_row_selection()
                return
        self.controller.set_active_library(library_id)

    def _select_library_by_row(self, row: int):
        if self._updating_library_list:
            return
        item = self.library_list.item(row)
        if item is None:
            self.library_id = None
            self.root_path = ""
            self.library_label.setText(self._ui_text("no_library_selected"))
            self.album_title.setText(self._ui_text("default_album"))
            self.album_subtitle.setText("0 photos")
            self.view.setModel(None)
            self.details_panel.set_item(None, [])
            self._set_details_visible(False)
            self._refresh_library_row_selection()
            return
        self._refresh_stats()
        self._update_library_action_state()
        library_id = int(item.data(Qt.UserRole))
        self.controller.set_active_library(library_id)
        self._refresh_library_row_selection()

    def choose_library(self):
        directory = QFileDialog.getExistingDirectory(self, self._ui_text("choose_library_prompt"))
        if directory:
            self._select_or_add_library(directory)

    def _on_libraries_changed(self, libraries):
        self._updating_library_list = True
        current_id = self.library_id
        self.library_list.clear()
        for row in libraries:
            label = str(row["root_path"])
            display_name = Path(label).name or label
            full_display_name = f"• {display_name}"
            item = QListWidgetItem()
            item.setToolTip(label)
            item.setData(Qt.UserRole, int(row["id"]))
            self.library_list.addItem(item)
            self._set_library_item_widget(item, full_display_name, label, int(row["id"]))
        if current_id is not None:
            for row_index in range(self.library_list.count()):
                if int(self.library_list.item(row_index).data(Qt.UserRole)) == current_id:
                    self.library_list.setCurrentRow(row_index)
                    break
            else:
                if self.library_list.count() == 0:
                    self.library_id = None
                    self.root_path = ""
                    self.library_label.setText(self._ui_text("no_library_selected"))
                    self.album_title.setText(self._ui_text("default_album"))
                    self.album_subtitle.setText("0 photos")
                    self.view.setModel(None)
                    self.details_panel.set_item(None, [])
                    self._set_details_visible(False)
                    self._refresh_stats()
        self._updating_library_list = False
        self._refresh_library_row_selection()
        if not libraries:
            self.library_id = None
            self.root_path = ""
            self.library_label.setText(self._ui_text("no_library_selected"))
            self.album_title.setText(self._ui_text("default_album"))
            self.album_subtitle.setText("0 photos")
            self.view.setModel(None)
            self.details_panel.set_item(None, [])
            self._set_details_visible(False)
            self._refresh_stats()
        self._update_library_action_state()

    def _set_library_item_widget(self, item: QListWidgetItem, display_name: str, root_path: str, library_id: int):
        row_widget = LibraryRowWidget(
            display_name,
            root_path,
            lambda lid=library_id, path=root_path: self._delete_library(lid, path),
        )
        row_widget.set_delete_tooltip(self._ui_text("delete"))
        row_widget.setMinimumHeight(40)
        item.setSizeHint(QSize(row_widget.sizeHint().width(), max(40, row_widget.sizeHint().height())))
        self.library_list.setItemWidget(item, row_widget)
        row_widget.set_selected(False)

    def _on_active_library_changed(self, library_id: int, root_path: str):
        self.library_id = library_id
        self.root_path = root_path
        self.library_label.setText(root_path)
        self.album_title.setText(Path(root_path).name or self._ui_text("default_album"))
        if hasattr(self, "gallery_model"):
            self.gallery_model.shutdown()
        self.gallery_model = GalleryModel(self.db, library_id)
        self.view.setModel(self.gallery_model)
        self.view.setIconSize(self.gallery_model._placeholder.size())
        self.view.selectionModel().currentChanged.connect(self._on_current_changed)
        self.details_panel.set_item(None, [])
        self._set_details_visible(False)
        self.search_box.blockSignals(True)
        self.search_box.clear()
        self.search_box.blockSignals(False)
        self._set_gallery_library_view()
        self._refresh_library_row_selection()

    def _on_scan_finished(self, summary):
        if self.library_id is not None:
            self._set_gallery_library_view()
            if self.search_box.text().strip():
                self._execute_search()
        self._set_status(
            self._ui_text("scan_complete").format(
                root_path=summary.root_path,
                seen=summary.files_seen,
                changed=summary.files_added + summary.files_updated,
                deleted=summary.files_deleted,
            )
        )

    def _on_analysis_finished(self, outcomes):
        if self.library_id is not None:
            self._set_gallery_library_view()
            if self.search_box.text().strip():
                self._execute_search()
        self._refresh_exiftool_status()
        self._set_status(self._ui_text("analysis_complete").format(count=len(outcomes)))

    def _on_current_changed(self, current: QModelIndex, previous: QModelIndex):
        if not current.isValid():
            self.details_panel.set_item(None, [])
            self._set_details_visible(False)
            return
        item = self.gallery_model.item(current.row())
        if item is None:
            self.details_panel.set_item(None, [])
            self._set_details_visible(False)
            return
        tags=  extract_keywords(read_image_metadata(item.file_path))
        self.details_panel.set_item(item, tags)
        self._set_details_visible(True)

    def _manual_scan_current_library(self):
        if self.library_id is None:
            return
        self.logger.info("Manual scan requested for library_id=%s root_path=%s", self.library_id, self.root_path)
        self.controller.scan_library(self.library_id)
        self._update_library_action_state()
        self._set_status(self._ui_text("manual_scan_started"))

    def _delete_library(self, library_id: int, root_path: str):
        response = QMessageBox.question(
            self,
            self._ui_text("delete_library_title"),
            self._ui_text("delete_library_message").format(root_path=root_path),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if response != QMessageBox.Yes:
            return
        try:
            self.logger.info("Deleting library_id=%s root_path=%s", library_id, root_path)
            self.controller.remove_library(library_id)
            self.vector_index.delete_library_indexes(library_id)
            last_library = self.settings.value("lastLibraryPath", "", str)
            if last_library and Path(last_library).resolve() == Path(root_path).resolve():
                self.settings.setValue("lastLibraryPath", "")
        except Exception as exc:
            self.logger.exception("Failed to delete library_id=%s", library_id)
            QMessageBox.critical(self, self._ui_text("delete_library_failed_title"), str(exc))
            return
        self._set_status(self._ui_text("library_deleted"))
        self._update_library_action_state()

    def _delete_current_library(self):
        if self.library_id is None:
            return
        self._delete_library(self.library_id, self.root_path)

    def closeEvent(self, event):
        self.logger.info("Application closing")
        self.controller.stop()
        if hasattr(self, "gallery_model"):
            self.gallery_model.shutdown()
        super().closeEvent(event)


def main():
    app = QApplication([])
    app.setWindowIcon(QIcon(r'docs\icon_256x256.ico'))
    window = MainWindow()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
