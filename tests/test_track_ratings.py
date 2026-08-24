import sqlite3

from app.db import get_track_ratings, init_favorite_db, set_track_rating


def _scan_database(path, track_path):
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE track_intelligence(
            rel_path TEXT PRIMARY KEY, user_rating REAL, rating_source TEXT,
            personal_score REAL
        )
        """
    )
    connection.execute("INSERT INTO track_intelligence(rel_path) VALUES (?)", (track_path,))
    connection.commit()
    connection.close()


def test_rating_is_saved_without_adding_a_favorite_and_updates_catalog(tmp_path):
    favorite_db = tmp_path / "favorites.db"
    scan_db = tmp_path / "scan.db"
    path = "2025/Club/Artist - Track.mp3"
    _scan_database(scan_db, path)

    result = set_track_rating(path.upper(), 5, favorite_db_path=favorite_db, scan_db_path=scan_db)
    assert result == {"path": path.upper(), "rating": 5, "favorite": False}
    assert get_track_ratings(favorite_db)[path.upper()] == 5

    connection = sqlite3.connect(scan_db)
    catalog_row = connection.execute(
        "SELECT user_rating, rating_source, personal_score FROM track_intelligence"
    ).fetchone()
    connection.close()
    assert catalog_row == (5.0, "player", 1.0)


def test_reset_does_not_remove_favorite_and_legacy_rating_is_migrated(tmp_path):
    favorite_db = tmp_path / "favorites.db"
    scan_db = tmp_path / "scan.db"
    path = "Club/Legacy.mp3"
    connection = sqlite3.connect(favorite_db)
    connection.execute("CREATE TABLE favorites(path TEXT PRIMARY KEY, genre TEXT, rating INTEGER DEFAULT 0)")
    connection.execute("INSERT INTO favorites(path, genre, rating) VALUES (?, ?, ?)", (path, "House", 4))
    connection.commit()
    connection.close()

    init_favorite_db(favorite_db)
    assert get_track_ratings(favorite_db)[path] == 4
    result = set_track_rating(path, 0, favorite_db_path=favorite_db, scan_db_path=scan_db)
    assert result["favorite"] is True
    assert path not in get_track_ratings(favorite_db)

    connection = sqlite3.connect(favorite_db)
    assert connection.execute("SELECT rating FROM favorites WHERE path=?", (path,)).fetchone()[0] == 0
    connection.close()
