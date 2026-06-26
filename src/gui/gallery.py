from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, Qt, QThread, Signal, Slot, QSize
from PySide6.QtGui import QImage, QPixmap, QColor, QPainter, QLinearGradient, QBrush, QFont

from src.core.database import DatabaseManager
from src.core.image_processing import save_thumbnail_png
from src.core.thumbnail_cache import ThumbnailCache


@dataclass(slots=True)
class GalleryItem:
    file_id: int
    file_path: str
    relative_path: str
    status: str
    size: int
    mtime_ns: int
    score: float | None = None
    last_analyzed_at: str | None = None
    xmp_state: str = "not_written"
    deleted_at: str | None = None
    thumbnail: QPixmap | None = None


class ThumbnailWorker(QObject):
    thumbnail_ready = Signal(int, QImage)

    def __init__(self, cache: ThumbnailCache, thumb_size: int = 320):
        super().__init__()
        self.cache = cache
        self.thumb_size = thumb_size

    @Slot(int, str, object, object)
    def load_thumbnail(self, file_id: int, file_path: str, mtime_ns: int, size: int):
        mtime_ns = int(mtime_ns)
        size = int(size)
        cache_path = self.cache.path_for(file_path, mtime_ns=mtime_ns, size=size, thumb_size=self.thumb_size)
        if cache_path.exists():
            cached = QImage(str(cache_path))
            if not cached.isNull():
                self.thumbnail_ready.emit(file_id, cached)
                return

        try:
            saved = save_thumbnail_png(file_path, cache_path, thumb_size=self.thumb_size)
        except Exception:
            return
        image = QImage(str(saved))
        if image.isNull():
            return
        self.thumbnail_ready.emit(file_id, image)


class GalleryModel(QAbstractListModel):
    FileIdRole = Qt.UserRole + 1
    FilePathRole = Qt.UserRole + 2
    RelativePathRole = Qt.UserRole + 3
    StatusRole = Qt.UserRole + 4
    SizeRole = Qt.UserRole + 5
    LastAnalyzedRole = Qt.UserRole + 6
    XmpStateRole = Qt.UserRole + 7

    request_thumbnail = Signal(int, str, object, object)

    def __init__(self, db: DatabaseManager, library_id: int | None = None):
        super().__init__()
        self.db = db
        self.library_id = library_id
        self.page_size = 200
        self._total_count = 0
        self._items: list[GalleryItem] = []
        self._thumb_requests: set[int] = set()
        self._thumb_cache: dict[int, QPixmap] = {}
        self._disk_cache = ThumbnailCache()
        self._placeholder = self._build_placeholder()

        self._thumb_thread = QThread(self)
        self._thumb_worker = ThumbnailWorker(self._disk_cache)
        self._thumb_worker.moveToThread(self._thumb_thread)
        self.request_thumbnail.connect(self._thumb_worker.load_thumbnail)
        self._thumb_worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._thumb_thread.start()

        if library_id is not None:
            self.refresh(library_id)

    def shutdown(self):
        self._thumb_thread.quit()
        self._thumb_thread.wait(2000)

    def _build_placeholder(self) -> QPixmap:
        pixmap = QPixmap(320, 320)
        pixmap.fill(QColor("#1f2937"))
        painter = QPainter(pixmap)
        gradient = QLinearGradient(0, 0, 320, 320)
        gradient.setColorAt(0.0, QColor("#334155"))
        gradient.setColorAt(1.0, QColor("#111827"))
        painter.fillRect(0, 0, 320, 320, QBrush(gradient))
        painter.setPen(QColor("#94a3b8"))
        painter.setFont(QFont("Segoe UI", 10))
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "Loading")
        painter.end()
        return pixmap

    def roleNames(self):
        return {
            self.FileIdRole: b"fileId",
            self.FilePathRole: b"filePath",
            self.RelativePathRole: b"relativePath",
            self.StatusRole: b"status",
            self.SizeRole: b"size",
            self.LastAnalyzedRole: b"lastAnalyzedAt",
            self.XmpStateRole: b"xmpState",
        }

    def refresh(self, library_id: int):
        self.beginResetModel()
        self.library_id = library_id
        self._total_count = self.db.count_library_files(library_id)
        self._items = []
        self._thumb_requests.clear()
        self._thumb_cache.clear()
        self.endResetModel()
        self.fetchMore(QModelIndex())

    def set_search_results(self, library_id: int, rows, score_map: dict[int, float] | None = None):
        self.beginResetModel()
        self.library_id = library_id
        self._total_count = len(rows)
        self._items = []
        self._thumb_requests.clear()
        self._thumb_cache.clear()
        score_map = score_map or {}
        for row in rows:
            file_id = int(row["id"])
            self._items.append(
                GalleryItem(
                    file_id=file_id,
                    file_path=str(row["file_path"]),
                    relative_path=str(row["relative_path"] or Path(str(row["file_path"])).name),
                    status=str(row["status"]),
                    size=int(row["size"]),
                    mtime_ns=int(row["mtime_ns"]),
                    score=score_map.get(file_id),
                    last_analyzed_at=row["last_analyzed_at"],
                    xmp_state=str(row["xmp_state"] or "not_written"),
                    deleted_at=row["deleted_at"],
                    thumbnail=None,
                )
            )
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return len(self._items)

    def canFetchMore(self, parent=QModelIndex()):
        if self.library_id is None:
            return False
        return len(self._items) < self._total_count

    def fetchMore(self, parent=QModelIndex()):
        if self.library_id is None:
            return
        offset = len(self._items)
        batch = self.db.list_gallery_files(self.library_id, limit=self.page_size, offset=offset)
        if not batch:
            return
        self.beginInsertRows(QModelIndex(), offset, offset + len(batch) - 1)
        for row in batch:
            item = GalleryItem(
                file_id=int(row["id"]),
                file_path=str(row["file_path"]),
                relative_path=str(row["relative_path"] or Path(str(row["file_path"])).name),
                status=str(row["status"]),
                size=int(row["size"]),
                mtime_ns=int(row["mtime_ns"]),
                last_analyzed_at=row["last_analyzed_at"],
                xmp_state=str(row["xmp_state"] or "not_written"),
                deleted_at=row["deleted_at"],
                thumbnail=self._thumb_cache.get(int(row["id"])),
            )
            self._items.append(item)
        self.endInsertRows()

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        if row >= len(self._items):
            while self.canFetchMore():
                self.fetchMore(QModelIndex())
                if row < len(self._items):
                    break
        if row >= len(self._items):
            return None

        item = self._items[row]

        if role in (Qt.DisplayRole, self.RelativePathRole):
            return item.relative_path if item.score is None else f"{item.relative_path}\n{item.score:.3f}"
        if role == Qt.DecorationRole:
            if item.thumbnail is None:
                if item.file_id not in self._thumb_requests:
                    self._thumb_requests.add(item.file_id)
                    self.request_thumbnail.emit(item.file_id, item.file_path, item.mtime_ns, item.size)
                return self._placeholder
            return item.thumbnail
        if role == self.FileIdRole:
            return item.file_id
        if role == self.FilePathRole:
            return item.file_path
        if role == self.StatusRole:
            return item.status
        if role == self.SizeRole:
            return item.size
        if role == self.LastAnalyzedRole:
            return item.last_analyzed_at
        if role == self.XmpStateRole:
            return item.xmp_state
        if role == Qt.ToolTipRole:
            base = f"{item.relative_path}\n{item.status}\n{item.file_path}"
            if item.score is not None:
                base += f"\nscore: {item.score:.3f}"
            return base
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def item(self, row: int) -> GalleryItem | None:
        if 0 <= row < len(self._items):
            return self._items[row]
        return None

    @Slot(int, QImage)
    def _on_thumbnail_ready(self, file_id: int, image: QImage):
        pixmap = QPixmap.fromImage(image)
        self._thumb_cache[file_id] = pixmap
        self._thumb_requests.discard(file_id)
        for row, item in enumerate(self._items):
            if item.file_id == file_id:
                item.thumbnail = pixmap
                index = self.index(row)
                self.dataChanged.emit(index, index, [Qt.DecorationRole])
                break
