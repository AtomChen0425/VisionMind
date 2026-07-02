from __future__ import annotations

from functools import partial
import os
from pathlib import Path
from typing import Callable

from .app_paths import get_model_dir


ProgressCallback = Callable[[str, int, int | None], None]


def _emit(progress_callback: ProgressCallback | None, stage: str, current: int, total: int | None) -> None:
    if progress_callback is not None:
        progress_callback(stage, current, total)


def _get_pretrained_cfg(model_name: str, pretrained: str) -> dict:
    import open_clip

    pretrained_module = open_clip.pretrained
    return pretrained_module.get_pretrained_cfg(model_name, pretrained)


def is_pretrained_cached(
    model_name: str,
    pretrained: str,
    *,
    cache_dir: str | Path | None = None,
) -> bool:
    cache_root = Path(cache_dir) if cache_dir is not None else get_model_dir()
    cfg = _get_pretrained_cfg(model_name, pretrained)
    if not cfg:
        return False

    if "file" in cfg:
        return Path(cfg["file"]).exists()

    url = cfg.get("url", "")
    if url:
        filename = os.path.basename(url)
        return (cache_root / filename).exists()

    hf_hub = cfg.get("hf_hub", "")
    if not hf_hub:
        return False

    from huggingface_hub import hf_hub_download
    from open_clip.constants import HF_SAFE_WEIGHTS_NAME, HF_WEIGHTS_NAME

    model_id, filename = os.path.split(hf_hub)
    if not filename:
        filename = HF_WEIGHTS_NAME

    candidates = [filename]
    if filename == HF_WEIGHTS_NAME:
        candidates.insert(0, HF_SAFE_WEIGHTS_NAME)
    elif filename.endswith((".bin", ".pth")):
        candidates.append(filename[:-4] + ".safetensors")

    for candidate in candidates:
        try:
            hf_hub_download(
                repo_id=model_id,
                filename=candidate,
                cache_dir=str(cache_root),
                local_files_only=True,
            )
            return True
        except Exception:
            continue
    return False


class _GuiTqdm:
    def __init__(self, *args, **kwargs):
        self.total = kwargs.get("total")
        self.n = kwargs.get("initial", 0)
        self.desc = kwargs.get("desc", "")
        self._progress_callback: ProgressCallback | None = kwargs.pop("_progress_callback", None)
        self._stage = kwargs.pop("_stage", "download")
        _emit(self._progress_callback, self._stage, int(self.n), self._total_or_none())

    def _total_or_none(self) -> int | None:
        try:
            return int(self.total) if self.total is not None else None
        except Exception:
            return None

    def update(self, n: int | float = 1) -> None:
        self.n += int(n)
        _emit(self._progress_callback, self._stage, int(self.n), self._total_or_none())

    def set_description(self, desc: str) -> None:
        self.desc = desc
        _emit(self._progress_callback, self._stage, int(self.n), self._total_or_none())

    def refresh(self) -> None:
        _emit(self._progress_callback, self._stage, int(self.n), self._total_or_none())

    def close(self) -> None:
        _emit(self._progress_callback, self._stage, int(self.n), self._total_or_none())

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False


def ensure_pretrained_weights(
    model_name: str,
    pretrained: str,
    *,
    cache_dir: str | Path | None = None,
    progress_callback: ProgressCallback | None = None,
) -> Path | None:
    cache_root = Path(cache_dir) if cache_dir is not None else get_model_dir()
    cache_root.mkdir(parents=True, exist_ok=True)
    cfg = _get_pretrained_cfg(model_name, pretrained)
    if not cfg:
        raise RuntimeError(f"Unknown open_clip pretrained config: {model_name}:{pretrained}")

    _emit(progress_callback, "check", 0, None)
    if is_pretrained_cached(model_name, pretrained, cache_dir=cache_root):
        _emit(progress_callback, "ready", 1, 1)
        return None

    import open_clip

    pretrained_module = open_clip.pretrained
    original_tqdm = getattr(pretrained_module, "tqdm", None)
    original_hf_hub_download = getattr(pretrained_module, "hf_hub_download", None)

    def _patched_hf_hub_download(*args, **kwargs):
        kwargs.setdefault(
            "tqdm_class",
            partial(_GuiTqdm, _progress_callback=progress_callback, _stage="download"),
        )
        return original_hf_hub_download(*args, **kwargs)

    try:
        if original_tqdm is not None:
            pretrained_module.tqdm = partial(_GuiTqdm, _progress_callback=progress_callback, _stage="download")
        if original_hf_hub_download is not None:
            pretrained_module.hf_hub_download = _patched_hf_hub_download
        _emit(progress_callback, "download", 0, None)
        target = pretrained_module.download_pretrained(cfg, cache_dir=str(cache_root))
        _emit(progress_callback, "ready", 1, 1)
        return Path(target) if target else None
    finally:
        if original_tqdm is not None:
            pretrained_module.tqdm = original_tqdm
        if original_hf_hub_download is not None:
            pretrained_module.hf_hub_download = original_hf_hub_download
