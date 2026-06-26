from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .analyzer import AnalysisService, AnalysisResult
from .database import DatabaseManager
from .exiftool_metadata import ExifToolTagWriter
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
    ):
        self.db = db
        self.analysis_service = analysis_service
        self.metadata_writer = metadata_writer or ExifToolTagWriter()
        self.vector_index = vector_index

    def process_file(self, file_id: int, image_path: str, *, label_candidates: Sequence[str] | None = None) -> ProcessingOutcome:
        result = self.analysis_service.analyze_image(image_path, labels=label_candidates)
        return self._write_result(file_id, image_path, result)

    def process_files(
        self,
        file_items: Sequence[tuple[int, str]],
        *,
        label_candidates: Sequence[str] | None = None,
    ) -> list[ProcessingOutcome]:
        image_paths = [image_path for _, image_path in file_items]
        results = self.analysis_service.analyze_images(image_paths, labels=label_candidates)
        outcomes = []
        for (file_id, image_path), result in zip(file_items, results):
            outcomes.append(self._write_result(file_id, image_path, result))
        return outcomes

    def _write_result(self, file_id: int, image_path: str, result: AnalysisResult) -> ProcessingOutcome:
        tags = [(prediction.tag_name, prediction.confidence) for prediction in result.tags]
        tags_written = int(self.db.replace_tags(file_id, tags, source="open_clip", model_name=result.model_name))

        metadata_written = False
        if result.tags:
            output_path = self.metadata_writer.write(image_path, [prediction.tag_name for prediction in result.tags if prediction.confidence > 0.5])
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
