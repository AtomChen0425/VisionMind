import os
import shutil
import tempfile
import unittest
from pathlib import Path

from src.core.database import DatabaseManager
from src.core.scanner import Scanner


class TestScanner(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.db = DatabaseManager(self.db_path)
        self.scanner = Scanner(self.db)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_scan_empty_directory(self):
        summary = self.scanner.scan(self.test_dir)
        self.assertEqual(summary.files_seen, 0)
        self.assertEqual(self.scanner.progress, 100)

    def test_scan_is_incremental_for_unchanged_files(self):
        image_path = Path(self.test_dir) / "photo.jpg"
        image_path.write_bytes(b"fake-image-data")

        first_summary = self.scanner.scan(self.test_dir)
        second_summary = self.scanner.scan(self.test_dir)

        self.assertEqual(first_summary.files_added, 1)
        self.assertEqual(second_summary.files_unchanged, 1)
        self.assertEqual(self.db.count_photos(), 1)

    def test_scan_respects_exclude_paths(self):
        included = Path(self.test_dir) / "included.jpg"
        excluded_dir = Path(self.test_dir) / "cache"
        excluded_dir.mkdir()
        excluded = excluded_dir / "hidden.jpg"
        included.write_bytes(b"fake-image-data")
        excluded.write_bytes(b"fake-image-data")

        summary = self.scanner.scan(self.test_dir, exclude_paths=[str(excluded_dir)])

        self.assertEqual(summary.files_added, 1)
        self.assertEqual(self.db.count_photos(), 1)


if __name__ == "__main__":
    unittest.main()
