from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import logging
import numpy as np

from .image_processing import load_image_for_processing


DEFAULT_LABELS = (
    "person",
    "portrait",
    "landscape",
    "architecture",
    "street",
    "night",
    "indoor",
    "outdoor",
    "food",
    "animal",
    "vehicle",
    "flower",
    "sport",
    "travel",
    "mountain",
    "beach",
    "forest",
    "city",
    "document",
    "product",
    "flight",
    "train",
    "boat",
    "car",
    "bus",
    "building"
)

PROMPT_TEMPLATE = "a photo of a {}"
logger = logging.getLogger(__name__)
@dataclass(slots=True)
class TagPrediction:
    tag_name: str
    confidence: float


@dataclass(slots=True)
class AnalysisResult:
    tags: list[TagPrediction]
    embedding: np.ndarray | None = None
    model_name: str = "unknown"


class OpenClipAnalyzer:
    def __init__(
        self,
        *,
        model_name: str = "ViT-B-32",
        pretrained: str = "laion2b_s34b_b79k",
        device: str | None = None,
    ):
        self.model_name = model_name
        self.pretrained = pretrained
        self._device_name = device
        self._model = None
        self._preprocess = None
        self._tokenizer = None
        self._device = None

    def available(self) -> bool:
        try:
            import open_clip  # noqa: F401
            import torch  # noqa: F401
        except Exception:
            return False
        return True

    def _resolve_device(self):
        import torch

        if self._device_name:
            return torch.device(self._device_name)
        if torch.cuda.is_available():
            return torch.device("cuda")
        mps_backend = getattr(torch.backends, "mps", None)
        if mps_backend is not None and mps_backend.is_available():
            return torch.device("mps")
        return torch.device("cpu")

    def _ensure_model(self):
        if self._model is not None:
            return

        try:
            import open_clip
            import torch
        except Exception as exc:  # pragma: no cover - import failure is environment-dependent
            raise RuntimeError("open-clip-torch and torch are required for AI analysis") from exc

        self._device = self._resolve_device()
        model, _, preprocess = open_clip.create_model_and_transforms(
            self.model_name,
            pretrained=self.pretrained,
        )
        model = model.to(self._device)
        tokenizer = open_clip.get_tokenizer(self.model_name)
        model.eval()
        self._model = model
        self._preprocess = preprocess
        self._tokenizer = tokenizer
        self._torch = torch

    def _normalize(self, vector: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(vector)
        if norm == 0:
            return vector
        return vector / norm

    def model_id(self) -> str:
        return f"{self.model_name}:{self.pretrained}"

    def infer(self, image_path: str, labels: Sequence[str] | None = None, top_k: int = 8) -> AnalysisResult:
        logger.debug("Inferring single image path=%s", image_path)
        return self.infer_batch([image_path], labels=labels, top_k=top_k)[0]
    def apply_prompt_template(self,labels):
        template=PROMPT_TEMPLATE
        return tuple(template.format(label) for label in labels)
    def infer_batch(self, image_paths: Sequence[str], labels: Sequence[str] | None = None, top_k: int = 8) -> list[AnalysisResult]:
        self._ensure_model()
        raw_labels= tuple(labels or DEFAULT_LABELS)
        labels = self.apply_prompt_template(raw_labels)
        image_paths = [str(path) for path in image_paths]
        if not image_paths:
            return []
        logger.info("Infer batch started count=%s model=%s", len(image_paths), self.model_id())

        image_tensors = []
        for image_path in image_paths:
            image = load_image_for_processing(image_path)
            image_tensors.append(self._preprocess(image.convert("RGB")))
        image_tensor = self._torch.stack(image_tensors, dim=0).to(self._device)
        label_tokens = self._tokenizer(list(labels)).to(self._device)

        with self._torch.no_grad():
            image_features = self._model.encode_image(image_tensor)
            text_features = self._model.encode_text(label_tokens)

            image_features = image_features / image_features.norm(dim=-1, keepdim=True)
            text_features = text_features / text_features.norm(dim=-1, keepdim=True)
            logits = (100.0 * image_features @ text_features.T).softmax(dim=-1)
            probabilities = logits.detach().cpu().numpy()
            embeddings = image_features.detach().cpu().numpy().astype(np.float32)

        results = []
        model_name = self.model_id()
        for row_index in range(len(image_paths)):
            row_probabilities = probabilities[row_index]
            ranked_indexes = np.argsort(row_probabilities)[::-1][: max(1, top_k)]
            tags = [
                TagPrediction(tag_name=raw_labels[index], confidence=float(row_probabilities[index]))
                for index in ranked_indexes
            ]
            results.append(AnalysisResult(tags=tags, embedding=embeddings[row_index], model_name=model_name))
        logger.info("Infer batch finished count=%s model=%s", len(results), model_name)
        return results

    def embedding_to_bytes(self, embedding: np.ndarray) -> bytes:
        normalized = self._normalize(np.asarray(embedding, dtype=np.float32))
        return normalized.astype(np.float32).tobytes()

    def text_to_embedding(self, text: str) -> np.ndarray:
        self._ensure_model()
        logger.debug("Creating text embedding text=%s", text)
        text_tokens = self._tokenizer([text]).to(self._device)
        with self._torch.no_grad():
            text_features = self._model.encode_text(text_tokens)
            text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        return text_features.squeeze(0).detach().cpu().numpy().astype(np.float32)


class AnalysisService:
    def __init__(self, analyzer: OpenClipAnalyzer):
        self.analyzer = analyzer

    def analyze_image(self, image_path: str, labels: Sequence[str] | None = None, top_k: int = 8) -> AnalysisResult:
        return self.analyzer.infer(image_path, labels=labels, top_k=top_k)

    def analyze_images(self, image_paths: Sequence[str], labels: Sequence[str] | None = None, top_k: int = 8) -> list[AnalysisResult]:
        return self.analyzer.infer_batch(image_paths, labels=labels, top_k=top_k)
