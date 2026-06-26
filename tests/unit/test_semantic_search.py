import unittest

from src.core.database import DatabaseManager
from src.core.semantic_search import SemanticSearchService


class FakeAnalyzer:
    def available(self):
        return True

    def model_id(self):
        return "ViT-B-32:laion2b_s34b_b79k"

    def text_to_embedding(self, text: str):
        return [1.0, 0.0]


class FakeAnalysisService:
    def __init__(self):
        self.analyzer = FakeAnalyzer()


class FakeVectorIndex:
    def search(self, library_id: int, model_name: str, query_vector, limit: int = 20):
        return [(2, 0.95), (1, 0.80)]


class SemanticSearchServiceTests(unittest.TestCase):
    def setUp(self):
        self.db = DatabaseManager(":memory:")
        with self.db.get_connection() as conn:
            conn.execute("INSERT INTO libraries (root_path) VALUES (?)", ("/library",))
            self.library_id = conn.execute("SELECT id FROM libraries WHERE root_path = ?", ("/library",)).fetchone()[0]
            conn.execute(
                "INSERT INTO files (library_id, file_path, relative_path, file_hash, mtime, mtime_ns, size, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (self.library_id, "/library/cat.jpg", "cat.jpg", "hash-1", 1.0, 1, 10, "analyzed"),
            )
            conn.execute(
                "INSERT INTO files (library_id, file_path, relative_path, file_hash, mtime, mtime_ns, size, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (self.library_id, "/library/dog.jpg", "dog.jpg", "hash-2", 1.0, 2, 10, "analyzed"),
            )
            conn.execute(
                "INSERT INTO files (library_id, file_path, relative_path, file_hash, mtime, mtime_ns, size, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (self.library_id, "/library/other.jpg", "other.jpg", "hash-3", 1.0, 3, 10, "analyzed"),
            )

    def test_mixed_search_combines_filename_and_semantic_results(self):
        service = SemanticSearchService(self.db, FakeAnalysisService(), FakeVectorIndex())

        file_ids, score_map, source_map = service.search(self.library_id, "cat", mode="mixed", limit=10)

        self.assertEqual(file_ids, [2, 1])
        self.assertEqual(score_map[2], 0.95)
        self.assertEqual(score_map[1], 0.80)
        self.assertEqual(source_map[2], "semantic")
        self.assertEqual(source_map[1], "semantic")

    def test_filename_search_uses_substring_match(self):
        service = SemanticSearchService(self.db, FakeAnalysisService(), FakeVectorIndex())

        file_ids, score_map, source_map = service.search(self.library_id, "dog", mode="filename", limit=10)

        self.assertEqual(file_ids, [2])
        self.assertEqual(score_map[2], 1.0)
        self.assertEqual(source_map[2], "filename")


if __name__ == "__main__":
    unittest.main()
