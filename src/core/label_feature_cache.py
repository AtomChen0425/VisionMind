from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Sequence

import numpy as np

try:
    import faiss
except Exception:  # pragma: no cover - dependency availability is environment-specific
    faiss = None

logger = logging.getLogger(__name__)


class LabelFeatureCache:
    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._memory_cache: dict[str, np.ndarray] = {}

    @staticmethod
    def _sanitize_text(value: str) -> str:
        return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value)

    @staticmethod
    def _normalize(vector: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(vector, axis=-1, keepdims=True)
        norm = np.where(norm == 0, 1.0, norm)
        return vector / norm

    def _cache_dir(self, model_id: str) -> Path:
        return self.base_dir / self._sanitize_text(model_id)

    def _index_path(self, model_id: str, cache_key: str) -> Path:
        return self._cache_dir(model_id) / f"{cache_key}.faiss"

    def _meta_path(self, model_id: str, cache_key: str) -> Path:
        return self._cache_dir(model_id) / f"{cache_key}.json"

    def _load_from_disk(self, model_id: str, cache_key: str, label_count: int) -> np.ndarray | None:
        if faiss is None:
            return None
        index_path = self._index_path(model_id, cache_key)
        meta_path = self._meta_path(model_id, cache_key)
        if not index_path.exists() or not meta_path.exists():
            return None
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if int(metadata.get("label_count", -1)) != int(label_count):
            return None
        try:
            index = faiss.read_index(str(index_path))
            if int(index.ntotal) != int(label_count):
                return None
            vectors = index.reconstruct_n(0, int(label_count))
            return np.asarray(vectors, dtype=np.float32)
        except Exception as exc:  # pragma: no cover - faiss load failures are environment-dependent
            logger.warning("Failed to load label feature cache model=%s key=%s error=%s", model_id, cache_key, exc)
            return None

    def _save_to_disk(self, model_id: str, cache_key: str, labels_signature: str, labels: Sequence[str], features: np.ndarray):
        if faiss is None:
            return
        cache_dir = self._cache_dir(model_id)
        cache_dir.mkdir(parents=True, exist_ok=True)
        index_path = self._index_path(model_id, cache_key)
        meta_path = self._meta_path(model_id, cache_key)
        try:
            index = faiss.IndexFlatIP(int(features.shape[1]))
            index.add(np.asarray(features, dtype=np.float32))
            faiss.write_index(index, str(index_path))
            meta_path.write_text(
                json.dumps(
                    {
                        "model_id": model_id,
                        "cache_key": cache_key,
                        "labels_signature": labels_signature,
                        "label_count": len(labels),
                        "labels": list(labels),
                        "dimension": int(features.shape[1]),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as exc:  # pragma: no cover - faiss write failures are environment-dependent
            logger.warning("Failed to save label feature cache model=%s key=%s error=%s", model_id, cache_key, exc)

    def get_or_build(
        self,
        *,
        model_id: str,
        labels: Sequence[str],
        prompt_template: str,
        labels_signature: str,
        tokenizer,
        model,
        device,
        torch_module,
    ) -> np.ndarray:
        normalized_labels = tuple(str(label) for label in labels)
        cache_key = hashlib.sha256(
            json.dumps(
                {
                    "model_id": model_id,
                    "prompt_template": prompt_template,
                    "labels_signature": labels_signature,
                    "labels": list(normalized_labels),
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        memory_key = f"{model_id}:{cache_key}"

        cached = self._memory_cache.get(memory_key)
        if cached is not None:
            return cached

        loaded = self._load_from_disk(model_id, cache_key, len(normalized_labels))
        if loaded is not None:
            self._memory_cache[memory_key] = loaded
            return loaded

        prompts = [prompt_template.format(label) for label in normalized_labels]
        text_tokens = tokenizer(prompts).to(device)
        with torch_module.no_grad():
            text_features = model.encode_text(text_tokens)
            text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        features = np.asarray(text_features.detach().cpu().numpy(), dtype=np.float32)
        features = self._normalize(features)
        self._memory_cache[memory_key] = features
        self._save_to_disk(model_id, cache_key, labels_signature, normalized_labels, features)
        return features
