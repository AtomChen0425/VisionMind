from __future__ import annotations

import os
from pathlib import Path

_MODEL_CACHE_CONFIGURED = False


def configure_model_cache(base_dir: str | Path = "data/model") -> Path:
    global _MODEL_CACHE_CONFIGURED

    root = Path(base_dir)
    root.mkdir(parents=True, exist_ok=True)

    cache_dirs = {
        "TORCH_HOME": root / "torch",
        "HF_HOME": root / "huggingface",
        "HUGGINGFACE_HUB_CACHE": root / "huggingface" / "hub",
        "TRANSFORMERS_CACHE": root / "huggingface" / "transformers",
        "XDG_CACHE_HOME": root / "xdg",
        "OPENCLIP_CACHE_DIR": root / "open_clip",
        "OPEN_CLIP_CACHE_DIR": root / "open_clip",
    }

    for env_name, env_path in cache_dirs.items():
        env_path.mkdir(parents=True, exist_ok=True)
        os.environ[env_name] = str(env_path)

    _MODEL_CACHE_CONFIGURED = True
    return root
