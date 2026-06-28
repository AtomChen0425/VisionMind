from __future__ import annotations

from pathlib import Path
import logging
import sys

from PySide6.QtCore import QEvent, QItemSelectionModel, QModelIndex, QMimeData, QProcess, QSettings, Qt, QSize, QUrl
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
    QMenu,
    QTextEdit,
    QVBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
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
        super().__init__("选择照片")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
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
    SECTION_LABELS = {
        "file": "文件",
        "camera": "相机",
        "capture": "拍摄",
        "exposure": "曝光",
        "lens": "镜头",
        "location": "位置",
        "text": "文本",
        "technical": "技术",
    }

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
        self.metadata_tree = QTreeWidget()
        self.metadata_tree.setColumnCount(2)
        self.metadata_tree.setHeaderLabels(["字段", "值"])
        self.metadata_tree.setRootIsDecorated(False)
        self.metadata_tree.setAlternatingRowColors(True)
        self.metadata_tree.setIndentation(18)
        self.metadata_tree.header().setStretchLastSection(True)
        self.metadata_tree.setMinimumHeight(200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        layout.addWidget(self.preview)
        layout.addWidget(self._label_block("文件", self.path))
        layout.addWidget(self._label_block("相对路径", self.relative_path))
        layout.addWidget(self._label_block("状态", self.status))
        layout.addWidget(self._label_block("元数据", self.metadata_state))
        layout.addWidget(self._label_block("标签", self.tags))
        layout.addWidget(self._label_block("图片信息", self.metadata_tree))

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
            self.preview.setText("选择照片")
            self.preview.set_source_pixmap(None)
            self.path.setText("-")
            self.relative_path.setText("-")
            self.status.setText("-")
            self.metadata_state.setText("-")
            self.tags.setPlainText("")
            self.metadata_tree.clear()
            self.metadata_tree.addTopLevelItem(QTreeWidgetItem(["选择照片后显示结构化元数据", ""]))
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
            text = "暂无标签"
        self.tags.setPlainText(text)
        try:
            metadata = read_image_metadata(item.file_path)
            self._set_metadata_tree(metadata)
        except Exception as exc:
            self.metadata_tree.clear()
            error_item = QTreeWidgetItem(["错误", f"读取元数据失败: {exc}"])
            self.metadata_tree.addTopLevelItem(error_item)

    def _set_metadata_tree(self, metadata: dict):
        self.metadata_tree.clear()
        if not metadata:
            self.metadata_tree.addTopLevelItem(QTreeWidgetItem(["暂无可显示的元数据", ""]))
            return

        for section_name, fields in metadata.items():
            label = self.SECTION_LABELS.get(section_name, section_name)
            section_item = QTreeWidgetItem([label, ""])
            section_font = section_item.font(0)
            section_font.setBold(True)
            section_item.setFont(0, section_font)
            section_item.setFirstColumnSpanned(True)
            section_item.setExpanded(True)

            if isinstance(fields, dict):
                for key, value in fields.items():
                    child = QTreeWidgetItem([str(key), self._format_metadata_value(value)])
                    section_item.addChild(child)
            else:
                section_item.addChild(QTreeWidgetItem(["值", self._format_metadata_value(fields)]))

            self.metadata_tree.addTopLevelItem(section_item)

        self.metadata_tree.expandAll()

    def _format_metadata_value(self, value):
        if value is None:
            return "-"
        if isinstance(value, list):
            return ", ".join(self._format_metadata_value(item) for item in value if item not in (None, ""))
        if isinstance(value, dict):
            parts = []
            for key, item in value.items():
                parts.append(f"{key}: {self._format_metadata_value(item)}")
            return "; ".join(parts)
        return str(value)


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
        self.logger.info("open_clip cache root=%s", self.analyzer.model_cache_root)
        self.analysis_service = AnalysisService(self.analyzer)
        self.vector_index = VectorIndexManager(self.db)
        self.search_service = SemanticSearchService(self.db, self.analysis_service, self.vector_index)
        self.pipeline = PhotoProcessingPipeline(self.db, self.analysis_service, ExifToolTagWriter(), self.vector_index)
        self.controller = AutoLibraryController(self.db, self.scanner, self.pipeline)

        self.settings = QSettings("PhotoManager", "PhotoManager")
        self.library_id: int | None = None
        self.root_path: str = ""
        self._updating_library_list = False
        self._search_mode = "混合"

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
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.search_mode = QComboBox()
        self.search_mode.addItems(["混合", "文件名", "语义"])
        self.search_mode.currentTextChanged.connect(self._on_search_mode_changed)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText('文字搜图，例如"海边日落"、"多人合影"')
        self.search_box.returnPressed.connect(self._execute_search)
        self.search_btn = QPushButton("搜索")
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
        self.brand_mark = QLabel("N")
        self.brand_mark.setObjectName("BrandMark")
        self.title_label = QLabel("AI 相册")
        self.title_label.setObjectName("AppTitle")
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setObjectName("IconButton")
        self.settings_btn.setEnabled(False)
        brand_row.addWidget(self.brand_mark)
        brand_row.addWidget(self.title_label, 1)
        brand_row.addWidget(self.settings_btn)
        left_layout.addLayout(brand_row)

        self.people_row = QFrame()
        self.people_row.setObjectName("NavRow")
        people_layout = QHBoxLayout(self.people_row)
        people_layout.setContentsMargins(10, 8, 10, 8)
        self.people_label = QLabel("人物")
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
        group_title = QLabel("分组")
        group_title.setObjectName("SectionLabel")
        self.choose_btn = QPushButton("➕")
        self.choose_btn.clicked.connect(self.choose_library)
        self.choose_btn.setObjectName("SmallIconButton")
        group_header.addWidget(group_title, 1)
        group_header.addWidget(self.choose_btn)
        left_layout.addLayout(group_header)

        self.library_list = QListWidget()
        self.library_list.currentRowChanged.connect(self._select_library_by_row)
        left_layout.addWidget(self.library_list, 1)

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

        self.library_label = QLabel("未选择相册")
        self.library_label.setObjectName("LibraryPathLabel")
        self.library_label.setWordWrap(True)

        self.excludes_box = QPlainTextEdit()
        self.excludes_box.setPlaceholderText("One exclude path per line")
        self.excludes_box.setMinimumHeight(82)
        self.excludes_box.setMaximumHeight(110)

        self.save_excludes_btn = QPushButton("保存排除")
        self.save_excludes_btn.clicked.connect(self._save_excludes)
        self.save_excludes_btn.setObjectName("SecondaryButton")

        self.scan_now_btn = QPushButton("扫描")
        self.scan_now_btn.clicked.connect(self._manual_scan_current_library)
        self.scan_now_btn.setObjectName("SecondaryButton")

        self.delete_library_btn = QPushButton("删除")
        self.delete_library_btn.clicked.connect(self._delete_current_library)
        self.delete_library_btn.setObjectName("DangerButton")

        library_tools = QFrame()
        library_tools.setObjectName("LibraryTools")
        tools_layout = QVBoxLayout(library_tools)
        tools_layout.setContentsMargins(10, 10, 10, 10)
        tools_layout.setSpacing(8)
        tools_layout.addWidget(self.library_label)
        tools_layout.addWidget(self.excludes_box)
        tools_layout.addWidget(self.save_excludes_btn)
        tool_buttons = QHBoxLayout()
        tool_buttons.setSpacing(8)
        tool_buttons.addWidget(self.scan_now_btn)
        tool_buttons.addWidget(self.delete_library_btn)
        tools_layout.addLayout(tool_buttons)
        left_layout.addWidget(library_tools)

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
        title_column.setSpacing(2)
        self.album_title = QLabel("默认相册")
        self.album_title.setObjectName("AlbumTitle")
        self.album_subtitle = QLabel("0 张照片")
        self.album_subtitle.setObjectName("AlbumSubtitle")
        title_column.addWidget(self.album_title)
        title_column.addWidget(self.album_subtitle)
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
        right_panel.setFixedWidth(380)
        right_panel.hide()
        self.details_panel = right_panel

        root.addWidget(left_panel)
        root.addWidget(main_panel, 1)
        root.addWidget(right_panel)

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

        self.setStyleSheet(
            """
            QWidget {
                color: #172033;
                font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
                font-size: 12px;
            }
            QMainWindow {
                background: #f4f7fb;
            }
            QFrame#Sidebar {
                background: #f8fafd;
                border-right: 1px solid #dce4ef;
            }
            QFrame#MainPanel {
                background: #f4f7fb;
            }
            QFrame#CenterPanel {
                background: transparent;
                border: none;
            }
            QFrame#DetailsPanel {
                background: #ffffff;
                border-left: 1px solid #dce4ef;
            }
            QFrame#TopBar {
                background: transparent;
            }
            QFrame#StatCard {
                background: transparent;
                border: none;
                border-radius: 0;
            }
            QFrame#DetailBlock {
                background: #f6f8fb;
                border: 1px solid #e1e8f2;
                border-radius: 8px;
                padding: 8px;
            }
            QFrame#NavRow {
                background: transparent;
                border-radius: 8px;
            }
            QFrame#NavRow:hover {
                background: #eef4ff;
            }
            QFrame#SidebarDivider {
                background: #dce4ef;
                border: none;
            }
            QFrame#SidebarFooter {
                background: transparent;
                border: none;
            }
            QFrame#LibraryTools {
                background: #ffffff;
                border: 1px solid #e1e8f2;
                border-radius: 8px;
            }
            QLabel#BrandMark {
                background: #162033;
                color: #ffffff;
                border: 2px solid #2f73d9;
                border-radius: 19px;
                min-width: 38px;
                max-width: 38px;
                min-height: 38px;
                max-height: 38px;
                qproperty-alignment: AlignCenter;
                font-size: 13px;
                font-weight: 800;
            }
            QLabel#AppTitle {
                font-size: 15px;
                font-weight: 800;
                color: #172033;
            }
            QLabel#AlbumTitle {
                color: #121a2b;
                font-size: 22px;
                font-weight: 800;
            }
            QLabel#AlbumSubtitle {
                color: #6f7d91;
                font-size: 12px;
            }
            QLabel#SectionLabel {
                color: #6f7d91;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel#MutedCount {
                color: #7d8da3;
                font-weight: 700;
            }
            QLabel#StatusLabel {
                color: #64748b;
                font-size: 11px;
            }
            QLabel#LibraryPathLabel {
                color: #475569;
                font-size: 11px;
            }
            QLabel#ExifToolStatusTag {
                color: #245f45;
                background: #e7f6ee;
                border: 1px solid #bce4cd;
                border-radius: 8px;
                padding: 7px 10px;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel#ExifToolPathTag {
                color: #7d8da3;
                font-size: 11px;
                font-family: Consolas, monospace;
            }
            QLabel#StatTitle, QLabel#DetailHeading {
                color: #7d8da3;
                font-size: 11px;
                text-transform: uppercase;
            }
            QLabel#StatValue {
                color: #172033;
                font-size: 18px;
                font-weight: 800;
            }
            QPushButton {
                background: #eef4ff;
                color: #24456f;
                border: 1px solid #cfe0f5;
                border-radius: 8px;
                padding: 9px 14px;
                font-weight: 700;
            }
            QPushButton:hover { background: #e4efff; }
            QPushButton:pressed { background: #d9e9ff; }
            QPushButton:disabled { background: #edf1f6; color: #94a3b8; border-color: #e1e8f2; }
            QPushButton#PrimaryButton {
                background: #2f73d9;
                color: #ffffff;
                border: none;
                border-radius: 8px;
                min-width: 78px;
            }
            QPushButton#PrimaryButton:hover { background: #2664c2; }
            QPushButton#SecondaryButton {
                background: #ffffff;
                color: #334155;
                border: 1px solid #d8e2ee;
            }
            QPushButton#SecondaryButton:hover { background: #f2f6fb; }
            QPushButton#DangerButton {
                background: #fff0f0;
                color: #c33131;
                border: 1px solid #ffd2d2;
            }
            QPushButton#DangerButton:hover {
                background: #ffe4e4;
            }
            QPushButton#IconButton, QPushButton#SmallIconButton {
                background: transparent;
                border: none;
                color: #59677c;
                padding: 4px;
                min-width: 28px;
                max-width: 50px;
                min-height: 28px;
                max-height: 50px;
                font-weight: 800;
            }
            QLineEdit, QTextEdit, QPlainTextEdit, QComboBox {
                background: #ffffff;
                color: #172033;
                border: 1px solid #d8e2ee;
                border-radius: 8px;
                padding: 10px 12px;
                selection-background-color: #2f73d9;
                selection-color: #ffffff;
            }
            QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {
                border: 1px solid #2f73d9;
                background: #ffffff;
            }
            QTreeWidget {
                background: #ffffff;
                color: #172033;
                border: 1px solid #d8e2ee;
                border-radius: 8px;
                alternate-background-color: #f5f9ff;
            }
            QTreeWidget::item {
                padding: 6px 8px;
            }
            QTreeWidget::item:selected {
                background: #dceaff;
                color: #1f4f93;
            }
            QHeaderView::section {
                background: #f1f5fa;
                color: #5f7189;
                padding: 8px 10px;
                border: none;
                border-bottom: 1px solid #d8e2ee;
                font-weight: 700;
            }
            QLineEdit {
                min-height: 24px;
                font-size: 13px;
            }
            QComboBox {
                min-width: 112px;
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
            QListWidget::item {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 8px;
                padding: 10px;
                margin: 2px 0;
                color: #475569;
            }
            QListWidget::item:hover {
                background: #eef4ff;
            }
            QListWidget::item:selected {
                background: #dceaff;
                border: 1px solid #c3d8f6;
                color: #1f4f93;
                font-weight: 700;
            }
            QListView::item {
                background: #ffffff;
                border: 1px solid #dfe7f1;
                border-radius: 8px;
                padding: 7px;
                margin: 3px;
                color: #172033;
            }
            QListView::item:hover {
                background: #f6faff;
                border: 1px solid #bcd3f4;
            }
            QListView::item:selected {
                background: #eef4ff;
                border: 2px solid #2f73d9;
            }
            QMenu {
                background: #ffffff;
                color: #172033;
                border: 1px solid #d8e2ee;
                border-radius: 8px;
                padding: 6px;
            }
            QMenu::item {
                padding: 8px 24px;
                border-radius: 6px;
            }
            QMenu::item:selected {
                background: #eef4ff;
                color: #1f4f93;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 10px;
            }
            QScrollBar::handle:vertical {
                background: #c9d6e6;
                border-radius: 5px;
                min-height: 32px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
            """
        )

    def _set_status(self, text: str):
        self.status_label.setText(text)

    def eventFilter(self, watched, event):
        if hasattr(self, "view") and watched is self.view.viewport() and event.type() == QEvent.MouseButtonPress:
            if self.view.indexAt(event.position().toPoint()).isValid():
                return super().eventFilter(watched, event)
            if event.button() in (Qt.LeftButton, Qt.RightButton):
                self.view.clearSelection()
                self.view.setCurrentIndex(QModelIndex())
                self.details_panel.set_item(None, [])
                self.details_panel.hide()
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
        open_action = menu.addAction("打开选中" if multi else "打开")
        reveal_action = menu.addAction("在文件夹中显示" if multi else "显示所在文件夹")
        copy_file_action = menu.addAction(f"复制 {len(items)} 个文件" if multi else "复制文件")
        copy_path_action = menu.addAction(f"复制 {len(items)} 个路径" if multi else "复制路径")
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
            if self._search_mode in ("Filename", "文件名"):
                rows = self.db.search_files_by_name(self.library_id, query, limit=200)
                self.gallery_model.set_search_results(self.library_id, rows)
            else:
                mode = "mixed" if self._search_mode in ("Mixed", "混合") else "semantic"
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
            self.people_count.setText("0")
            self.album_subtitle.setText("0 张照片")
            return
        stats = self.db.get_library_stats(self.library_id)
        self.total_card.set_value(str(stats["total_files"] or 0))
        self.pending_card.set_value(str(stats["pending_files"] or 0))
        self.analyzed_card.set_value(str(stats["analyzed_files"] or 0))
        self.error_card.set_value(str(stats["error_files"] or 0))
        total_files = int(stats["total_files"] or 0)
        self.people_count.setText(str(total_files))
        self.album_subtitle.setText(f"{total_files} 张照片")

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
            self.library_label.setText("未选择相册")
            self.album_title.setText("默认相册")
            self.album_subtitle.setText("0 张照片")
            self.view.setModel(None)
            self.details_panel.set_item(None, [])
            self.details_panel.hide()
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
            display_name = Path(label).name or label
            item = QListWidgetItem(f"▣  {display_name}")
            item.setToolTip(label)
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
                    self.library_label.setText("未选择相册")
                    self.album_title.setText("默认相册")
                    self.album_subtitle.setText("0 张照片")
                    self.view.setModel(None)
                    self.details_panel.set_item(None, [])
                    self.details_panel.hide()
                    self._refresh_stats()
        self._updating_library_list = False
        if not libraries:
            self.library_id = None
            self.root_path = ""
            self.library_label.setText("未选择相册")
            self.album_title.setText("默认相册")
            self.album_subtitle.setText("0 张照片")
            self.view.setModel(None)
            self.details_panel.set_item(None, [])
            self.details_panel.hide()
            self._refresh_stats()
        self._update_library_action_state()

    def _on_active_library_changed(self, library_id: int, root_path: str):
        self.library_id = library_id
        self.root_path = root_path
        self.library_label.setText(root_path)
        self.album_title.setText(Path(root_path).name or "默认相册")
        if hasattr(self, "gallery_model"):
            self.gallery_model.shutdown()
        self.gallery_model = GalleryModel(self.db, library_id)
        self.view.setModel(self.gallery_model)
        self.view.setIconSize(self.gallery_model._placeholder.size())
        self.view.selectionModel().currentChanged.connect(self._on_current_changed)
        self.details_panel.set_item(None, [])
        self.details_panel.hide()
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
            self.details_panel.hide()
            return
        item = self.gallery_model.item(current.row())
        if item is None:
            self.details_panel.set_item(None, [])
            self.details_panel.hide()
            return
        tags = self.db.list_tags_for_file(item.file_id)
        self.details_panel.set_item(item, tags)
        self.details_panel.show()

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
