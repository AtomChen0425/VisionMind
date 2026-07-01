from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Sequence

from .app_paths import get_labels_manifest_path

logger = logging.getLogger(__name__)

TEMPLATE_PATH = Path(__file__).with_name("default_labels.md")
RUNTIME_PATH = get_labels_manifest_path()


def ensure_label_manifest(runtime_path: str | Path = RUNTIME_PATH) -> Path:
    runtime = Path(runtime_path)
    runtime.parent.mkdir(parents=True, exist_ok=True)
    if runtime.exists():
        return runtime
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"Missing label template: {TEMPLATE_PATH}")
    shutil.copyfile(TEMPLATE_PATH, runtime)
    logger.info("Created runtime label manifest at %s", runtime)
    return runtime


def _parse_label_lines(text: str) -> list[str]:
    labels: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith(("-", "*")):
            label = stripped[1:].strip()
            if label:
                labels.append(label)
    return labels


def load_label_manifest(runtime_path: str | Path = RUNTIME_PATH) -> tuple[list[str], str, Path]:
    manifest_path = ensure_label_manifest(runtime_path)
    text = manifest_path.read_text(encoding="utf-8")
    labels = _parse_label_lines(text)
    if not labels:
        raise ValueError(f"No labels found in manifest: {manifest_path}")
    signature = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return labels, signature, manifest_path


def labels_as_markdown(labels: Sequence[str]) -> str:
    lines = ["# Default Label Set", ""]
    lines.extend(f"- {label}" for label in labels)
    lines.append("")
    return "\n".join(lines)
