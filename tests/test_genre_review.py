import sys
from pathlib import Path

from flask import Flask


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app import genre_review  # noqa: E402
from app.librosa_settings import librosa_test_bp  # noqa: E402


def test_review_candidate_becomes_runtime_and_training_correction(tmp_path, monkeypatch):
    store_file = tmp_path / "genre_review_queue.json"
    audio_file = tmp_path / "borderline.mp3"
    audio_file.write_bytes(b"audio" * 100)
    monkeypatch.setattr(genre_review, "GENRE_REVIEW_FILE", store_file)

    pending = genre_review.record_review_candidate(str(audio_file), {
        "predicted_genre": "Unknown",
        "confidence": 0.51,
        "top_candidates": [
            {"genre": "Drum & Bass", "confidence": 0.51},
            {"genre": "Club House", "confidence": 0.39},
        ],
        "rejected_reasons": ["conf 0.510 < threshold 0.650"],
        "segment_disagreement": True,
    })

    assert pending["status"] == "pending"
    assert len(genre_review.list_review_entries("pending")) == 1

    corrected = genre_review.save_manual_correction(
        str(audio_file),
        "Club House",
        language="English",
        version_type="Blend",
    )

    assert corrected["status"] == "corrected"
    assert genre_review.get_manual_correction(str(audio_file))["corrected_base_genre"] == "Club House"
    assert genre_review.iter_training_corrections({"Club House"})[0]["path"] == str(audio_file)
    assert genre_review.iter_training_corrections({"Drum & Bass"}) == []


def test_review_api_validates_and_deletes_correction(tmp_path, monkeypatch):
    store_file = tmp_path / "genre_review_queue.json"
    audio_file = tmp_path / "manual.mp3"
    audio_file.write_bytes(b"audio" * 100)
    monkeypatch.setattr(genre_review, "GENRE_REVIEW_FILE", store_file)

    app = Flask(__name__)
    app.register_blueprint(librosa_test_bp)
    client = app.test_client()

    response = client.post("/librosa-review/corrections", json={
        "path": str(audio_file),
        "base_genre": "Club House",
        "language": "English",
        "version_type": "Blend",
    })
    assert response.status_code == 200
    entry_id = response.get_json()["entry"]["id"]

    listing = client.get("/librosa-review?status=corrected").get_json()["entries"]
    assert listing[0]["corrected_version_type"] == "Blend"

    deleted = client.delete(f"/librosa-review/{entry_id}")
    assert deleted.status_code == 200
    assert genre_review.list_review_entries() == []


def test_review_rejects_unknown_style(tmp_path, monkeypatch):
    audio_file = tmp_path / "invalid.mp3"
    audio_file.write_bytes(b"audio")
    monkeypatch.setattr(genre_review, "GENRE_REVIEW_FILE", tmp_path / "store.json")

    try:
        genre_review.save_manual_correction(str(audio_file), "Invented Style")
    except ValueError as exc:
        assert "Неизвестный базовый стиль" in str(exc)
    else:
        raise AssertionError("unknown style must be rejected")


def test_training_override_survives_path_change_by_audio_fingerprint(tmp_path, monkeypatch):
    store_file = tmp_path / "genre_review_queue.json"
    original = tmp_path / "original.mp3"
    original.write_bytes(b"stable-audio" * 100)
    monkeypatch.setattr(genre_review, "GENRE_REVIEW_FILE", store_file)

    saved = genre_review.save_training_override(
        str(original), exclude_from_training=True, reviewed=True,
        reason="manual_training_exclusion",
    )
    moved = tmp_path / "moved.mp3"
    original.rename(moved)

    found = genre_review.get_training_override(str(moved))
    assert found["id"] == saved["id"]
    assert found["exclude_from_training"] is True
    fast_found = genre_review.find_training_override(
        genre_review.training_override_index(), str(moved),
    )
    assert fast_found["id"] == saved["id"]


def test_style_override_is_a_training_correction_and_exclusion_wins(tmp_path, monkeypatch):
    store_file = tmp_path / "genre_review_queue.json"
    audio_file = tmp_path / "review.mp3"
    audio_file.write_bytes(b"audio" * 100)
    monkeypatch.setattr(genre_review, "GENRE_REVIEW_FILE", store_file)

    genre_review.save_training_override(
        str(audio_file), style_override="Moombahton", reviewed=True,
    )
    assert genre_review.iter_training_corrections({"Moombahton"})[0]["path"] == str(audio_file)

    genre_review.save_training_override(
        str(audio_file), exclude_from_training=True, reviewed=True,
    )
    assert genre_review.iter_training_corrections({"Moombahton"}) == []
