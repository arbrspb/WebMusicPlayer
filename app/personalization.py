"""Stage 4: a personal rating model trained only on explicit user ratings."""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
import pickle
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import unquote

import numpy as np
from scipy.stats import spearmanr
from sklearn.ensemble import ExtraTreesClassifier, ExtraTreesRegressor
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import GroupShuffleSplit

from .catalog_intelligence import EFFECTIVE_STYLE_SQL, _blob_to_vector, init_catalog_intelligence_db
from .db import get_track_ratings
from .deep_embeddings import MODEL_ID as DEEP_MODEL_ID, blob_to_vector as _deep_blob_to_vector
from .paths import (
    FAVORITE_DB_FILE,
    PERSONAL_RATING_MODEL_FILE,
    PERSONAL_RATING_REPORT_FILE,
    REKORDBOX_OUTPUT_DIR,
    SCAN_DB_FILE,
)


MODEL_VERSION = "4.1-personal-rating-deep-v1"
DEFAULT_REKORDBOX_FILE = REKORDBOX_OUTPUT_DIR / "parsed_rekordbox.json"
CHARACTER_FIELDS = (
    "energy", "density", "brightness", "danceability", "vocalness", "valence", "bpm"
)
CATEGORY_FIELDS = ("effective_style", "effective_language", "role", "mood", "version_type")


def _connect(db_path=SCAN_DB_FILE):
    connection = sqlite3.connect(os.fspath(db_path), timeout=60)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=60000")
    return connection


def _set_progress(progress, **values):
    if isinstance(progress, dict):
        progress.update(values)


def _save_state(connection, payload):
    now = _dt.datetime.now().isoformat()
    connection.execute(
        """
        INSERT INTO catalog_intelligence_state(key, value, updated_at)
        VALUES ('personal_rating_model', ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (json.dumps(payload, ensure_ascii=False), now),
    )


def _canonical_path(value):
    text = unicodedata.normalize("NFC", unquote(str(value or "")).strip())
    text = text.replace("/", "\\")
    text = re.sub(r"^[A-Za-z]:\\+", "", text)
    text = re.sub(r"\\+", r"\\", text).strip("\\")
    return text.casefold()


def _canonical_filename(value):
    path = _canonical_path(value)
    name = path.rsplit("\\", 1)[-1]
    return re.sub(r"\s+", " ", name).strip()


def _load_explicit_ratings(
        rekordbox_path,
        favorite_db_path=None,
        *,
        use_rekordbox=True,
        use_player_ratings=True,
):
    records = []
    if use_rekordbox and rekordbox_path and os.path.isfile(rekordbox_path):
        with open(rekordbox_path, "r", encoding="utf-8") as source:
            raw_tracks = json.load(source)
        for track in raw_tracks if isinstance(raw_tracks, list) else []:
            try:
                rating = int(track.get("Rating", track.get("rating", 0)) or 0)
            except (TypeError, ValueError):
                continue
            if 1 <= rating <= 5:
                path = track.get("path") or track.get("Path") or ""
                if path:
                    records.append({"path": path, "rating": rating, "source": "rekordbox"})

    if use_player_ratings and favorite_db_path and os.path.isfile(favorite_db_path):
        try:
            # Ratings made in the player are appended last and override an older export.
            records.extend(
                {"path": path, "rating": int(rating), "source": "player"}
                for path, rating in get_track_ratings(favorite_db_path).items() if path
            )
        except sqlite3.Error:
            pass
    return records


def _match_ratings_to_catalog(connection, records):
    catalog_paths = [row[0] for row in connection.execute(
        "SELECT rel_path FROM track_intelligence WHERE embedding IS NOT NULL"
    ).fetchall()]
    exact = {_canonical_path(path): path for path in catalog_paths}
    filenames = defaultdict(list)
    for path in catalog_paths:
        filenames[_canonical_filename(path)].append(path)

    matched = {}
    ambiguous = 0
    for record in records:
        rel_path = exact.get(_canonical_path(record["path"]))
        if rel_path is None:
            candidates = filenames.get(_canonical_filename(record["path"]), [])
            if len(candidates) == 1:
                rel_path = candidates[0]
            elif len(candidates) > 1:
                ambiguous += 1
        if rel_path:
            matched[rel_path] = {
                "rating": int(record["rating"]),
                "source": record["source"],
            }
    return matched, ambiguous


def _category_vocab(rows):
    result = {}
    for field in CATEGORY_FIELDS:
        counts = Counter(str(row[field] or "Unknown") for row in rows)
        result[field] = sorted(counts)
    return result


def _decode_raw_features(value, expected_dim):
    if not expected_dim:
        return np.empty(0, dtype=np.float32)
    try:
        decoded = np.asarray(json.loads(value), dtype=np.float32).reshape(-1)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if decoded.size != expected_dim:
        return None
    return np.nan_to_num(decoded)


def _feature_vector(
        row, embedding_dim, raw_feature_dim, categories, deep_embedding_dim=0
):
    embedding = _blob_to_vector(row["embedding"], row["embedding_dim"])
    if embedding is None or embedding.size != embedding_dim:
        return None
    raw_features = _decode_raw_features(row["raw_features"], raw_feature_dim)
    if raw_features is None:
        return None
    deep_available = 0.0
    deep_embedding = np.empty(0, dtype=np.float32)
    if deep_embedding_dim:
        deep_embedding = _deep_blob_to_vector(
            row["deep_embedding"], row["deep_embedding_dim"], row["deep_embedding_dtype"]
        )
        if deep_embedding is None or deep_embedding.size != deep_embedding_dim:
            deep_embedding = np.zeros(deep_embedding_dim, dtype=np.float32)
        else:
            deep_available = 1.0
    numeric = [
        float(row["energy"] or 0), float(row["density"] or 0),
        float(row["brightness"] or 0), float(row["danceability"] or 0),
        float(row["vocalness"] or 0), float(row["valence"] or 0.5),
        min(max(float(row["bpm"] or 0) / 200.0, 0.0), 1.5),
    ]
    encoded = []
    for field in CATEGORY_FIELDS:
        value = str(row[field] or "Unknown")
        encoded.extend(1.0 if item == value else 0.0 for item in categories[field])
    return np.concatenate((
        embedding.astype(np.float32), raw_features,
        deep_embedding.astype(np.float32),
        np.asarray(
            numeric + ([deep_available] if deep_embedding_dim else []) + encoded,
            dtype=np.float32,
        ),
    ))


def _training_rows(connection, matched, deep_model_id=DEEP_MODEL_ID):
    if not matched:
        return []
    paths = list(matched)
    rows = []
    for start in range(0, len(paths), 800):
        batch = paths[start:start + 800]
        placeholders = ",".join("?" for _ in batch)
        rows.extend(connection.execute(
            f"""
            SELECT ti.*, sr.features AS raw_features, {EFFECTIVE_STYLE_SQL} AS effective_style,
                   COALESCE(NULLIF(ti.model_language, ''), NULLIF(sr.language, ''), 'Unknown') AS effective_language,
                   COALESCE(NULLIF(sr.version_type, ''), 'Unknown') AS version_type,
                   de.embedding AS deep_embedding,
                   de.embedding_dim AS deep_embedding_dim,
                   de.embedding_dtype AS deep_embedding_dtype
            FROM track_intelligence ti
            LEFT JOIN scan_results sr ON sr.rel_path = ti.rel_path
            LEFT JOIN track_deep_embeddings de
              ON de.rel_path=ti.rel_path AND de.model_id=? AND de.status='completed'
            WHERE ti.rel_path IN ({placeholders}) AND ti.embedding IS NOT NULL
            """,
            [deep_model_id, *batch],
        ).fetchall())
    return rows


def _predict_models(artifact, matrix):
    regressor = artifact["regressor"]
    classifier = artifact["classifier"]
    predicted = np.clip(regressor.predict(matrix), 1.0, 5.0)
    probabilities = classifier.predict_proba(matrix)
    high_index = list(classifier.classes_).index(1)
    high_probability = probabilities[:, high_index]
    tree_predictions = np.vstack([tree.predict(matrix) for tree in regressor.estimators_])
    disagreement = np.std(tree_predictions, axis=0)
    confidence = np.clip(1.0 - disagreement / 1.6, 0.10, 1.0)
    normalized_stars = np.clip((predicted - 1.0) / 4.0, 0.0, 1.0)
    personal_score = np.clip(normalized_stars * 0.55 + high_probability * 0.45, 0.0, 1.0)
    return predicted, high_probability, confidence, personal_score


def train_personal_rating_model(
        *,
        db_path=SCAN_DB_FILE,
        rekordbox_path=DEFAULT_REKORDBOX_FILE,
        favorite_db_path=FAVORITE_DB_FILE,
        model_path=PERSONAL_RATING_MODEL_FILE,
        report_path=PERSONAL_RATING_REPORT_FILE,
        progress=None,
        n_estimators=350,
        use_rekordbox=True,
        use_player_ratings=True,
        use_deep_embeddings=True,
):
    """Train and apply a personal preference model without treating rating=0 as negative."""
    init_catalog_intelligence_db(db_path)
    _set_progress(progress, status="loading_ratings", processed=0, total=0, error="")
    explicit = _load_explicit_ratings(
        os.fspath(rekordbox_path),
        favorite_db_path,
        use_rekordbox=use_rekordbox,
        use_player_ratings=use_player_ratings,
    )
    if not explicit:
        raise ValueError(
            "Не найдено оценок 1–5★. Включите оценки плеера или импорт Rekordbox."
        )
    connection = _connect(db_path)
    try:
        matched, ambiguous = _match_ratings_to_catalog(connection, explicit)
        rows = _training_rows(connection, matched)
        if len(rows) < 100:
            raise ValueError(
                f"Для персональной модели найдено только {len(rows)} оценённых треков; требуется минимум 100"
            )
        ratings = np.asarray([matched[row["rel_path"]]["rating"] for row in rows], dtype=float)
        if len(np.unique(ratings)) < 2:
            raise ValueError("Для обучения нужны хотя бы две разные оценки")
        embedding_dim = Counter(int(row["embedding_dim"]) for row in rows).most_common(1)[0][0]
        rows = [row for row in rows if int(row["embedding_dim"]) == embedding_dim]
        deep_dimensions = Counter(
            int(row["deep_embedding_dim"] or 0)
            for row in rows if int(row["deep_embedding_dim"] or 0) > 0
        )
        deep_embedding_dim = (
            deep_dimensions.most_common(1)[0][0]
            if deep_dimensions and use_deep_embeddings else 0
        )
        raw_dimensions = Counter()
        for row in rows:
            try:
                raw_dimensions[len(json.loads(row["raw_features"]))] += 1
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        raw_feature_dim = raw_dimensions.most_common(1)[0][0] if raw_dimensions else 0
        categories = _category_vocab(rows)
        vectors, kept_rows = [], []
        for row in rows:
            vector = _feature_vector(
                row, embedding_dim, raw_feature_dim, categories, deep_embedding_dim
            )
            if vector is not None:
                vectors.append(vector)
                kept_rows.append(row)
        matrix = np.vstack(vectors)
        ratings = np.asarray([matched[row["rel_path"]]["rating"] for row in kept_rows], dtype=float)
        high_labels = (ratings >= 4).astype(int)
        groups = np.asarray([row["version_group"] or row["rel_path"] for row in kept_rows])
        split = GroupShuffleSplit(n_splits=1, test_size=0.20, random_state=42)
        train_idx, test_idx = next(split.split(matrix, ratings, groups))

        counts = Counter(ratings[train_idx].astype(int))
        sample_weights = np.asarray([
            1.0 / math.sqrt(counts[int(rating)]) for rating in ratings[train_idx]
        ])
        sample_weights /= float(np.mean(sample_weights))
        workers = max(1, min(8, os.cpu_count() or 1))
        regressor = ExtraTreesRegressor(
            n_estimators=int(n_estimators), min_samples_leaf=3, max_features=0.75,
            random_state=42, n_jobs=workers,
        )
        classifier = ExtraTreesClassifier(
            n_estimators=int(n_estimators), min_samples_leaf=3, max_features=0.75,
            class_weight="balanced", random_state=43, n_jobs=workers,
        )
        _set_progress(progress, status="training", processed=0, total=len(kept_rows), error="")
        regressor.fit(matrix[train_idx], ratings[train_idx], sample_weight=sample_weights)
        classifier.fit(matrix[train_idx], high_labels[train_idx], sample_weight=sample_weights)

        artifact = {
            "version": MODEL_VERSION,
            "trained_at": _dt.datetime.now().isoformat(),
            "embedding_dim": embedding_dim,
            "deep_embedding_dim": deep_embedding_dim,
            "deep_model_id": DEEP_MODEL_ID if deep_embedding_dim else None,
            "raw_feature_dim": raw_feature_dim,
            "categories": categories,
            "regressor": regressor,
            "classifier": classifier,
            "feature_dim": int(matrix.shape[1]),
        }
        validation_prediction, validation_high, _, _ = _predict_models(artifact, matrix[test_idx])
        predicted_high = (validation_high >= 0.5).astype(int)
        correlation = spearmanr(ratings[test_idx], validation_prediction).statistic
        metrics = {
            "mae_stars": round(float(mean_absolute_error(ratings[test_idx], validation_prediction)), 6),
            "rmse_stars": round(float(mean_squared_error(ratings[test_idx], validation_prediction) ** 0.5), 6),
            "spearman": round(float(correlation if np.isfinite(correlation) else 0.0), 6),
            "within_one_star": round(float(np.mean(np.abs(ratings[test_idx] - validation_prediction) <= 1.0)), 6),
            "high_rating_accuracy": round(float(accuracy_score(high_labels[test_idx], predicted_high)), 6),
            "high_rating_precision": round(float(precision_score(high_labels[test_idx], predicted_high, zero_division=0)), 6),
            "high_rating_recall": round(float(recall_score(high_labels[test_idx], predicted_high, zero_division=0)), 6),
            "high_rating_roc_auc": round(float(roc_auc_score(high_labels[test_idx], validation_high)), 6),
            "high_rating_average_precision": round(float(average_precision_score(high_labels[test_idx], validation_high)), 6),
            "validation_tracks": int(len(test_idx)),
        }
        artifact["metrics"] = metrics
        Path(model_path).parent.mkdir(parents=True, exist_ok=True)
        with open(model_path, "wb") as target:
            pickle.dump(artifact, target)

        connection.execute(
            "UPDATE track_intelligence SET user_rating=NULL, rating_source=NULL, "
            "predicted_rating=NULL, rating_confidence=NULL, personal_score=NULL, personal_model_version=NULL"
        )
        connection.executemany(
            "UPDATE track_intelligence SET user_rating=?, rating_source=? WHERE rel_path=?",
            [
                (entry["rating"], entry["source"], path)
                for path, entry in matched.items()
            ],
        )
        connection.commit()

        total = int(connection.execute(
            "SELECT COUNT(1) FROM track_intelligence WHERE embedding IS NOT NULL"
        ).fetchone()[0])
        cursor = connection.execute(
            f"""
            SELECT ti.*, sr.features AS raw_features, {EFFECTIVE_STYLE_SQL} AS effective_style,
                   COALESCE(NULLIF(ti.model_language, ''), NULLIF(sr.language, ''), 'Unknown') AS effective_language,
                   COALESCE(NULLIF(sr.version_type, ''), 'Unknown') AS version_type,
                   de.embedding AS deep_embedding,
                   de.embedding_dim AS deep_embedding_dim,
                   de.embedding_dtype AS deep_embedding_dtype
            FROM track_intelligence ti
            LEFT JOIN scan_results sr ON sr.rel_path = ti.rel_path
            LEFT JOIN track_deep_embeddings de
              ON de.rel_path=ti.rel_path AND de.model_id=? AND de.status='completed'
            WHERE ti.embedding IS NOT NULL
            ORDER BY ti.rel_path
            """,
            (DEEP_MODEL_ID,),
        )
        processed = 0
        _set_progress(progress, status="predicting_catalog", processed=0, total=total, error="")
        while True:
            batch = cursor.fetchmany(1000)
            if not batch:
                break
            batch_vectors, valid_rows = [], []
            for row in batch:
                vector = _feature_vector(
                    row, embedding_dim, raw_feature_dim, categories, deep_embedding_dim
                )
                if vector is not None:
                    batch_vectors.append(vector)
                    valid_rows.append(row)
            if valid_rows:
                predicted, high_probability, confidence, personal = _predict_models(
                    artifact, np.vstack(batch_vectors)
                )
                updates = []
                for index, row in enumerate(valid_rows):
                    actual = row["user_rating"]
                    personal_value = (
                        (float(actual) - 1.0) / 4.0
                        if actual is not None
                        else 0.5 + (float(personal[index]) - 0.5) * float(confidence[index])
                    )
                    updates.append((
                        float(predicted[index]), float(confidence[index]), float(personal_value),
                        MODEL_VERSION, row["rel_path"],
                    ))
                connection.executemany(
                    """
                    UPDATE track_intelligence
                    SET predicted_rating=?, rating_confidence=?, personal_score=?, personal_model_version=?
                    WHERE rel_path=?
                    """,
                    updates,
                )
            processed += len(batch)
            connection.commit()
            _set_progress(progress, status="predicting_catalog", processed=processed, total=total, error="")

        rating_distribution = dict(sorted(Counter(int(value) for value in ratings).items()))
        report = {
            "status": "completed",
            "model_version": MODEL_VERSION,
            "trained_at": artifact["trained_at"],
            "explicit_ratings": len(explicit),
            "matched_ratings": len(kept_rows),
            "unmatched_ratings": max(0, len(explicit) - len(matched)),
            "ambiguous_filenames": ambiguous,
            "catalog_predictions": processed,
            "rating_distribution": rating_distribution,
            "rating_sources": sorted({entry["source"] for entry in matched.values()}),
            "deep_embedding_dim": deep_embedding_dim,
            "metrics": metrics,
        }
        _save_state(connection, report)
        connection.commit()
        Path(report_path).parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as target:
            json.dump(report, target, ensure_ascii=False, indent=2)
        _set_progress(progress, **report, processed=processed, total=total, error="")
        return report
    except Exception as exc:
        failure = {"status": "error", "error": str(exc), "trained_at": _dt.datetime.now().isoformat()}
        _save_state(connection, failure)
        connection.commit()
        _set_progress(progress, **failure)
        raise
    finally:
        connection.close()


def personalization_status(db_path=SCAN_DB_FILE, model_path=PERSONAL_RATING_MODEL_FILE):
    init_catalog_intelligence_db(db_path)
    connection = _connect(db_path)
    try:
        row = connection.execute(
            """
            SELECT COUNT(1),
                   SUM(CASE WHEN user_rating BETWEEN 1 AND 5 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN predicted_rating IS NOT NULL THEN 1 ELSE 0 END),
                   AVG(CASE WHEN user_rating BETWEEN 1 AND 5 THEN user_rating END),
                   AVG(predicted_rating)
            FROM track_intelligence
            """
        ).fetchone()
        state_row = connection.execute(
            "SELECT value FROM catalog_intelligence_state WHERE key='personal_rating_model'"
        ).fetchone()
        state = json.loads(state_row[0]) if state_row and state_row[0] else {}
        rating_sources = {
            str(source): int(count)
            for source, count in connection.execute(
                """
                SELECT COALESCE(rating_source, 'none'), COUNT(1)
                FROM track_intelligence
                WHERE user_rating BETWEEN 1 AND 5
                GROUP BY rating_source
                """
            ).fetchall()
        }
        pending_predictions = 0
        model_version = state.get("model_version")
        if os.path.isfile(model_path) and model_version:
            pending_predictions = int(connection.execute(
                """
                SELECT COUNT(1) FROM track_intelligence
                WHERE embedding IS NOT NULL
                  AND (personal_model_version IS NULL OR personal_model_version != ?)
                """,
                (model_version,),
            ).fetchone()[0])
        return {
            "model_exists": os.path.isfile(model_path),
            "model_version": model_version,
            "catalog_tracks": int(row[0] or 0),
            "rated_tracks": int(row[1] or 0),
            "predicted_tracks": int(row[2] or 0),
            "average_user_rating": float(row[3] or 0),
            "average_predicted_rating": float(row[4] or 0),
            "pending_predictions": pending_predictions,
            "rating_sources": rating_sources,
            "state": state,
        }
    finally:
        connection.close()


def apply_personal_rating_model(
        *, db_path=SCAN_DB_FILE, model_path=PERSONAL_RATING_MODEL_FILE,
        progress=None, only_pending=True,
):
    """Apply an existing model to new catalog rows without retraining or reading audio."""
    if not os.path.isfile(model_path):
        return {"status": "skipped", "reason": "model_missing", "processed": 0}
    with open(model_path, "rb") as source:
        artifact = pickle.load(source)
    deep_model_id = artifact.get("deep_model_id") or DEEP_MODEL_ID
    deep_embedding_dim = int(artifact.get("deep_embedding_dim", 0) or 0)
    init_catalog_intelligence_db(db_path)
    connection = _connect(db_path)
    try:
        where = "ti.embedding IS NOT NULL"
        params = []
        if only_pending:
            where += " AND (ti.personal_model_version IS NULL OR ti.personal_model_version != ?)"
            params.append(artifact["version"])
        total = int(connection.execute(
            f"SELECT COUNT(1) FROM track_intelligence ti WHERE {where}", params
        ).fetchone()[0])
        _set_progress(progress, status="predicting_catalog", processed=0, total=total, error="")
        cursor = connection.execute(
            f"""
            SELECT ti.*, sr.features AS raw_features, {EFFECTIVE_STYLE_SQL} AS effective_style,
                   COALESCE(NULLIF(ti.model_language, ''), NULLIF(sr.language, ''), 'Unknown') AS effective_language,
                   COALESCE(NULLIF(sr.version_type, ''), 'Unknown') AS version_type,
                   de.embedding AS deep_embedding,
                   de.embedding_dim AS deep_embedding_dim,
                   de.embedding_dtype AS deep_embedding_dtype
            FROM track_intelligence ti
            LEFT JOIN scan_results sr ON sr.rel_path = ti.rel_path
            LEFT JOIN track_deep_embeddings de
              ON de.rel_path=ti.rel_path AND de.model_id=? AND de.status='completed'
            WHERE {where}
            ORDER BY ti.rel_path
            """,
            [deep_model_id, *params],
        )
        processed = 0
        while True:
            batch = cursor.fetchmany(1000)
            if not batch:
                break
            vectors, valid_rows = [], []
            for row in batch:
                vector = _feature_vector(
                    row, artifact["embedding_dim"], artifact.get("raw_feature_dim", 0),
                    artifact["categories"], deep_embedding_dim,
                )
                if vector is not None and vector.size == artifact["feature_dim"]:
                    vectors.append(vector)
                    valid_rows.append(row)
            if valid_rows:
                predicted, _, confidence, personal = _predict_models(artifact, np.vstack(vectors))
                updates = []
                for index, row in enumerate(valid_rows):
                    actual = row["user_rating"]
                    personal_value = (
                        (float(actual) - 1.0) / 4.0
                        if actual is not None
                        else 0.5 + (float(personal[index]) - 0.5) * float(confidence[index])
                    )
                    updates.append((
                        float(predicted[index]), float(confidence[index]), personal_value,
                        artifact["version"], row["rel_path"],
                    ))
                connection.executemany(
                    """
                    UPDATE track_intelligence
                    SET predicted_rating=?, rating_confidence=?, personal_score=?, personal_model_version=?
                    WHERE rel_path=?
                    """,
                    updates,
                )
            processed += len(batch)
            connection.commit()
            _set_progress(progress, status="predicting_catalog", processed=processed, total=total, error="")
        result = {"status": "completed", "processed": processed, "total": total}
        _set_progress(progress, **result, error="")
        return result
    finally:
        connection.close()
