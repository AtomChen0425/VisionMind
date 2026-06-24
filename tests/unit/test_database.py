import tempfile
import unittest
from pathlib import Path

from src.core.database import DatabaseManager


class DatabaseManagerTests(unittest.TestCase):
    def _create_library(self, db, root_path="/library"):
        with db.get_connection() as conn:
            conn.execute("INSERT INTO libraries (root_path) VALUES (?)", (root_path,))
            return conn.execute("SELECT id FROM libraries WHERE root_path = ?", (root_path,)).fetchone()[0]

    def test_in_memory_database_initializes(self):
        db = DatabaseManager(":memory:")
        library_id = self._create_library(db, "/memory-library")
        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO files (library_id, file_path, relative_path, file_hash, mtime, mtime_ns, size, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (library_id, "/tmp/a.jpg", "a.jpg", "hash-a", 1.0, 1, 10, "pending"),
            )
            rows = conn.execute("SELECT file_path, file_hash FROM files").fetchall()

        self.assertEqual([(row["file_path"], row["file_hash"]) for row in rows], [("/tmp/a.jpg", "hash-a")])

    def test_filename_only_database_initializes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "photo_manager.db"
            db = DatabaseManager(str(db_path))
            library_id = self._create_library(db, "/disk-library")

            self.assertTrue(db_path.exists())

            with db.get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO files (library_id, file_path, relative_path, file_hash, mtime, mtime_ns, size, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (library_id, "/tmp/b.jpg", "b.jpg", "hash-b", 2.0, 2, 20, "pending"),
                )
                conn.execute(
                    """
                    INSERT INTO files (library_id, file_path, relative_path, file_hash, mtime, mtime_ns, size, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (library_id, "/tmp/c.jpg", "c.jpg", "hash-c", 3.0, 3, 30, "pending"),
                )

            self.assertEqual(
                [(row["id"], row["file_path"], row["status"], row["size"]) for row in db.get_photos(limit=1, offset=0)],
                [(1, "/tmp/b.jpg", "pending", 20)],
            )
            self.assertEqual(
                [(row["id"], row["file_path"], row["status"], row["size"]) for row in db.get_photos(limit=1, offset=1)],
                [(2, "/tmp/c.jpg", "pending", 30)],
            )

    def test_replace_tags_deduplicates_existing_values(self):
        db = DatabaseManager(":memory:")
        library_id = self._create_library(db, "/library")
        with db.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO files (library_id, file_path, relative_path, file_hash, mtime, mtime_ns, size, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (library_id, "/tmp/photo.jpg", "photo.jpg", "hash", 1.0, 1, 10, "pending"),
            )
            file_id = conn.execute("SELECT id FROM files WHERE file_path = ?", ("/tmp/photo.jpg",)).fetchone()[0]

        changed = db.replace_tags(file_id, [("Portrait", 0.9), ("portrait", 0.8), ("Night", 0.5)])

        self.assertTrue(changed)
        self.assertEqual(
            [(row["tag_name"], row["confidence"]) for row in db.get_tags(file_id)],
            [("night", 0.5), ("portrait", 0.9)],
        )

    def test_foreign_keys_are_enabled(self):
        db = DatabaseManager(":memory:")
        with db.get_connection() as conn:
            foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]

        self.assertEqual(foreign_keys, 1)


if __name__ == "__main__":
    unittest.main()
