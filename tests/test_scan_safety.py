import sqlite3

from app.db import create_scan_db_backup


def _create_scan_db(path, rows):
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE scan_results (id INTEGER PRIMARY KEY, rel_path TEXT UNIQUE, genre TEXT)"
    )
    connection.executemany(
        "INSERT INTO scan_results(rel_path, genre) VALUES (?, ?)",
        [(f"Music/track-{index}.mp3", "Club House") for index in range(rows)],
    )
    connection.commit()
    connection.close()


def test_non_empty_scan_database_is_backed_up_atomically(tmp_path):
    source = tmp_path / "scan_results.db"
    backup = tmp_path / "scan_results.backup.db"
    _create_scan_db(source, 3)

    result = create_scan_db_backup(source, backup)

    assert result["backed_up"] is True
    assert result["rows"] == 3
    connection = sqlite3.connect(backup)
    assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert connection.execute("SELECT COUNT(*) FROM scan_results").fetchone()[0] == 3
    connection.close()


def test_empty_scan_database_does_not_overwrite_existing_backup(tmp_path):
    source = tmp_path / "scan_results.db"
    backup = tmp_path / "scan_results.backup.db"
    _create_scan_db(source, 0)
    _create_scan_db(backup, 2)

    result = create_scan_db_backup(source, backup)

    assert result["backed_up"] is False
    assert result["reason"] == "empty_or_missing"
    connection = sqlite3.connect(backup)
    assert connection.execute("SELECT COUNT(*) FROM scan_results").fetchone()[0] == 2
    connection.close()
