"""Embeddingi SigLIP (wielojęzyczne): obraz ↔ tekst w jednej przestrzeni. Zapytania po polsku działają."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from ..config import SETTINGS
from ..gpu import free_torch

log = logging.getLogger(__name__)


class Embedder:
    def __init__(self) -> None:
        import torch
        from transformers import AutoModel, AutoProcessor

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.dtype = torch.float16 if self.device == "cuda" else torch.float32
        self.model = AutoModel.from_pretrained(SETTINGS.embed_model, dtype=self.dtype).to(self.device).eval()
        self.processor = AutoProcessor.from_pretrained(SETTINGS.embed_model)
        # SigLIP: p(dopasowanie) = sigmoid(scale * cos + bias) — skalibrowane, porównywalne między zapytaniami
        self.logit_scale = float(self.model.logit_scale.exp().item()) if hasattr(self.model, "logit_scale") else 100.0
        self.logit_bias = float(self.model.logit_bias.item()) if hasattr(self.model, "logit_bias") else -10.0

    def match_prob(self, sims: np.ndarray) -> np.ndarray:
        z = self.logit_scale * sims + self.logit_bias
        return 1.0 / (1.0 + np.exp(-z))

    @staticmethod
    def _norm(x: np.ndarray) -> np.ndarray:
        return x / (np.linalg.norm(x, axis=-1, keepdims=True) + 1e-8)

    @staticmethod
    def _tensor(out: Any) -> Any:
        """transformers ≥5 zwraca ModelOutput z get_*_features; starsze wersje zwracają tensor."""
        if hasattr(out, "pooler_output") and out.pooler_output is not None:
            return out.pooler_output
        if hasattr(out, "last_hidden_state"):
            return out.last_hidden_state
        return out

    def images(self, paths: list[str | Path]) -> np.ndarray:
        import torch
        from PIL import Image

        ims = [Image.open(p).convert("RGB") for p in paths]
        if not ims:
            return np.zeros((0, 0), dtype=np.float32)
        inputs = self.processor(images=ims, return_tensors="pt").to(self.device)
        with torch.no_grad():
            feats = self._tensor(self.model.get_image_features(pixel_values=inputs["pixel_values"].to(self.dtype)))
        return self._norm(feats.float().cpu().numpy())

    def text(self, texts: list[str]) -> np.ndarray:
        import torch

        inputs = self.processor(text=texts, padding="max_length", truncation=True, max_length=64, return_tensors="pt").to(self.device)
        with torch.no_grad():
            feats = self._tensor(self.model.get_text_features(input_ids=inputs["input_ids"]))
        return self._norm(feats.float().cpu().numpy())

    def shot_embedding(self, keyframes: list[str]) -> np.ndarray | None:
        if not keyframes:
            return None
        return self._norm(self.images(keyframes).mean(axis=0))

    def close(self) -> None:
        del self.model, self.processor
        free_torch()
