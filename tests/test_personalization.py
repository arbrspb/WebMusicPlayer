import json
import sqlite3

import numpy as np

from app import catalog_intelligence as ci
from app import deep_embeddings as deep
from app.personalization import personalization_status, train_personal_rating_model


def _personal_fixture(tmp_path, count=120):
    db_path = tmp_path / "scan_results.db"
    rekordbox_path = tmp_path / "parsed_rekordbox.json"
    favorite_path = tmp_path / "missing_favorite.db"
    model_path = tmp_path / "personal.pkl"
    report_path = tmp_path / "personal-report.json"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE scan_results (
            rel_path TEXT PRIMARY KEY, genre TEXT, base_genre TEXT,
            language TEXT, version_type TEXT, features TEXT
        )
        """
    )
    connection.commit()
    connection.close()
    ci.init_catalog_intelligence_db(db_path)
    connection = sqlite3.connect(db_path)
    records = []
    for index in range(count):
        rating = index % 5 + 1
        rel_path = f"Club\\Track {index}.mp3"
        # Make the preference learnable from the acoustic and character profile.
        value = (rating - 1) / 4
        embedding = np.asarray([value, value ** 2, index / count, 1 - value], dtype=np.float32)
        connection.execute(
            "INSERT INTO scan_results(rel_path, genre, base_genre, language, version_type, features) VALUES (?, ?, ?, ?, ?, ?)",
            (rel_path, "Club House", "Club House", "English", "Remix", json.dumps([value, value ** 2, 1 - value])),
        )
        connection.execute(
            """
            INSERT INTO track_intelligence(
                rel_path, mtime, profile_version, energy, density, brightness,
                danceability, vocalness, valence, bpm, mood, role,
                embedding, embedding_dim, embedding_source, version_group,
                quality_flags, updated_at
            ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 4, 'test', ?, '[]', 'now')
            """,
            (
                rel_path, ci.PROFILE_VERSION, value, value, value, value,
                0.5, value, 120 + rating, "Bright", "Peak",
                embedding.tobytes(), f"group-{index}",
            ),
        )
        records.append({"path": f"Z:/Club/Track {index}.mp3", "Rating": rating})
    connection.commit()
    connection.close()
    rekordbox_path.write_text(json.dumps(records), encoding="utf-8")
    return db_path, rekordbox_path, favorite_path, model_path, report_path


def test_personal_rating_model_uses_only_explicit_stars_and_predicts_catalog(tmp_path):
    db_path, rekordbox_path, favorite_path, model_path, report_path = _personal_fixture(tmp_path)
    progress = {}
    result = train_personal_rating_model(
        db_path=db_path,
        rekordbox_path=rekordbox_path,
        favorite_db_path=favorite_path,
        model_path=model_path,
        report_path=report_path,
        progress=progress,
        n_estimators=40,
    )
    assert result["status"] == "completed"
    assert result["matched_ratings"] == 120
    assert result["catalog_predictions"] == 120
    assert result["metrics"]["mae_stars"] < 0.8
    assert progress["status"] == "completed"
    status = personalization_status(db_path=db_path, model_path=model_path)
    assert status["model_exists"] is True
    assert status["rated_tracks"] == 120
    assert status["predicted_tracks"] == 120
    connection = sqlite3.connect(db_path)
    row = connection.execute(
        "SELECT user_rating, predicted_rating, personal_score, rating_source FROM track_intelligence LIMIT 1"
    ).fetchone()
    connection.close()
    assert 1 <= row[0] <= 5
    assert 1 <= row[1] <= 5
    assert 0 <= row[2] <= 1
    assert row[3] == "rekordbox"


def test_personal_model_works_without_rekordbox_and_uses_deep_vectors(tmp_path):
    db_path, rekordbox_path, favorite_path, model_path, report_path = _personal_fixture(tmp_path)
    favorites = sqlite3.connect(favorite_path)
    favorites.execute("CREATE TABLE favorites(path TEXT PRIMARY KEY, rating INTEGER)")
    favorites.executemany(
        "INSERT INTO favorites(path, rating) VALUES (?, ?)",
        [(f"Club\\Track {index}.mp3", index % 5 + 1) for index in range(120)],
    )
    favorites.commit()
    favorites.close()

    connection = sqlite3.connect(db_path)
    rows = connection.execute("SELECT rel_path FROM track_intelligence").fetchall()
    connection.executemany(
        """
        INSERT INTO track_deep_embeddings(
            rel_path, model_id, mtime, status, embedding, embedding_dim,
            embedding_dtype, segment_count, provider, error, updated_at
        ) VALUES (?, ?, 1, 'completed', ?, 3, 'float16', 3, 'CPU', NULL, 'now')
        """,
        [
            (
                row[0], deep.MODEL_ID,
                deep.vector_to_blob(np.asarray([index % 5, 1, index / 120], dtype=np.float32)),
            )
            for index, row in enumerate(rows)
        ],
    )
    connection.commit()
    connection.close()

    result = train_personal_rating_model(
        db_path=db_path,
        rekordbox_path=rekordbox_path,
        favorite_db_path=favorite_path,
        model_path=model_path,
        report_path=report_path,
        n_estimators=30,
        use_rekordbox=False,
        use_player_ratings=True,
        use_deep_embeddings=True,
    )
    assert result["status"] == "completed"
    assert result["rating_sources"] == ["player"]
    assert result["deep_embedding_dim"] == 3
