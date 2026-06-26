from __future__ import annotations

from pathlib import Path
from typing import Sequence

import numpy as np

try:
    import faiss
except Exception:  # pragma: no cover - dependency availability is environment-specific
    faiss = None

from .database import DatabaseManager


class VectorIndexManager:
    def __init__(self, db: DatabaseManager, base_dir: str | Path = "data/indexes"):
        self.db = db
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._memory_indexes: dict[str, object] = {}
    @staticmethod
    def _sanitize_model_name(model_name: str) -> str:
        return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in model_name)

    def index_path(self, library_id: int, model_name: str) -> Path:
        return self.base_dir / str(library_id) / f"{self._sanitize_model_name(model_name)}.faiss"

    @staticmethod
    def _normalize(vector: Sequence[float]) -> np.ndarray:
        array = np.asarray(vector, dtype=np.float32).reshape(-1)
        norm = np.linalg.norm(array)
        if norm == 0:
            return array
        return array / norm

    def _ensure_faiss(self):
        return faiss is not None

    def _new_index(self, dimension: int):
        if not self._ensure_faiss():
            return None
        return faiss.IndexIDMap2(faiss.IndexFlatIP(int(dimension)))

    def _load_index(self, library_id: int, model_name: str, dimension: int):
        if not self._ensure_faiss():
            return None, self.index_path(library_id, model_name)
        index_path = self.index_path(library_id, model_name)
        cache_key = f"{library_id}_{model_name}"
        if cache_key in self._memory_indexes:
            return self._memory_indexes[cache_key], index_path
        
        index_info = self.db.get_vector_index(library_id, model_name)
        
        if index_info is not None and Path(index_info["index_path"]).exists():
            index_path = Path(index_info["index_path"])
            index = faiss.read_index(str(index_path))
            self._memory_indexes[cache_key] = index # insert into memory cache
            return index, index_path
        
        index = self._new_index(dimension)
        self._memory_indexes[cache_key] = index
        return index, index_path

    def _save_index(self, index, library_id: int, model_name: str, dimension: int, index_path: Path):
        if index is None or not self._ensure_faiss():
            return
        cache_key = f"{library_id}_{model_name}"
        self._memory_indexes[cache_key] = index # insert into memory cache
        
        index_path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(index, str(index_path))
        self.db.upsert_vector_index(library_id, model_name, dimension, str(index_path))

    def _remove_file_id(self, index, file_id: int):
        try:
            ids = np.array([file_id], dtype=np.int64)
            selector = faiss.IDSelectorBatch(ids) 
            removed_count = index.remove_ids(selector)
            return removed_count
        except Exception as e:
            print(f"Warning: Failed to remove ID {file_id} from FAISS: {e}")
            return 0

    def upsert_embedding(self, library_id: int, model_name: str, file_id: int, vector: Sequence[float]) -> bool:
        if not self._ensure_faiss():
            return False
        normalized = self._normalize(vector)
        index, index_path = self._load_index(library_id, model_name, normalized.size)
        if index is None:
            return False

        if hasattr(index, "d") and int(index.d) != int(normalized.size):
            rebuilt = self.rebuild_from_embeddings(library_id, model_name)
            if rebuilt is not None and int(rebuilt.d) == int(normalized.size):
                index = rebuilt
            else:
                index = self._new_index(normalized.size)
                if index is None:
                    return False

        if hasattr(index, "remove_ids"):
            self._remove_file_id(index, file_id)
        elif index.ntotal > 0:
            index = self.rebuild_from_embeddings(library_id, model_name)

        index.add_with_ids(normalized.reshape(1, -1), np.asarray([int(file_id)], dtype=np.int64))
        self._save_index(index, library_id, model_name, normalized.size, index_path)
        return True

    def rebuild_from_embeddings(self, library_id: int, model_name: str):
        if not self._ensure_faiss():
            return None
        rows = self.db.list_embeddings_for_library(library_id, model_name)
        if not rows:
            return None

        index = None
        vectors = []
        ids = []
        for row in rows:
            vector = np.frombuffer(row["vector"], dtype=np.float32)
            if vector.size == 0:
                continue
            if index is None:
                index = self._new_index(int(vector.size))
                if index is None:
                    return None
            if vector.size != index.d:
                continue
            vectors.append(vector.reshape(1, -1))
            ids.append(int(row["file_id"]))
        if index is None or not vectors:
            return None

        index.add_with_ids(np.vstack(vectors), np.asarray(ids, dtype=np.int64))
        self._save_index(index, library_id, model_name, int(index.d), self.index_path(library_id, model_name))
        return index

    def search(self, library_id: int, model_name: str, query_vector: Sequence[float], limit: int = 20) -> list[tuple[int, float]]:
        if not self._ensure_faiss():
            return []
        normalized = self._normalize(query_vector)
        index_info = self.db.get_vector_index(library_id, model_name)
        if index_info is None:
            index = self.rebuild_from_embeddings(library_id, model_name)
        else:
            index_path = Path(index_info["index_path"])
            if not index_path.exists():
                index = self.rebuild_from_embeddings(library_id, model_name)
            else:
                index = faiss.read_index(str(index_path))

        if index is None:
            return []
        if hasattr(index, "d") and int(index.d) != int(normalized.size):
            return []
        if index.ntotal == 0:
            return []

        try:
            scores, ids = index.search(normalized.reshape(1, -1), int(limit))
        except Exception:
            rebuilt = self.rebuild_from_embeddings(library_id, model_name)
            if rebuilt is None or rebuilt.ntotal == 0 or int(rebuilt.d) != int(normalized.size):
                return []
            scores, ids = rebuilt.search(normalized.reshape(1, -1), int(limit))
        results: list[tuple[int, float]] = []
        for file_id, score in zip(ids[0], scores[0]):
            if int(file_id) < 0:
                continue
            results.append((int(file_id), float(score)))
        return results

    def delete_library_indexes(self, library_id: int):
        index_dir = self.base_dir / str(library_id)
        if index_dir.exists():
            for path in index_dir.glob("*.faiss"):
                try:
                    path.unlink()
                except OSError:
                    continue
            try:
                index_dir.rmdir()
            except OSError:
                pass
        self.db.delete_vector_indexes_for_library(library_id)
