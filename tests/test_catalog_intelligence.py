import json
import pickle
import sqlite3

import numpy as np
from sklearn.ensemble import RandomForestClassifier

from app import catalog_intelligence as ci
from app import deep_embeddings as deep


def _fixture_catalog(tmp_path, count=18):
    db_path = tmp_path / "scan_results.db"
    model_path = tmp_path / "genre_model.pkl"
    artifact_path = tmp_path / "catalog_embedding.pkl"
    params = {
        "n_mfcc": 4,
        "features": {
            "mfcc": True,
            "chroma": True,
            "zcr": True,
            "spectral_centroid": True,
            "spectral_bandwidth": True,
            "rms": True,
            "onset_strength": True,
            "tempo": True,
            "spectral_flatness": True,
            "silence_ratio": True,
            "energy_entropy": True,
            "energy_ratio": True,
        },
    }
    layout, acoustic_length = ci._feature_layout(params)
    with open(model_path, "wb") as model_file:
        pickle.dump(
            {"librosa_params": params, "expected_feature_len": acoustic_length + 4},
            model_file,
        )

    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE scan_results (
            rel_path TEXT UNIQUE, genre TEXT, mtime REAL, confidence REAL,
            features TEXT, base_genre TEXT, genre_family TEXT, language TEXT,
            version_type TEXT, mood TEXT, taxonomy_json TEXT
        )
        """
    )
    rng = np.random.default_rng(42)
    for index in range(count):
        vector = rng.normal(index / count, 0.35, acoustic_length).astype(float)
        tempo_slice = layout.get("tempo")
        if tempo_slice:
            vector[tempo_slice.start] = 82 + index * 3
        rms_slice = layout.get("rms")
        if rms_slice:
            vector[rms_slice.start] = 0.05 + index / (count * 5)
        path = f"Club/Artist {index} - Song {index}.mp3"
        connection.execute(
            """
            INSERT INTO scan_results(
                rel_path, genre, mtime, confidence, features, base_genre,
                genre_family, language, version_type, mood, taxonomy_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                path, "Club House", float(index), 0.9, json.dumps(vector.tolist()),
                "Club House", "House", "English" if index % 2 else "Russian",
                "Remix", "", "{}",
            ),
        )
    connection.commit()
    connection.close()
    return db_path, model_path, artifact_path


def test_catalog_index_builds_profiles_embeddings_and_stats(tmp_path):
    db_path, model_path, artifact_path = _fixture_catalog(tmp_path)
    progress = {}

    result = ci.build_catalog_index(
        db_path=db_path,
        model_path=model_path,
        artifact_path=artifact_path,
        dimensions=6,
        progress=progress,
    )

    assert result["status"] == "completed"
    assert result["indexed_tracks"] == 18
    assert artifact_path.exists()
    stats = ci.catalog_stats(db_path)
    assert stats["profiled_tracks"] == 18
    assert stats["embedded_tracks"] == 18
    assert stats["coverage"] == 1.0
    profile = ci.get_track_intelligence("Club/Artist 0 - Song 0.mp3", db_path)
    assert profile["embedding_dim"] == 6
    for metric in ("energy", "density", "brightness", "danceability", "vocalness", "valence"):
        assert 0.0 <= profile[metric] <= 1.0


def test_intelligent_similarity_collections_and_versions_are_queryable(tmp_path):
    db_path, model_path, artifact_path = _fixture_catalog(tmp_path)
    ci.build_catalog_index(
        db_path=db_path,
        model_path=model_path,
        artifact_path=artifact_path,
        dimensions=6,
    )
    source = "Club/Artist 0 - Song 0.mp3"
    similar = ci.find_similar_intelligent(source, limit=5, db_path=db_path)
    assert len(similar) == 5
    assert all(item["path"] != source for item in similar)
    assert all(-1.0 <= item["acoustic_similarity"] <= 1.0 for item in similar)
    assert all(0.0 <= item["bpm_similarity"] <= 1.0 for item in similar)

    collections = ci.list_smart_collections(db_path)
    assert {item["slug"] for item in collections} >= {"warmup", "build", "peak", "vocal"}
    assert sum(item["count"] for item in collections if item["slug"] in {"warmup", "build", "peak"}) == 18

    connection = sqlite3.connect(db_path)
    connection.execute(
        "UPDATE track_intelligence SET version_group='same-song' WHERE rel_path IN (?, ?)",
        (source, "Club/Artist 1 - Song 1.mp3"),
    )
    connection.commit()
    connection.close()
    versions = ci.find_track_versions(source, db_path=db_path)
    assert [item["rel_path"] for item in versions] == ["Club/Artist 1 - Song 1.mp3"]


def test_catalog_schema_and_reindex_are_idempotent(tmp_path):
    db_path, model_path, artifact_path = _fixture_catalog(tmp_path, count=10)
    ci.init_catalog_intelligence_db(db_path)
    ci.init_catalog_intelligence_db(db_path)
    first = ci.build_catalog_index(
        db_path=db_path, model_path=model_path, artifact_path=artifact_path, dimensions=4
    )
    second = ci.build_catalog_index(
        db_path=db_path, model_path=model_path, artifact_path=artifact_path, dimensions=4
    )
    assert first["status"] == second["status"] == "completed"
    assert ci.catalog_stats(db_path)["profiled_tracks"] == 10


def test_catalog_version_key_ignores_mix_key_bpm_and_pool_tags():
    names = [
        "Des & Del - Flying Like A Dragon (Extended Mix) 9A [Tech House].mp3",
        "Des & Del - Flying Like A Dragon [Intro Clean] 1A 130.mp3",
        "Des & Del - Flying Like A Dragon (Original Mix) [Retail Records].mp3",
    ]
    assert len({ci.catalog_version_group_key(name) for name in names}) == 1


def test_incremental_sync_indexes_only_new_scan_rows(tmp_path):
    db_path, model_path, artifact_path = _fixture_catalog(tmp_path, count=10)
    ci.build_catalog_index(
        db_path=db_path, model_path=model_path, artifact_path=artifact_path, dimensions=4
    )
    connection = sqlite3.connect(db_path)
    feature_json = connection.execute(
        "SELECT features FROM scan_results LIMIT 1"
    ).fetchone()[0]
    connection.execute(
        """
        INSERT INTO scan_results(
            rel_path, genre, mtime, confidence, features, base_genre,
            genre_family, language, version_type, mood, taxonomy_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("Club/New Track.mp3", "Club House", 999.0, 0.9, feature_json,
         "Club House", "House", "English", "Original", "", "{}"),
    )
    connection.commit()
    connection.close()

    assert ci.catalog_stats(db_path)["pending_tracks"] == 1
    result = ci.sync_catalog_index(
        db_path=db_path, model_path=model_path, artifact_path=artifact_path
    )
    assert result["status"] == "completed"
    assert result["processed"] == 1
    assert ci.catalog_stats(db_path)["pending_tracks"] == 0
    assert ci.catalog_stats(db_path)["profiled_tracks"] == 11


def test_current_model_labels_filters_and_reference_matching(tmp_path):
    db_path, model_path, artifact_path = _fixture_catalog(tmp_path, count=20)
    ci.build_catalog_index(
        db_path=db_path, model_path=model_path, artifact_path=artifact_path, dimensions=6
    )
    connection = sqlite3.connect(db_path)
    rows = connection.execute(
        "SELECT rel_path, features FROM scan_results ORDER BY rel_path"
    ).fetchall()
    matrix = np.vstack([np.asarray(json.loads(row[1]), dtype=float) for row in rows])
    labels = np.asarray([
        "Club House" if index < len(rows) // 2 else "Hip-Hop"
        for index in range(len(rows))
    ])
    connection.execute(
        "UPDATE scan_results SET base_genre=NULL, language=NULL, taxonomy_json=NULL"
    )
    connection.commit()
    connection.close()
    model = RandomForestClassifier(n_estimators=30, random_state=42).fit(matrix, labels)
    with open(model_path, "rb") as model_file:
        old_meta = pickle.load(model_file)
    with open(model_path, "wb") as model_file:
        pickle.dump({
            **old_meta,
            "model": model,
            "version": "test-current",
            "code_version": "test",
            "training_time": "now",
            "expected_feature_len": int(matrix.shape[1]),
            "class_thresholds": {"Club House": 0.0, "Hip-Hop": 0.0},
            "librosa_params": {
                **old_meta["librosa_params"],
                "genre_threshold": 0.0,
                "min_genre_margin": 0.0,
            },
        }, model_file)

    refresh = ci.refresh_catalog_model_labels(db_path=db_path, model_path=model_path)
    assert refresh["status"] == "completed"
    assert refresh["accepted_styles"] == 20
    options = ci.catalog_filter_options(db_path)
    assert {item["value"] for item in options["styles"]} >= {"Club House", "Hip-Hop"}

    source = rows[0][0]
    matched = ci.match_reference_tracks(
        [source], filters={"style": "Hip-Hop"}, limit=5,
        exclude_versions=False, db_path=db_path
    )
    assert matched["references_used"] == [source]
    assert matched["items"]
    assert all(item["base_genre"] == "Hip-Hop" for item in matched["items"])
    filtered_collection = ci.smart_collection_tracks(
        "build", filters={"style": "Hip-Hop"}, db_path=db_path
    )
    assert all(item["base_genre"] == "Hip-Hop" for item in filtered_collection)


def test_catalog_refresh_replaces_old_machine_label_but_preserves_manual_label(tmp_path):
    db_path, model_path, artifact_path = _fixture_catalog(tmp_path, count=20)
    ci.build_catalog_index(
        db_path=db_path, model_path=model_path, artifact_path=artifact_path, dimensions=6
    )
    connection = sqlite3.connect(db_path)
    rows = connection.execute(
        "SELECT rel_path, features FROM scan_results ORDER BY rel_path"
    ).fetchall()
    matrix = np.vstack([np.asarray(json.loads(row[1]), dtype=float) for row in rows])
    labels = np.asarray(["Tech House"] * len(rows))
    model = RandomForestClassifier(n_estimators=30, random_state=42).fit(matrix, labels)
    with open(model_path, "rb") as model_file:
        old_meta = pickle.load(model_file)
    with open(model_path, "wb") as model_file:
        pickle.dump({
            **old_meta,
            "model": model,
            "version": "test-new-style",
            "code_version": "test",
            "training_time": "now",
            "expected_feature_len": int(matrix.shape[1]),
            "class_thresholds": {"Tech House": 0.0},
            "librosa_params": {
                **old_meta["librosa_params"],
                "genre_threshold": 0.0,
                "min_genre_margin": 0.0,
            },
        }, model_file)

    machine_path = rows[0][0]
    manual_path = rows[1][0]
    connection.execute(
        "UPDATE scan_results SET base_genre=?, taxonomy_json=? WHERE rel_path=?",
        ("Club House", json.dumps({
            "base_genre": "Club House", "base_genre_source": "audio_model",
        }), machine_path),
    )
    connection.execute(
        "UPDATE scan_results SET base_genre=?, taxonomy_json=? WHERE rel_path=?",
        ("Hip-Hop", json.dumps({
            "base_genre": "Hip-Hop", "base_genre_source": "manual_correction",
        }), manual_path),
    )
    connection.commit()
    connection.close()

    refresh = ci.refresh_catalog_model_labels(db_path=db_path, model_path=model_path)
    assert refresh["status"] == "completed"
    connection = sqlite3.connect(db_path)
    refreshed = dict(connection.execute(
        "SELECT rel_path, model_base_genre FROM track_intelligence "
        "WHERE rel_path IN (?, ?)",
        (machine_path, manual_path),
    ).fetchall())
    connection.close()
    assert refreshed[machine_path] == "Tech House"
    assert refreshed[manual_path] == "Hip-Hop"

    options = ci.catalog_filter_options(db_path)
    assert "Tech House" in {item["value"] for item in options["styles"]}
    matched = ci.match_reference_tracks(
        [machine_path], filters={"style": "Tech House"}, limit=5,
        exclude_versions=False, db_path=db_path,
    )
    assert matched["items"]
    assert all(item["base_genre"] == "Tech House" for item in matched["items"])


def test_reference_matching_normalizes_saved_preference_weights(tmp_path):
    db_path, model_path, artifact_path = _fixture_catalog(tmp_path, count=12)
    ci.build_catalog_index(
        db_path=db_path, model_path=model_path, artifact_path=artifact_path, dimensions=5
    )
    result = ci.match_reference_tracks(
        ["Club/Artist 0 - Song 0.mp3"],
        limit=3,
        exclude_versions=False,
        weights={"acoustic": 70, "character": 20, "semantic": 0, "bpm": 10},
        db_path=db_path,
    )
    assert result["items"]
    assert {
        "similarity", "acoustic_similarity", "deep_similarity",
        "semantic_similarity", "character_similarity", "bpm_similarity",
        "personal_score",
    }.issubset(result["items"][0])
    assert result["weights"] == {
        "deep": 40 / 150,
        "acoustic": 70 / 150,
        "character": 20 / 150,
        "semantic": 0.0,
        "bpm": 10 / 150,
        "personal": 10 / 150,
    }


def test_reference_matching_uses_deep_effnet_vectors_when_available(tmp_path):
    db_path, model_path, artifact_path = _fixture_catalog(tmp_path, count=12)
    ci.build_catalog_index(
        db_path=db_path, model_path=model_path, artifact_path=artifact_path, dimensions=5
    )
    connection = sqlite3.connect(db_path)
    rows = connection.execute("SELECT rel_path, mtime FROM scan_results ORDER BY rel_path").fetchall()
    now = "2026-08-11T00:00:00"
    payload = []
    for index, (path, mtime) in enumerate(rows):
        vector = np.asarray(
            [1.0, 0.0, 0.0] if index < 2 else [-1.0, 0.0, 0.0], dtype=np.float32
        )
        payload.append((
            path, deep.MODEL_ID, mtime, "completed", deep.vector_to_blob(vector),
            3, "float16", 3, "CPUExecutionProvider", None, now,
        ))
    connection.executemany(
        """
        INSERT INTO track_deep_embeddings(
            rel_path, model_id, mtime, status, embedding, embedding_dim,
            embedding_dtype, segment_count, provider, error, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        payload,
    )
    connection.commit()
    connection.close()

    result = ci.match_reference_tracks(
        [rows[0][0]],
        limit=3,
        exclude_versions=False,
        weights={
            "deep": 100, "acoustic": 0, "character": 0,
            "semantic": 0, "bpm": 0, "personal": 0,
        },
        db_path=db_path,
    )
    assert result["items"][0]["path"] == rows[1][0]
    assert result["items"][0]["deep_similarity"] == 1.0
