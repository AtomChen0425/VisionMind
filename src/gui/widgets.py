from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QLayout,
    QLayoutItem,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.metadata_reader import read_image_metadata
from src.gui.i18n import normalize_language, tr


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


class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None, margin: int = 0, h_spacing: int = 6, v_spacing: int = 6):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item: QLayoutItem):
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        left, top, right, bottom = self.getContentsMargins()
        effective_rect = rect.adjusted(left, top, -right, -bottom)
        x = effective_rect.x()
        y = effective_rect.y()
        line_height = 0

        for item in self._items:
            widget = item.widget()
            if widget is not None:
                widget.adjustSize()
            next_x = x + item.sizeHint().width() + self._h_spacing
            if next_x - self._h_spacing > effective_rect.right() and line_height > 0:
                x = effective_rect.x()
                y += line_height + self._v_spacing
                next_x = x + item.sizeHint().width() + self._h_spacing
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = next_x
            line_height = max(line_height, item.sizeHint().height())

        return y + line_height - rect.y() + bottom


class TagWrapWidget(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("TagWrapWidget")
        self.flow_layout = FlowLayout(self, margin=0, h_spacing=6, v_spacing=6)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def add_widget(self, widget: QWidget):
        self.flow_layout.addWidget(widget)
        self.updateGeometry()
        self.adjustSize()

    def clear(self):
        while self.flow_layout.count():
            item = self.flow_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.updateGeometry()
        self.adjustSize()

    def heightForWidth(self, width: int) -> int:
        return self.flow_layout.heightForWidth(width)

    def sizeHint(self):
        width = self.width() if self.width() > 0 else 320
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self):
        return self.flow_layout.minimumSize()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.updateGeometry()


class AspectPreviewLabel(QLabel):
    def __init__(self):
        super().__init__("Select photo")
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
    SECTION_KEYS = {
        "file": "section_file",
        "camera": "section_camera",
        "capture": "section_capture",
        "exposure": "section_exposure",
        "lens": "section_lens",
        "location": "section_location",
        "text": "section_text",
        "technical": "section_technical",
    }

    def __init__(self):
        super().__init__()
        self._language = "en"
        self._current_item = None
        self._current_tags = []
        self.setObjectName("DetailsPanel")
        self.preview = AspectPreviewLabel()

        self.path = QLabel("-")
        self.relative_path = QLabel("-")
        self.status = QLabel("-")
        self.metadata_state = QLabel("-")
        self.tags_wrap = TagWrapWidget()
        self.metadata_tree = QTreeWidget()
        self.metadata_tree.setColumnCount(2)
        self.metadata_tree.setRootIsDecorated(False)
        self.metadata_tree.setAlternatingRowColors(True)
        self.metadata_tree.setIndentation(18)
        self.metadata_tree.header().setStretchLastSection(True)
        self.metadata_tree.setMinimumHeight(200)
        self._heading_labels: dict[str, QLabel] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        layout.addWidget(self.preview)
        layout.addWidget(self._label_block("file", self.path))
        layout.addWidget(self._label_block("relative_path", self.relative_path))
        layout.addWidget(self._label_block("status", self.status))
        layout.addWidget(self._label_block("metadata", self.metadata_state))
        layout.addWidget(self._label_block("keywords", self.tags_wrap))
        layout.addWidget(self._label_block("image_info", self.metadata_tree))
        self._apply_language()
        self.set_item(None, [])

    def _apply_language(self):
        labels = self.metadata_tree.headerItem()
        labels.setText(0, tr(self._language, "field"))
        labels.setText(1, tr(self._language, "value"))
        for key, heading in self._heading_labels.items():
            heading.setText(tr(self._language, key))

    def set_language(self, language: str):
        self._language = normalize_language(language)
        self._apply_language()
        if hasattr(self, "_current_item"):
            self.set_item(self._current_item, self._current_tags)

    def _label_block(self, title_key: str, widget: QWidget):
        container = QFrame()
        container.setObjectName("DetailBlock")
        block_layout = QVBoxLayout(container)
        block_layout.setContentsMargins(0, 0, 0, 0)
        block_layout.setSpacing(4)
        heading = QLabel(tr(self._language, title_key))
        heading.setObjectName("DetailHeading")
        block_layout.addWidget(heading)
        block_layout.addWidget(widget)
        self._heading_labels[title_key] = heading
        return container

    def set_item(self, item, tags):
        self._current_item = item
        self._current_tags = list(tags) if tags is not None else []

        if item is None:
            self.preview.setText(tr(self._language, "preview_empty"))
            self.preview.set_source_pixmap(None)
            self.path.setText("-")
            self.relative_path.setText("-")
            self.status.setText("-")
            self.metadata_state.setText("-")
            self._set_keyword_chips([])
            self.metadata_tree.clear()
            self.metadata_tree.addTopLevelItem(QTreeWidgetItem([tr(self._language, "detail_empty"), ""]))
            return

        pixmap = item.thumbnail or QPixmap(320, 320)
        if item.thumbnail is None:
            pixmap.fill(QColor("#f3f0e8"))
        self.preview.setText("")
        self.preview.set_source_pixmap(pixmap)
        self.path.setText(item.file_path)
        self.relative_path.setText(item.relative_path)
        if item.status == "error" and item.last_error:
            self.status.setText(tr(self._language, "status_error", error=item.last_error))
        else:
            self.status.setText(item.status)
        self.metadata_state.setText(item.xmp_state)
        self._set_keyword_chips([str(row) for row in tags])
        try:
            metadata = read_image_metadata(item.file_path)
            self._set_metadata_tree(metadata)
        except Exception as exc:
            self.metadata_tree.clear()
            error_item = QTreeWidgetItem([tr(self._language, "error"), tr(self._language, "metadata_error", error=exc)])
            self.metadata_tree.addTopLevelItem(error_item)

    def _set_metadata_tree(self, metadata: dict):
        self.metadata_tree.clear()
        if not metadata:
            self.metadata_tree.addTopLevelItem(QTreeWidgetItem([tr(self._language, "no_metadata"), ""]))
            return

        for section_name, fields in metadata.items():
            label_key = self.SECTION_KEYS.get(section_name, section_name)
            label = tr(self._language, label_key) if label_key != section_name else section_name
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
                section_item.addChild(QTreeWidgetItem([tr(self._language, "value_row"), self._format_metadata_value(fields)]))

            self.metadata_tree.addTopLevelItem(section_item)

        self.metadata_tree.expandAll()

    def _set_keyword_chips(self, keywords: list[str]):
        self.tags_wrap.clear()

        if not keywords:
            empty = QLabel(tr(self._language, "no_keywords"))
            empty.setObjectName("KeywordEmpty")
            empty.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.tags_wrap.add_widget(empty)
            return

        for keyword in keywords:
            chip = QLabel(keyword)
            chip.setObjectName("KeywordChip")
            chip.setTextInteractionFlags(Qt.TextSelectableByMouse)
            chip.setWordWrap(False)
            chip.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
            self.tags_wrap.add_widget(chip)

        self.tags_wrap.updateGeometry()

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
