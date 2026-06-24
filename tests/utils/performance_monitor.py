import argparse
import statistics
import time
import sys
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.database import DatabaseManager


def measure(db_path: str, limit: int = 100, offset: int = 0, iterations: int = 20):
    db = DatabaseManager(db_path)
    timings = []
    tracemalloc.start()

    for _ in range(iterations):
        start = time.perf_counter()
        db.get_photos(limit=limit, offset=offset)
        timings.append(time.perf_counter() - start)

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return {
        "iterations": iterations,
        "calls": iterations,
        "limit": limit,
        "offset": offset,
        "min_ms": min(timings) * 1000,
        "avg_ms": statistics.mean(timings) * 1000,
        "max_ms": max(timings) * 1000,
        "current_kb": current / 1024,
        "peak_kb": peak / 1024,
    }


def main():
    parser = argparse.ArgumentParser(description="Measure PhotoManager pagination performance")
    parser.add_argument("--db-path", default="data/photo_manager.db")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--iterations", type=int, default=20)
    args = parser.parse_args()

    Path(args.db_path).parent.mkdir(parents=True, exist_ok=True)
    result = measure(args.db_path, args.limit, args.offset, args.iterations)
    print(result)


if __name__ == "__main__":
    main()
