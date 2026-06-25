import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import src.core.exiftool_metadata as exiftool_metadata

from src.core.exiftool_metadata import ExifToolTagWriter


class ExifToolMetadataWriterTests(unittest.TestCase):
    def test_updates_file_metadata_in_place_with_exiftool_command(self):
        commands: list[list[bytes]] = []
        outer = self

        class FakeHelper:
            def __init__(self, **kwargs):
                executable = kwargs.get("executable")
                outer.assertTrue(str(executable).endswith("exiftool.exe"))

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def set_tags(self, filenames, tags=None, params=None):
                commands.append([*filenames, tags, params])
                return []

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_dir = Path(tmp_dir)
            image_path = tmp_dir / "sample.jpg"
            tool_path = tmp_dir / "exiftool.exe"
            tool_path.write_text("stub", encoding="utf-8")
            Image.new("RGB", (640, 480), color=(128, 64, 32)).save(image_path, quality=95)

            with mock.patch.object(exiftool_metadata, "ExifToolHelper", FakeHelper):
                writer = ExifToolTagWriter(tool_dir=tmp_dir, exiftool_path=tool_path)
                with mock.patch(
                    "src.core.exiftool_metadata.read_image_metadata",
                    return_value={"Subject": "Existing; Portrait", "ImageDescription": "Sunset"},
                ):
                    output_path = writer.write(image_path, ["Portrait", "Night", "portrait"], title="Sunset")

            self.assertEqual(output_path, image_path)
            self.assertEqual(len(commands), 1)
            command = commands[0]
            self.assertEqual(command[0], str(image_path))
            self.assertEqual(command[1]["XMP-dc:Subject"], ["Existing", "Portrait", "Night"])
            self.assertEqual(command[1]["IPTC:Keywords"], ["Existing", "Portrait", "Night"])
            self.assertEqual(command[1]["XMP-dc:Title"], "Sunset")
            self.assertEqual(command[1]["EXIF:ImageDescription"], "Sunset")
            self.assertEqual(command[1]["IPTC:Caption-Abstract"], "Sunset")
            self.assertEqual(command[2], ["-overwrite_original", "-P", "-m", "-charset", "filename=UTF8", "-charset", "exif=UTF8"])

    def test_skips_write_when_metadata_already_matches(self):
        class FakeHelper:
            def __init__(self, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def set_tags(self, filenames, tags=None, params=None):
                raise AssertionError("set_tags should not be called")

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_dir = Path(tmp_dir)
            image_path = tmp_dir / "sample.jpg"
            tool_path = tmp_dir / "exiftool.exe"
            tool_path.write_text("stub", encoding="utf-8")
            Image.new("RGB", (640, 480), color=(12, 34, 56)).save(image_path, quality=95)

            with mock.patch.object(exiftool_metadata, "ExifToolHelper", FakeHelper):
                writer = ExifToolTagWriter(tool_dir=tmp_dir, exiftool_path=tool_path)
                with mock.patch(
                    "src.core.exiftool_metadata.read_image_metadata",
                    return_value={"Subject": "Portrait; Night", "ImageDescription": "Sunset"},
                ):
                    with mock.patch("src.core.exiftool_metadata.extract_keywords", return_value=["Portrait", "Night"]):
                        with mock.patch("src.core.exiftool_metadata.extract_title", return_value="Sunset"):
                            output_path = writer.write(image_path, ["Portrait", "Night", "portrait"], title="Sunset")

            self.assertEqual(output_path, image_path)


if __name__ == "__main__":
    unittest.main()
    
