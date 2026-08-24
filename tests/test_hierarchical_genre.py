import numpy as np
import pytest

from app.hierarchical_genre import (
    HierarchicalGenreClassifier,
    family_decision,
    family_for_style,
)


class FixedModel:
    def __init__(self, classes, probabilities):
        self.classes_ = np.asarray(classes, dtype=object)
        self.probabilities = np.asarray(probabilities, dtype=float)
        self.n_features_in_ = 2

    def predict_proba(self, features):
        return np.tile(self.probabilities, (len(features), 1))


def test_family_mapping_keeps_overlapping_house_styles_together():
    assert family_for_style("Club House") == "House"
    assert family_for_style("Tech House") == "House"
    assert family_for_style("Hip-Hop") == "Urban"
    assert family_for_style("Drum & Bass") == "Bass"
    assert family_for_style("Moombahton") == "Electronic / Club"
    assert family_for_style("Disco") == "Disco / Funk"


def test_hierarchy_conditions_style_probability_on_family():
    flat = FixedModel(
        ["Club House", "Tech House", "Hip-Hop"],
        [0.30, 0.25, 0.45],
    )
    family = FixedModel(["House", "Urban"], [0.85, 0.15])
    house = FixedModel(["Club House", "Tech House"], [0.80, 0.20])
    classifier = HierarchicalGenreClassifier(
        flat, family_model=family, subtype_models={"House": house},
        hierarchy_weight=0.8,
    )
    probabilities = classifier.predict_proba(np.ones((1, 2)))[0]
    result = dict(zip(classifier.classes_, probabilities))
    assert result["Club House"] > result["Hip-Hop"]
    assert abs(float(np.sum(probabilities)) - 1.0) < 1e-9


def test_family_decision_averages_segments():
    flat = FixedModel(["Club House", "Hip-Hop"], [0.5, 0.5])
    family = FixedModel(["House", "Urban"], [0.72, 0.28])
    classifier = HierarchicalGenreClassifier(flat, family_model=family)
    decision = family_decision(classifier, np.ones((3, 2)))
    assert decision["family"] == "House"
    assert decision["confidence"] == pytest.approx(0.72)
