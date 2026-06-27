from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .analyzer import AnalysisService, AnalysisResult
from .database import DatabaseManager
from .exiftool_metadata import ExifToolTagWriter
from .image_processing import save_thumbnail_png
from .thumbnail_cache import ThumbnailCache
from .vector_index import VectorIndexManager


@dataclass(slots=True)
class ProcessingOutcome:
    file_id: int
    image_path: str
    tags_written: int
    embedding_written: bool
    metadata_written: bool


class PhotoProcessingPipeline:
    def __init__(
        self,
        db: DatabaseManager,
        analysis_service: AnalysisService,
        metadata_writer: ExifToolTagWriter | None = None,
        vector_index: VectorIndexManager | None = None,
        thumbnail_cache: ThumbnailCache | None = None,
        analysis_thumbnail_size: int = 320,
    ):
        self.db = db
        self.analysis_service = analysis_service
        self.metadata_writer = metadata_writer or ExifToolTagWriter()
        self.vector_index = vector_index
        self.thumbnail_cache = thumbnail_cache or ThumbnailCache()
        self.analysis_thumbnail_size = max(64, int(analysis_thumbnail_size))

    def process_file(
        self,
        file_id: int,
        image_path: str,
        *,
        mtime_ns: int | None = None,
        size: int | None = None,
        label_candidates: Sequence[str] | None = None,
    ) -> ProcessingOutcome:
        analysis_path = str(self._ensure_analysis_thumbnail(image_path, mtime_ns=mtime_ns, size=size))
        result = self.analysis_service.analyze_image(analysis_path, labels=label_candidates)
        return self._write_result(file_id, image_path, result)

    def process_files(
        self,
        file_items: Sequence[tuple[int, str] | tuple[int, str, int, int]],
        *,
        label_candidates: Sequence[str] | None = None,
    ) -> list[ProcessingOutcome]:
        normalized_items = [self._normalize_file_item(item) for item in file_items]
        analysis_paths = [
            str(self._ensure_analysis_thumbnail(image_path, mtime_ns=mtime_ns, size=size))
            for _, image_path, mtime_ns, size in normalized_items
        ]
        results = self.analysis_service.analyze_images(analysis_paths, labels=label_candidates)
        outcomes = []
        for (file_id, image_path, _mtime_ns, _size), result in zip(normalized_items, results):
            outcomes.append(self._write_result(file_id, image_path, result))
        return outcomes

    def _normalize_file_item(self, item: tuple[int, str] | tuple[int, str, int, int]) -> tuple[int, str, int | None, int | None]:
        if len(item) >= 4:
            file_id, image_path, mtime_ns, size = item[:4]
            return int(file_id), str(image_path), int(mtime_ns), int(size)
        file_id, image_path = item[:2]
        return int(file_id), str(image_path), None, None

    def _ensure_analysis_thumbnail(self, image_path: str, *, mtime_ns: int | None, size: int | None) -> Path:
        source_path = Path(image_path)
        if mtime_ns is None or size is None:
            stat_result = source_path.stat()
            mtime_ns = int(stat_result.st_mtime_ns)
            size = int(stat_result.st_size)
        cache_path = self.thumbnail_cache.path_for(
            str(source_path),
            mtime_ns=int(mtime_ns),
            size=int(size),
            thumb_size=self.analysis_thumbnail_size,
        )
        if not cache_path.exists():
            save_thumbnail_png(source_path, cache_path, thumb_size=self.analysis_thumbnail_size)
        return cache_path

    def _write_result(self, file_id: int, image_path: str, result: AnalysisResult) -> ProcessingOutcome:
        tags = [(prediction.tag_name, prediction.confidence) for prediction in result.tags]
        tags_written = int(self.db.replace_tags(file_id, tags, source="open_clip", model_name=result.model_name))

        metadata_written = False
        if result.tags:
            output_path = self.metadata_writer.write(image_path, [prediction.tag_name for prediction in result.tags if prediction.confidence > 0.2])
            metadata_written = output_path.exists()
            self.db.set_metadata_state(file_id, "written")

        embedding_written = False
        if result.embedding is not None:
            embedding_bytes = self.analysis_service.analyzer.embedding_to_bytes(result.embedding)
            embedding_written = self.db.upsert_embedding(
                file_id,
                embedding_bytes,
                dimensions=int(result.embedding.shape[0]),
                model_name=result.model_name,
            )
            if self.vector_index is not None:
                file_row = self.db.get_file_by_id(file_id)
                if file_row is not None:
                    self.vector_index.upsert_embedding(int(file_row["library_id"]), result.model_name, file_id, result.embedding)

        self.db.set_file_analyzed(file_id)
        return ProcessingOutcome(
            file_id=file_id,
            image_path=image_path,
            tags_written=tags_written,
            embedding_written=embedding_written,
            metadata_written=metadata_written,
        )
