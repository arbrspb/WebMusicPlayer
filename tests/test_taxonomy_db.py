import sqlite3
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app import db  # noqa: E402


def test_old_scan_database_is_migrated_and_taxonomy_is_saved(tmp_path, monkeypatch):
    scan_db = tmp_path / "scan_results.db"
    with sqlite3.connect(scan_db) as connection:
        connection.execute("""
            CREATE TABLE scan_results (
                id INTEGER PRIMARY KEY,
                rel_path TEXT UNIQUE,
                genre TEXT,
                mtime REAL,
                confidence REAL,
                features TEXT
            )
        """)

    monkeypatch.setattr(db, "SCAN_DB", str(scan_db))
    db.ensure_scan_results_yamnet_columns()

    taxonomy = {
        "base_genre": "Club House",
        "genre_family": "House",
        "language": "Russian",
        "version_type": "Remix",
        "mood": "Ставим",
        "dj_category": "Русские Ремиксы",
    }
    db.save_scan_result(
        "Artist - Track.mp3",
        "Русские Ремиксы",
        123.0,
        0.91,
        features=[1.0, 2.0],
        taxonomy=taxonomy,
    )

    with sqlite3.connect(scan_db) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(scan_results)")}
        stored = connection.execute(
            "SELECT genre, base_genre, language, version_type, mood FROM scan_results"
        ).fetchone()

    assert {
        "rf_proba", "yamnet_prior", "fused_proba", "base_genre",
        "genre_family", "language", "version_type", "mood", "taxonomy_json",
    }.issubset(columns)
    assert stored == ("Русские Ремиксы", "Club House", "Russian", "Remix", "Ставим")
    assert db.load_scan_taxonomy("Artist - Track.mp3") == taxonomy
