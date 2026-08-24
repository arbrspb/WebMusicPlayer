"""Memory-conscious fusion of acoustic and Discogs EffNet genre models."""
from __future__ import annotations

import os

import numpy as np
from sklearn.decomposition import PCA
from sklearn.ensemble import RandomForestClassifier


def align_probabilities(probabilities, source_classes, target_classes):
    """Align classifier probabilities to another stable class order."""
    values = np.asarray(probabilities, dtype=float)
    if values.ndim == 1:
        values = values.reshape(1, -1)
    source = [str(value) for value in source_classes]
    target = [str(value) for value in target_classes]
    result = np.zeros((values.shape[0], len(target)), dtype=float)
    target_index = {value: index for index, value in enumerate(target)}
    for source_index, value in enumerate(source):
        index = target_index.get(value)
        if index is not None and source_index < values.shape[1]:
            result[:, index] = values[:, source_index]
    sums = result.sum(axis=1, keepdims=True)
    return np.divide(result, sums, out=np.zeros_like(result), where=sums > 1e-12)


def fuse_probabilities(acoustic, deep, alpha):
    """Blend two already aligned probability matrices row by row."""
    acoustic_values = np.asarray(acoustic, dtype=float)
    deep_values = np.asarray(deep, dtype=float)
    if acoustic_values.shape != deep_values.shape:
        raise ValueError("Probability matrices must have identical shapes")
    weight = min(max(float(alpha), 0.0), 0.65)
    fused = (1.0 - weight) * acoustic_values + weight * deep_values
    sums = fused.sum(axis=1, keepdims=True)
    return np.divide(fused, sums, out=np.zeros_like(fused), where=sums > 1e-12)


class EffNetGenreHead:
    """PCA + compact RF trained on frozen Discogs Multi-EffNet vectors.

    The class is intentionally pickle-friendly because it is stored inside the
    existing ``genre_model.pkl`` artifact.  It never decodes audio itself.
    """

    def __init__(self, pca_dimensions=48, random_state=42, n_estimators=220):
        self.pca_dimensions = max(8, int(pca_dimensions))
        self.random_state = int(random_state)
        self.n_estimators = max(80, int(n_estimators))
        self.pca = None
        self.classifier = None
        self.classes_ = np.asarray([], dtype=object)
        self.embedding_dim_ = 0

    def fit(self, vectors, labels):
        matrix = np.asarray(vectors, dtype=np.float32)
        targets = np.asarray(labels, dtype=object)
        if matrix.ndim != 2 or matrix.shape[0] != targets.shape[0]:
            raise ValueError("EffNet vectors and labels have incompatible shapes")
        if matrix.shape[0] < 8 or len(set(targets.tolist())) < 2:
            raise ValueError("Not enough EffNet training examples/classes")
        self.embedding_dim_ = int(matrix.shape[1])
        components = min(
            self.pca_dimensions,
            max(1, matrix.shape[0] - 1),
            matrix.shape[1],
        )
        self.pca = PCA(
            n_components=components,
            svd_solver="randomized",
            random_state=self.random_state,
        )
        reduced = self.pca.fit_transform(matrix).astype(np.float32, copy=False)
        self.classifier = RandomForestClassifier(
            n_estimators=self.n_estimators,
            max_depth=20,
            min_samples_leaf=3,
            max_features="sqrt",
            class_weight="balanced_subsample",
            random_state=self.random_state,
            n_jobs=-1,
        )
        self.classifier.fit(reduced, targets)
        self.classes_ = np.asarray(self.classifier.classes_, dtype=object)
        return self

    def predict_proba(self, vectors):
        if self.pca is None or self.classifier is None:
            raise ValueError("EffNet genre head is not fitted")
        matrix = np.asarray(vectors, dtype=np.float32)
        if matrix.ndim == 1:
            matrix = matrix.reshape(1, -1)
        if matrix.shape[1] != self.embedding_dim_:
            raise ValueError(
                f"Expected EffNet vector length {self.embedding_dim_}, got {matrix.shape[1]}"
            )
        reduced = self.pca.transform(matrix).astype(np.float32, copy=False)
        return np.asarray(self.classifier.predict_proba(reduced), dtype=float)

    def aligned_probabilities(self, vectors, target_classes):
        return align_probabilities(
            self.predict_proba(vectors), self.classes_, target_classes
        )


def available_embedding_rows(paths, embeddings, indices=None):
    """Return local positions and a compact float32 matrix for known paths."""
    positions = []
    rows = []
    source_indices = range(len(paths)) if indices is None else indices
    for local_position, source_index in enumerate(source_indices):
        path = str(paths[int(source_index)])
        vector = embeddings.get(path)
        if vector is None:
            vector = embeddings.get(os.path.abspath(path))
        if vector is None:
            continue
        positions.append(local_position)
        rows.append(np.asarray(vector, dtype=np.float32).reshape(-1))
    if not rows:
        return np.asarray([], dtype=int), np.empty((0, 0), dtype=np.float32)
    expected = rows[0].size
    valid_positions = []
    valid_rows = []
    for position, row in zip(positions, rows):
        if row.size == expected:
            valid_positions.append(position)
            valid_rows.append(row)
    return np.asarray(valid_positions, dtype=int), np.vstack(valid_rows)


def fuse_available_rows(acoustic, target_classes, head, vectors, positions, alpha):
    """Fuse only rows with a deep vector; missing rows stay acoustic-only."""
    result = np.asarray(acoustic, dtype=float).copy()
    positions = np.asarray(positions, dtype=int)
    if not positions.size:
        return result
    deep = head.aligned_probabilities(vectors, target_classes)
    result[positions] = fuse_probabilities(result[positions], deep, alpha)
    return result
