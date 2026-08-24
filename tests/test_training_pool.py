import time
import concurrent.futures
import json
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from app import models


def test_scan_process_pool_dependency_is_available_globally():
    assert models.concurrent.futures.ProcessPoolExecutor is concurrent.futures.ProcessPoolExecutor

from app.models import (
    TrainingPoolStalledError,
    _evaluate_training_quality_gate,
    _evaluate_active_model_on_current_validation,
    _deduplicate_items_by_exact_path,
    _effective_trainable_styles,
    _load_training_feature_cache,
    _iter_bounded_executor_results,
    _save_training_feature_cache,
    _prepare_rekordbox_training_tracks,
    _reconcile_confirmed_and_rekordbox_labels,
    _select_training_pool_sources,
    _select_progressive_training_styles,
    _strict_deduplicate_training_rows,
    _select_safe_effnet_fusion,
    _select_training_batch_workers,
    _training_feature_signature,
    _training_memory_decision,
    _build_training_pool_preflight,
)


def _double_task(task):
    return task[1] * 2


def _slow_task(task):
    time.sleep(0.08)
    return task[1]


def test_preparation_assistant_blocks_class_collapse_and_keeps_safe_folder(monkeypatch):
    monkeypatch.setattr(models, "get_training_preflight_report", lambda: {
        "minimum_tracks_per_style": 200,
        "rows": [
            {"style": "Future House", "enabled": True, "confirmed_folders": 2, "selected_tracks": 250},
            {"style": "Club House", "enabled": True, "confirmed_folders": 4, "selected_tracks": 800},
        ],
        "last_run": {"selection": {"after": 1000, "rekordbox_selected": 50, "strict_dedup": {
            "input_tracks": 1040, "dropped_duplicates": 5, "dropped_conflicts": 3,
        }}},
    })
    monkeypatch.setattr(models, "dataset_summary", lambda: {
        "style_track_counts": {"Future House": 250, "Club House": 1000},
        "status_track_counts": {"confirmed": 1250, "suggested": 20, "excluded": 10},
    })
    base_problem = {
        "risk": "high", "review_queue_tracks": 8, "validation_errors": 5,
        "disputed_percent": 12.0, "mixed_name_warning": True,
        "label_conflicts": 0, "fingerprint_duplicates": 0,
        "group_duplicate_tracks": 0,
        "confusion_pairs": [
            {"true_style": "x", "predicted_style": "y", "count": 4},
            {"true_style": "x", "predicted_style": "z", "count": 3},
        ],
    }
    monkeypatch.setattr(models, "build_training_problem_folders", lambda: {"items": [
        {**base_problem, "id": "future", "base_style": "Future House", "training_tracks": 180, "path": "Future"},
        {**base_problem, "id": "club", "base_style": "Club House", "training_tracks": 200, "path": "Club"},
    ]})

    preview = models.get_training_preparation_assistant()
    assert preview["safe_folder_ids"] == ["club"]
    assert preview["blocked_styles"] == ["Future House"]
    future = next(row for row in preview["styles"] if row["style"] == "Future House")
    assert future["training_tracks_after"] == 70
    assert "заблокировано" in future["warning"]
    assert preview["pipeline_automatic"]["processing_errors_last_run"] == 10


def test_preparation_assistant_apply_accepts_only_fresh_safe_preview(monkeypatch):
    preview = {"preview_token": "fresh", "safe_folder_ids": ["safe-folder"]}
    monkeypatch.setattr(models, "get_training_preparation_assistant", lambda: preview)
    applied = []
    monkeypatch.setattr(models, "update_training_folders", lambda ids, status=None: (
        applied.append((ids, status)) or {"changed": len(ids), "summary": {}}
    ))

    with pytest.raises(ValueError, match="План изменился"):
        models.apply_training_preparation_assistant(["safe-folder"], "stale")
    with pytest.raises(ValueError, match="нет в подтверждённом"):
        models.apply_training_preparation_assistant(["other"], "fresh")
    result = models.apply_training_preparation_assistant(["safe-folder"], "fresh")
    assert result["changed"] == 1
    assert applied == [(["safe-folder"], "excluded")]


def test_preparation_assistant_treats_completed_review_as_currently_clean(monkeypatch):
    monkeypatch.setattr(models, "get_training_preflight_report", lambda: {
        "minimum_tracks_per_style": 200,
        "rows": [{"style": "Moombahton", "enabled": True, "confirmed_folders": 1, "selected_tracks": 500}],
        "last_run": {"selection": {}},
    })
    monkeypatch.setattr(models, "dataset_summary", lambda: {
        "style_track_counts": {"Moombahton": 500},
        "status_track_counts": {"confirmed": 500},
    })
    monkeypatch.setattr(models, "build_training_problem_folders", lambda: {"items": [{
        "id": "reviewed", "base_style": "Moombahton", "training_tracks": 500,
        "path": "Moombahton", "risk": "high", "review_complete": True,
        "pending_disputed_tracks": 0, "review_queue_tracks": 15,
        "validation_errors": 20, "disputed_percent": 12.0,
        "mixed_name_warning": True, "label_conflicts": 0,
        "confusion_pairs": [{"true_style": "Moombahton", "predicted_style": "Hip-Hop", "count": 12}],
    }]})

    preview = models.get_training_preparation_assistant()
    row = preview["recommendations"][0]
    assert row["recommendation"] == "leave"
    assert row["review_errors"] == 0
    assert row["historical_review_errors"] == 35
    assert preview["safe_folder_ids"] == []


def test_bounded_executor_returns_every_task():
    tasks = [(f"track-{index}.mp3", index) for index in range(12)]

    results = list(
        _iter_bounded_executor_results(
            tasks,
            _double_task,
            max_workers=2,
            pending_multiplier=2,
            stall_timeout_seconds=2,
            executor_factory=ThreadPoolExecutor,
        )
    )

    assert len(results) == len(tasks)
    assert {result for _task, result, error in results if error is None} == {
        index * 2 for index in range(12)
    }
    assert all(error is None for _task, _result, error in results)


def test_bounded_process_executor_smoke():
    tasks = [-1, -2, -3, -4, -5, -6]

    results = list(
        _iter_bounded_executor_results(
            tasks,
            abs,
            max_workers=2,
            pending_multiplier=2,
            stall_timeout_seconds=30,
        )
    )

    assert {result for _task, result, error in results if error is None} == set(range(1, 7))
    assert all(error is None for _task, _result, error in results)


def test_training_workers_are_reduced_when_memory_is_low():
    gib = 1024 ** 3

    assert _select_training_batch_workers(2, 10 * gib) == 2
    assert _select_training_batch_workers(2, 5 * gib) == 1
    assert _select_training_batch_workers(4, 7 * gib) == 3


def test_training_pauses_before_windows_commit_is_exhausted():
    gib = 1024 ** 3

    assert _training_memory_decision(2, 10 * gib, 2 * gib) == (
        "pause", 0, 2 * gib,
    )
    assert _training_memory_decision(2, 10 * gib, 5 * gib) == (
        "run", 1, 5 * gib,
    )


def test_training_feature_checkpoint_roundtrip_and_file_change(tmp_path):
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"audio-v1")
    params = {"sample_rate": 22050, "features": {"mfcc": True}}
    signature = _training_feature_signature(params)
    task = (str(audio), "Club House", 22050, 30, 15, params)
    result = (
        np.asarray([1.0, 2.0]),
        [np.asarray([1.0, 2.0])],
        "Club House",
        str(audio),
    )
    cache = tmp_path / "features.db"

    assert _save_training_feature_cache([(task, result)], signature, cache) == 1
    restored = _load_training_feature_cache([task], signature, cache)
    assert len(restored) == 1
    cached_result = next(iter(restored.values()))
    assert np.allclose(cached_result[0], result[0])

    time.sleep(0.01)
    audio.write_bytes(b"audio-v2-is-different")
    assert _load_training_feature_cache([task], signature, cache) == {}


def test_strict_dedup_keeps_one_copy_and_quarantines_conflicting_labels(
        monkeypatch, tmp_path,
):
    report_path = tmp_path / "label_conflicts.csv"
    monkeypatch.setattr(models, "TRAINING_LABEL_CONFLICTS_FILE", report_path)
    rows = [
        np.array([1.0, 2.0, 3.0]),
        np.array([1.0, 2.0, 3.0]),
        np.array([4.0, 5.0, 6.0]),
        np.array([4.0, 5.0, 6.0]),
        np.array([7.0, 8.0, 9.0]),
    ]
    indices, report = _strict_deduplicate_training_rows(
        ["a.mp3", "b.mp3", "c.mp3", "d.mp3", "e.mp3"],
        rows,
        ["Club House", "Club House", "Pop", "Rock", "Hip-Hop"],
        [
            {"training_source": "samples"},
            {"training_source": "dataset_builder"},
            {"training_source": "samples"},
            {"training_source": "samples"},
            {"training_source": "samples"},
        ],
    )

    assert indices.tolist() == [1, 4]
    assert report["dropped_duplicates"] == 1
    assert report["dropped_conflicts"] == 2
    assert report["conflict_groups"] == 1
    assert report_path.is_file()


def test_pre_extraction_dedup_uses_path_not_similar_filename(tmp_path):
    first = (str(tmp_path / "pool-a" / "Track (Remix).mp3"), "Club House")
    second = (str(tmp_path / "pool-b" / "Track Remix.mp3"), "Tech House")
    corrected = (first[0], "Deep House")

    selected = _deduplicate_items_by_exact_path(
        [first, second, corrected],
        get_path_fn=lambda row: row[0],
        prefer_last=True,
    )

    assert selected == [corrected, second]


def test_confirmed_folder_conflict_cannot_silently_train_rekordbox_label(
        tmp_path, monkeypatch,
):
    library = tmp_path / "Music"
    confirmed_track = library / "Prime Time" / "MOOMBAHTON" / "KARYO - Draai.mp3"
    confirmed_track.parent.mkdir(parents=True)
    confirmed_track.write_bytes(b"audio")
    report_path = tmp_path / "training_source_label_conflicts.csv"
    monkeypatch.setattr(models, "TRAINING_SOURCE_LABEL_CONFLICTS_FILE", report_path)
    dataset_rows = [{
        "path": str(confirmed_track),
        "base_genre": "Moombahton",
        "taxonomy": {
            "training_source": "dataset_builder",
            "training_folder_id": "confirmed-moombahton",
        },
    }]
    rekordbox_tracks = [{
        "path": r"Z:\Prime Time\MOOMBAHTON\KARYO - Draai.mp3",
        "source_path": r"Z:\Prime Time\MOOMBAHTON\KARYO - Draai.mp3",
        "raw_genre": "Moomb, HIP-HOP, Moomb",
        "genre": "Hip-Hop",
    }]

    selected, report = _reconcile_confirmed_and_rekordbox_labels(
        dataset_rows,
        rekordbox_tracks,
        {},
        str(library),
        effective_styles={"Moombahton", "Hip-Hop"},
    )

    assert selected == []
    assert report["policy"] == "quarantine_confirmed_rekordbox_conflicts"
    assert report["conflict_tracks"] == 1
    assert report["excluded_rekordbox_tracks"] == 1
    assert report["excluded_confirmed_tracks"] == 1
    assert report["conflicting_confirmed_paths"] == [str(confirmed_track)]
    assert report["conflict_pairs"] == [{
        "confirmed_style": "Moombahton",
        "rekordbox_style": "Hip-Hop",
        "count": 1,
    }]
    assert report["items"][0]["rekordbox_raw_genre"] == "Moomb, HIP-HOP, Moomb"
    assert report_path.is_file()


def test_matching_confirmed_and_rekordbox_labels_remain_available(tmp_path):
    library = tmp_path / "Music"
    track_path = library / "MOOMBAHTON" / "track.mp3"
    dataset_rows = [{
        "path": str(track_path),
        "base_genre": "Moombahton",
        "taxonomy": {"training_source": "dataset_builder"},
    }]
    rekordbox_track = {
        "path": r"Z:\MOOMBAHTON\track.mp3",
        "source_path": r"Z:\MOOMBAHTON\track.mp3",
        "raw_genre": "Moomb",
        "genre": "Moombahton",
    }

    selected, report = _reconcile_confirmed_and_rekordbox_labels(
        dataset_rows,
        [rekordbox_track],
        {},
        str(library),
        effective_styles={"Moombahton"},
        write_report=False,
    )

    assert selected == [rekordbox_track]
    assert report["conflict_tracks"] == 0


def test_disabled_rekordbox_style_does_not_quarantine_enabled_folder_label(tmp_path):
    library = tmp_path / "Music"
    track_path = library / "MOOMBAHTON" / "track.mp3"
    dataset_rows = [{
        "path": str(track_path),
        "base_genre": "Moombahton",
        "taxonomy": {"training_source": "dataset_builder"},
    }]
    rekordbox_track = {
        "path": r"Z:\MOOMBAHTON\track.mp3",
        "source_path": r"Z:\MOOMBAHTON\track.mp3",
        "raw_genre": "RNB",
        "genre": "RnB",
    }

    selected, report = _reconcile_confirmed_and_rekordbox_labels(
        dataset_rows,
        [rekordbox_track],
        {},
        str(library),
        effective_styles={"Moombahton"},
        write_report=False,
    )

    assert selected == [rekordbox_track]
    assert report["conflict_tracks"] == 0


def test_quick_pool_uses_the_same_cross_source_conflict_quarantine(
        tmp_path, monkeypatch,
):
    library = tmp_path / "Music"
    track_path = library / "MOOMBAHTON" / "KARYO - Draai.mp3"
    dataset_row = {
        "path": str(track_path),
        "base_genre": "Moombahton",
        "taxonomy": {"training_source": "dataset_builder"},
    }
    rekordbox_track = {
        "path": r"Z:\MOOMBAHTON\KARYO - Draai.mp3",
        "source_path": r"Z:\MOOMBAHTON\KARYO - Draai.mp3",
        "raw_genre": "Moomb, HIP-HOP, Moomb",
        "genre": "Hip-Hop",
    }
    monkeypatch.setattr(models, "SAMPLES_DIR", tmp_path / "empty-samples")
    monkeypatch.setattr(models, "load_librosa_settings", lambda: {
        "use_rekordbox": True,
        "sample_rate": 22050,
        "offset": 0,
        "duration": 30,
        "max_tracks_per_genre": 400,
        "rekordbox_track_limit": 0,
    })
    monkeypatch.setattr(models, "get_training_dataset_settings", lambda: {
        "excluded_styles": [],
        "max_tracks_per_style": 800,
        "min_tracks_per_style": 1,
    })
    monkeypatch.setattr(models, "load_genre_settings", lambda: {
        "hip-hop": {"genre": "Hip-Hop", "is_trainable": True},
    })
    monkeypatch.setattr(models, "dataset_summary", lambda: {
        "style_track_counts": {"Moombahton": 1},
    })
    monkeypatch.setattr(models, "iter_confirmed_training_tracks", lambda: iter([dataset_row]))
    monkeypatch.setattr(models, "iter_training_corrections", lambda _styles: iter([]))
    monkeypatch.setattr(models, "load_rekordbox_json_tracks", lambda *_args: [rekordbox_track])
    monkeypatch.setattr(models, "load_config", lambda: {"music_dir": str(library)})

    pool = models._prepare_quick_quality_pool()

    assert pool["tasks"] == []
    assert pool["source_label_consistency"]["conflict_tracks"] == 1
    assert pool["source_label_consistency"]["excluded_confirmed_tracks"] == 1


def test_builder_style_is_effective_before_rekordbox_filter_and_exclusion_wins(
        tmp_path,
):
    genre_settings = {
        "club": {"genre": "Club House", "is_trainable": True},
    }
    rekordbox_tracks = [
        {
            "path": str(tmp_path / f"disco-{index}.mp3"),
            "Genre": "Disco",
            "genre": "Disco",
        }
        for index in range(220)
    ]

    effective = _effective_trainable_styles(
        genre_settings,
        {"Disco": 250},
    )
    prepared = _prepare_rekordbox_training_tracks(
        rekordbox_tracks,
        genre_settings,
        effective,
    )
    by_style = {"Disco": prepared}
    _limits, selected, report = _select_training_pool_sources(
        {"Disco": 250},
        by_style,
        max_per_style=400,
        min_per_style=200,
    )

    assert "Disco" in effective
    assert report["Disco"]["rekordbox_available"] == 220
    assert report["Disco"]["rekordbox_target"] > 0
    assert any(track["_training_base_genre"] == "Disco" for track in selected)

    excluded_effective = _effective_trainable_styles(
        genre_settings,
        {"Disco": 250},
        {"Disco"},
    )
    excluded_tracks = _prepare_rekordbox_training_tracks(
        rekordbox_tracks,
        genre_settings,
        excluded_effective,
        {"Disco"},
    )
    assert "Disco" not in excluded_effective
    assert excluded_tracks == []


def test_training_plan_counts_rekordbox_metadata_without_probing_audio_paths(
        tmp_path, monkeypatch,
):
    export_dir = tmp_path / "reckordbox"
    export_dir.mkdir()
    export_file = export_dir / "parsed_rekordbox.json"
    export_file.write_text(json.dumps([
        {"Genre": "Disco", "path": r"Z:\\missing\\one.mp3"},
        {"Genre": "Disco", "path": r"Z:\\missing\\two.mp3"},
        {"Genre": "", "path": r"Z:\\missing\\service.wav"},
    ]), encoding="utf-8")

    monkeypatch.setattr(models, "REKORDBOX_OUTPUT_DIR", export_dir)
    monkeypatch.setattr(models, "dataset_summary", lambda: {
        "style_track_counts": {"Disco": 250},
        "style_folder_counts": {"Disco": 3},
    })
    monkeypatch.setattr(models, "get_training_dataset_settings", lambda: {
        "max_tracks_per_style": 800,
        "min_tracks_per_style": 200,
        "excluded_styles": [],
    })
    monkeypatch.setattr(models, "load_librosa_settings", lambda: {
        "use_rekordbox": True,
        "max_tracks_per_genre": 400,
    })
    monkeypatch.setattr(models, "load_genre_settings", lambda: {
        "disco": {"genre": "Disco", "is_trainable": True},
    })
    monkeypatch.setattr(models, "_sample_style_counts", lambda *_settings: {})
    monkeypatch.setattr(models, "_active_model_snapshot", lambda: ([], True))
    monkeypatch.setattr(models, "load_training_run_report", lambda: {})
    monkeypatch.setattr(
        models,
        "load_rekordbox_json_tracks",
        lambda *_args, **_kwargs: pytest.fail(
            "UI plan must not invoke strict path-aware Rekordbox loading"
        ),
    )

    report = models.get_training_preflight_report()

    disco = next(row for row in report["rows"] if row["style"] == "Disco")
    assert disco["rekordbox_tracks"] == 2
    assert disco["available_tracks"] == 252


@pytest.mark.parametrize(
    "use_builder,use_samples,expected_names",
    [
        (True, False, {"builder.mp3"}),
        (False, True, {"sample.mp3"}),
        (True, True, {"builder.mp3", "sample.mp3"}),
    ],
)
def test_quick_pool_honours_independent_builder_and_reference_sources(
        tmp_path, monkeypatch, use_builder, use_samples, expected_names,
):
    reference = tmp_path / "reference"
    reference_style = reference / "Club House"
    reference_style.mkdir(parents=True)
    sample_track = reference_style / "sample.mp3"
    sample_track.write_bytes(b"sample")
    builder_track = tmp_path / "builder.mp3"
    builder_track.write_bytes(b"builder")
    settings = {
        "excluded_styles": [],
        "max_tracks_per_style": 10,
        "min_tracks_per_style": 1,
        "use_dataset_builder": use_builder,
        "use_reference_samples": use_samples,
        "reference_samples_path": str(reference),
    }
    monkeypatch.setattr(models, "get_training_dataset_settings", lambda: settings)
    monkeypatch.setattr(models, "load_librosa_settings", lambda: {
        "use_rekordbox": False, "sample_rate": 22050,
        "offset": 0, "duration": 30,
    })
    monkeypatch.setattr(models, "load_genre_settings", lambda: {
        "club house": {"genre": "Club House", "is_trainable": True},
    })
    monkeypatch.setattr(models, "dataset_summary", lambda: {
        "style_track_counts": {"Club House": 1},
    })
    monkeypatch.setattr(models, "iter_confirmed_training_tracks", lambda: iter([{
        "path": str(builder_track), "base_genre": "Club House",
        "taxonomy": {"training_source": "dataset_builder"},
    }]))
    monkeypatch.setattr(models, "iter_training_corrections", lambda _styles: iter([]))

    pool = models._prepare_quick_quality_pool()

    assert {os.path.basename(task[0]) for task in pool["tasks"]} == expected_names


def test_training_plan_reports_sources_separately(monkeypatch, tmp_path):
    reference = tmp_path / "reference"
    style_dir = reference / "Techno"
    style_dir.mkdir(parents=True)
    for index in range(3):
        (style_dir / f"sample-{index}.mp3").write_bytes(b"audio")
    monkeypatch.setattr(models, "get_training_dataset_settings", lambda: {
        "max_tracks_per_style": 800,
        "min_tracks_per_style": 1,
        "excluded_styles": [],
        "use_dataset_builder": False,
        "use_reference_samples": True,
        "reference_samples_path": str(reference),
    })
    monkeypatch.setattr(models, "dataset_summary", lambda: {
        "style_track_counts": {"Club House": 99},
        "style_folder_counts": {"Club House": 1},
    })
    monkeypatch.setattr(models, "load_librosa_settings", lambda: {
        "use_rekordbox": False,
    })
    monkeypatch.setattr(models, "load_genre_settings", lambda: {
        "techno": {"genre": "Techno", "is_trainable": True},
    })
    monkeypatch.setattr(models, "_active_model_snapshot", lambda: ([], True))
    monkeypatch.setattr(models, "load_training_run_report", lambda: {})

    report = models.get_training_preflight_report()

    techno = next(row for row in report["rows"] if row["style"] == "Techno")
    assert techno["builder_tracks"] == 0
    assert techno["samples_tracks"] == 3
    assert report["source_settings"]["dataset_builder_enabled"] is False
    assert report["source_settings"]["reference_samples_enabled"] is True


def test_genre_training_rekordbox_switch_is_independent_from_legacy_setting():
    assert models._rekordbox_training_enabled(
        {"use_rekordbox_training": False}, {"use_rekordbox": True},
    ) is False
    assert models._rekordbox_training_enabled(
        {"use_rekordbox_training": True}, {"use_rekordbox": False},
    ) is True
    assert models._rekordbox_training_enabled(
        {}, {"use_rekordbox": True},
    ) is True


def test_rekordbox_loader_skips_unlabelled_rows_before_path_resolution(
        tmp_path, monkeypatch,
):
    audio_file = tmp_path / "labelled.mp3"
    audio_file.write_bytes(b"audio")
    export_file = tmp_path / "parsed_rekordbox.json"
    export_file.write_text(json.dumps([
        {"Genre": "", "path": r"Z:\\missing\\service.wav"},
        {"Genre": "Club House", "path": "labelled.mp3"},
    ]), encoding="utf-8")
    resolved = []

    def fake_resolve(path, _music_dir):
        resolved.append(path)
        return str(audio_file)

    monkeypatch.setattr(models, "load_config", lambda: {"music_dir": str(tmp_path)})
    monkeypatch.setattr(models, "resolve_mapped_music_path", fake_resolve)

    tracks = models.load_rekordbox_json_tracks(
        str(export_file),
        {"club house": {"genre": "Club House", "is_trainable": True}},
    )

    assert len(tracks) == 1
    assert resolved == ["labelled.mp3"]


def test_pool_preflight_stops_when_preview_builder_source_disappears():
    report = _build_training_pool_preflight(
        {"Disco"},
        dataset_builder_counts={},
        local_counts={},
        rekordbox_counts={},
        capped_local_counts={},
        capped_rekordbox_counts={},
        expected_rows=[{
            "style": "Disco",
            "enabled": True,
            "builder_tracks": 250,
            "samples_tracks": 0,
            "selected_tracks": 200,
            "selected_rekordbox_tracks": 0,
        }],
        minimum_required=200,
    )

    assert report["passed"] is False
    assert any("dataset_builder" in issue for issue in report["issues"])


def test_quick_quality_uses_cached_features_and_is_diagnostic_only(monkeypatch):
    rng = np.random.default_rng(42)
    params = {
        "sample_rate": 22050,
        "features": {"mfcc": True},
        "validation_size": 0.2,
        "random_state": 42,
    }
    tasks = []
    cached = {}
    signature = models._training_feature_signature(params)
    for style, center in (("A", -2.0), ("B", 2.0)):
        for index in range(40):
            path = f"{style}-artist{index}-song{index}.mp3"
            task = (path, style, 22050, 0, 30, params)
            features = rng.normal(center, 0.25, size=134)
            result = (features, [features], style, path)
            tasks.append(task)
            cached[models._training_cache_key(task, signature)] = result

    monkeypatch.setattr(models, "_prepare_quick_quality_pool", lambda: {
        "tasks": tasks,
        "librosa_params": params,
        "effective_styles": ["A", "B"],
        "source_mix": {},
        "cap": {},
        "dataset_builder_counts": {"A": 40, "B": 40},
    })
    monkeypatch.setattr(
        models, "_load_training_feature_cache",
        lambda _tasks, _signature: cached,
    )
    monkeypatch.setattr(
        models, "_active_model_classes_readonly", lambda: ([], False),
    )
    monkeypatch.setattr(
        models, "_active_model_snapshot",
        lambda: pytest.fail("diagnostic must not update active metadata"),
    )

    result = models.quick_training_quality_assessment()

    assert result["status"] == "completed"
    assert result["diagnostic_only"] is True
    assert result["classes"] == ["A", "B"]
    assert result["split"]["group_aware"] is True
    assert result["pipeline"]["feature_extraction"] is False
    assert result["pipeline"]["model_saved"] is False
    assert result["pipeline"]["quality_gate_changed"] is False


def test_bounded_executor_watchdog_reports_pending_paths():
    tasks = [("slow-track.mp3", 1)]

    with pytest.raises(TrainingPoolStalledError) as exc_info:
        list(
            _iter_bounded_executor_results(
                tasks,
                _slow_task,
                max_workers=1,
                stall_timeout_seconds=0.01,
                executor_factory=ThreadPoolExecutor,
            )
        )

    assert "slow-track.mp3" in str(exc_info.value)
    assert exc_info.value.pending_paths == ["slow-track.mp3"]


def test_training_quality_gate_accepts_a_precise_candidate():
    validation = {"macro avg": {"f1-score": 0.82}, "accuracy": 0.83}
    rejection = {
        "accepted_precision": 0.94,
        "coverage": 0.68,
        "per_class": {
            "Club House": {"accepted_tracks": 20, "accepted_precision": 0.95},
            "Drum & Bass": {"accepted_tracks": 18, "accepted_precision": 0.94},
        },
    }

    report = _evaluate_training_quality_gate(validation, rejection, {})

    assert report["passed"] is True
    assert report["reasons"] == []


def test_training_quality_gate_rejects_low_coverage_and_dead_classes():
    validation = {"macro avg": {"f1-score": 0.56}, "accuracy": 0.59}
    rejection = {
        "accepted_precision": 0.83,
        "coverage": 0.23,
        "per_class": {
            "Club House": {"accepted_tracks": 4, "accepted_precision": 0.25},
            "Pop": {"accepted_tracks": 0, "accepted_precision": 0.0},
            "Tech House": {"accepted_tracks": 0, "accepted_precision": 0.0},
        },
    }

    report = _evaluate_training_quality_gate(validation, rejection, {})

    assert report["passed"] is False
    assert "Club House" in report["per_class_failures"]
    assert "Pop" in report["per_class_failures"]
    assert "Tech House" in report["per_class_failures"]
    assert any("покрытие" in reason for reason in report["reasons"])


def test_training_quality_gate_can_be_disabled_explicitly():
    validation = {"macro avg": {"f1-score": 0.1}}
    rejection = {
        "accepted_precision": 0.1,
        "coverage": 0.1,
        "per_class": {"Pop": {"accepted_tracks": 0, "accepted_precision": 0.0}},
    }

    report = _evaluate_training_quality_gate(
        validation,
        rejection,
        {"training_quality_gate_enabled": False},
    )

    assert report["passed"] is True
    assert report["reasons"] == []


def test_training_quality_gate_protects_previously_active_styles():
    validation = {
        "macro avg": {"f1-score": 0.75},
        "Club House": {"f1-score": 0.71},
        "Drum & Bass": {"f1-score": 0.31},
    }
    rejection = {
        "accepted_precision": 0.94,
        "coverage": 0.68,
        "per_class": {
            "Club House": {"accepted_tracks": 20, "accepted_precision": 0.95},
            "Drum & Bass": {"accepted_tracks": 18, "accepted_precision": 0.94},
        },
    }

    report = _evaluate_training_quality_gate(
        validation,
        rejection,
        {"training_min_retained_style_f1": 0.55},
        protected_styles={"Club House", "Drum & Bass", "Hip-Hop"},
    )

    assert report["passed"] is False
    assert "Drum & Bass" in report["retained_style_failures"]
    assert "Hip-Hop" in report["retained_style_failures"]


def test_training_quality_gate_rejects_material_drop_of_retained_style():
    validation = {
        "macro avg": {"f1-score": 0.76},
        "Club House": {"f1-score": 0.69},
        "Drum & Bass": {"f1-score": 0.78},
    }
    rejection = {
        "accepted_precision": 0.93,
        "coverage": 0.70,
        "per_class": {
            "Club House": {"accepted_tracks": 20, "accepted_precision": 0.91},
            "Drum & Bass": {"accepted_tracks": 18, "accepted_precision": 0.94},
        },
    }

    report = _evaluate_training_quality_gate(
        validation,
        rejection,
        {},
        protected_styles={"Club House", "Drum & Bass"},
    )

    assert report["passed"] is False
    assert report["requirements"]["minimum_retained_style_f1"] == 0.70
    assert "Club House" in report["retained_style_failures"]


def test_training_quality_gate_compares_retained_style_with_active_model():
    validation = {
        "macro avg": {"f1-score": 0.82},
        "Club House": {"f1-score": 0.81, "recall": 0.80},
        "Drum & Bass": {"f1-score": 0.85, "recall": 0.85},
    }
    rejection = {
        "accepted_precision": 0.94,
        "coverage": 0.60,
        "per_class": {
            "Club House": {"accepted_tracks": 20, "accepted_precision": 0.95},
            "Drum & Bass": {"accepted_tracks": 20, "accepted_precision": 0.95},
        },
    }

    report = _evaluate_training_quality_gate(
        validation,
        rejection,
        {"training_max_retained_style_recall_drop": 0.05},
        protected_styles={"Club House", "Drum & Bass"},
        active_style_metrics={
            "Club House": {"recall": 0.90},
            "Drum & Bass": {"recall": 0.87},
        },
    )

    assert report["passed"] is False
    assert "Club House" in report["retained_style_failures"]
    assert "Drum & Bass" not in report["retained_style_failures"]


class _WeakEffNetHead:
    classes_ = np.asarray(["A", "B"], dtype=object)

    def aligned_probabilities(self, vectors, target_classes):
        count = len(vectors)
        return np.tile(np.asarray([[0.55, 0.45]]), (count, 1))


def test_effnet_fusion_must_pass_absolute_quality_floor():
    paths = [f"track-{index}.mp3" for index in range(8)]
    labels = np.asarray(["A", "B"] * 4, dtype=object)
    embeddings = {path: np.asarray([1.0, 0.0]) for path in paths}
    threshold_idx = np.asarray([0, 1, 2, 3])
    validation_idx = np.asarray([4, 5, 6, 7])
    base = np.asarray([[0.55, 0.45], [0.45, 0.55]] * 2)

    result, _alpha, report = _select_safe_effnet_fusion(
        _WeakEffNetHead(),
        paths,
        labels,
        embeddings,
        threshold_idx,
        base,
        validation_idx,
        base,
        np.asarray(["A", "B"], dtype=object),
        {"effnet_genre_min_macro_f1": 1.01},
    )

    assert report["enabled"] is False
    assert report["reason"] == "below_absolute_quality_floor"
    assert np.allclose(result, base)


def test_active_model_comparison_skips_incompatible_feature_schema(
        monkeypatch, tmp_path,
):
    from types import SimpleNamespace

    model_path = tmp_path / "genre_model.pkl"
    with model_path.open("wb") as target:
        import pickle
        pickle.dump({
            "model": SimpleNamespace(
                classes_=np.asarray(["Club House", "Hip-Hop"], dtype=object)
            ),
            "expected_feature_len": 4,
        }, target)
    monkeypatch.setattr(models, "MODEL_PATH", str(model_path))

    metrics, report = _evaluate_active_model_on_current_validation(
        [[np.asarray([1.0, 2.0, 3.0])]],
        np.asarray(["Club House"], dtype=object),
        np.asarray([0]),
        {"Club House"},
    )

    assert metrics == {}
    assert report["available"] is False
    assert report["reason"] == "feature_schema_changed"


def test_progressive_precheck_never_removes_requested_new_style():
    truth = np.asarray([
        "Club House", "Club House", "Club House", "Club House",
        "Rock", "Rock", "Rock", "Rock",
        "Pop", "Pop", "Pop", "Pop",
    ], dtype=object)
    predicted = np.asarray([
        "Club House", "Club House", "Rock", "Rock",
        "Rock", "Rock", "Rock", "Rock",
        "Rock", "Rock", "Pop", "Rock",
    ], dtype=object)

    admitted, report = _select_progressive_training_styles(
        truth,
        predicted,
        {"Club House", "Rock", "Pop"},
        {"Club House"},
        {
            "training_new_style_min_f1": 0.60,
            "training_new_style_min_recall": 0.50,
            "training_new_style_min_support": 3,
        },
    )

    assert admitted == {"Club House", "Rock", "Pop"}
    assert report["mode"] == "diagnostic_only"
    assert report["rows"]["Club House"]["status"] == "retained"
    assert report["rows"]["Rock"]["status"] == "precheck_passed"
    assert report["rows"]["Pop"]["status"] == "quality_warning"
    assert report["quality_warning_styles"] == ["Pop"]
    assert report["deferred_styles"] == []


def test_progressive_precheck_keeps_full_fifteen_style_candidate():
    styles = [
        "Afro House", "Bass House", "Club House", "Deep House", "Disco",
        "Drum & Bass", "Future House", "Hip-Hop", "Lounge", "Moombahton",
        "Pop", "RnB", "Rock", "Tech House", "Trap",
    ]
    truth = np.asarray([style for style in styles for _ in range(3)], dtype=object)
    predicted = truth.copy()
    predicted[0] = "Club House"
    admitted, report = _select_progressive_training_styles(
        truth,
        predicted,
        set(styles),
        {"Club House", "Drum & Bass", "Hip-Hop"},
        {
            "training_new_style_min_f1": 0.99,
            "training_new_style_min_recall": 0.99,
            "training_new_style_min_support": 2,
        },
    )

    assert admitted == set(styles)
    assert report["deferred_styles"] == []
    assert set(report["admitted_styles"]) == set(styles)

    run_report = models._build_training_run_report(
        False,
        ["Club House", "Drum & Bass", "Hip-Hop"],
        True,
        styles,
        {},
        {"per_class": {}},
        {},
        {},
        {style: 200 for style in styles},
        {"progressive_admission": report},
        {"passed": False, "reasons": ["quality"]},
        {},
        {},
    )
    assert set(run_report["candidate_classes"]) == set(styles)
