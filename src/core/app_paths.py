from __future__ import annotations

import sys
from pathlib import Path


APP_NAME = "VisionMind"


def get_project_root() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parents[2]


def _default_base_dir() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(sys.executable).resolve().parent / "data"
    return Path(__file__).resolve().parents[2] / "data"


def get_app_data_dir() -> Path:
    root = _default_base_dir()
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_database_path() -> Path:
    return get_app_data_dir() / "photo_manager.db"


def get_logs_dir() -> Path:
    path = get_app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_model_dir() -> Path:
    path = get_app_data_dir() / "model"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_cache_dir() -> Path:
    path = get_app_data_dir() / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_thumbnail_cache_dir() -> Path:
    path = get_cache_dir() / "thumbnails"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_index_dir() -> Path:
    path = get_app_data_dir() / "indexes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_tools_dir() -> Path:
    path = get_app_data_dir() / "tools"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_exiftool_dir() -> Path:
    path = get_tools_dir() / "exiftool"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_labels_manifest_path() -> Path:
    path = get_app_data_dir() / "labels.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def get_resource_path(*parts: str) -> Path:
    return get_project_root().joinpath(*parts)
