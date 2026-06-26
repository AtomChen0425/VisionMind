import unittest

from src.core.supported_image_types import METADATA_SUPPORTED_IMAGE_EXTENSIONS, SCAN_SUPPORTED_IMAGE_EXTENSIONS


class SupportedImageTypesTests(unittest.TestCase):
    def test_metadata_supported_types_are_limited(self):
        self.assertIn(".jpg", METADATA_SUPPORTED_IMAGE_EXTENSIONS)
        self.assertIn(".png", METADATA_SUPPORTED_IMAGE_EXTENSIONS)
        self.assertNotIn(".heic", METADATA_SUPPORTED_IMAGE_EXTENSIONS)
        self.assertIn(".cr2", METADATA_SUPPORTED_IMAGE_EXTENSIONS)

    def test_scan_supports_heic(self):
        self.assertIn(".heic", SCAN_SUPPORTED_IMAGE_EXTENSIONS)
        self.assertIn(".cr2", SCAN_SUPPORTED_IMAGE_EXTENSIONS)
        self.assertIn(".arw", SCAN_SUPPORTED_IMAGE_EXTENSIONS)


if __name__ == "__main__":
    unittest.main()
