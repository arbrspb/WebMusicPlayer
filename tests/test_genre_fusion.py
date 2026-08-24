import numpy as np

from app.genre_fusion import (
    EffNetGenreHead,
    align_probabilities,
    available_embedding_rows,
    fuse_available_rows,
)


def test_align_probabilities_reorders_and_fills_missing_classes():
    actual = align_probabilities([[0.8, 0.2]], ["House", "Pop"], ["Pop", "DnB", "House"])
    assert np.allclose(actual, [[0.2, 0.0, 0.8]])


def test_effnet_head_fuses_only_available_rows():
    rng = np.random.default_rng(42)
    vectors = np.vstack([
        rng.normal(-2.0, 0.1, size=(12, 16)),
        rng.normal(2.0, 0.1, size=(12, 16)),
    ]).astype(np.float32)
    labels = np.asarray(["House"] * 12 + ["DnB"] * 12)
    head = EffNetGenreHead(pca_dimensions=8, n_estimators=80).fit(vectors, labels)
    paths = ["a.mp3", "b.mp3"]
    embeddings = {"a.mp3": vectors[0]}
    positions, known = available_embedding_rows(paths, embeddings)
    acoustic = np.asarray([[0.45, 0.55], [0.45, 0.55]])
    fused = fuse_available_rows(acoustic, ["DnB", "House"], head, known, positions, 0.35)
    assert not np.allclose(fused[0], acoustic[0])
    assert np.allclose(fused[1], acoustic[1])
    assert np.allclose(fused.sum(axis=1), 1.0)
