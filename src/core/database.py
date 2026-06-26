from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Sequence

import os


class DatabaseManager:
    def __init__(self, db_path: str = "data/photo_manager.db"):
        self.db_path = db_path
        self._memory_connection = sqlite3.connect(":memory:") if db_path == ":memory:" else None
        if self._memory_connection is not None:
            self._memory_connection.row_factory = sqlite3.Row
        self._init_db()

    @contextmanager
    def get_connection(self):
        if self._memory_connection is not None:
            conn = self._memory_connection
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            try:
                yield conn
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()
            return

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS libraries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    root_path TEXT UNIQUE NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    library_id INTEGER NOT NULL,
                    file_path TEXT UNIQUE NOT NULL,
                    relative_path TEXT NOT NULL,
                    file_hash TEXT,
                    mtime REAL NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    size INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    deleted_at TEXT,
                    last_analyzed_at TEXT,
                    last_scan_run_id INTEGER,
                    last_error TEXT,
                    xmp_state TEXT NOT NULL DEFAULT 'not_written',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(library_id) REFERENCES libraries(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id INTEGER NOT NULL,
                    tag_name TEXT NOT NULL,
                    confidence REAL,
                    source TEXT NOT NULL DEFAULT 'open_clip',
                    model_name TEXT NOT NULL DEFAULT 'unknown',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE,
                    UNIQUE(file_id, tag_name, source, model_name)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS embeddings (
                    file_id INTEGER PRIMARY KEY,
                    model_name TEXT NOT NULL DEFAULT 'unknown',
                    dimensions INTEGER NOT NULL,
                    vector BLOB NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS scan_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    library_id INTEGER NOT NULL,
                    root_path TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running',
                    files_seen INTEGER NOT NULL DEFAULT 0,
                    files_changed INTEGER NOT NULL DEFAULT 0,
                    files_deleted INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    finished_at TEXT,
                    resume_token TEXT,
                    error_message TEXT,
                    FOREIGN KEY(library_id) REFERENCES libraries(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS library_excludes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    library_id INTEGER NOT NULL,
                    path TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(library_id) REFERENCES libraries(id) ON DELETE CASCADE,
                    UNIQUE(library_id, path)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vector_indexes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    library_id INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    dimension INTEGER NOT NULL DEFAULT 0,
                    index_path TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(library_id, model_name),
                    FOREIGN KEY(library_id) REFERENCES libraries(id) ON DELETE CASCADE
                )
                """
            )
            self._migrate_legacy_schema(conn)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_libraries_root_path ON libraries(root_path)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_library_path ON files(library_id, file_path)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_library_status ON files(library_id, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_hash ON files(file_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tags_name ON tags(tag_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_embeddings_model ON embeddings(model_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_library_excludes_library ON library_excludes(library_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_vector_indexes_library_model ON vector_indexes(library_id, model_name)")

    def _table_columns(self, conn: sqlite3.Connection, table_name: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {row["name"] for row in rows}

    def _migrate_legacy_schema(self, conn: sqlite3.Connection):
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        if "files" not in tables:
            return

        file_columns = self._table_columns(conn, "files")
        migrated_legacy_files = False

        if "library_id" not in file_columns:
            conn.execute("ALTER TABLE files ADD COLUMN library_id INTEGER")
            migrated_legacy_files = True
        if "relative_path" not in file_columns:
            conn.execute("ALTER TABLE files ADD COLUMN relative_path TEXT")
            migrated_legacy_files = True
        if "mtime_ns" not in file_columns:
            conn.execute("ALTER TABLE files ADD COLUMN mtime_ns INTEGER")
            migrated_legacy_files = True
        if "deleted_at" not in file_columns:
            conn.execute("ALTER TABLE files ADD COLUMN deleted_at TEXT")
        if "last_analyzed_at" not in file_columns:
            conn.execute("ALTER TABLE files ADD COLUMN last_analyzed_at TEXT")
        if "last_scan_run_id" not in file_columns:
            conn.execute("ALTER TABLE files ADD COLUMN last_scan_run_id INTEGER")
        if "last_error" not in file_columns:
            conn.execute("ALTER TABLE files ADD COLUMN last_error TEXT")
        if "xmp_state" not in file_columns:
            conn.execute("ALTER TABLE files ADD COLUMN xmp_state TEXT DEFAULT 'not_written'")
        if "created_at" not in file_columns:
            conn.execute("ALTER TABLE files ADD COLUMN created_at TEXT")
        if "updated_at" not in file_columns:
            conn.execute("ALTER TABLE files ADD COLUMN updated_at TEXT")

        if "libraries" not in tables:
            conn.execute(
                """
                CREATE TABLE libraries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    root_path TEXT UNIQUE NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        if migrated_legacy_files:
            conn.execute(
                """
                INSERT OR IGNORE INTO libraries (id, root_path)
                VALUES (1, '__legacy__')
                """
            )
            conn.execute(
                """
                UPDATE files
                SET library_id = COALESCE(library_id, 1),
                    relative_path = COALESCE(relative_path, '')
                """
            )
            rows = conn.execute("SELECT id, file_path, relative_path FROM files").fetchall()
            for row in rows:
                relative_path = row["relative_path"] or Path(str(row["file_path"])).name
                conn.execute(
                    """
                    UPDATE files
                    SET relative_path = ?, library_id = COALESCE(library_id, 1), xmp_state = COALESCE(xmp_state, 'not_written')
                    WHERE id = ?
                    """,
                    (relative_path, row["id"]),
                )

        if "tags" in tables:
            tag_columns = self._table_columns(conn, "tags")
            if "source" not in tag_columns:
                conn.execute("ALTER TABLE tags ADD COLUMN source TEXT DEFAULT 'open_clip'")
            if "model_name" not in tag_columns:
                conn.execute("ALTER TABLE tags ADD COLUMN model_name TEXT DEFAULT 'legacy'")
            if "created_at" not in tag_columns:
                conn.execute("ALTER TABLE tags ADD COLUMN created_at TEXT")

        if "embeddings" in tables:
            embedding_columns = self._table_columns(conn, "embeddings")
            if "model_name" not in embedding_columns:
                conn.execute("ALTER TABLE embeddings ADD COLUMN model_name TEXT DEFAULT 'legacy'")
            if "dimensions" not in embedding_columns:
                conn.execute("ALTER TABLE embeddings ADD COLUMN dimensions INTEGER DEFAULT 0")
            if "created_at" not in embedding_columns:
                conn.execute("ALTER TABLE embeddings ADD COLUMN created_at TEXT")
            if "updated_at" not in embedding_columns:
                conn.execute("ALTER TABLE embeddings ADD COLUMN updated_at TEXT")

        if "library_excludes" not in tables:
            conn.execute(
                """
                CREATE TABLE library_excludes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    library_id INTEGER NOT NULL,
                    path TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(library_id) REFERENCES libraries(id) ON DELETE CASCADE,
                    UNIQUE(library_id, path)
                )
                """
            )
        if "vector_indexes" not in tables:
            conn.execute(
                """
                CREATE TABLE vector_indexes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    library_id INTEGER NOT NULL,
                    model_name TEXT NOT NULL,
                    dimension INTEGER NOT NULL DEFAULT 0,
                    index_path TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(library_id, model_name),
                    FOREIGN KEY(library_id) REFERENCES libraries(id) ON DELETE CASCADE
                )
                """
            )

    def register_library(self, root_path: str) -> int:
        normalized_root = str(Path(root_path).resolve())
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO libraries (root_path)
                VALUES (?)
                ON CONFLICT(root_path) DO UPDATE SET updated_at = CURRENT_TIMESTAMP
                """,
                (normalized_root,),
            )
            row = conn.execute("SELECT id FROM libraries WHERE root_path = ?", (normalized_root,)).fetchone()
            return int(row["id"])

    def create_scan_run(self, library_id: int, root_path: str) -> int:
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO scan_runs (library_id, root_path)
                VALUES (?, ?)
                """,
                (library_id, str(Path(root_path).resolve())),
            )
            return int(cursor.lastrowid)

    def finish_scan_run(self, scan_run_id: int, status: str, error_message: str | None = None):
        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE scan_runs
                SET status = ?, finished_at = CURRENT_TIMESTAMP, error_message = ?
                WHERE id = ?
                """,
                (status, error_message, scan_run_id),
            )

    def update_scan_run_progress(self, scan_run_id: int, *, files_seen: int, files_changed: int, files_deleted: int):
        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE scan_runs
                SET files_seen = ?, files_changed = ?, files_deleted = ?
                WHERE id = ?
                """,
                (files_seen, files_changed, files_deleted, scan_run_id),
            )

    def get_library(self, root_path: str):
        normalized_root = str(Path(root_path).resolve())
        with self.get_connection() as conn:
            return conn.execute(
                "SELECT id, root_path, created_at, updated_at FROM libraries WHERE root_path = ?",
                (normalized_root,),
            ).fetchone()

    def list_libraries(self):
        with self.get_connection() as conn:
            return conn.execute(
                """
                SELECT id, root_path, created_at, updated_at
                FROM libraries
                ORDER BY root_path ASC
                """
            ).fetchall()

    def get_file_by_path(self, file_path: str):
        normalized_path = str(Path(file_path).resolve())
        with self.get_connection() as conn:
            return conn.execute(
                """
                SELECT id, library_id, file_path, relative_path, file_hash, mtime, mtime_ns, size,
                       status, deleted_at, last_analyzed_at, last_scan_run_id, last_error, xmp_state
                FROM files
                WHERE file_path = ?
                """,
                (normalized_path,),
            ).fetchone()

    def get_file_by_id(self, file_id: int):
        with self.get_connection() as conn:
            return conn.execute(
                """
                SELECT id, library_id, file_path, relative_path, file_hash, mtime, mtime_ns, size,
                       status, deleted_at, last_analyzed_at, last_scan_run_id, last_error, xmp_state
                FROM files
                WHERE id = ?
                """,
                (file_id,),
            ).fetchone()

    def upsert_file_record(
        self,
        *,
        library_id: int,
        file_path: str,
        relative_path: str,
        file_hash: str | None,
        mtime: float,
        mtime_ns: int,
        size: int,
        status: str = "pending",
        last_scan_run_id: int | None = None,
    ) -> tuple[int, bool]:
        normalized_path = str(Path(file_path).resolve())
        stored_hash = file_hash or f"meta:{mtime_ns}:{size}"
        with self.get_connection() as conn:
            existing = conn.execute(
                """
                SELECT id, file_hash, mtime, mtime_ns, size, status, relative_path, library_id, deleted_at
                FROM files
                WHERE file_path = ?
                """,
                (normalized_path,),
            ).fetchone()

            if existing is None:
                cursor = conn.execute(
                    """
                    INSERT INTO files (
                        library_id, file_path, relative_path, file_hash, mtime, mtime_ns, size,
                        status, deleted_at, last_scan_run_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                    """,
                    (
                        library_id,
                        normalized_path,
                        relative_path,
                        stored_hash,
                        mtime,
                        mtime_ns,
                        size,
                        status,
                        last_scan_run_id,
                    ),
                )
                return int(cursor.lastrowid), True

            changed = (
                existing["file_hash"] != stored_hash
                or int(existing["mtime_ns"]) != int(mtime_ns)
                or int(existing["size"]) != int(size)
                or existing["relative_path"] != relative_path
                or int(existing["library_id"]) != int(library_id)
            )

            if changed:
                conn.execute(
                    """
                    UPDATE files
                    SET library_id = ?,
                        relative_path = ?,
                        file_hash = ?,
                        mtime = ?,
                        mtime_ns = ?,
                        size = ?,
                        status = ?,
                        deleted_at = NULL,
                        last_scan_run_id = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE file_path = ?
                    """,
                    (
                        library_id,
                        relative_path,
                        stored_hash,
                        mtime,
                        mtime_ns,
                        size,
                        status,
                        last_scan_run_id,
                        normalized_path,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE files
                    SET status = ?,
                        deleted_at = NULL,
                        last_scan_run_id = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE file_path = ?
                    """,
                    (status, last_scan_run_id, normalized_path),
                )

            row = conn.execute("SELECT id FROM files WHERE file_path = ?", (normalized_path,)).fetchone()
            return int(row["id"]), changed

    def mark_missing_files(self, library_id: int, seen_paths: set[str]) -> int:
        with self.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT file_path
                FROM files
                WHERE library_id = ? AND deleted_at IS NULL
                """,
                (library_id,),
            ).fetchall()

            missing_paths = [row["file_path"] for row in rows if row["file_path"] not in seen_paths]
            if not missing_paths:
                return 0

            conn.executemany(
                """
                UPDATE files
                SET status = 'deleted',
                    deleted_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE file_path = ?
                """,
                [(path,) for path in missing_paths],
            )
            return len(missing_paths)

    def set_file_analyzed(self, file_id: int):
        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE files
                SET status = 'analyzed', last_analyzed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (file_id,),
            )

    def set_file_error(self, file_id: int, error_message: str):
        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE files
                SET status = 'error', last_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (error_message, file_id),
            )

    def set_metadata_state(self, file_id: int, state: str):
        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE files
                SET xmp_state = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (state, file_id),
            )

    def set_xmp_state(self, file_id: int, state: str):
        self.set_metadata_state(file_id, state)

    def replace_tags(
        self,
        file_id: int,
        tags: Sequence[tuple[str, float | None]],
        *,
        source: str = "open_clip",
        model_name: str = "unknown",
    ) -> bool:
        normalized: list[tuple[str, float | None]] = []
        seen = set()
        for tag_name, confidence in tags:
            clean_name = tag_name.strip().lower()
            if not clean_name or clean_name in seen:
                continue
            seen.add(clean_name)
            normalized.append((clean_name, confidence))

        with self.get_connection() as conn:
            existing = conn.execute(
                """
                SELECT tag_name, confidence
                FROM tags
                WHERE file_id = ? AND source = ? AND model_name = ?
                ORDER BY tag_name
                """,
                (file_id, source, model_name),
            ).fetchall()
            existing_rows = [(row["tag_name"], row["confidence"]) for row in existing]
            desired_rows = sorted(normalized, key=lambda item: item[0])
            if existing_rows == desired_rows:
                return False

            conn.execute(
                "DELETE FROM tags WHERE file_id = ? AND source = ? AND model_name = ?",
                (file_id, source, model_name),
            )
            conn.executemany(
                """
                INSERT INTO tags (file_id, tag_name, confidence, source, model_name)
                VALUES (?, ?, ?, ?, ?)
                """,
                [(file_id, tag_name, confidence, source, model_name) for tag_name, confidence in desired_rows],
            )
            conn.execute(
                """
                UPDATE files
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (file_id,),
            )
            return True

    def get_tags(self, file_id: int, *, source: str = "open_clip", model_name: str = "unknown"):
        with self.get_connection() as conn:
            return conn.execute(
                """
                SELECT tag_name, confidence
                FROM tags
                WHERE file_id = ? AND source = ? AND model_name = ?
                ORDER BY tag_name
                """,
                (file_id, source, model_name),
            ).fetchall()

    def upsert_embedding(
        self,
        file_id: int,
        vector: bytes,
        *,
        dimensions: int,
        model_name: str = "unknown",
    ) -> bool:
        with self.get_connection() as conn:
            existing = conn.execute(
                "SELECT vector, dimensions, model_name FROM embeddings WHERE file_id = ?",
                (file_id,),
            ).fetchone()
            if existing is not None and existing["vector"] == vector and int(existing["dimensions"]) == int(dimensions) and existing["model_name"] == model_name:
                return False

            conn.execute(
                """
                INSERT INTO embeddings (file_id, model_name, dimensions, vector, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(file_id) DO UPDATE SET
                    model_name = excluded.model_name,
                    dimensions = excluded.dimensions,
                    vector = excluded.vector,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (file_id, model_name, dimensions, sqlite3.Binary(vector)),
            )
            return True

    def get_embedding(self, file_id: int):
        with self.get_connection() as conn:
            return conn.execute(
                "SELECT file_id, model_name, dimensions, vector FROM embeddings WHERE file_id = ?",
                (file_id,),
            ).fetchone()

    def search_embeddings(self, query_vector: Sequence[float], limit: int = 20, model_name: str | None = None):
        try:
            import numpy as np
        except Exception as exc:  # pragma: no cover - numpy is an explicit dependency
            raise RuntimeError("numpy is required for vector search") from exc

        query = np.asarray(query_vector, dtype=np.float32)
        if query.ndim != 1 or query.size == 0:
            raise ValueError("query_vector must be a non-empty 1D vector")
        query_norm = np.linalg.norm(query)
        if query_norm == 0:
            raise ValueError("query_vector must not be the zero vector")
        query = query / query_norm

        with self.get_connection() as conn:
            if model_name is None:
                rows = conn.execute(
                    """
                    SELECT e.file_id, e.model_name, e.dimensions, e.vector, f.file_path
                    FROM embeddings e
                    JOIN files f ON f.id = e.file_id
                    WHERE f.deleted_at IS NULL
                    """
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT e.file_id, e.model_name, e.dimensions, e.vector, f.file_path
                    FROM embeddings e
                    JOIN files f ON f.id = e.file_id
                    WHERE f.deleted_at IS NULL AND e.model_name = ?
                    """,
                    (model_name,),
                ).fetchall()

        results = []
        for row in rows:
            vector = np.frombuffer(row["vector"], dtype=np.float32)
            if vector.size != query.size:
                continue
            vector_norm = np.linalg.norm(vector)
            if vector_norm == 0:
                continue
            score = float(np.dot(query, vector / vector_norm))
            results.append(
                {
                    "file_id": int(row["file_id"]),
                    "file_path": row["file_path"],
                    "model_name": row["model_name"],
                    "score": score,
                }
            )

        results.sort(key=lambda item: item["score"], reverse=True)
        return results[:limit]

    def get_photos(self, limit=100, offset=0):
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT id, file_path, status, size
                FROM files
                ORDER BY id
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            )
            return cursor.fetchall()

    def count_photos(self):
        with self.get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM files")
            return cursor.fetchone()[0]

    def list_files_for_library(self, library_id: int):
        with self.get_connection() as conn:
            return conn.execute(
                """
                SELECT id, file_path, relative_path, file_hash, mtime, mtime_ns, size, status, deleted_at,
                       last_analyzed_at, last_error, xmp_state
                FROM files
                WHERE library_id = ?
                ORDER BY id
                """,
                (library_id,),
            ).fetchall()

    def list_files_by_ids(self, library_id: int, file_ids: Sequence[int]):
        normalized_ids = [int(file_id) for file_id in file_ids]
        if not normalized_ids:
            return []
        placeholders = ",".join("?" for _ in normalized_ids)
        with self.get_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT id, file_path, relative_path, status, size, mtime_ns, last_analyzed_at, xmp_state, deleted_at
                FROM files
                WHERE library_id = ? AND deleted_at IS NULL AND id IN ({placeholders})
                """,
                [library_id, *normalized_ids],
            ).fetchall()
        by_id = {int(row["id"]): row for row in rows}
        return [by_id[file_id] for file_id in normalized_ids if file_id in by_id]

    def search_files_by_name(self, library_id: int, query: str, limit: int = 50):
        pattern = f"%{query.strip().lower()}%"
        with self.get_connection() as conn:
            return conn.execute(
                """
                SELECT id, file_path, relative_path, status, size, mtime_ns, last_analyzed_at, xmp_state, deleted_at
                FROM files
                WHERE library_id = ? AND deleted_at IS NULL
                  AND (LOWER(relative_path) LIKE ? OR LOWER(file_path) LIKE ?)
                ORDER BY relative_path ASC
                LIMIT ?
                """,
                (library_id, pattern, pattern, limit),
            ).fetchall()

    def list_embeddings_for_library(self, library_id: int, model_name: str | None = None):
        with self.get_connection() as conn:
            if model_name is None:
                return conn.execute(
                    """
                    SELECT e.file_id, e.model_name, e.dimensions, e.vector
                    FROM embeddings e
                    JOIN files f ON f.id = e.file_id
                    WHERE f.library_id = ? AND f.deleted_at IS NULL
                    """,
                    (library_id,),
                ).fetchall()
            return conn.execute(
                """
                SELECT e.file_id, e.model_name, e.dimensions, e.vector
                FROM embeddings e
                JOIN files f ON f.id = e.file_id
                WHERE f.library_id = ? AND f.deleted_at IS NULL AND e.model_name = ?
                """,
                (library_id, model_name),
            ).fetchall()

    def upsert_vector_index(self, library_id: int, model_name: str, dimension: int, index_path: str):
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO vector_indexes (library_id, model_name, dimension, index_path, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(library_id, model_name) DO UPDATE SET
                    dimension = excluded.dimension,
                    index_path = excluded.index_path,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (library_id, model_name, dimension, index_path),
            )

    def get_vector_index(self, library_id: int, model_name: str):
        with self.get_connection() as conn:
            return conn.execute(
                """
                SELECT id, library_id, model_name, dimension, index_path, updated_at
                FROM vector_indexes
                WHERE library_id = ? AND model_name = ?
                """,
                (library_id, model_name),
            ).fetchone()

    def delete_vector_indexes_for_library(self, library_id: int):
        with self.get_connection() as conn:
            conn.execute("DELETE FROM vector_indexes WHERE library_id = ?", (library_id,))

    def list_gallery_files(self, library_id: int, limit: int = 200, offset: int = 0):
        with self.get_connection() as conn:
            return conn.execute(
                """
                SELECT id, file_path, relative_path, status, size, mtime_ns, last_analyzed_at, xmp_state, deleted_at
                FROM files
                WHERE library_id = ? AND deleted_at IS NULL
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (library_id, limit, offset),
            ).fetchall()

    def list_pending_files(self, library_id: int):
        with self.get_connection() as conn:
            return conn.execute(
                """
                SELECT id, file_path, relative_path, status, size, mtime_ns, last_analyzed_at, xmp_state, deleted_at
                FROM files
                WHERE library_id = ? AND deleted_at IS NULL AND status IN ('pending', 'error')
                ORDER BY id ASC
                """,
                (library_id,),
            ).fetchall()

    def count_library_files(self, library_id: int):
        with self.get_connection() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM files WHERE library_id = ? AND deleted_at IS NULL",
                (library_id,),
            ).fetchone()[0]

    def get_library_stats(self, library_id: int):
        with self.get_connection() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_files,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_files,
                    SUM(CASE WHEN status = 'analyzed' THEN 1 ELSE 0 END) AS analyzed_files,
                    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error_files,
                    SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END) AS deleted_files
                FROM files
                WHERE library_id = ?
                """,
                (library_id,),
            ).fetchone()
            return row

    def get_file_tags(self, file_id: int, *, source: str = "open_clip", model_name: str = "unknown"):
        return self.get_tags(file_id, source=source, model_name=model_name)

    def list_tags_for_file(self, file_id: int):
        with self.get_connection() as conn:
            return conn.execute(
                """
                SELECT tag_name, confidence, source, model_name
                FROM tags
                WHERE file_id = ?
                ORDER BY confidence DESC, tag_name ASC
                """,
                (file_id,),
            ).fetchall()

    def get_library_excludes(self, library_id: int):
        with self.get_connection() as conn:
            return conn.execute(
                """
                SELECT path
                FROM library_excludes
                WHERE library_id = ?
                ORDER BY path ASC
                """,
                (library_id,),
            ).fetchall()

    def set_library_excludes(self, library_id: int, paths: Sequence[str]):
        normalized_paths = []
        seen = set()
        for path in paths:
            cleaned = str(Path(path).expanduser())
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            normalized_paths.append(cleaned)

        with self.get_connection() as conn:
            conn.execute("DELETE FROM library_excludes WHERE library_id = ?", (library_id,))
            conn.executemany(
                """
                INSERT INTO library_excludes (library_id, path)
                VALUES (?, ?)
                """,
                [(library_id, path) for path in normalized_paths],
            )

    def add_library_exclude(self, library_id: int, path: str):
        cleaned = str(Path(path).expanduser())
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO library_excludes (library_id, path)
                VALUES (?, ?)
                """,
                (library_id, cleaned),
            )

    def remove_library_exclude(self, library_id: int, path: str):
        cleaned = str(Path(path).expanduser())
        with self.get_connection() as conn:
            conn.execute(
                "DELETE FROM library_excludes WHERE library_id = ? AND path = ?",
                (library_id, cleaned),
            )
