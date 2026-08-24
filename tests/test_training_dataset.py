import json
import csv
from pathlib import Path

import pytest

import app.training_dataset as training_dataset
from app import genre_review


def _patch_store(monkeypatch, tmp_path):
    store = tmp_path / "training_dataset.json"
    keywords = tmp_path / "folder_keywords.json"
    keywords.write_text(json.dumps({
        "russian remix": {"genre": "Русские Ремиксы", "is_trainable": True},
        "club house": {"genre": "Club House", "is_trainable": True},
        "festival trap": {"genre": "Hip-Hop", "is_trainable": True},
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(training_dataset, "TRAINING_DATASET_FILE", store)
    monkeypatch.setattr(training_dataset, "GENRE_SETTINGS_FILE", keywords)
    return store


def test_prelabel_separates_style_language_version_and_theme(monkeypatch, tmp_path):
    _patch_store(monkeypatch, tmp_path)
    result = training_dataset.infer_folder_taxonomy(
        tmp_path / "Russian" / "Drum & Bass" / "Remix"
    )
    assert result["base_style"] == "Drum & Bass"
    assert result["language"] == "Russian"
    assert result["version_type"] == "Remix"
    assert result["status"] == "suggested"

    russian_house = training_dataset.infer_folder_taxonomy(tmp_path / "Russian Remix")
    assert russian_house["base_style"] == "Club House"
    assert russian_house["language"] == "Russian"
    assert russian_house["version_type"] == "Remix"


def test_prelabel_marks_mixed_style_folder_ambiguous(monkeypatch, tmp_path):
    _patch_store(monkeypatch, tmp_path)
    result = training_dataset.infer_folder_taxonomy(tmp_path / "Rnb Pop Hip-Hop")
    assert result["status"] == "ambiguous"
    assert result["base_style"] == ""
    assert result["conflicts"]


def test_personal_folder_rule_has_priority_over_universal_alias(monkeypatch, tmp_path):
    _patch_store(monkeypatch, tmp_path)
    result = training_dataset.infer_folder_taxonomy(tmp_path / "Festival Trap")
    assert result["base_style"] == "Hip-Hop"
    assert "legacy" in " ".join(result["reasons"]).lower()


def test_nested_folder_inherits_selected_source_style(monkeypatch, tmp_path):
    _patch_store(monkeypatch, tmp_path)
    source = tmp_path / "Club House"
    nested = source / "archive"
    result = training_dataset.infer_folder_taxonomy(nested, source)
    assert result["base_style"] == "Club House"
    assert result["status"] == "suggested"


def test_preview_confirm_and_iterate_tracks(monkeypatch, tmp_path):
    _patch_store(monkeypatch, tmp_path)
    music = tmp_path / "Music"
    club = music / "2025" / "DJ Pool - Club House"
    mixed = music / "Mixed House Hip-Hop"
    club.mkdir(parents=True)
    mixed.mkdir(parents=True)
    (club / "one.mp3").write_bytes(b"one")
    (club / "ignore.flac").write_bytes(b"flac")
    (mixed / "two.mp3").write_bytes(b"two")

    source = training_dataset.add_training_source("2025", music_root=music)
    assert source["relative_path"] == "2025"
    summary = training_dataset.preview_training_sources({})
    assert summary["folder_count"] == 1
    listing = training_dataset.list_training_folders()
    row = listing["items"][0]
    assert row["base_style"] == "Club House"
    assert row["track_count"] == 1

    changed = training_dataset.confirm_high_confidence(0.85)
    assert changed["changed"] == 1
    tracks = list(training_dataset.iter_confirmed_training_tracks())
    assert len(tracks) == 1
    assert tracks[0]["base_genre"] == "Club House"
    assert tracks[0]["taxonomy"]["training_source"] == "dataset_builder"


def test_folder_listing_filters_and_sorts_before_pagination(monkeypatch, tmp_path):
    _patch_store(monkeypatch, tmp_path)
    music = tmp_path / "Music"
    folders = [
        ("Zulu Club House", "Club House", 3),
        ("Alpha Moombahton", "Moombahton", 22),
        ("Beta Moombahton", "Moombahton", 105),
    ]
    for name, _style, count in folders:
        folder = music / name
        folder.mkdir(parents=True)
        for index in range(count):
            (folder / f"{index:03d}.mp3").write_bytes(b"audio")
    training_dataset.add_training_source(music, music_root=music)
    training_dataset.preview_training_sources({})
    listing = training_dataset.list_training_folders(
        style="Moombahton", track_range="20-49", sort_by="path", sort_dir="asc",
    )
    assert listing["total"] == 1
    assert listing["items"][0]["base_style"] == "Moombahton"
    assert listing["items"][0]["track_count"] == 22
    assert "Club House" in listing["available_styles"]

    descending = training_dataset.list_training_folders(
        style="Moombahton", sort_by="tracks", sort_dir="desc", limit=1,
    )
    assert descending["total"] == 2
    assert descending["items"][0]["track_count"] == 105


def test_folder_listing_rejects_unknown_filter_and_sort(monkeypatch, tmp_path):
    _patch_store(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match="диапазон"):
        training_dataset.list_training_folders(track_range="unknown")
    with pytest.raises(ValueError, match="сортировки"):
        training_dataset.list_training_folders(sort_by="unknown")


def test_unavailable_sources_do_not_erase_saved_folder_review(monkeypatch, tmp_path):
    _patch_store(monkeypatch, tmp_path)
    music = tmp_path / "Music"
    folder = music / "Club House"
    folder.mkdir(parents=True)
    (folder / "one.mp3").write_bytes(b"one")

    training_dataset.add_training_source(folder, music_root=music)
    training_dataset.preview_training_sources({})
    row = training_dataset.list_training_folders()["items"][0]
    training_dataset.update_training_folders(
        [row["id"]], status="confirmed", taxonomy={"base_style": "Club House"},
    )
    folder.rename(music / "temporarily-offline")

    progress = {}
    with pytest.raises(OSError, match="Предыдущая разметка сохранена"):
        training_dataset.preview_training_sources(progress)

    summary = training_dataset.dataset_summary()
    assert summary["folder_count"] == 1
    assert summary["track_count"] == 1
    assert summary["confirmed_tracks"] == 1
    assert training_dataset.list_training_folders()["items"][0]["status"] == "confirmed"
    assert progress["status"] == "error"

    backup = training_dataset.TRAINING_DATASET_FILE.with_name(
        "training_dataset.backup.json"
    )
    assert backup.is_file()
    assert json.loads(backup.read_text(encoding="utf-8"))["folders"]


def test_per_track_exclusion_and_style_override_apply_before_training_pool(
        monkeypatch, tmp_path,
):
    _patch_store(monkeypatch, tmp_path)
    monkeypatch.setattr(genre_review, "GENRE_REVIEW_FILE", tmp_path / "review.json")
    music = tmp_path / "Music"
    folder = music / "Moombahton"
    folder.mkdir(parents=True)
    excluded = folder / "excluded.mp3"
    corrected = folder / "corrected.mp3"
    untouched = folder / "untouched.mp3"
    excluded.write_bytes(b"excluded-audio")
    corrected.write_bytes(b"corrected-audio")
    untouched.write_bytes(b"untouched-audio")
    training_dataset.add_training_source(folder, music_root=music)
    training_dataset.preview_training_sources({})
    row = training_dataset.list_training_folders()["items"][0]
    training_dataset.update_training_folders(
        [row["id"]], status="confirmed", taxonomy={"base_style": "Moombahton"},
    )

    genre_review.save_training_override(str(excluded), exclude_from_training=True)
    genre_review.save_training_override(str(corrected), style_override="Hip-Hop")
    tracks = list(training_dataset.iter_confirmed_training_tracks())

    by_name = {Path(item["path"]).name: item for item in tracks}
    assert set(by_name) == {"corrected.mp3", "untouched.mp3"}
    assert by_name["corrected.mp3"]["base_genre"] == "Hip-Hop"
    assert by_name["corrected.mp3"]["taxonomy"]["training_source"] == "manual_review"
    assert by_name["untouched.mp3"]["base_genre"] == "Moombahton"


def test_validation_error_is_review_only_until_user_excludes_track(
        monkeypatch, tmp_path,
):
    _patch_store(monkeypatch, tmp_path)
    monkeypatch.setattr(genre_review, "GENRE_REVIEW_FILE", tmp_path / "review.json")
    music = tmp_path / "Music"
    folder = music / "Moombahton"
    folder.mkdir(parents=True)
    track = folder / "borderline.mp3"
    track.write_bytes(b"borderline-audio")
    training_dataset.add_training_source(folder, music_root=music)
    training_dataset.preview_training_sources({})
    folder_row = training_dataset.list_training_folders()["items"][0]
    training_dataset.update_training_folders(
        [folder_row["id"]], status="confirmed", taxonomy={"base_style": "Moombahton"},
    )

    reports = {}
    for name in (
        "training_errors", "training_review_queue", "training_label_conflicts",
        "training_duplicates", "training_conflicts",
    ):
        reports[name] = tmp_path / f"{name}.csv"
        monkeypatch.setattr(training_dataset, name.upper() + "_FILE", reports[name])
    with reports["training_errors"].open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=[
            "path", "group", "true_base_genre", "predicted_base_genre",
            "top1_probability", "margin", "is_error",
        ])
        writer.writeheader()
        writer.writerow({
            "path": str(track), "group": "g1", "true_base_genre": "Moombahton",
            "predicted_base_genre": "Hip-Hop", "top1_probability": "0.71",
            "margin": "0.12", "is_error": "1",
        })
    for name in ("training_review_queue", "training_label_conflicts", "training_duplicates", "training_conflicts"):
        reports[name].write_text("", encoding="utf-8")

    disputed = training_dataset.list_training_disputed_tracks(music_dir=music)
    assert disputed["total"] == 1
    assert disputed["items"][0]["review_status"] == "pending"
    problem = training_dataset.list_training_problem_folders()["items"][0]
    assert problem["disputed_tracks"] == 1
    assert problem["pending_disputed_tracks"] == 1
    assert len(list(training_dataset.iter_confirmed_training_tracks())) == 1

    result = training_dataset.update_training_track_override(
        disputed["items"][0]["id"], "exclude", confirm_large_change=True,
        music_dir=music,
    )
    assert result["entry"]["exclude_from_training"] is True
    problem = training_dataset.list_training_problem_folders()["items"][0]
    assert problem["disputed_tracks"] == 1
    assert problem["pending_disputed_tracks"] == 0
    assert problem["excluded_disputed_tracks"] == 1
    assert problem["review_complete"] is True
    assert problem["review_resolved_tracks"] == 1
    assert list(training_dataset.iter_confirmed_training_tracks()) == []

    result = training_dataset.update_training_track_override(
        disputed["items"][0]["id"], "style", style_override="Club House",
        music_dir=music,
    )
    assert result["entry"]["style_override"] == "Club House"
    problem = training_dataset.list_training_problem_folders()["items"][0]
    assert problem["pending_disputed_tracks"] == 0
    assert problem["excluded_disputed_tracks"] == 0
    assert problem["reviewed_disputed_tracks"] == 1
    assert problem["style_override_tracks"] == 1


def test_bulk_track_exclusion_uses_filtered_ids_and_persists(monkeypatch, tmp_path):
    _patch_store(monkeypatch, tmp_path)
    monkeypatch.setattr(genre_review, "GENRE_REVIEW_FILE", tmp_path / "review.json")
    music = tmp_path / "Music"
    folder = music / "Moombahton"
    folder.mkdir(parents=True)
    tracks = [folder / "one.mp3", folder / "two.mp3"]
    for track in tracks:
        track.write_bytes((track.stem + "-audio").encode())
    training_dataset.add_training_source(folder, music_root=music)
    training_dataset.preview_training_sources({})
    folder_row = training_dataset.list_training_folders()["items"][0]
    training_dataset.update_training_folders(
        [folder_row["id"]], status="confirmed", taxonomy={"base_style": "Moombahton"},
    )
    for name in (
        "training_errors", "training_review_queue", "training_label_conflicts",
        "training_duplicates", "training_conflicts",
    ):
        path = tmp_path / f"{name}.csv"
        monkeypatch.setattr(training_dataset, name.upper() + "_FILE", path)
        path.write_text("", encoding="utf-8")
    with training_dataset.TRAINING_ERRORS_FILE.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=[
            "path", "true_base_genre", "predicted_base_genre", "is_error",
        ])
        writer.writeheader()
        for track in tracks:
            writer.writerow({
                "path": str(track), "true_base_genre": "Moombahton",
                "predicted_base_genre": "Hip-Hop", "is_error": "1",
            })

    filtered = training_dataset.training_disputed_track_ids(
        style="Moombahton", confused_with="Hip-Hop", status="pending", music_dir=music,
    )
    assert filtered["total"] == 2
    result = training_dataset.exclude_training_tracks(
        filtered["track_ids"], confirm_large_change=True, music_dir=music,
    )
    assert result["changed"] == 2
    assert training_dataset.training_disputed_track_ids(
        style="Moombahton", status="pending", music_dir=music,
    )["total"] == 0
    problem = training_dataset.list_training_problem_folders()["items"][0]
    assert problem["pending_disputed_tracks"] == 0
    assert problem["excluded_disputed_tracks"] == 2
    assert problem["review_complete"] is True
    assert list(training_dataset.iter_confirmed_training_tracks()) == []


def test_objective_pipeline_exclusion_remains_automatic_not_manual(
        monkeypatch, tmp_path,
):
    _patch_store(monkeypatch, tmp_path)
    monkeypatch.setattr(genre_review, "GENRE_REVIEW_FILE", tmp_path / "review.json")
    music = tmp_path / "Music"
    folder = music / "Moombahton"
    folder.mkdir(parents=True)
    track = folder / "duplicate.mp3"
    track.write_bytes(b"duplicate-audio")
    training_dataset.add_training_source(folder, music_root=music)
    training_dataset.preview_training_sources({})
    folder_row = training_dataset.list_training_folders()["items"][0]
    training_dataset.update_training_folders(
        [folder_row["id"]], status="confirmed", taxonomy={"base_style": "Moombahton"},
    )
    for name in (
        "training_errors", "training_review_queue", "training_label_conflicts",
        "training_duplicates", "training_conflicts",
    ):
        path = tmp_path / f"{name}.csv"
        monkeypatch.setattr(training_dataset, name.upper() + "_FILE", path)
        path.write_text("", encoding="utf-8")
    with training_dataset.TRAINING_LABEL_CONFLICTS_FILE.open(
            "w", encoding="utf-8-sig", newline="",
    ) as target:
        writer = csv.DictWriter(target, fieldnames=["path", "decision", "fingerprint_group"])
        writer.writeheader()
        writer.writerow({
            "path": str(track), "decision": "duplicate_excluded",
            "fingerprint_group": "fp-1",
        })

    item = training_dataset.list_training_disputed_tracks(music_dir=music)["items"][0]
    assert item["review_status"] == "automatic"
    assert item["objective_excluded"] is True
    assert genre_review.list_review_entries() == []


@pytest.mark.parametrize(
    ("status", "participates"),
    [
        ("confirmed", True),
        ("suggested", False),
        ("ambiguous", False),
        ("unmapped", False),
        ("excluded", False),
    ],
)
def test_only_confirmed_folder_status_participates(
        monkeypatch, tmp_path, status, participates,
):
    _patch_store(monkeypatch, tmp_path)
    music = tmp_path / "Music"
    folder = music / "Club House"
    folder.mkdir(parents=True)
    (folder / "one.mp3").write_bytes(b"audio")
    training_dataset.add_training_source(folder, music_root=music)
    training_dataset.preview_training_sources({})
    row = training_dataset.list_training_folders()["items"][0]
    training_dataset.update_training_folders(
        [row["id"]], status=status, taxonomy={"base_style": "Club House"},
    )

    tracks = list(training_dataset.iter_confirmed_training_tracks())
    assert bool(tracks) is participates


def test_problem_folder_report_aggregates_without_changing_status(
        monkeypatch, tmp_path,
):
    _patch_store(monkeypatch, tmp_path)
    music = tmp_path / "Music"
    folder = music / "Vocal House _ Future House"
    folder.mkdir(parents=True)
    for index in range(10):
        (folder / f"track-{index}.mp3").write_bytes(b"audio")
    training_dataset.add_training_source(folder, music_root=music)
    training_dataset.preview_training_sources({})
    row = training_dataset.list_training_folders()["items"][0]
    training_dataset.update_training_folders(
        [row["id"]], status="confirmed", taxonomy={"base_style": "Future House"},
    )

    reports = {}
    for name in (
        "training_errors", "training_review_queue", "training_label_conflicts",
        "training_duplicates", "training_conflicts",
    ):
        reports[name] = tmp_path / f"{name}.csv"
    monkeypatch.setattr(training_dataset, "TRAINING_ERRORS_FILE", reports["training_errors"])
    monkeypatch.setattr(training_dataset, "TRAINING_REVIEW_QUEUE_FILE", reports["training_review_queue"])
    monkeypatch.setattr(training_dataset, "TRAINING_LABEL_CONFLICTS_FILE", reports["training_label_conflicts"])
    monkeypatch.setattr(training_dataset, "TRAINING_DUPLICATES_FILE", reports["training_duplicates"])
    monkeypatch.setattr(training_dataset, "TRAINING_CONFLICTS_FILE", reports["training_conflicts"])

    def write(name, fields, values):
        with reports[name].open("w", encoding="utf-8-sig", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=fields)
            writer.writeheader()
            writer.writerows(values)

    track = str(folder / "track-1.mp3")
    write("training_errors", ["path", "true_base_genre", "predicted_base_genre", "is_error"], [
        {"path": track, "true_base_genre": "Future House", "predicted_base_genre": "Club House", "is_error": "1"},
    ])
    write("training_review_queue", ["path"], [{"path": track}])
    write("training_label_conflicts", ["path", "decision"], [
        {"path": track, "decision": "conflicting_labels_excluded"},
    ])
    write("training_duplicates", ["path", "group"], [])
    write("training_conflicts", ["style_a", "style_b"], [])

    report = training_dataset.list_training_problem_folders()
    assert report["total"] == 1
    problem = report["items"][0]
    assert problem["status"] == "confirmed"
    assert problem["review_queue_tracks"] == 1
    assert problem["validation_errors"] == 1
    assert problem["label_conflicts"] == 1
    assert problem["mixed_name_warning"] is True
    assert problem["confusion_pairs"][0] == {
        "true_style": "Future House", "predicted_style": "Club House", "count": 1,
    }
    assert training_dataset.list_training_folders()["items"][0]["status"] == "confirmed"


def test_mixed_name_warning_does_not_split_specific_house_style():
    assert training_dataset._mixed_folder_name_signals({
        "path": r"D:\Music\Deep House",
    }) == []
    warnings = training_dataset._mixed_folder_name_signals({
        "path": r"D:\Music\Vocal House _ Future House",
    })
    assert any("vocal house" in value for value in warnings)


def test_problem_folder_report_exposes_low_risk_without_changing_thresholds(
        monkeypatch, tmp_path,
):
    _patch_store(monkeypatch, tmp_path)
    music = tmp_path / "Music"
    folder = music / "Club House"
    folder.mkdir(parents=True)
    for index in range(10):
        (folder / f"track-{index}.mp3").write_bytes(b"audio")
    training_dataset.add_training_source(folder, music_root=music)
    training_dataset.preview_training_sources({})
    row = training_dataset.list_training_folders()["items"][0]
    training_dataset.update_training_folders(
        [row["id"]], status="confirmed", taxonomy={"base_style": "Club House"},
    )

    reports = {}
    for name in (
        "training_errors", "training_review_queue", "training_label_conflicts",
        "training_duplicates", "training_conflicts",
    ):
        reports[name] = tmp_path / f"{name}.csv"
        monkeypatch.setattr(training_dataset, name.upper() + "_FILE", reports[name])
    with reports["training_errors"].open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=["path", "true_base_genre", "predicted_base_genre", "is_error"])
        writer.writeheader()
        writer.writerow({
            "path": str(folder / "track-1.mp3"), "true_base_genre": "Club House",
            "predicted_base_genre": "Pop", "is_error": "1",
        })
    for name in ("training_review_queue", "training_label_conflicts", "training_duplicates", "training_conflicts"):
        reports[name].write_text("", encoding="utf-8")

    report = training_dataset.list_training_problem_folders(limit=2000)
    assert report["total"] == 1
    assert report["items"][0]["risk"] == "low"
    assert report["summary"]["low_risk_folders"] == 1


def test_dataset_settings_and_track_style_counts(monkeypatch, tmp_path):
    _patch_store(monkeypatch, tmp_path)
    music = tmp_path / "Music"
    folder = music / "Tech House"
    folder.mkdir(parents=True)
    for index in range(3):
        (folder / f"track-{index}.mp3").write_bytes(b"audio")
    training_dataset.add_training_source(folder, music_root=music)
    training_dataset.preview_training_sources({})
    row = training_dataset.list_training_folders()["items"][0]
    training_dataset.update_training_folders([row["id"]], status="confirmed")

    settings = training_dataset.update_training_dataset_settings({"max_tracks_per_style": 1500})
    summary = training_dataset.dataset_summary()
    assert settings["max_tracks_per_style"] == 1500
    assert summary["settings"]["max_tracks_per_style"] == 1500
    assert summary["settings"]["min_tracks_per_style"] == 200
    assert {"Other", "Новогодние", "House"}.issubset(
        summary["settings"]["excluded_styles"]
    )
    assert summary["style_folder_counts"] == {"Tech House": 1}
    assert summary["style_track_counts"] == {"Tech House": 3}


def test_diverse_cap_is_deterministic_and_keeps_multiple_folders(tmp_path):
    from app.models import (
        _cap_training_tasks_by_style,
        _independent_class_cap_indices,
        _rekordbox_track_base_style,
        _training_source_mix_targets,
    )

    tasks = []
    for folder_index in range(3):
        folder = tmp_path / f"set-{folder_index}"
        for track_index in range(60):
            tasks.append((str(folder / f"track-{track_index}.mp3"), "Club House", 22050, 0, 30, {}))
    priority = str(tmp_path / "set-2" / "track-59.mp3")
    selected_one, report_one = _cap_training_tasks_by_style(tasks, 100, {priority})
    selected_two, report_two = _cap_training_tasks_by_style(tasks, 100, {priority})

    assert [row[0] for row in selected_one] == [row[0] for row in selected_two]
    assert len(selected_one) == 100
    assert priority in {row[0] for row in selected_one}
    assert report_one == report_two
    assert report_one["per_style"]["Club House"]["folders"] == 3
    assert report_one["per_style"]["Club House"]["dropped"] == 80
    assert _training_source_mix_targets(3000, 400, 1200) == (800, 400)
    assert _training_source_mix_targets(3000, 100, 1200) == (1100, 100)
    assert _training_source_mix_targets(0, 150, 1200) == (0, 150)

    labels = ["Lounge"] * 230 + ["Club House"] * 1000
    indices, maximum = _independent_class_cap_indices(labels, 800, 42)
    selected_labels = [labels[index] for index in indices]
    assert maximum == 800
    assert selected_labels.count("Lounge") == 230
    assert selected_labels.count("Club House") == 800
    assert _rekordbox_track_base_style(
        {"raw_genre": "Русские Ремиксы", "genre": "Русские Ремиксы", "path": "track.mp3"},
        {},
    ) == "Club House"


def test_training_run_report_does_not_invent_old_classes():
    from app.models import _build_training_run_report

    report = _build_training_run_report(
        False,
        [],
        False,
        ["Afro House", "Club House"],
        {"Afro House": {"precision": 0.8, "recall": 0.7, "f1-score": 0.75, "support": 20}},
        {"per_class": {"Afro House": {"accepted_precision": 0.95, "accepted_tracks": 10}}},
        {"Afro House": 0.7},
        {},
        {"Afro House": 100, "Club House": 120},
        {"before": 220, "after": 220},
        {"passed": False, "reasons": ["quality"]},
        {},
        {},
    )
    assert report["active_before_known"] is False
    assert report["active_after"] is None
    assert report["candidate_styles"] == ["Afro House", "Club House"]
    assert report["added_styles"] == []


def test_manual_confirmation_requires_style(monkeypatch, tmp_path):
    _patch_store(monkeypatch, tmp_path)
    music = tmp_path / "Music"
    unknown = music / "Collection 2025"
    unknown.mkdir(parents=True)
    (unknown / "one.mp3").write_bytes(b"one")
    training_dataset.add_training_source(unknown, music_root=music)
    training_dataset.preview_training_sources({})
    row = training_dataset.list_training_folders()["items"][0]
    try:
        training_dataset.update_training_folders([row["id"]], status="confirmed")
    except ValueError as exc:
        assert "без базового стиля" in str(exc)
    else:
        raise AssertionError("confirmation without a style must fail")

    result = training_dataset.update_training_folders(
        [row["id"]],
        status="confirmed",
        taxonomy={"base_style": "Tech House", "language": "Foreign"},
    )
    assert result["changed"] == 1
    assert result["summary"]["style_counts"] == {"Tech House": 1}


def test_training_source_settings_are_persisted_and_sanitised(monkeypatch, tmp_path):
    _patch_store(monkeypatch, tmp_path)
    reference = tmp_path / "Reference Samples"

    saved = training_dataset.update_training_dataset_settings({
        "use_dataset_builder": False,
        "use_reference_samples": True,
        "reference_samples_path": str(reference),
        "use_rekordbox_training": False,
    })

    assert saved["use_dataset_builder"] is False
    assert saved["use_reference_samples"] is True
    assert saved["reference_samples_path"] == str(reference)
    assert saved["use_rekordbox_training"] is False
    reloaded = training_dataset.get_training_dataset_settings()
    assert reloaded == saved


def test_training_dataset_http_api(monkeypatch, tmp_path):
    from flask import Flask
    import app.routes as routes

    _patch_store(monkeypatch, tmp_path)
    music = tmp_path / "Music"
    folder = music / "DJ Pool - Drum & Bass"
    folder.mkdir(parents=True)
    (folder / "one.mp3").write_bytes(b"one")

    monkeypatch.setattr(routes, "get_advanced_mode", lambda: True)
    monkeypatch.setattr(routes, "load_config", lambda: {"music_dir": str(music)})
    monkeypatch.setattr(routes, "get_training_preflight_report", lambda: {
        "max_tracks_per_style": training_dataset.get_training_dataset_settings()["max_tracks_per_style"],
        "rows": [], "selected_total": 0, "last_run": {},
    })
    monkeypatch.setattr(routes, "list_training_problem_folders", lambda **_values: {
        "items": [{"id": "folder", "risk": "high"}],
        "total": 1,
        "offset": 0,
        "limit": 100,
        "summary": {"problem_folders": 1},
    })
    monkeypatch.setattr(routes, "get_training_preparation_assistant", lambda: {
        "preview_token": "preview", "safe_folder_ids": ["folder"],
        "summary": {"safe_to_apply_folders": 1},
    })
    monkeypatch.setattr(routes, "apply_training_preparation_assistant", lambda ids, token: {
        "changed": len(ids), "applied_folder_ids": ids, "summary": {},
    })
    routes.global_state["training_dataset_thread"] = None
    routes.global_state["training_dataset_progress"] = {
        "status": "idle", "processed": 0, "total": 0,
        "folders": 0, "tracks": 0, "error": "",
    }

    flask_app = Flask(__name__)
    flask_app.secret_key = "test"
    routes.register_routes(flask_app)
    client = flask_app.test_client()

    added = client.post("/api/training-dataset/sources", json={"path": "DJ Pool - Drum & Bass"})
    assert added.status_code == 200
    started = client.post("/api/training-dataset/preview", json={})
    assert started.status_code == 202
    routes.global_state["training_dataset_thread"].join(timeout=5)

    listing = client.get("/api/training-dataset/folders").get_json()
    assert listing["total"] == 1
    folder_id = listing["items"][0]["id"]
    updated = client.patch(
        "/api/training-dataset/folders",
        json={"ids": [folder_id], "status": "confirmed"},
    )
    assert updated.status_code == 200
    assert updated.get_json()["summary"]["confirmed_tracks"] == 1

    problems = client.get("/api/training-dataset/problem-folders").get_json()
    assert problems["total"] == 1
    assert problems["items"][0]["risk"] == "high"

    assistant = client.get("/api/training-dataset/preparation-assistant")
    assert assistant.status_code == 200
    assert assistant.get_json()["preview_token"] == "preview"
    applied = client.post(
        "/api/training-dataset/preparation-assistant/apply",
        json={"folder_ids": ["folder"], "preview_token": "preview"},
    )
    assert applied.status_code == 200
    assert applied.get_json()["applied_folder_ids"] == ["folder"]

    settings = client.patch(
        "/api/training-dataset/settings",
        json={"max_tracks_per_style": 1500, "excluded_styles": ["Tech House"]},
    )
    assert settings.status_code == 200
    assert settings.get_json()["settings"]["max_tracks_per_style"] == 1500
    assert {"Tech House", "Other", "Новогодние", "House"}.issubset(
        settings.get_json()["settings"]["excluded_styles"]
    )
