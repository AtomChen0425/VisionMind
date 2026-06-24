import tempfile
import unittest
from pathlib import Path

from PIL import Image

from src.core.metadata_reader import extract_keywords, extract_title, read_image_metadata


class MetadataReaderTests(unittest.TestCase):
    def test_reads_exif_fields(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            image_path = Path(tmp_dir) / "camera.jpg"
            exif = Image.Exif()
            exif[271] = "Canon"
            exif[272] = "EOS R"
            exif[37386] = "50/1"
            exif[33437] = "28/10"
            exif[33434] = "1/125"
            exif[34855] = 200
            exif[36867] = "2026:06:24 12:34:56"

            Image.new("RGB", (800, 600), color=(10, 20, 30)).save(image_path, exif=exif)

            metadata = read_image_metadata(image_path)

            self.assertEqual(metadata["Make"], "Canon")
            self.assertEqual(metadata["Model"], "EOS R")
            self.assertIn("DateTimeOriginal", metadata)

    def test_extracts_keywords_and_title_from_metadata_dict(self):
        metadata = {
            "Subject": "Portrait; Night",
            "ImageDescription": "Sunset",
        }

        self.assertEqual(extract_keywords(metadata), ["Portrait", "Night"])
        self.assertEqual(extract_title(metadata), "Sunset")


if __name__ == "__main__":
    unittest.main()
