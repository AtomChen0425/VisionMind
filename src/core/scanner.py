import os
import hashlib
from .database import DatabaseManager

class Scanner:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        self.progress = 0
        self.current_file = ""

    def _calculate_hash(self, file_path, block_size=65536):
        sha256 = hashlib.sha256()
        try:
            with open(file_path, 'rb') as f:
                for block in iter(lambda: f.read(block_size), b''):
                    sha256.update(block)
            return sha256.hexdigest()
        except Exception:
            return None

    def scan(self, root_dir):
        files_to_scan = []
        for root, _, files in os.walk(root_dir):
            for file in files:
                if file.lower().endswith(('.jpg', '.jpeg', '.png', '.tiff')):
                    files_to_scan.append(os.path.join(root, file))

        total = len(files_to_scan)
        if total == 0:
            self.progress = 100
            return

        # 修复：使用 with 语句正确获取连接对象，并利用上下文管理器自动处理事务
        with self.db.get_connection() as conn:
            for i, file_path in enumerate(files_to_scan):
                self.current_file = file_path
                self.progress = int((i + 1) / total * 100)

                try:
                    mtime = os.path.getmtime(file_path)
                    size = os.path.getsize(file_path)
                    file_hash = self._calculate_hash(file_path)

                    if file_hash:
                        conn.execute(
                            "INSERT OR REPLACE INTO files (file_path, file_hash, mtime, size, status) VALUES (?, ?, ?, ?, ?)",
                            (file_path, file_hash, mtime, size, 'pending')
                        )
                except (OSError, PermissionError) as e:
                    print(f"Skipping {file_path}: {e}")
                    continue




    def get_scan_progress(self):
        return {"progress": self.progress, "current_file": self.current_file}
