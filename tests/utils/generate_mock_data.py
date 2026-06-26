import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.database import DatabaseManager


def _build_hash(file_path: str) -> str:
    return hashlib.sha256(file_path.encode("utf-8")).hexdigest()


def generate_mock_data(db_path: str, rows: int = 100_000):
    db_file = Path(db_path)
    for suffix in ("", "-journal", "-wal", "-shm"):
        candidate = db_file.with_name(db_file.name + suffix)
        if candidate.exists():
            candidate.unlink()

    db = DatabaseManager(db_path)
    with db.get_connection() as conn:
        conn.execute("DELETE FROM vector_indexes")
        conn.execute("DELETE FROM embeddings")
        conn.execute("DELETE FROM tags")
        conn.execute("DELETE FROM files")
        conn.execute("DELETE FROM libraries")
        conn.execute("INSERT INTO libraries (id, root_path) VALUES (?, ?)", (1, "/mock/library"))

        for index in range(rows):
            file_path = f"/mock/library/photo_{index:07d}.jpg"
            file_hash = _build_hash(file_path)
            conn.execute(
                "INSERT INTO files (library_id, file_path, file_hash, mtime, size, status, relative_path, mtime_ns) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (1, file_path, file_hash, float(index), 1024 + index, "indexed", f"photo_{index:07d}.jpg", index),
            )

            file_id = conn.execute(
                "SELECT id FROM files WHERE file_hash = ?",
                (file_hash,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO tags (file_id, tag_name, confidence) VALUES (?, ?, ?)",
                (file_id, "mock-tag", 0.9),
            )
            conn.execute(
                "INSERT INTO embeddings (file_id, model_name, dimensions, vector) VALUES (?, ?, ?, ?)",
                (file_id, "mock-model", 2, b"mock-vector"),
            )


def main():
    parser = argparse.ArgumentParser(description="Generate mock PhotoManager data")
    parser.add_argument("--db-path", default="data/photo_manager.db")
    parser.add_argument("--rows", type=int, default=100_000)
    args = parser.parse_args()

    Path(args.db_path).parent.mkdir(parents=True, exist_ok=True)
    generate_mock_data(args.db_path, args.rows)


if __name__ == "__main__":
    main()
