import tempfile
import unittest

from src.core.thumbnail_cache import ThumbnailCache


class ThumbnailCacheTests(unittest.TestCase):
    def test_key_changes_when_source_metadata_changes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cache = ThumbnailCache(cache_dir=tmp_dir)
            key_a = cache.key("C:/photos/a.jpg", mtime_ns=1, size=100, thumb_size=320)
            key_b = cache.key("C:/photos/a.jpg", mtime_ns=2, size=100, thumb_size=320)
            key_c = cache.key("C:/photos/a.jpg", mtime_ns=1, size=200, thumb_size=320)

            self.assertNotEqual(key_a, key_b)
            self.assertNotEqual(key_a, key_c)


if __name__ == "__main__":
    unittest.main()
