import sqlite3

from app import db


def _use_temp_scan_db(tmp_path, monkeypatch):
    scan_db = tmp_path / "scan_results.db"
    monkeypatch.setattr(db, "SCAN_DB", str(scan_db))
    db.init_scan_db()
    return scan_db


def test_deferred_language_is_durable_and_updates_dj_category(tmp_path, monkeypatch):
    scan_db = _use_temp_scan_db(tmp_path, monkeypatch)
    taxonomy = {
        "base_genre": "Club House",
        "genre_family": "House",
        "language": "Unknown",
        "language_source": "rf_rejected",
        "version_type": "Remix",
        "dj_category": "Club House",
    }
    db.save_scan_result(
        "Russian remix.mp3",
        "Club House",
        1.0,
        0.8,
        features=[1.0, 2.0],
        taxonomy=taxonomy,
        defer_vocal_language=True,
    )

    assert db.get_language_enrichment_stats()["pending"] == 1
    assert db.claim_next_language_enrichment() == "Russian remix.mp3"
    db.finish_language_enrichment("Russian remix.mp3", {
        "language": "Russian",
        "confidence": 0.94,
        "status": "accepted",
        "source": "faster-whisper",
    })

    with sqlite3.connect(scan_db) as connection:
        genre, language = connection.execute(
            "SELECT genre, language FROM scan_results WHERE rel_path=?",
            ("Russian remix.mp3",),
        ).fetchone()
    assert genre == "Русские Ремиксы"
    assert language == "Russian"
    assert db.get_language_enrichment_stats()["completed"] == 1


def test_explicit_metadata_language_does_not_enter_whisper_queue(tmp_path, monkeypatch):
    _use_temp_scan_db(tmp_path, monkeypatch)
    db.save_scan_result(
        "English tagged.mp3",
        "Club House",
        1.0,
        0.9,
        features=[1.0],
        taxonomy={
            "base_genre": "Club House",
            "language": "English",
            "language_source": "metadata",
        },
        defer_vocal_language=True,
    )

    stats = db.get_language_enrichment_stats()
    assert stats["not_needed"] == 1
    assert stats["total"] == 0
    assert db.claim_next_language_enrichment() is None


def test_inconclusive_whisper_does_not_keep_legacy_rf_russian_category(tmp_path, monkeypatch):
    scan_db = _use_temp_scan_db(tmp_path, monkeypatch)
    db.save_scan_result(
        "Foreign club track.mp3",
        "Русские Ремиксы",
        1.0,
        0.81,
        features=[1.0, 2.0],
        taxonomy={
            "base_genre": "Club House",
            "genre_family": "House",
            "language": "Russian",
            "language_confidence": 0.88,
            "language_source": "rf",
            "version_type": "Remix",
            "dj_category": "Русские Ремиксы",
        },
        defer_vocal_language=True,
    )

    assert db.claim_next_language_enrichment() == "Foreign club track.mp3"
    db.finish_language_enrichment("Foreign club track.mp3", {
        "language": "Unknown",
        "confidence": 0.0,
        "status": "insufficient_speech",
        "source": "faster-whisper",
    })

    with sqlite3.connect(scan_db) as connection:
        genre, language, taxonomy_json = connection.execute(
            "SELECT genre, language, taxonomy_json FROM scan_results WHERE rel_path=?",
            ("Foreign club track.mp3",),
        ).fetchone()

    import json
    taxonomy = json.loads(taxonomy_json)
    assert genre == "Club House"
    assert language == "Unknown"
    assert taxonomy["language_source"] == "vocal_inconclusive"
    assert db.get_language_enrichment_stats()["completed"] == 1


def test_retry_failed_language_enrichment_only_resets_failed(tmp_path, monkeypatch):
    scan_db = _use_temp_scan_db(tmp_path, monkeypatch)
    for name in ("failed.mp3", "completed.mp3", "not-needed.mp3"):
        db.save_scan_result(
            name, "Club House", 1.0, 0.9, features=[1.0],
            taxonomy={"base_genre": "Club House", "language": "Unknown"},
            defer_vocal_language=True,
        )
    with sqlite3.connect(scan_db) as connection:
        connection.execute("UPDATE language_enrichment SET status='failed', attempts=3, error='bad' WHERE rel_path='failed.mp3'")
        connection.execute("UPDATE language_enrichment SET status='completed', attempts=2, error=NULL WHERE rel_path='completed.mp3'")
        connection.execute("UPDATE language_enrichment SET status='not_needed', attempts=1, error=NULL WHERE rel_path='not-needed.mp3'")
        connection.commit()

    assert db.retry_failed_language_enrichment() == 1
    with sqlite3.connect(scan_db) as connection:
        rows = dict(connection.execute("SELECT rel_path, status FROM language_enrichment"))
        attempts, error = connection.execute(
            "SELECT attempts, error FROM language_enrichment WHERE rel_path='failed.mp3'"
        ).fetchone()
    assert rows == {
        "failed.mp3": "pending",
        "completed.mp3": "completed",
        "not-needed.mp3": "not_needed",
    }
    assert attempts == 0
    assert error is None
