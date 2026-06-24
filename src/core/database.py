import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

class DatabaseManager:
    def __init__(self, db_path="data/photo_manager.db"):
        self.db_path = db_path
        self._memory_connection = sqlite3.connect(":memory:") if db_path == ":memory:" else None
        self._init_db()

    @contextmanager
    def get_connection(self):
        if self._memory_connection is not None:
            conn = self._memory_connection
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                yield conn
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()
            return

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        parent_dir = Path(self.db_path).parent
        if self.db_path != ":memory:" and str(parent_dir) not in ("", "."):
            os.makedirs(parent_dir, exist_ok=True)
        with self.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_path TEXT UNIQUE NOT NULL,
                    file_hash TEXT UNIQUE NOT NULL,
                    mtime REAL NOT NULL,
                    size INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending'
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER NOT NULL,
                    tag_name TEXT NOT NULL,
                    confidence REAL,
                    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    file_id INTEGER PRIMARY KEY,
                    vector BLOB NOT NULL,
                    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_hash ON files(file_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(tag_name)")
            conn.commit()

    def get_photos(self, limit=100, offset=0):
        with self.get_connection() as conn:
            cursor = conn.execute(
                "SELECT id, file_path, status FROM files ORDER BY id LIMIT ? OFFSET ?",
                (limit, offset)
            )
            return cursor.fetchall()

    def count_photos(self):
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM files")
            return cursor.fetchone()[0]
