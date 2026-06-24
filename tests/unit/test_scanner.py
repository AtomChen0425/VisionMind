import unittest
import os
import tempfile
import shutil
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
        # 验证空目录扫描不会触发除零异常
        try:
            self.scanner.scan(self.test_dir)
            self.assertEqual(self.scanner.progress, 100)
        except ZeroDivisionError:
            self.fail("scan() raised ZeroDivisionError on empty directory!")

if __name__ == '__main__':
    unittest.main()
