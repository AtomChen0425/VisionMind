from __future__ import annotations

import argparse
import platform
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.exiftool_manager import ExifToolManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ensure ExifTool is available in a local folder.")
    parser.add_argument(
        "--tool-dir",
        default="data/tools/exiftool",
        help="Folder to search for or download ExifTool into.",
    )
    parser.add_argument(
        "--print-platform",
        action="store_true",
        help="Print the detected platform before resolving ExifTool.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    manager = ExifToolManager(args.tool_dir)

    if args.print_platform:
        print(platform.system().lower())

    exiftool_path = manager.ensure_exiftool()
    print(str(exiftool_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
