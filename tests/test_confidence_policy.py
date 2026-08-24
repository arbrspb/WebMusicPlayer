import sys
from pathlib import Path

import numpy as np


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app.models import (  # noqa: E402
    _calculate_class_thresholds,
    _evaluate_rejection_policy,
    extract_features_from_track,
)


def test_class_thresholds_and_unknown_policy():
    classes = np.array(["A", "B"])
    y_true = np.array(["A"] * 5 + ["B"] * 5)
    probabilities = np.array([
        [.80, .20], [.75, .25], [.70, .30], [.65, .35], [.60, .40],
        [.20, .80], [.25, .75], [.30, .70], [.35, .65], [.40, .60],
    ])

    thresholds, diagnostics = _calculate_class_thresholds(
        y_true,
        probabilities,
        classes,
        target_precision=.90,
        fallback_threshold=.50,
        min_margin=.10,
        min_predictions=2,
    )
    report = _evaluate_rejection_policy(
        y_true,
        probabilities,
        classes,
        thresholds,
        fallback_threshold=.50,
        min_margin=.10,
    )

    assert thresholds == {"A": .6, "B": .6}
    assert all(item["status"] == "target_reached" for item in diagnostics.values())
    assert report["accepted_precision"] == 1.0
    assert report["coverage"] == 1.0


def test_auto_threshold_never_drops_below_configured_floor():
    classes = np.array(["A", "B"])
    y_true = np.array(["A"] * 4 + ["B"] * 4)
    probabilities = np.array([
        [.70, .30], [.65, .35], [.60, .40], [.58, .42],
        [.30, .70], [.35, .65], [.40, .60], [.42, .58],
    ])

    thresholds, diagnostics = _calculate_class_thresholds(
        y_true,
        probabilities,
        classes,
        target_precision=.90,
        fallback_threshold=.55,
        min_margin=.10,
        min_predictions=2,
    )

    assert thresholds == {"A": .58, "B": .58}
    assert all(item["minimum_threshold"] == .55 for item in diagnostics.values())


def test_auto_threshold_maximizes_coverage_after_runtime_floor():
    classes = np.array(["A", "B"])
    y_true = np.array(["A", "A", "B", "A", "A"])
    probabilities = np.array([
        [.50, .50],
        [.54, .46],
        [.60, .40],
        [.65, .35],
        [.80, .20],
    ])

    thresholds, diagnostics = _calculate_class_thresholds(
        y_true,
        probabilities,
        classes,
        target_precision=.90,
        fallback_threshold=.55,
        min_margin=.0,
        min_predictions=2,
    )

    # At .55 the accepted A subset has precision 2/3.  The lowest threshold
    # that really satisfies the policy is .65, which also has maximum coverage
    # among valid candidates (.65 accepts 2, .80 only 1).
    assert thresholds["A"] == .65
    assert diagnostics["A"]["validation_precision"] == 1.0
    assert diagnostics["A"]["accepted_validation_tracks"] == 2


def test_unreachable_precision_is_reported_instead_of_fake_fallback():
    classes = np.array(["A", "B"])
    y_true = np.array(["B", "B", "A", "A", "B", "B"])
    probabilities = np.array([
        [.90, .10], [.85, .15], [.80, .20],
        [.20, .80], [.15, .85], [.10, .90],
    ])

    thresholds, diagnostics = _calculate_class_thresholds(
        y_true,
        probabilities,
        classes,
        target_precision=.90,
        fallback_threshold=.55,
        min_margin=.10,
        min_predictions=2,
    )

    assert thresholds == {"A": .99, "B": .85}
    assert diagnostics["A"]["status"] == "precision_unreachable"
    assert "best_available_precision" in diagnostics["A"]
    assert diagnostics["B"]["status"] == "target_reached"


def test_segment_disagreement_raises_decision_threshold():
    classes = np.array(["A", "B"])
    y_true = np.array(["A", "A", "B"])
    probabilities = np.array([
        [.60, .40],
        [.80, .20],
        [.20, .80],
    ])
    disagreements = np.array([True, False, False])

    report = _evaluate_rejection_policy(
        y_true,
        probabilities,
        classes,
        {"A": .55, "B": .55},
        fallback_threshold=.55,
        min_margin=.10,
        segment_disagreement=disagreements,
        segment_disagreement_penalty=.10,
    )

    assert report["accepted_tracks"] == 2
    assert report["unknown_tracks"] == 1
    assert report["accepted_precision"] == 1.0
    assert report["disagreement_tracks"] == 1


def test_rekordbox_preferences_do_not_leak_into_genre_features():
    audio_features = np.arange(4, dtype=float)
    track = {"rating": 5, "bpm": 128, "color": "Кач", "situation": "Ставим"}

    genre_features = extract_features_from_track(track, audio_features)
    recommendation_features = extract_features_from_track(
        track,
        audio_features,
        include_metadata=True,
    )

    assert np.array_equal(genre_features[-4:], np.array([0.0, 0.0, -1.0, 0.0]))
    assert not np.array_equal(recommendation_features[-4:], genre_features[-4:])
