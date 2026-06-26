import unittest

import pytest

from src.core.pipeline import ProcessingOutcome

pytest.importorskip("PySide6")

from src.gui.workers import AnalysisWorker


class FakeDatabase:
    def __init__(self):
        self.errors = []

    def set_file_error(self, file_id: int, message: str):
        self.errors.append((file_id, message))


class FakePipeline:
    def __init__(self):
        self.db = FakeDatabase()
        self.batches = []

    def process_files(self, file_items):
        self.batches.append(list(file_items))
        return [
            ProcessingOutcome(
                file_id=file_id,
                image_path=image_path,
                tags_written=1,
                embedding_written=True,
                metadata_written=True,
            )
            for file_id, image_path in file_items
        ]

    def process_file(self, file_id: int, image_path: str):
        raise AssertionError("process_file should only be used as batch fallback")


class AnalysisWorkerTests(unittest.TestCase):
    def test_run_processes_files_in_configured_batches(self):
        rows = [{"id": index, "file_path": f"image-{index}.jpg"} for index in range(1, 6)]
        pipeline = FakePipeline()
        worker = AnalysisWorker(pipeline, rows, batch_size=2)
        progress = []
        worker.progress_changed.connect(lambda current, total, path: progress.append((current, total, path)))

        worker.run()

        self.assertEqual(
            pipeline.batches,
            [
                [(1, "image-1.jpg"), (2, "image-2.jpg")],
                [(3, "image-3.jpg"), (4, "image-4.jpg")],
                [(5, "image-5.jpg")],
            ],
        )
        self.assertEqual([item[0] for item in progress], [1, 2, 3, 4, 5])
        self.assertEqual(pipeline.db.errors, [])


if __name__ == "__main__":
    unittest.main()
