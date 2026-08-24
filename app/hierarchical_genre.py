"""Hierarchical acoustic genre classification.

The public wrapper intentionally follows the small subset of the sklearn
classifier API used by the application.  It keeps the flat classifier as a
robust prior and combines it with two conditional decisions:

    audio -> genre family -> style inside the predicted family

This is especially useful for the overlapping House subclasses while staying
pickle-compatible with the existing model file and inference code.
"""
from __future__ import annotations

from collections import Counter

import numpy as np

from .track_taxonomy import genre_family


def family_for_style(style):
    value = str(style or "Other")
    family = str(genre_family(value) or "Other")
    return family


def _normalise_rows(values):
    matrix = np.nan_to_num(np.asarray(values, dtype=float), copy=False)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    sums = matrix.sum(axis=1, keepdims=True)
    return np.divide(
        matrix,
        sums,
        out=np.full_like(matrix, 1.0 / max(1, matrix.shape[1])),
        where=sums > 1e-12,
    )


class HierarchicalGenreClassifier:
    """Fitted flat + family + per-family classifiers with sklearn-like API."""

    def __init__(
            self,
            flat_model,
            family_model=None,
            subtype_models=None,
            hierarchy_weight=0.72,
    ):
        self.flat_model = flat_model
        self.family_model = family_model
        self.subtype_models = dict(subtype_models or {})
        self.hierarchy_weight = min(max(float(hierarchy_weight), 0.0), 1.0)
        self.classes_ = np.asarray(getattr(flat_model, "classes_", []), dtype=object)
        self.n_features_in_ = getattr(flat_model, "n_features_in_", None)
        self.family_by_class_ = np.asarray(
            [family_for_style(value) for value in self.classes_], dtype=object
        )

    def _flat_probabilities(self, features):
        return _normalise_rows(self.flat_model.predict_proba(features))

    def predict_family_proba(self, features):
        if self.family_model is not None:
            probabilities = _normalise_rows(self.family_model.predict_proba(features))
            return probabilities, np.asarray(self.family_model.classes_, dtype=object)

        flat = self._flat_probabilities(features)
        families = np.asarray(sorted(set(self.family_by_class_.tolist())), dtype=object)
        result = np.zeros((flat.shape[0], len(families)), dtype=float)
        for family_index, family in enumerate(families):
            result[:, family_index] = flat[:, self.family_by_class_ == family].sum(axis=1)
        return _normalise_rows(result), families

    def predict_proba(self, features):
        flat = self._flat_probabilities(features)
        if self.family_model is None or self.classes_.size < 2:
            return flat

        family_probabilities, family_classes = self.predict_family_proba(features)
        hierarchical = np.zeros_like(flat)
        for family_index, family_value in enumerate(family_classes):
            family = str(family_value)
            member_indices = np.flatnonzero(self.family_by_class_ == family)
            if not member_indices.size:
                continue
            family_mass = family_probabilities[:, family_index]
            subtype_model = self.subtype_models.get(family)
            if subtype_model is not None:
                subtype_raw = _normalise_rows(subtype_model.predict_proba(features))
                subtype_classes = [str(value) for value in subtype_model.classes_]
                conditional = np.zeros((flat.shape[0], member_indices.size), dtype=float)
                for local_index, global_index in enumerate(member_indices):
                    style = str(self.classes_[global_index])
                    if style in subtype_classes:
                        conditional[:, local_index] = subtype_raw[:, subtype_classes.index(style)]
                conditional = _normalise_rows(conditional)
            else:
                conditional = _normalise_rows(flat[:, member_indices])
            hierarchical[:, member_indices] = conditional * family_mass[:, None]

        hierarchical = _normalise_rows(hierarchical)
        weight = self.hierarchy_weight
        return _normalise_rows((1.0 - weight) * flat + weight * hierarchical)

    def predict(self, features):
        probabilities = self.predict_proba(features)
        return self.classes_[np.argmax(probabilities, axis=1)]


def fit_hierarchical_classifier(
        flat_model,
        features,
        labels,
        model_factory,
        *,
        hierarchy_weight=0.72,
        minimum_family_samples=20,
):
    """Fit family/subtype heads on the same features as a fitted flat model."""
    features = np.asarray(features, dtype=float)
    labels = np.asarray(labels, dtype=object)
    family_labels = np.asarray([family_for_style(value) for value in labels], dtype=object)
    family_counts = Counter(str(value) for value in family_labels)
    style_counts = Counter(str(value) for value in labels)
    report = {
        "enabled": False,
        "hierarchy_weight": float(hierarchy_weight),
        "family_counts": dict(sorted(family_counts.items())),
        "style_counts": dict(sorted(style_counts.items())),
        "subtype_families": {},
        "skipped_families": {},
    }

    unique_families = sorted(family_counts)
    if len(unique_families) < 2:
        report["reason"] = "недостаточно семейств"
        return HierarchicalGenreClassifier(flat_model), report

    family_model = model_factory(family_labels)
    family_model.fit(features, family_labels)
    subtype_models = {}
    for family in unique_families:
        mask = family_labels == family
        family_styles = sorted(set(str(value) for value in labels[mask]))
        family_total = int(np.sum(mask))
        if len(family_styles) < 2:
            report["skipped_families"][family] = "один подстиль"
            continue
        if family_total < max(2, int(minimum_family_samples)):
            report["skipped_families"][family] = "мало примеров"
            continue
        subtype = model_factory(labels[mask])
        subtype.fit(features[mask], labels[mask])
        subtype_models[family] = subtype
        report["subtype_families"][family] = {
            "styles": family_styles,
            "samples": family_total,
        }

    report["enabled"] = True
    report["families"] = [str(value) for value in family_model.classes_]
    return HierarchicalGenreClassifier(
        flat_model,
        family_model=family_model,
        subtype_models=subtype_models,
        hierarchy_weight=hierarchy_weight,
    ), report


def family_decision(model, features):
    """Return averaged family decision for one track's segment matrix."""
    if not hasattr(model, "predict_family_proba"):
        return None
    probabilities, classes = model.predict_family_proba(features)
    mean = _normalise_rows(np.mean(probabilities, axis=0))[0]
    ranking = np.argsort(mean)[::-1]
    first = int(ranking[0])
    second = int(ranking[1]) if ranking.size > 1 else first
    return {
        "family": str(classes[first]),
        "confidence": float(mean[first]),
        "margin": float(mean[first] - mean[second]),
        "probabilities": {
            str(label): float(value) for label, value in zip(classes, mean)
        },
    }

