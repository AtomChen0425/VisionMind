from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .analyzer import AnalysisService
from .database import DatabaseManager
from .vector_index import VectorIndexManager


@dataclass(slots=True)
class SearchResult:
    file_id: int
    score: float
    source: str


class SemanticSearchService:
    def __init__(self, db: DatabaseManager, analysis_service: AnalysisService, vector_index: VectorIndexManager):
        self.db = db
        self.analysis_service = analysis_service
        self.vector_index = vector_index

    @staticmethod
    def _dedupe_ids(results: Sequence[SearchResult]) -> list[SearchResult]:
        seen: set[int] = set()
        deduped: list[SearchResult] = []
        for result in results:
            if result.file_id in seen:
                continue
            seen.add(result.file_id)
            deduped.append(result)
        return deduped

    def search_file_names(self, library_id: int, query: str, limit: int = 50) -> list[SearchResult]:
        cleaned = query.strip().lower()
        if not cleaned:
            return []
        rows = self.db.search_files_by_name(library_id, cleaned, limit=limit)
        return [SearchResult(file_id=int(row["id"]), score=1.0, source="filename") for row in rows]

    def search_semantic(self, library_id: int, query: str, limit: int = 50) -> list[SearchResult]:
        cleaned = "a photo of "+ query.strip()
        if not cleaned or not self.analysis_service.analyzer.available():
            return []
        model_name = self.analysis_service.analyzer.model_id()
        query_vector = self.analysis_service.analyzer.text_to_embedding(cleaned)
        rows = self.vector_index.search(library_id, model_name, query_vector, limit=limit)
        return [SearchResult(file_id=file_id, score=score, source="semantic") for file_id, score in rows]

    def search(self, library_id: int, query: str, mode: str = "mixed", limit: int = 50) -> tuple[list[int], dict[int, float], dict[int, str]]:
        mode = mode.lower()
        
        
        if mode == "filename":
            filename_results = self.search_file_names(library_id, query, limit=limit)
            ordered = filename_results
        elif mode == "semantic":
            semantic_results = self.search_semantic(library_id, query, limit=limit)
            ordered = semantic_results
        else:
            filename_results = self.search_file_names(library_id, query, limit=limit)
            semantic_results = self.search_semantic(library_id, query, limit=limit)
            ordered = semantic_results + filename_results

        deduped = self._dedupe_ids(ordered)[:limit]
        score_map = {result.file_id: result.score for result in deduped}
        source_map = {result.file_id: result.source for result in deduped}
        return [result.file_id for result in deduped], score_map, source_map
