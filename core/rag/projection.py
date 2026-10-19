"""Flatten 4096-number vectors to 2D points for a map of the index (PCA with plain NumPy).

PCA keeps the two directions in which the vectors differ most. The map is a sketch: points close
together are usually similar, but two dimensions cannot keep every distance, so search results are
judged by the real cosine distances, not by the map. ``explained`` says how much of the spread the
two axes keep.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Projection:
    points: np.ndarray        # (n, 2)
    mean: np.ndarray          # to place new vectors (a question) on the same map
    axes: np.ndarray          # (2, dims)
    explained: float          # share of the variance the two axes keep, 0..1

    def place(self, vectors) -> np.ndarray:
        return (_normalise(np.asarray(vectors, dtype=float)) - self.mean) @ self.axes.T


def _normalise(vectors: np.ndarray) -> np.ndarray:
    return vectors / np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-12)


def pca_2d(vectors) -> Projection:
    data = _normalise(np.asarray(vectors, dtype=float))  # cosine search: compare directions, not lengths
    mean = data.mean(axis=0)
    centred = data - mean
    _, singular, axes = np.linalg.svd(centred, full_matrices=False)
    variance = singular ** 2
    explained = float(variance[:2].sum() / variance.sum()) if variance.sum() else 0.0
    return Projection(centred @ axes[:2].T, mean, axes[:2], explained)
