import tempfile
import unittest
from pathlib import Path

from PIL import Image

from src.core.image_processing import create_thumbnail


class ImageProcessingTests(unittest.TestCase):
    def test_create_thumbnail_preserves_aspect_ratio(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "wide.png"
            image = Image.new("RGB", (1600, 800), color=(255, 0, 0))
            image.save(image_path)

            thumbnail = create_thumbnail(image_path, thumb_size=320)

            self.assertLessEqual(thumbnail.width, 320)
            self.assertLessEqual(thumbnail.height, 320)
            self.assertEqual(round(thumbnail.width / thumbnail.height, 2), 2.0)


if __name__ == "__main__":
    unittest.main()
