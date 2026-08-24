"""Compare flat and family-first genre classification on a saved candidate model.

This diagnostic reuses the aggregate features saved by a training run, so it
does not read the audio collection again and never overwrites genre_model.pkl.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import StratifiedGroupKFold


def _classifier() -> RandomForestClassifier:
    return RandomForestClassifier(
        n_estimators=300,
        max_depth=24,
        min_samples_leaf=2,
        max_features=0.5,
        class_weight="balanced_subsample",
        random_state=42,
        n_jobs=-1,
    )


def _load_dataset(model_path: Path):
    meta = joblib.load(model_path)
    feature_cache = meta["train_features_dict"]
    keys = np.asarray(meta["norm_keys_list"], dtype=object)
    labels = np.asarray(meta["labels"], dtype=object)
    groups = np.asarray(meta["training_groups"], dtype=object)
    taxonomies = np.asarray(meta["training_taxonomies"], dtype=object)

    usable = np.asarray([key in feature_cache for key in keys], dtype=bool)
    x = np.asarray([feature_cache[key] for key in keys[usable]], dtype=float)
    y = labels[usable]
    group_values = groups[usable]
    families = np.asarray(
        [taxonomy.get("genre_family", "Other") for taxonomy in taxonomies[usable]],
        dtype=object,
    )
    return x, y, families, group_values


def evaluate(model_path: Path, folds: int = 3) -> dict:
    x, y, families, groups = _load_dataset(model_path)
    splitter = StratifiedGroupKFold(
        n_splits=folds,
        shuffle=True,
        random_state=42,
    )
    flat_predictions = np.empty(len(y), dtype=object)
    hierarchical_predictions = np.empty(len(y), dtype=object)
    family_predictions = np.empty(len(y), dtype=object)

    for train_index, test_index in splitter.split(x, y, groups):
        flat_model = _classifier()
        flat_model.fit(x[train_index], y[train_index])
        flat_predictions[test_index] = flat_model.predict(x[test_index])

        family_model = _classifier()
        family_model.fit(x[train_index], families[train_index])
        predicted_families = family_model.predict(x[test_index])
        family_predictions[test_index] = predicted_families

        family_submodels = {}
        family_defaults = {}
        for family in np.unique(families[train_index]):
            family_train = train_index[families[train_index] == family]
            family_counts = Counter(y[family_train])
            family_defaults[family] = family_counts.most_common(1)[0][0]
            if len(family_counts) < 2:
                continue
            submodel = _classifier()
            submodel.fit(x[family_train], y[family_train])
            family_submodels[family] = submodel

        for family in np.unique(predicted_families):
            local_positions = np.flatnonzero(predicted_families == family)
            source_positions = test_index[local_positions]
            submodel = family_submodels.get(family)
            if submodel is None:
                predicted_style = family_defaults.get(family, Counter(y[train_index]).most_common(1)[0][0])
                hierarchical_predictions[source_positions] = predicted_style
            else:
                hierarchical_predictions[source_positions] = submodel.predict(x[source_positions])

    labels = sorted(np.unique(y).tolist())
    family_labels = sorted(np.unique(families).tolist())
    return {
        "source_model": str(model_path),
        "tracks": int(len(y)),
        "groups": int(len(set(groups.tolist()))),
        "folds": folds,
        "class_distribution": dict(Counter(y)),
        "family_distribution": dict(Counter(families)),
        "flat": {
            "accuracy": float(accuracy_score(y, flat_predictions)),
            "report": classification_report(
                y, flat_predictions, labels=labels, output_dict=True, zero_division=0
            ),
        },
        "hierarchical": {
            "accuracy": float(accuracy_score(y, hierarchical_predictions)),
            "report": classification_report(
                y,
                hierarchical_predictions,
                labels=labels,
                output_dict=True,
                zero_division=0,
            ),
        },
        "family": {
            "accuracy": float(accuracy_score(families, family_predictions)),
            "report": classification_report(
                families,
                family_predictions,
                labels=family_labels,
                output_dict=True,
                zero_division=0,
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "model",
        nargs="?",
        type=Path,
        default=Path("genre_model.stage2_2.rejected.pkl"),
    )
    parser.add_argument("--folds", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.model, args.folds), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
