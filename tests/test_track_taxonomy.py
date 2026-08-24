import sys
import pickle
from collections import Counter
from pathlib import Path

import numpy as np


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app import librosa_settings, models  # noqa: E402
from app.models import (  # noqa: E402
    _build_training_groups,
    _balanced_track_indices,
    _eligible_base_genres,
    _expand_track_segments,
    _expected_audio_feature_length,
    _predict_track_probabilities,
    _three_way_grouped_indices,
    extract_features,
)
from app.track_taxonomy import (  # noqa: E402
    infer_language_from_path,
    parse_track_taxonomy,
    taxonomy_from_training_label,
    track_group_key,
)


class FixedProbabilityModel:
    def __init__(self, classes, probabilities):
        self.classes_ = np.asarray(classes)
        self.probabilities = np.asarray(probabilities, dtype=float)
        self.n_features_in_ = 4

    def predict_proba(self, features):
        return np.tile(self.probabilities, (len(features), 1))


class FeatureProbabilityModel:
    classes_ = np.asarray(["A", "B"])

    def predict_proba(self, features):
        features = np.asarray(features, dtype=float)
        first = np.clip(features[:, 0], 0.0, 1.0)
        return np.column_stack([first, 1.0 - first])


class FeatureGenreProbabilityModel:
    classes_ = np.asarray(["Club House", "Hip-Hop"])
    n_features_in_ = 4

    def predict_proba(self, features):
        features = np.asarray(features, dtype=float)
        first = np.clip(features[:, 0], 0.0, 1.0)
        return np.column_stack([first, 1.0 - first])


def test_russian_remix_without_style_uses_club_house_bucket():
    taxonomy = parse_track_taxonomy(raw_genre="Russian Remix")

    assert taxonomy.base_genre == "Club House"
    assert taxonomy.genre_family == "House"
    assert taxonomy.language == "Russian"
    assert taxonomy.version_type == "Remix"
    assert taxonomy.dj_category == "Русские Ремиксы"


def test_explicit_style_has_priority_over_russian_remix_bucket():
    dnb = parse_track_taxonomy(raw_genre="DrumNBass, Russian, Remix")
    hip_hop = parse_track_taxonomy(raw_genre="HIP-HOP, Russian")

    assert (dnb.base_genre, dnb.language, dnb.dj_category) == (
        "Drum & Bass", "Russian", "Drum & Bass",
    )
    assert (hip_hop.base_genre, hip_hop.language, hip_hop.dj_category) == (
        "Hip-Hop", "Russian", "Hip-Hop",
    )
    assert dnb.genre_family == "Bass"
    assert hip_hop.genre_family == "Urban"


def test_stage2_styles_have_stable_families():
    assert parse_track_taxonomy(fallback_genre="Deep House").genre_family == "House"
    assert parse_track_taxonomy(fallback_genre="Tech House").genre_family == "House"
    assert parse_track_taxonomy(fallback_genre="Disco").genre_family == "Disco / Funk"
    assert parse_track_taxonomy(fallback_genre="Moombahton").genre_family == "Electronic / Club"
    assert parse_track_taxonomy(fallback_genre="Moombahcore").genre_family == "Electronic / Club"
    assert parse_track_taxonomy(fallback_genre="Reggaeton").genre_family == "Latin"
    assert parse_track_taxonomy(fallback_genre="Pop").genre_family == "Pop / Commercial"


def test_foreign_house_and_mood_are_separate_axes():
    taxonomy = parse_track_taxonomy(raw_genre="Club House, ENG, Light, Ставим")

    assert taxonomy.base_genre == "Club House"
    assert taxonomy.language == "English"
    assert taxonomy.dj_category == "Club House"
    assert taxonomy.mood == "Light, Ставим"


def test_cyrillic_russian_label_is_recognised():
    taxonomy = parse_track_taxonomy(raw_genre="Русские Ремиксы")

    assert taxonomy.base_genre == "Club House"
    assert taxonomy.language == "Russian"
    assert taxonomy.dj_category == "Русские Ремиксы"


def test_blend_and_bootleg_are_preserved_as_distinct_version_types():
    blend = parse_track_taxonomy(
        fallback_genre="Club House",
        title="Artist - Track (DJ Blend)",
    )
    bootleg = parse_track_taxonomy(
        fallback_genre="Drum & Bass",
        title="Artist - Track (Club Bootleg)",
    )

    assert blend.version_type == "Blend"
    assert bootleg.version_type == "Bootleg"


def test_training_folder_does_not_force_non_russian_class_to_foreign():
    russian_dnb = taxonomy_from_training_label(
        "Drum & Bass",
        "Артист - Русская песня (DnB Remix).mp3",
    )
    unknown_dnb = taxonomy_from_training_label(
        "Drum & Bass",
        "Artist - Track (DnB Remix).mp3",
    )

    assert russian_dnb.language == "Russian"
    assert unknown_dnb.language == "Unknown"


def test_club_house_folder_and_explicit_path_markers_supply_language_labels():
    foreign_club = taxonomy_from_training_label(
        "Club House",
        r"D:\samples\Club House\Artist - Track.mp3",
    )
    russian_club = taxonomy_from_training_label(
        "Club House",
        r"D:\samples\Club House\Артист - Песня.mp3",
    )
    foreign_dnb = taxonomy_from_training_label(
        "Drum & Bass",
        r"Z:\library\EURO\Artist - Track.mp3",
    )

    assert foreign_club.language == "Foreign"
    assert russian_club.language == "Russian"
    assert foreign_dnb.language == "Foreign"
    assert infer_language_from_path(r"Z:\library\EVRO\Artist - Track.mp3") == "Foreign"
    assert infer_language_from_path(r"Z:\library\ENG\Artist - Track.mp3") == "English"
    assert infer_language_from_path(r"Z:\library\Russian\Artist - Track.mp3") == "Russian"


def test_track_versions_get_same_name_group():
    original = track_group_key("Artist - Song (Original Mix).mp3")
    remix = track_group_key("Artist - Song (DJ Example Remix 128 BPM).mp3")

    assert original == remix == "artist song"


def test_grouped_three_way_split_has_no_group_leakage():
    labels = np.asarray(["A", "B"] * 20)
    groups = np.asarray([f"track-{index // 2}" for index in range(40)], dtype=object)
    features = np.arange(40 * 4, dtype=float).reshape(40, 4)

    train, threshold, validation = _three_way_grouped_indices(
        features,
        labels,
        groups,
        holdout_fraction=0.25,
        random_state=42,
    )

    train_groups = set(groups[train])
    threshold_groups = set(groups[threshold])
    validation_groups = set(groups[validation])
    assert train_groups.isdisjoint(threshold_groups)
    assert train_groups.isdisjoint(validation_groups)
    assert threshold_groups.isdisjoint(validation_groups)
    assert set(labels[train]) == {"A", "B"}
    assert set(labels[threshold]) == {"A", "B"}
    assert set(labels[validation]) == {"A", "B"}


def test_rare_or_disabled_base_styles_are_filtered_before_balancing():
    labels = np.asarray(
        ["Club House"] * 160
        + ["Drum & Bass"] * 120
        + ["Hip-Hop"] * 100
        + ["Disco"] * 3
        + ["Lounge"],
    )

    eligible, skipped, counts = _eligible_base_genres(
        labels,
        {"Русские Ремиксы", "Club House", "Drum & Bass", "Hip-Hop", "Disco"},
        min_tracks_per_genre=80,
    )

    assert eligible == {"Club House", "Drum & Bass", "Hip-Hop"}
    assert skipped == {"Disco": 3, "Lounge": 1}
    assert counts["Club House"] == 160


def test_balancing_limits_large_class_at_track_level():
    labels = np.asarray(["Club House"] * 200 + ["Drum & Bass"] * 100 + ["Hip-Hop"] * 120)

    indices, class_limit = _balanced_track_indices(labels, max_class_ratio=1.5, random_state=42)
    balanced = Counter(labels[indices])

    assert class_limit == 150
    assert balanced == Counter({"Club House": 150, "Hip-Hop": 120, "Drum & Bass": 100})


def test_segment_rows_keep_track_groups_and_probabilities_are_aggregated():
    segments = [
        [np.asarray([0.9, 1.0]), np.asarray([0.1, 2.0])],
        [np.asarray([0.8, 3.0]), np.asarray([0.7, 4.0])],
        [np.asarray([0.6, 5.0])],
    ]
    labels = np.asarray(["A", "A", "B"])
    groups = np.asarray(["track-1", "track-2", "track-3"])

    rows, expanded_labels, expanded_groups = _expand_track_segments(
        segments,
        labels,
        groups,
        np.asarray([0, 2]),
    )
    probabilities, disagreement = _predict_track_probabilities(
        FeatureProbabilityModel(),
        segments,
        np.asarray([0, 1]),
    )

    assert rows.shape == (3, 2)
    assert expanded_labels.tolist() == ["A", "A", "B"]
    assert expanded_groups.tolist() == ["track-1", "track-1", "track-3"]
    assert np.allclose(probabilities[0], [0.5, 0.5])
    assert np.allclose(probabilities[1], [0.75, 0.25])
    assert disagreement.tolist() == [True, False]


def test_one_different_segment_does_not_trigger_blanket_disagreement_penalty():
    segments = [[
        np.asarray([0.9, 1.0]),
        np.asarray([0.8, 1.0]),
        np.asarray([0.1, 1.0]),
    ]]

    _probabilities, disagreement = _predict_track_probabilities(
        FeatureProbabilityModel(),
        segments,
        np.asarray([0]),
    )

    assert disagreement.tolist() == [False]


def test_feature_oom_rejects_vector_instead_of_saving_zero_placeholders(monkeypatch):
    params = {
        "n_mfcc": 6,
        "features": {
            "mfcc": True,
            "delta_mfcc": True,
            "delta2_mfcc": True,
            "mfcc_std": True,
            "rms": True,
            "silence_ratio": True,
            "energy_entropy": True,
            "energy_ratio": True,
        },
    }

    def fail_feature(*_args, **_kwargs):
        raise MemoryError("synthetic allocation failure")

    monkeypatch.setattr(models.librosa.feature, "mfcc", fail_feature)
    monkeypatch.setattr(models.librosa.feature, "rms", fail_feature)
    result = extract_features(np.zeros(4096, dtype=float), 22050, params)

    assert isinstance(result, tuple)
    features, error = result
    assert features.size == 0
    assert error.startswith("MemoryError:")


def test_name_and_audio_duplicate_links_form_one_component(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "TRAINING_DUPLICATES_FILE", tmp_path / "duplicates.csv")
    paths = [
        "Artist - Song (Original Mix).mp3",
        "Artist - Song (Remix).mp3",
        "Completely Different Name.mp3",
    ]
    features = np.asarray([
        [0.0, 0.0, 1.0, 8.0],
        [0.0, 2.0, 7.0, 9.0],
        [0.0, 2.0, 7.0, 9.0],
    ])

    groups = _build_training_groups(paths, features, ["A", "A", "A"])

    assert len(set(groups)) == 1
    assert models.TRAINING_DUPLICATES_FILE.exists()


def test_v3_runtime_rejects_disagreeing_segments_with_penalty(tmp_path, monkeypatch):
    audio_file = tmp_path / "new_track.mp3"
    audio_file.write_bytes(b"")
    model_file = tmp_path / "taxonomy_model.pkl"
    with model_file.open("wb") as stream:
        pickle.dump({
            "version": "3.1-segment-policy",
            "model": FeatureGenreProbabilityModel(),
            "expected_feature_len": 4,
            "class_thresholds": {"Club House": 0.55, "Hip-Hop": 0.55},
            "librosa_params": {
                "genre_threshold": 0.55,
                "segment_disagreement_penalty": 0.1,
                "min_genre_margin": 0.1,
                "yamnet_enabled": False,
                "use_id3": False,
                "features": {},
            },
        }, stream)

    monkeypatch.setattr(models, "MODEL_PATH", str(model_file))
    monkeypatch.setattr(models, "get_advanced_mode", lambda: True)
    monkeypatch.setattr(models, "load_genre_settings", lambda: {})
    monkeypatch.setattr(librosa_settings, "load_librosa_settings", lambda: {
        "genre_threshold": 0.55,
        "segment_disagreement_penalty": 0.1,
        "min_genre_margin": 0.1,
        "yamnet_enabled": False,
    })
    monkeypatch.setattr(models, "_extract_multisegment_features", lambda *_args, **_kwargs: (
        np.asarray([0.6, 0.0, 0.0, 0.0]),
        [
            np.asarray([0.8, 0.0, 0.0, 0.0]),
            np.asarray([0.2, 0.0, 0.0, 0.0]),
            np.asarray([0.8, 0.0, 0.0, 0.0]),
        ],
        [(np.zeros(100), 22050, 0.0)] * 3,
        [],
    ))

    result = models.get_genre(str(audio_file), return_meta=True)

    assert result[0] == "Unknown"
    assert result[3]["segment_disagreement"] is True
    assert result[3]["decision_threshold"] == 0.65


def test_runtime_prefers_vocal_language_over_rf_fallback(tmp_path, monkeypatch):
    audio_file = tmp_path / "new_track.mp3"
    audio_file.write_bytes(b"")
    model_file = tmp_path / "taxonomy_model.pkl"
    base_model = FixedProbabilityModel(["Club House", "Hip-Hop"], [0.8, 0.2])
    language_model = FixedProbabilityModel(["Foreign", "Russian"], [0.9, 0.1])
    with model_file.open("wb") as stream:
        pickle.dump({
            "version": "3.1-segment-policy",
            "model": base_model,
            "language_model": language_model,
            "expected_feature_len": 4,
            "class_thresholds": {"Club House": 0.55, "Hip-Hop": 0.55},
            "librosa_params": {
                "genre_threshold": 0.55,
                "min_genre_margin": 0.1,
                "yamnet_enabled": False,
                "use_id3": False,
                "features": {},
            },
        }, stream)

    monkeypatch.setattr(models, "MODEL_PATH", str(model_file))
    monkeypatch.setattr(models, "get_advanced_mode", lambda: True)
    monkeypatch.setattr(models, "load_genre_settings", lambda: {})
    monkeypatch.setattr(librosa_settings, "load_librosa_settings", lambda: {
        "genre_threshold": 0.55,
        "min_genre_margin": 0.1,
        "yamnet_enabled": False,
        "vocal_language_enabled": True,
        "vocal_language_rf_fallback": True,
    })
    monkeypatch.setattr(models, "_extract_multisegment_features", lambda *_args, **_kwargs: (
        np.zeros(4),
        [np.zeros(4)],
        [(np.zeros(100), 22050, 0.0)],
        [],
    ))
    monkeypatch.setattr(models, "detect_vocal_language", lambda *_args, **_kwargs: {
        "language": "English",
        "confidence": 0.93,
        "source": "faster-whisper",
        "status": "accepted",
    })

    result = models.get_genre(str(audio_file), return_meta=True)
    taxonomy = result[3]["taxonomy"]

    assert result[0] == "Club House"
    assert taxonomy["language"] == "English"
    assert taxonomy["language_confidence"] == 0.93
    assert taxonomy["language_source"] == "vocal"
    assert taxonomy["language_probabilities"] is None

    monkeypatch.setattr(
        models,
        "detect_vocal_language",
        lambda *_args, **_kwargs: pytest.fail("Whisper must be deferred during library indexing"),
    )
    deferred = models.get_genre(
        str(audio_file),
        return_meta=True,
        defer_vocal_language=True,
    )
    deferred_taxonomy = deferred[3]["taxonomy"]
    assert deferred[0] == "Club House"
    assert deferred_taxonomy["language"] == "Unknown"
    assert deferred_taxonomy["language_source"] == "pending_vocal"
    assert deferred_taxonomy["provisional_language"] == "Foreign"
    assert deferred_taxonomy["provisional_language_source"] == "rf"


def test_runtime_manual_correction_overrides_rejected_audio_decision(tmp_path, monkeypatch):
    audio_file = tmp_path / "borderline_blend.mp3"
    audio_file.write_bytes(b"")
    model_file = tmp_path / "taxonomy_model.pkl"
    with model_file.open("wb") as stream:
        pickle.dump({
            "version": "3.1-segment-policy",
            "model": FeatureGenreProbabilityModel(),
            "expected_feature_len": 4,
            "class_thresholds": {"Club House": 0.55, "Hip-Hop": 0.55},
            "librosa_params": {
                "genre_threshold": 0.55,
                "segment_disagreement_penalty": 0.1,
                "min_genre_margin": 0.1,
                "yamnet_enabled": False,
                "use_id3": False,
                "features": {},
            },
        }, stream)

    monkeypatch.setattr(models, "MODEL_PATH", str(model_file))
    monkeypatch.setattr(models, "get_advanced_mode", lambda: True)
    monkeypatch.setattr(models, "load_genre_settings", lambda: {})
    monkeypatch.setattr(librosa_settings, "load_librosa_settings", lambda: {
        "genre_threshold": 0.55,
        "segment_disagreement_penalty": 0.1,
        "min_genre_margin": 0.1,
        "yamnet_enabled": False,
    })
    monkeypatch.setattr(models, "_extract_multisegment_features", lambda *_args, **_kwargs: (
        np.asarray([0.6, 0.0, 0.0, 0.0]),
        [
            np.asarray([0.8, 0.0, 0.0, 0.0]),
            np.asarray([0.2, 0.0, 0.0, 0.0]),
            np.asarray([0.8, 0.0, 0.0, 0.0]),
        ],
        [(np.zeros(100), 22050, 0.0)] * 3,
        [],
    ))
    monkeypatch.setattr(models, "get_manual_correction", lambda _path: {
        "id": "manual",
        "status": "corrected",
        "corrected_base_genre": "Drum & Bass",
        "corrected_language": "English",
        "corrected_version_type": "Blend",
    })

    result = models.get_genre(str(audio_file), return_meta=True)
    taxonomy = result[3]["taxonomy"]

    assert result[0] == "Drum & Bass"
    assert taxonomy["base_genre_source"] == "manual_correction"
    assert taxonomy["language"] == "English"
    assert taxonomy["language_source"] == "manual_correction"
    assert taxonomy["version_type"] == "Blend"
    assert result[3]["acoustic_prediction"] == "Club House"


def test_v3_runtime_applies_individual_language_threshold(tmp_path, monkeypatch):
    audio_file = tmp_path / "new_track.mp3"
    audio_file.write_bytes(b"")
    model_file = tmp_path / "taxonomy_model.pkl"
    base_model = FixedProbabilityModel(["Club House", "Drum & Bass"], [0.8, 0.2])
    language_model = FixedProbabilityModel(["Foreign", "Russian"], [0.45, 0.55])
    with model_file.open("wb") as stream:
        pickle.dump({
            "version": "3.0-segment-taxonomy",
            "model": base_model,
            "language_model": language_model,
            "expected_feature_len": 4,
            "class_thresholds": {"Club House": 0.5, "Drum & Bass": 0.5},
            "language_class_thresholds": {"Foreign": 0.5, "Russian": 0.6},
            "librosa_params": {
                "genre_threshold": 0.5,
                "min_genre_margin": 0.1,
                "language_threshold": 0.5,
                "yamnet_enabled": False,
                "use_id3": False,
                "features": {},
            },
        }, stream)

    monkeypatch.setattr(models, "MODEL_PATH", str(model_file))
    monkeypatch.setattr(models, "get_advanced_mode", lambda: True)
    monkeypatch.setattr(models, "load_genre_settings", lambda: {})
    monkeypatch.setattr(librosa_settings, "load_librosa_settings", lambda: {
        "genre_threshold": 0.5,
        "min_genre_margin": 0.1,
        "language_threshold": 0.5,
        "yamnet_enabled": False,
    })
    monkeypatch.setattr(models, "_extract_multisegment_features", lambda *_args, **_kwargs: (
        np.zeros(4),
        [np.zeros(4)],
        [(np.zeros(100), 22050, 0.0)],
        [],
    ))

    result = models.get_genre(str(audio_file), return_meta=True)
    taxonomy = result[3]["taxonomy"]

    assert result[0] == "Club House"
    assert taxonomy["base_genre"] == "Club House"
    assert taxonomy["language"] == "Unknown"
    assert taxonomy["language_confidence"] == 0.55


def test_v2_runtime_combines_base_style_and_predicted_language(tmp_path, monkeypatch):
    audio_file = tmp_path / "new_track.mp3"
    audio_file.write_bytes(b"")
    model_file = tmp_path / "taxonomy_model.pkl"
    base_model = FixedProbabilityModel(["Club House", "Drum & Bass"], [0.8, 0.2])
    language_model = FixedProbabilityModel(["Foreign", "Russian"], [0.1, 0.9])
    with model_file.open("wb") as stream:
        pickle.dump({
            "version": "3.0-segment-taxonomy",
            "model": base_model,
            "language_model": language_model,
            "expected_feature_len": 4,
            "class_thresholds": {"Club House": 0.5, "Drum & Bass": 0.5},
            "librosa_params": {
                "genre_threshold": 0.5,
                "min_genre_margin": 0.1,
                "language_threshold": 0.6,
                "yamnet_enabled": False,
                "use_id3": False,
                "features": {},
            },
        }, stream)

    monkeypatch.setattr(models, "MODEL_PATH", str(model_file))
    monkeypatch.setattr(models, "get_advanced_mode", lambda: True)
    monkeypatch.setattr(models, "load_genre_settings", lambda: {})
    monkeypatch.setattr(librosa_settings, "load_librosa_settings", lambda: {
        "genre_threshold": 0.5,
        "min_genre_margin": 0.1,
        "language_threshold": 0.6,
        "yamnet_enabled": False,
    })
    monkeypatch.setattr(models, "_extract_multisegment_features", lambda *_args, **_kwargs: (
        np.zeros(4),
        [np.zeros(4)],
        [(np.zeros(100), 22050, 0.0)],
        [],
    ))

    result = models.get_genre(str(audio_file), return_meta=True)
    taxonomy = result[3]["taxonomy"]

    assert result[0] == "Русские Ремиксы"
    assert result[1] == 0.8
    assert taxonomy["base_genre"] == "Club House"
    assert taxonomy["language"] == "Russian"
    assert taxonomy["dj_category"] == "Русские Ремиксы"
