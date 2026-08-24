"""Character profiles, compact music embeddings and smart catalog queries."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import math
import os
import pickle
import re
import sqlite3
import threading
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

from .deep_embeddings import MODEL_ID as DEEP_MODEL_ID, blob_to_vector as _deep_blob_to_vector
from .genre_fusion import fuse_probabilities
from .paths import (
    CATALOG_EMBEDDING_MODEL_FILE,
    MODEL_FILE,
    SCAN_DB_FILE,
    YAMNET_CLASS_MAP_FILE,
    YAMNET_MODEL_FILE,
)
from .track_taxonomy import derive_dj_category, genre_family, parse_track_taxonomy, track_group_key


logger = logging.getLogger(__name__)

PROFILE_VERSION = "3.0-character-embedding-v1"
ACOUSTIC_EMBEDDING_SOURCE = "acoustic-pca-32-v1"
YAMNET_EMBEDDING_SOURCE = "yamnet-1024-v1"
DEFAULT_PCA_DIMENSIONS = 32
DEFAULT_CANDIDATE_LIMIT = 5000

_yamnet_session = None
_yamnet_session_path = None
_yamnet_lock = threading.Lock()


SMART_COLLECTIONS = {
    "warmup": {
        "title": "Warm-up / фон",
        "description": "Спокойные треки для начала и фона.",
        "where": "energy < 0.40",
        "order": "energy ASC, danceability DESC",
    },
    "build": {
        "title": "Развитие",
        "description": "Средняя энергия, подходящая для развития сета.",
        "where": "energy >= 0.40 AND energy < 0.68",
        "order": "energy ASC, danceability DESC",
    },
    "peak": {
        "title": "Peak time",
        "description": "Самые энергичные и плотные треки.",
        "where": "energy >= 0.68",
        "order": "energy DESC, density DESC",
    },
    "vocal": {
        "title": "Вокальные",
        "description": "Треки с высокой вероятностью вокала после глубокого YAMNet-анализа.",
        "where": "vocalness >= 0.70",
        "order": "vocalness DESC, energy DESC",
    },
    "instrumental": {
        "title": "Инструментальные",
        "description": "Минимум вокала по глубокому YAMNet-анализу.",
        "where": "vocalness <= 0.25",
        "order": "danceability DESC, energy DESC",
    },
    "bright": {
        "title": "Светлые / позитивные",
        "description": "Более светлый гармонический характер.",
        "where": "valence >= 0.65",
        "order": "valence DESC, energy DESC",
    },
    "dark": {
        "title": "Тёмные / напряжённые",
        "description": "Более тёмный и напряжённый характер.",
        "where": "valence <= 0.35",
        "order": "valence ASC, energy DESC",
    },
    "clean": {
        "title": "Без заметных проблем",
        "description": "Нет автоматически обнаруженных флагов качества.",
        "where": "quality_flags = '[]'",
        "order": "energy DESC, rel_path ASC",
    },
    "personal_favorites": {
        "title": "Для вас · 4★+",
        "description": "Реальные оценки и наиболее уверенные прогнозы персональной модели.",
        "where": "COALESCE(user_rating, predicted_rating, 0) >= 4.0",
        "order": "personal_score DESC, rating_confidence DESC, rel_path ASC",
    },
}


def _connect(db_path=SCAN_DB_FILE):
    connection = sqlite3.connect(os.fspath(db_path), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


def init_catalog_intelligence_db(db_path=SCAN_DB_FILE):
    """Create the additive stage-3 tables without changing scan_results rows."""
    connection = _connect(db_path)
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS track_intelligence (
                rel_path TEXT PRIMARY KEY,
                mtime REAL,
                profile_version TEXT NOT NULL,
                energy REAL NOT NULL DEFAULT 0,
                density REAL NOT NULL DEFAULT 0,
                brightness REAL NOT NULL DEFAULT 0,
                danceability REAL NOT NULL DEFAULT 0,
                vocalness REAL NOT NULL DEFAULT 0,
                valence REAL NOT NULL DEFAULT 0.5,
                bpm REAL NOT NULL DEFAULT 0,
                mood TEXT,
                role TEXT,
                embedding BLOB,
                embedding_dim INTEGER NOT NULL DEFAULT 0,
                embedding_source TEXT,
                semantic_embedding BLOB,
                semantic_dim INTEGER NOT NULL DEFAULT 0,
                semantic_source TEXT,
                model_genre TEXT,
                model_base_genre TEXT,
                model_genre_family TEXT,
                model_genre_confidence REAL,
                model_language TEXT,
                model_language_confidence REAL,
                model_version TEXT,
                user_rating REAL,
                predicted_rating REAL,
                rating_confidence REAL,
                personal_score REAL,
                rating_source TEXT,
                personal_model_version TEXT,
                audio_fingerprint TEXT,
                version_group TEXT,
                quality_flags TEXT NOT NULL DEFAULT '[]',
                profile_json TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_track_intelligence_energy
                ON track_intelligence(energy);
            CREATE INDEX IF NOT EXISTS idx_track_intelligence_mood
                ON track_intelligence(mood);
            CREATE INDEX IF NOT EXISTS idx_track_intelligence_role
                ON track_intelligence(role);
            CREATE INDEX IF NOT EXISTS idx_track_intelligence_fingerprint
                ON track_intelligence(audio_fingerprint);
            CREATE INDEX IF NOT EXISTS idx_track_intelligence_version_group
                ON track_intelligence(version_group);

            CREATE TABLE IF NOT EXISTS catalog_intelligence_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS track_deep_embeddings (
                rel_path TEXT NOT NULL,
                model_id TEXT NOT NULL,
                mtime REAL,
                status TEXT NOT NULL DEFAULT 'pending',
                embedding BLOB,
                embedding_dim INTEGER NOT NULL DEFAULT 0,
                embedding_dtype TEXT NOT NULL DEFAULT 'float16',
                segment_count INTEGER NOT NULL DEFAULT 0,
                provider TEXT,
                error TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (rel_path, model_id)
            );
            CREATE INDEX IF NOT EXISTS idx_deep_embeddings_status
                ON track_deep_embeddings(model_id, status);
            """
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(track_intelligence)").fetchall()
        }
        intelligence_columns = {
            "model_genre": "TEXT",
            "model_base_genre": "TEXT",
            "model_genre_family": "TEXT",
            "model_genre_confidence": "REAL",
            "model_language": "TEXT",
            "model_language_confidence": "REAL",
            "model_version": "TEXT",
            "user_rating": "REAL",
            "predicted_rating": "REAL",
            "rating_confidence": "REAL",
            "personal_score": "REAL",
            "rating_source": "TEXT",
            "personal_model_version": "TEXT",
        }
        for name, sql_type in intelligence_columns.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE track_intelligence ADD COLUMN {name} {sql_type}"
                )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_track_intelligence_model_style "
            "ON track_intelligence(model_base_genre)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_track_intelligence_model_family "
            "ON track_intelligence(model_genre_family)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_track_intelligence_personal_score "
            "ON track_intelligence(personal_score)"
        )
        connection.commit()
    finally:
        connection.close()


def _json_load(value, fallback=None):
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _clip01(value):
    return float(np.clip(float(value), 0.0, 1.0))


def _vector_to_blob(vector):
    if vector is None:
        return None
    array = np.asarray(vector, dtype=np.float32).reshape(-1)
    return array.tobytes()


def _blob_to_vector(blob, expected_dim=0):
    if blob is None:
        return None
    array = np.frombuffer(blob, dtype=np.float32).copy()
    if expected_dim and array.size != int(expected_dim):
        return None
    return array


def _normalize_vector(vector):
    array = np.nan_to_num(np.asarray(vector, dtype=np.float32).reshape(-1))
    norm = float(np.linalg.norm(array))
    return array / norm if norm > 1e-12 else array


def _feature_layout(params):
    enabled = params.get("features", {}) or {}
    n_mfcc = int(params.get("n_mfcc", 13))
    sizes = [
        ("mfcc", n_mfcc),
        ("chroma", 12),
        ("spectral_contrast", 7),
        ("zcr", 1),
        ("tonnetz", 6),
        ("spectral_centroid", 2),
        ("spectral_bandwidth", 2),
        ("spectral_rolloff", 1),
        ("rms", 1),
        ("onset_strength", 2),
        ("tempo", 1),
        ("tempogram", 3),
        ("delta_mfcc", n_mfcc),
        ("delta2_mfcc", n_mfcc),
        ("spectral_flatness", 1),
        ("pitch", 3),
        ("silence_ratio", 1),
        ("energy_entropy", 1),
        ("spectral_skewness", 2),
        ("harmonic_ratio", 1),
        ("mfcc_std", n_mfcc),
        ("energy_ratio", 2),
        ("spectral_stats", 12),
    ]
    layout = {}
    cursor = 0
    for name, size in sizes:
        feature_enabled = enabled.get(name, False)
        if name == "tempo":
            feature_enabled = enabled.get("tempo", False) or enabled.get("bpm", False)
        if not feature_enabled:
            continue
        layout[name] = slice(cursor, cursor + size)
        cursor += size
    return layout, cursor


def _load_active_feature_contract(model_path=MODEL_FILE):
    with open(model_path, "rb") as model_file:
        meta = pickle.load(model_file)
    params = meta.get("librosa_params", {})
    layout, acoustic_length = _feature_layout(params)
    return {
        "params": params,
        "layout": layout,
        "acoustic_length": acoustic_length,
        "expected_feature_len": int(meta.get("expected_feature_len") or acoustic_length),
    }


def _slice_value(features, layout, name, offset=0, default=0.0):
    feature_slice = layout.get(name)
    if feature_slice is None:
        return float(default)
    index = feature_slice.start + int(offset)
    if index >= len(features):
        return float(default)
    value = float(features[index])
    return value if np.isfinite(value) else float(default)


def _chroma_valence(features, layout):
    feature_slice = layout.get("chroma")
    if feature_slice is None or feature_slice.stop > len(features):
        return 0.5
    chroma = np.maximum(0.0, np.asarray(features[feature_slice], dtype=float))
    if not np.any(chroma):
        return 0.5
    chroma /= float(chroma.sum())
    major_template = np.asarray([1.0, 0.15, 0.25, 0.15, 0.8, 0.4, 0.15, 0.9, 0.15, 0.35, 0.1, 0.3])
    minor_template = np.asarray([1.0, 0.15, 0.2, 0.75, 0.15, 0.35, 0.15, 0.9, 0.45, 0.15, 0.55, 0.15])
    major_score = max(float(np.dot(chroma, np.roll(major_template, shift))) for shift in range(12))
    minor_score = max(float(np.dot(chroma, np.roll(minor_template, shift))) for shift in range(12))
    difference = (major_score - minor_score) / max(abs(major_score) + abs(minor_score), 1e-9)
    return _clip01(0.5 + difference * 1.6)


def _scale_metric(value, bounds, default=0.5):
    low, high = bounds
    if not np.isfinite(value) or high <= low:
        return float(default)
    return _clip01((float(value) - float(low)) / (float(high) - float(low)))


def _character_scalars(features, layout):
    return {
        "rms": _slice_value(features, layout, "rms"),
        "onset": _slice_value(features, layout, "onset_strength"),
        "onset_std": _slice_value(features, layout, "onset_strength", 1),
        "tempo": _slice_value(features, layout, "tempo"),
        "zcr": _slice_value(features, layout, "zcr"),
        "centroid": _slice_value(features, layout, "spectral_centroid"),
        "bandwidth": _slice_value(features, layout, "spectral_bandwidth"),
        "flatness": _slice_value(features, layout, "spectral_flatness"),
        "silence": _slice_value(features, layout, "silence_ratio"),
        "entropy": _slice_value(features, layout, "energy_entropy"),
        "high_energy_ratio": _slice_value(features, layout, "energy_ratio"),
        "valence": _chroma_valence(features, layout),
    }


def _infer_vocalness(language, taxonomy):
    language = str(language or taxonomy.get("language") or "Unknown")
    if language == "Instrumental":
        return 0.05
    if language in {"Russian", "English", "Other"}:
        return 0.78
    return 0.42


def _infer_mood(energy, valence, density, existing_mood=""):
    if existing_mood:
        return str(existing_mood).split(",", 1)[0].strip()
    if energy >= 0.72 and valence >= 0.58:
        return "Euphoric"
    if energy >= 0.70 and (valence <= 0.42 or density >= 0.72):
        return "Aggressive"
    if energy <= 0.38 and valence <= 0.42:
        return "Melancholic"
    if energy <= 0.42 and valence >= 0.58:
        return "Light"
    if valence >= 0.66:
        return "Positive"
    if valence <= 0.34:
        return "Dark"
    return "Neutral"


def _infer_role(energy, danceability, density):
    if energy < 0.32:
        return "Background"
    if energy < 0.46:
        return "Warm-up"
    if energy < 0.64:
        return "Build"
    if energy >= 0.78 and density >= 0.58:
        return "Peak"
    if danceability >= 0.55:
        return "Drive"
    return "Bridge"


def build_character_profile(features, contract, calibration, taxonomy=None, language=None, yamnet_vocalness=None):
    """Build a stable 0..1 character profile from one stored feature vector."""
    taxonomy = taxonomy if isinstance(taxonomy, dict) else {}
    acoustic = np.nan_to_num(np.asarray(features, dtype=float).reshape(-1))[: contract["acoustic_length"]]
    layout = contract["layout"]
    raw = _character_scalars(acoustic, layout)
    bounds = calibration.get("bounds", calibration)
    rms = _scale_metric(raw["rms"], bounds.get("rms", (0.0, 1.0)))
    onset = _scale_metric(raw["onset"], bounds.get("onset", (0.0, 1.0)))
    zcr = _scale_metric(raw["zcr"], bounds.get("zcr", (0.0, 1.0)))
    centroid = _scale_metric(raw["centroid"], bounds.get("centroid", (0.0, 1.0)))
    bandwidth = _scale_metric(raw["bandwidth"], bounds.get("bandwidth", (0.0, 1.0)))
    flatness = _scale_metric(raw["flatness"], bounds.get("flatness", (0.0, 1.0)))
    entropy = _scale_metric(raw["entropy"], bounds.get("entropy", (0.0, 1.0)))
    high_ratio = _clip01(raw["high_energy_ratio"])
    tempo = max(0.0, float(raw["tempo"]))
    tempo_energy = _clip01((tempo - 70.0) / 90.0) if tempo else 0.35

    energy = _clip01(0.45 * rms + 0.25 * onset + 0.15 * tempo_energy + 0.15 * high_ratio)
    density = _clip01(0.42 * onset + 0.20 * zcr + 0.18 * flatness + 0.20 * bandwidth)
    brightness = _clip01(0.68 * centroid + 0.20 * bandwidth + 0.12 * zcr)
    tempo_affinity = max(
        math.exp(-((tempo - 124.0) / 25.0) ** 2),
        math.exp(-((tempo - 90.0) / 20.0) ** 2),
    ) if tempo else 0.0
    danceability = _clip01(0.50 * tempo_affinity + 0.28 * onset + 0.22 * (1.0 - abs(high_ratio - 0.65)))
    vocalness = _clip01(
        yamnet_vocalness
        if yamnet_vocalness is not None
        else _infer_vocalness(language, taxonomy)
    )
    # Chroma-based valence has a narrow natural range, therefore it is
    # calibrated against this concrete music collection instead of using an
    # arbitrary absolute threshold.
    valence = _scale_metric(raw["valence"], bounds.get("valence", (0.35, 0.65)))
    existing_mood = taxonomy.get("mood") or ""
    mood = _infer_mood(energy, valence, density, existing_mood)
    role = _infer_role(energy, danceability, density)

    quality_flags = []
    # Older scan databases can contain a legacy duration value (for example
    # 30.0) in this slot.  Treat only a valid 0..1 ratio as silence.
    if 0.0 <= raw["silence"] <= 1.0 and raw["silence"] >= 0.35:
        quality_flags.append("high_silence")
    if rms <= 0.05:
        quality_flags.append("very_quiet")
    if tempo <= 0:
        quality_flags.append("missing_bpm")
    if entropy <= 0.05 and raw["entropy"] != 0:
        quality_flags.append("low_energy_variation")

    return {
        "energy": round(energy, 6),
        "density": round(density, 6),
        "brightness": round(brightness, 6),
        "danceability": round(danceability, 6),
        "vocalness": round(vocalness, 6),
        "valence": round(valence, 6),
        "bpm": round(tempo, 3),
        "mood": mood,
        "role": role,
        "quality_flags": quality_flags,
        "raw_metrics": {key: round(float(value), 6) for key, value in raw.items()},
    }


def _profile_fingerprint(standardized_features):
    vector = np.nan_to_num(np.asarray(standardized_features, dtype=float).reshape(-1))
    quantized = np.clip(np.rint(vector * 4.0), -127, 127).astype(np.int8)
    return hashlib.sha1(quantized.tobytes()).hexdigest()[:24]


def catalog_version_group_key(path):
    """Return a collection-oriented key for alternate edits of one song.

    The training split key intentionally keeps some bracket tags.  For the
    catalog these tags (genre, Camelot key, promo label) prevent real alternate
    versions from meeting, so they are removed in this additional layer.
    """
    key = track_group_key(path)
    key = re.sub(r"\b(?:[1-9]|1[0-2])[ab]\b", " ", key, flags=re.IGNORECASE)
    key = re.sub(
        r"\b(?:afro|club|deep|future|progressive|tech|bass)?\s*house\b"
        r"|\bdrum\s*(?:and|n)?\s*bass\b|\bdnb\b|\bhip\s*hop\b"
        r"|\btrap\b|\brnb\b|\bpop\b|\brock\b|\bnu\s*disco\b",
        " ",
        key,
        flags=re.IGNORECASE,
    )
    key = re.sub(r"\b(?:retail\s+records?|promo|official\s+audio|explicit)\b", " ", key, flags=re.IGNORECASE)
    key = re.sub(r"\s+", " ", key).strip()
    return key or track_group_key(path)


def _save_state(connection, key, value):
    now = _dt.datetime.now().isoformat()
    connection.execute(
        """
        INSERT INTO catalog_intelligence_state(key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (key, json.dumps(value, ensure_ascii=False), now),
    )


def load_catalog_state(key, db_path=SCAN_DB_FILE, default=None):
    init_catalog_intelligence_db(db_path)
    connection = _connect(db_path)
    try:
        row = connection.execute(
            "SELECT value FROM catalog_intelligence_state WHERE key = ?", (key,)
        ).fetchone()
        return _json_load(row[0], default) if row else default
    finally:
        connection.close()


def save_catalog_state(key, value, db_path=SCAN_DB_FILE):
    """Persist lightweight pipeline state for UI status after an app restart."""
    init_catalog_intelligence_db(db_path)
    connection = _connect(db_path)
    try:
        _save_state(connection, key, value)
        connection.commit()
    finally:
        connection.close()


def _iter_feature_batches(connection, batch_size=1000, limit=None):
    sql = """
        SELECT rel_path, mtime, features, base_genre, genre_family, language, mood, taxonomy_json
        FROM scan_results
        WHERE features IS NOT NULL
        ORDER BY rel_path
    """
    params = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (max(1, int(limit)),)
    cursor = connection.execute(sql, params)
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        yield rows


def _decode_feature_rows(rows, acoustic_length):
    decoded = []
    valid_rows = []
    for row in rows:
        features = _json_load(row["features"], [])
        try:
            vector = np.nan_to_num(np.asarray(features, dtype=float).reshape(-1))
        except (TypeError, ValueError):
            continue
        if vector.size < acoustic_length:
            continue
        decoded.append(vector[:acoustic_length])
        valid_rows.append(row)
    if not decoded:
        return valid_rows, np.empty((0, acoustic_length), dtype=float)
    return valid_rows, np.vstack(decoded)


def _calibration_bounds(metric_values):
    bounds = {}
    for name, values in metric_values.items():
        finite = np.asarray(values, dtype=float)
        finite = finite[np.isfinite(finite)]
        if finite.size:
            low, high = np.percentile(finite, [5, 95])
            if high <= low:
                high = low + 1.0
            bounds[name] = [float(low), float(high)]
        else:
            bounds[name] = [0.0, 1.0]
    return bounds


def build_catalog_index(
        db_path=SCAN_DB_FILE,
        model_path=MODEL_FILE,
        artifact_path=CATALOG_EMBEDDING_MODEL_FILE,
        *,
        limit=None,
        dimensions=DEFAULT_PCA_DIMENSIONS,
        progress=None,
        stop_event=None,
):
    """Build character profiles and PCA embeddings from the existing scan DB."""
    init_catalog_intelligence_db(db_path)
    contract = _load_active_feature_contract(model_path)
    acoustic_length = contract["acoustic_length"]
    connection = _connect(db_path)
    progress = progress if isinstance(progress, dict) else {}
    progress.update({"status": "fitting", "processed": 0, "total": 0, "error": ""})
    try:
        total_sql = "SELECT COUNT(1) FROM scan_results WHERE features IS NOT NULL"
        total = int(connection.execute(total_sql).fetchone()[0])
        if limit is not None:
            total = min(total, max(1, int(limit)))
        progress["total"] = total

        scaler = StandardScaler()
        sample_vectors = []
        sample_limit = min(20000, total)
        metric_values = {name: [] for name in (
            "rms", "onset", "zcr", "centroid", "bandwidth", "flatness", "entropy", "valence"
        )}
        seen = 0
        for rows in _iter_feature_batches(connection, limit=limit):
            if stop_event is not None and stop_event.is_set():
                progress["status"] = "stopped"
                return dict(progress)
            _valid_rows, matrix = _decode_feature_rows(rows, acoustic_length)
            if not len(matrix):
                continue
            scaler.partial_fit(matrix)
            remaining = max(0, sample_limit - len(sample_vectors))
            if remaining:
                sample_vectors.extend(matrix[:remaining].astype(np.float32))
            for vector in matrix:
                raw = _character_scalars(vector, contract["layout"])
                for name in metric_values:
                    metric_values[name].append(raw[name])
            seen += len(matrix)
            progress["processed"] = seen

        if seen < 2:
            raise ValueError("В scan_results недостаточно корректных признаков для индекса")
        pca_dimensions = max(2, min(int(dimensions), acoustic_length, len(sample_vectors) - 1))
        sample_matrix = scaler.transform(np.asarray(sample_vectors, dtype=float))
        pca = PCA(n_components=pca_dimensions, svd_solver="randomized", random_state=42)
        pca.fit(sample_matrix)
        calibration = {"bounds": _calibration_bounds(metric_values)}
        artifact = {
            "profile_version": PROFILE_VERSION,
            "embedding_source": ACOUSTIC_EMBEDDING_SOURCE,
            "feature_contract": contract,
            "scaler": scaler,
            "pca": pca,
            "calibration": calibration,
            "trained_tracks": seen,
            "created_at": _dt.datetime.now().isoformat(),
        }
        artifact_path = Path(artifact_path)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_artifact = artifact_path.with_suffix(".pkl.tmp")
        with open(temporary_artifact, "wb") as artifact_file:
            pickle.dump(artifact, artifact_file)
            artifact_file.flush()
            os.fsync(artifact_file.fileno())
        os.replace(temporary_artifact, artifact_path)

        progress.update({"status": "indexing", "processed": 0})
        indexed = 0
        now = _dt.datetime.now().isoformat()
        for rows in _iter_feature_batches(connection, limit=limit):
            if stop_event is not None and stop_event.is_set():
                connection.commit()
                progress["status"] = "stopped"
                return dict(progress)
            valid_rows, matrix = _decode_feature_rows(rows, acoustic_length)
            if not len(matrix):
                continue
            standardized = scaler.transform(matrix)
            embeddings = pca.transform(standardized)
            embeddings = np.vstack([_normalize_vector(vector) for vector in embeddings])
            payload = []
            for row, feature_vector, standardized_vector, embedding in zip(
                    valid_rows, matrix, standardized, embeddings
            ):
                taxonomy = _json_load(row["taxonomy_json"], {}) or {}
                if row["mood"] and not taxonomy.get("mood"):
                    taxonomy["mood"] = row["mood"]
                profile = build_character_profile(
                    feature_vector,
                    contract,
                    calibration,
                    taxonomy=taxonomy,
                    language=row["language"],
                )
                payload.append((
                    row["rel_path"], row["mtime"], PROFILE_VERSION,
                    profile["energy"], profile["density"], profile["brightness"],
                    profile["danceability"], profile["vocalness"], profile["valence"],
                    profile["bpm"], profile["mood"], profile["role"],
                    _vector_to_blob(embedding), int(len(embedding)), ACOUSTIC_EMBEDDING_SOURCE,
                    _profile_fingerprint(standardized_vector), catalog_version_group_key(row["rel_path"]),
                    json.dumps(profile["quality_flags"], ensure_ascii=False),
                    json.dumps(profile, ensure_ascii=False), now,
                ))
            connection.executemany(
                """
                INSERT INTO track_intelligence(
                    rel_path, mtime, profile_version, energy, density, brightness,
                    danceability, vocalness, valence, bpm, mood, role,
                    embedding, embedding_dim, embedding_source,
                    audio_fingerprint, version_group, quality_flags, profile_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rel_path) DO UPDATE SET
                    mtime=excluded.mtime,
                    profile_version=excluded.profile_version,
                    energy=excluded.energy,
                    density=excluded.density,
                    brightness=excluded.brightness,
                    danceability=excluded.danceability,
                    vocalness=excluded.vocalness,
                    valence=excluded.valence,
                    bpm=excluded.bpm,
                    mood=excluded.mood,
                    role=excluded.role,
                    embedding=excluded.embedding,
                    embedding_dim=excluded.embedding_dim,
                    embedding_source=excluded.embedding_source,
                    audio_fingerprint=excluded.audio_fingerprint,
                    version_group=excluded.version_group,
                    quality_flags=excluded.quality_flags,
                    profile_json=excluded.profile_json,
                    updated_at=excluded.updated_at
                """,
                payload,
            )
            connection.commit()
            indexed += len(payload)
            progress["processed"] = indexed

        state = {
            "status": "completed",
            "indexed_tracks": indexed,
            "embedding_dimensions": pca_dimensions,
            "embedding_source": ACOUSTIC_EMBEDDING_SOURCE,
            "artifact_path": str(artifact_path),
            "profile_version": PROFILE_VERSION,
            "completed_at": _dt.datetime.now().isoformat(),
        }
        _save_state(connection, "catalog_index", state)
        connection.commit()
        progress.update({"status": "completed", "processed": indexed, **state})
        return dict(progress)
    except Exception as exc:
        logger.exception("Catalog index build failed: %s", exc)
        progress.update({"status": "error", "error": str(exc)})
        return dict(progress)
    finally:
        connection.close()


def sync_catalog_index(
        db_path=SCAN_DB_FILE,
        model_path=MODEL_FILE,
        artifact_path=CATALOG_EMBEDDING_MODEL_FILE,
        *,
        progress=None,
        stop_event=None,
):
    """Index only new or changed scan rows using the existing PCA artifact.

    This function never opens audio files.  It is intended to run immediately
    after library scanning has already stored the 134-value feature vector.
    """
    progress = progress if isinstance(progress, dict) else {}
    if not Path(artifact_path).is_file():
        result = {
            "status": "needs_rebuild",
            "processed": 0,
            "total": 0,
            "error": "Индекс ещё не построен",
        }
        progress.update(result)
        return result

    init_catalog_intelligence_db(db_path)
    artifact = _load_embedding_artifact(artifact_path)
    contract = artifact["feature_contract"]
    active_contract = _load_active_feature_contract(model_path)
    if (
            int(contract.get("acoustic_length", 0)) != int(active_contract.get("acoustic_length", -1))
            or contract.get("params", {}).get("features") != active_contract.get("params", {}).get("features")
            or int(contract.get("params", {}).get("n_mfcc", 13))
            != int(active_contract.get("params", {}).get("n_mfcc", 13))
    ):
        result = {
            "status": "needs_rebuild",
            "processed": 0,
            "total": 0,
            "error": "Набор аудиопризнаков изменился — требуется перестроить индекс",
        }
        progress.update(result)
        return result

    stale_where = """
        sr.features IS NOT NULL AND (
            ti.rel_path IS NULL
            OR COALESCE(ti.mtime, -1) != COALESCE(sr.mtime, -1)
            OR ti.profile_version != ?
        )
    """
    connection = _connect(db_path)
    try:
        total = int(connection.execute(
            f"""
            SELECT COUNT(1)
            FROM scan_results sr
            LEFT JOIN track_intelligence ti ON ti.rel_path = sr.rel_path
            WHERE {stale_where}
            """,
            (PROFILE_VERSION,),
        ).fetchone()[0])
        progress.update({"status": "syncing", "processed": 0, "total": total, "error": ""})
        if not total:
            progress["status"] = "completed"
            return dict(progress)

        cursor = connection.execute(
            f"""
            SELECT sr.rel_path, sr.mtime, sr.features, sr.base_genre,
                   sr.genre_family, sr.language, sr.mood, sr.taxonomy_json
            FROM scan_results sr
            LEFT JOIN track_intelligence ti ON ti.rel_path = sr.rel_path
            WHERE {stale_where}
            ORDER BY sr.rel_path
            """,
            (PROFILE_VERSION,),
        )
        processed = 0
        now = _dt.datetime.now().isoformat()
        while True:
            if stop_event is not None and stop_event.is_set():
                progress["status"] = "stopped"
                break
            rows = cursor.fetchmany(1000)
            if not rows:
                progress["status"] = "completed"
                break
            valid_rows, matrix = _decode_feature_rows(rows, contract["acoustic_length"])
            if not len(matrix):
                continue
            standardized = artifact["scaler"].transform(matrix)
            embeddings = artifact["pca"].transform(standardized)
            embeddings = np.vstack([_normalize_vector(vector) for vector in embeddings])
            payload = []
            for row, feature_vector, standardized_vector, embedding in zip(
                    valid_rows, matrix, standardized, embeddings
            ):
                taxonomy = _json_load(row["taxonomy_json"], {}) or {}
                if row["mood"] and not taxonomy.get("mood"):
                    taxonomy["mood"] = row["mood"]
                profile = build_character_profile(
                    feature_vector,
                    contract,
                    artifact["calibration"],
                    taxonomy=taxonomy,
                    language=row["language"],
                )
                payload.append((
                    row["rel_path"], row["mtime"], PROFILE_VERSION,
                    profile["energy"], profile["density"], profile["brightness"],
                    profile["danceability"], profile["vocalness"], profile["valence"],
                    profile["bpm"], profile["mood"], profile["role"],
                    _vector_to_blob(embedding), int(len(embedding)), ACOUSTIC_EMBEDDING_SOURCE,
                    _profile_fingerprint(standardized_vector), catalog_version_group_key(row["rel_path"]),
                    json.dumps(profile["quality_flags"], ensure_ascii=False),
                    json.dumps(profile, ensure_ascii=False), now,
                ))
            connection.executemany(
                """
                INSERT INTO track_intelligence(
                    rel_path, mtime, profile_version, energy, density, brightness,
                    danceability, vocalness, valence, bpm, mood, role,
                    embedding, embedding_dim, embedding_source,
                    audio_fingerprint, version_group, quality_flags, profile_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rel_path) DO UPDATE SET
                    mtime=excluded.mtime,
                    profile_version=excluded.profile_version,
                    energy=excluded.energy,
                    density=excluded.density,
                    brightness=excluded.brightness,
                    danceability=excluded.danceability,
                    vocalness=excluded.vocalness,
                    valence=excluded.valence,
                    bpm=excluded.bpm,
                    mood=excluded.mood,
                    role=excluded.role,
                    embedding=excluded.embedding,
                    embedding_dim=excluded.embedding_dim,
                    embedding_source=excluded.embedding_source,
                    audio_fingerprint=excluded.audio_fingerprint,
                    version_group=excluded.version_group,
                    quality_flags=excluded.quality_flags,
                    profile_json=excluded.profile_json,
                    updated_at=excluded.updated_at
                """,
                payload,
            )
            connection.commit()
            processed += len(payload)
            progress["processed"] = processed

        state = {
            "status": progress["status"],
            "synced_tracks": processed,
            "pending_before_sync": total,
            "completed_at": _dt.datetime.now().isoformat(),
        }
        _save_state(connection, "catalog_sync", state)
        connection.commit()
        progress.update(state)
        return dict(progress)
    except Exception as exc:
        logger.exception("Catalog incremental sync failed: %s", exc)
        progress.update({"status": "error", "error": str(exc)})
        return dict(progress)
    finally:
        connection.close()


def _active_model_version(model_meta):
    return "{}:{}:{}".format(
        model_meta.get("version", "unknown"),
        model_meta.get("code_version", "unknown"),
        model_meta.get("training_time", "unknown"),
    )


def refresh_catalog_model_labels(
        db_path=SCAN_DB_FILE,
        model_path=MODEL_FILE,
        *,
        progress=None,
        stop_event=None,
):
    """Refresh filterable style/language labels from stored features.

    Old scan labels are kept in ``scan_results``.  The current model's accepted
    decisions are stored separately in ``track_intelligence``, so a rollback is
    simply ignoring/removing this derived layer.
    """
    progress = progress if isinstance(progress, dict) else {}
    init_catalog_intelligence_db(db_path)
    with open(model_path, "rb") as model_file:
        model_meta = pickle.load(model_file)
    model = model_meta.get("model")
    if model is None or not hasattr(model, "predict_proba"):
        result = {"status": "error", "processed": 0, "total": 0, "error": "Модель не поддерживает вероятности"}
        progress.update(result)
        return result

    expected_len = int(model_meta.get("expected_feature_len") or getattr(model, "n_features_in_", 0))
    model_version = _active_model_version(model_meta)
    params = model_meta.get("librosa_params", {}) or {}
    global_threshold = float(params.get("genre_threshold", 0.55))
    min_margin = float(params.get("min_genre_margin", 0.1))
    class_thresholds = model_meta.get("class_thresholds", {}) or {}
    language_model = model_meta.get("language_model")
    language_thresholds = model_meta.get("language_class_thresholds", {}) or {}
    global_language_threshold = float(params.get("language_threshold", 0.6))
    effnet_head = model_meta.get("effnet_genre_head")
    effnet_alpha = float(model_meta.get("effnet_genre_fusion_alpha", 0.35) or 0.35)

    connection = _connect(db_path)
    try:
        stale_where = "COALESCE(ti.model_version, '') != ? AND sr.features IS NOT NULL"
        total = int(connection.execute(
            f"""
            SELECT COUNT(1) FROM scan_results sr
            JOIN track_intelligence ti ON ti.rel_path = sr.rel_path
            WHERE {stale_where}
            """,
            (model_version,),
        ).fetchone()[0])
        progress.update({"status": "refreshing_labels", "processed": 0, "total": total, "error": ""})
        if not total:
            progress["status"] = "completed"
            return dict(progress)

        cursor = connection.execute(
            f"""
            SELECT sr.rel_path, sr.features, sr.base_genre, sr.language, sr.taxonomy_json,
                   de.embedding AS deep_embedding,
                   de.embedding_dim AS deep_embedding_dim,
                   de.embedding_dtype AS deep_embedding_dtype
            FROM scan_results sr
            JOIN track_intelligence ti ON ti.rel_path = sr.rel_path
            LEFT JOIN track_deep_embeddings de
              ON de.rel_path=sr.rel_path AND de.model_id=?
             AND de.status='completed'
             AND COALESCE(de.mtime, -1)=COALESCE(sr.mtime, -1)
            WHERE {stale_where}
            ORDER BY sr.rel_path
            """,
            (DEEP_MODEL_ID, model_version),
        )
        processed = accepted_styles = accepted_languages = 0
        while True:
            if stop_event is not None and stop_event.is_set():
                progress["status"] = "stopped"
                break
            rows = cursor.fetchmany(1000)
            if not rows:
                progress["status"] = "completed"
                break
            valid_rows = []
            vectors = []
            for row in rows:
                vector = np.asarray(_json_load(row["features"], []), dtype=float).reshape(-1)
                if expected_len and vector.size != expected_len:
                    continue
                valid_rows.append(row)
                vectors.append(np.nan_to_num(vector))
            if not vectors:
                continue
            matrix = np.vstack(vectors)
            probabilities = np.asarray(model.predict_proba(matrix), dtype=float)
            classes = np.asarray(model.classes_)
            if effnet_head is not None:
                deep_positions = []
                deep_vectors = []
                for index, row in enumerate(valid_rows):
                    deep_vector = _deep_blob_to_vector(
                        row["deep_embedding"],
                        row["deep_embedding_dim"] or 0,
                        row["deep_embedding_dtype"] or "float16",
                    )
                    if deep_vector is not None:
                        deep_positions.append(index)
                        deep_vectors.append(deep_vector)
                if deep_positions:
                    deep_matrix = np.vstack(deep_vectors).astype(np.float32, copy=False)
                    deep_probabilities = effnet_head.aligned_probabilities(
                        deep_matrix, classes,
                    )
                    positions = np.asarray(deep_positions, dtype=int)
                    probabilities[positions] = fuse_probabilities(
                        probabilities[positions], deep_probabilities, effnet_alpha,
                    )
            family_probabilities = None
            family_classes = None
            if hasattr(model, "predict_family_proba"):
                family_probabilities, family_classes = model.predict_family_proba(matrix)
                family_probabilities = np.asarray(family_probabilities, dtype=float)
                family_classes = np.asarray(family_classes, dtype=object)
            language_probabilities = None
            language_classes = None
            if language_model is not None and hasattr(language_model, "predict_proba"):
                language_probabilities = np.asarray(language_model.predict_proba(matrix), dtype=float)
                language_classes = np.asarray(language_model.classes_)

            payload = []
            for index, row in enumerate(valid_rows):
                ranking = np.argsort(probabilities[index])[::-1]
                top_index = int(ranking[0])
                second_index = int(ranking[1]) if len(ranking) > 1 else top_index
                candidate = str(classes[top_index])
                confidence = float(probabilities[index, top_index])
                margin = confidence - float(probabilities[index, second_index])
                threshold = max(global_threshold, float(class_thresholds.get(candidate, global_threshold)))

                existing_taxonomy = _json_load(row["taxonomy_json"], {}) or {}
                base_genre_source = str(
                    existing_taxonomy.get("base_genre_source") or "unknown"
                ).strip().lower()
                explicit_base = str(
                    row["base_genre"] or existing_taxonomy.get("base_genre") or ""
                ).strip()
                if (
                        explicit_base in {"", "Unknown", "Other"}
                        or base_genre_source not in {"metadata", "manual_correction"}
                ):
                    explicit_base = ""
                accepted_candidate = confidence >= threshold and margin >= min_margin
                base_genre = explicit_base or (candidate if accepted_candidate else "Unknown")
                if (
                        not explicit_base
                        and base_genre == "Unknown"
                        and family_probabilities is not None
                ):
                    family_ranking = np.argsort(family_probabilities[index])[::-1]
                    family_top = int(family_ranking[0])
                    family_second = int(family_ranking[1]) if family_ranking.size > 1 else family_top
                    family_confidence = float(family_probabilities[index, family_top])
                    family_margin = family_confidence - float(
                        family_probabilities[index, family_second]
                    )
                    if (
                            str(family_classes[family_top]) == "House"
                            and family_confidence >= float(params.get("family_fallback_threshold", 0.68))
                            and family_margin >= float(params.get("family_fallback_margin", 0.15))
                    ):
                        base_genre = "House"
                        confidence = family_confidence
                style_confidence = 1.0 if explicit_base else confidence
                if base_genre != "Unknown":
                    accepted_styles += 1

                path_taxonomy = parse_track_taxonomy(
                    fallback_genre=base_genre or "Other",
                    path=row["rel_path"],
                )
                language_source = str(
                    existing_taxonomy.get("language_source") or "unknown"
                ).strip().lower()
                explicit_language = str(
                    row["language"] or existing_taxonomy.get("language")
                    or path_taxonomy.language or "Unknown"
                )
                if language_source not in {"metadata", "manual_correction", "vocal"}:
                    explicit_language = "Unknown"
                language = explicit_language if explicit_language != "Unknown" else None
                language_confidence = 1.0 if language else 0.0
                if (
                        language is None
                        and language_probabilities is not None
                        and base_genre != "Unknown"
                ):
                    language_index = int(np.argmax(language_probabilities[index]))
                    language_candidate = str(language_classes[language_index])
                    language_confidence = float(language_probabilities[index, language_index])
                    language_threshold = max(
                        global_language_threshold,
                        float(language_thresholds.get(language_candidate, global_language_threshold)),
                    )
                    if language_confidence >= language_threshold:
                        language = language_candidate
                if not language:
                    language = "Unknown"
                if language != "Unknown":
                    accepted_languages += 1

                dj_category = (
                    derive_dj_category(base_genre, language)
                    if base_genre != "Unknown" else "Unknown"
                )
                model_family = (
                    genre_family(base_genre) if base_genre != "Unknown" else "Unknown"
                )
                payload.append((
                    dj_category,
                    base_genre,
                    model_family,
                    style_confidence,
                    language,
                    language_confidence,
                    model_version,
                    row["rel_path"],
                ))

            connection.executemany(
                """
                UPDATE track_intelligence
                SET model_genre=?, model_base_genre=?, model_genre_family=?,
                    model_genre_confidence=?,
                    model_language=?, model_language_confidence=?, model_version=?
                WHERE rel_path=?
                """,
                payload,
            )
            connection.commit()
            processed += len(payload)
            progress.update({
                "processed": processed,
                "accepted_styles": accepted_styles,
                "accepted_languages": accepted_languages,
            })

        state = {
            "status": progress["status"],
            "processed": processed,
            "accepted_styles": accepted_styles,
            "accepted_languages": accepted_languages,
            "model_version": model_version,
            "completed_at": _dt.datetime.now().isoformat(),
        }
        _save_state(connection, "catalog_model_labels", state)
        connection.commit()
        progress.update(state)
        return dict(progress)
    except Exception as exc:
        logger.exception("Catalog model label refresh failed: %s", exc)
        progress.update({"status": "error", "error": str(exc)})
        return dict(progress)
    finally:
        connection.close()


def _load_embedding_artifact(artifact_path=CATALOG_EMBEDDING_MODEL_FILE):
    with open(artifact_path, "rb") as artifact_file:
        return pickle.load(artifact_file)


def _yamnet_outputs(audio, sample_rate, model_path=YAMNET_MODEL_FILE):
    global _yamnet_session, _yamnet_session_path
    import librosa
    import onnxruntime as ort

    model_path = os.fspath(model_path)
    with _yamnet_lock:
        if _yamnet_session is None or _yamnet_session_path != model_path:
            _yamnet_session = ort.InferenceSession(
                model_path, providers=["CPUExecutionProvider"]
            )
            _yamnet_session_path = model_path
        session = _yamnet_session
    waveform = np.asarray(audio, dtype=np.float32).reshape(-1)
    if sample_rate != 16000:
        waveform = librosa.resample(waveform, orig_sr=sample_rate, target_sr=16000)
    waveform = librosa.util.normalize(waveform).astype(np.float32)
    outputs = session.run(None, {session.get_inputs()[0].name: waveform})
    scores = next((item for item in outputs if item.ndim == 2 and item.shape[1] == 521), None)
    embedding = next((item for item in outputs if item.ndim == 2 and item.shape[1] == 1024), None)
    return scores, embedding


def _yamnet_vocalness(mean_scores):
    if mean_scores is None or not Path(YAMNET_CLASS_MAP_FILE).exists():
        return None
    import csv
    labels = []
    with open(YAMNET_CLASS_MAP_FILE, "r", encoding="utf-8", newline="") as class_file:
        reader = csv.DictReader(class_file)
        for row in reader:
            labels.append(row.get("display_name") or row.get("name") or "")
    vocal_terms = ("singing", "speech", "rapping", "vocal music", "choir", "voice")
    values = [
        float(mean_scores[index])
        for index, label in enumerate(labels[: len(mean_scores)])
        if any(term in label.lower() for term in vocal_terms)
    ]
    if not values:
        return None
    # YAMNet's broad ``Music`` class often receives ~0.9 while more specific
    # ``Singing``/``Rapping`` scores stay around 0.01..0.08 even for clearly
    # vocal music.  Convert that sparse evidence non-linearly instead of
    # treating the raw AudioSet probability as a vocal percentage.
    strongest = sorted(values, reverse=True)[:5]
    evidence = max(strongest) * 0.65 + sum(strongest) * 0.35
    return _clip01(1.0 - math.exp(-28.0 * evidence))


def analyze_track_intelligence(
        full_path,
        rel_path=None,
        *,
        db_path=SCAN_DB_FILE,
        artifact_path=CATALOG_EMBEDDING_MODEL_FILE,
        model_path=MODEL_FILE,
        include_yamnet=True,
):
    """Analyze one file deeply and persist its character/YAMNet profile."""
    from .models import _extract_multisegment_features

    artifact = _load_embedding_artifact(artifact_path)
    contract = artifact["feature_contract"]
    params = contract["params"]
    averaged, _segments, audio_segments, errors = _extract_multisegment_features(
        os.fspath(full_path), params
    )
    acoustic = np.asarray(averaged, dtype=float)[: contract["acoustic_length"]]
    standardized = artifact["scaler"].transform(acoustic.reshape(1, -1))[0]
    compact_embedding = _normalize_vector(
        artifact["pca"].transform(standardized.reshape(1, -1))[0]
    )

    semantic_rows = []
    score_rows = []
    if include_yamnet and Path(YAMNET_MODEL_FILE).exists():
        for audio, sample_rate, _offset in audio_segments:
            scores, embedding = _yamnet_outputs(audio, sample_rate)
            if scores is not None:
                score_rows.append(np.mean(scores, axis=0))
            if embedding is not None:
                semantic_rows.append(np.mean(embedding, axis=0))
    mean_scores = np.mean(score_rows, axis=0) if score_rows else None
    semantic_embedding = _normalize_vector(np.mean(semantic_rows, axis=0)) if semantic_rows else None
    vocalness = _yamnet_vocalness(mean_scores)

    init_catalog_intelligence_db(db_path)
    connection = _connect(db_path)
    try:
        rel_path = os.fspath(rel_path or full_path)
        scan_row = connection.execute(
            """
            SELECT mtime, language, mood, taxonomy_json
            FROM scan_results WHERE rel_path = ?
            """,
            (rel_path,),
        ).fetchone()
        taxonomy = _json_load(scan_row["taxonomy_json"], {}) if scan_row else {}
        if scan_row and scan_row["mood"] and not taxonomy.get("mood"):
            taxonomy["mood"] = scan_row["mood"]
        profile = build_character_profile(
            acoustic,
            contract,
            artifact["calibration"],
            taxonomy=taxonomy,
            language=scan_row["language"] if scan_row else None,
            yamnet_vocalness=vocalness,
        )
        now = _dt.datetime.now().isoformat()
        mtime = os.path.getmtime(full_path)
        connection.execute(
            """
            INSERT INTO track_intelligence(
                rel_path, mtime, profile_version, energy, density, brightness,
                danceability, vocalness, valence, bpm, mood, role,
                embedding, embedding_dim, embedding_source,
                semantic_embedding, semantic_dim, semantic_source,
                audio_fingerprint, version_group, quality_flags, profile_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(rel_path) DO UPDATE SET
                mtime=excluded.mtime,
                profile_version=excluded.profile_version,
                energy=excluded.energy,
                density=excluded.density,
                brightness=excluded.brightness,
                danceability=excluded.danceability,
                vocalness=excluded.vocalness,
                valence=excluded.valence,
                bpm=excluded.bpm,
                mood=excluded.mood,
                role=excluded.role,
                embedding=excluded.embedding,
                embedding_dim=excluded.embedding_dim,
                embedding_source=excluded.embedding_source,
                semantic_embedding=excluded.semantic_embedding,
                semantic_dim=excluded.semantic_dim,
                semantic_source=excluded.semantic_source,
                audio_fingerprint=excluded.audio_fingerprint,
                version_group=excluded.version_group,
                quality_flags=excluded.quality_flags,
                profile_json=excluded.profile_json,
                updated_at=excluded.updated_at
            """,
            (
                rel_path, mtime, PROFILE_VERSION,
                profile["energy"], profile["density"], profile["brightness"],
                profile["danceability"], profile["vocalness"], profile["valence"],
                profile["bpm"], profile["mood"], profile["role"],
                _vector_to_blob(compact_embedding), len(compact_embedding), ACOUSTIC_EMBEDDING_SOURCE,
                _vector_to_blob(semantic_embedding), len(semantic_embedding) if semantic_embedding is not None else 0,
                YAMNET_EMBEDDING_SOURCE if semantic_embedding is not None else None,
                _profile_fingerprint(standardized), catalog_version_group_key(rel_path),
                json.dumps(profile["quality_flags"], ensure_ascii=False),
                json.dumps(profile, ensure_ascii=False), now,
            ),
        )
        connection.commit()
        return {
            "rel_path": rel_path,
            "profile": profile,
            "embedding_source": ACOUSTIC_EMBEDDING_SOURCE,
            "embedding_dimensions": int(len(compact_embedding)),
            "semantic_source": YAMNET_EMBEDDING_SOURCE if semantic_embedding is not None else None,
            "semantic_dimensions": int(len(semantic_embedding)) if semantic_embedding is not None else 0,
            "segment_errors": errors,
        }
    finally:
        connection.close()


def get_track_intelligence(rel_path, db_path=SCAN_DB_FILE):
    init_catalog_intelligence_db(db_path)
    connection = _connect(db_path)
    try:
        row = connection.execute(
            f"""
            SELECT ti.*, sr.genre, sr.base_genre,
                   {EFFECTIVE_FAMILY_SQL} AS effective_family,
                   sr.language, sr.version_type
            FROM track_intelligence ti
            LEFT JOIN scan_results sr ON sr.rel_path = ti.rel_path
            WHERE ti.rel_path = ?
            """,
            (os.fspath(rel_path),),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result.pop("embedding", None)
        result.pop("semantic_embedding", None)
        result["genre_family"] = result.pop("effective_family", None) or "Unknown"
        result["quality_flags"] = _json_load(result.get("quality_flags"), [])
        result["profile"] = _json_load(result.pop("profile_json", None), {})
        return result
    finally:
        connection.close()


def catalog_stats(db_path=SCAN_DB_FILE):
    init_catalog_intelligence_db(db_path)
    connection = _connect(db_path)
    try:
        total_scan = int(connection.execute("SELECT COUNT(1) FROM scan_results").fetchone()[0])
        row = connection.execute(
            """
            SELECT COUNT(1),
                   SUM(CASE WHEN embedding IS NOT NULL THEN 1 ELSE 0 END),
                   SUM(CASE WHEN semantic_embedding IS NOT NULL THEN 1 ELSE 0 END),
                   AVG(energy), AVG(density), AVG(vocalness)
            FROM track_intelligence
            """
        ).fetchone()
        roles = {
            role: count
            for role, count in connection.execute(
                "SELECT role, COUNT(1) FROM track_intelligence GROUP BY role"
            ).fetchall()
        }
        moods = {
            mood: count
            for mood, count in connection.execute(
                "SELECT mood, COUNT(1) FROM track_intelligence GROUP BY mood"
            ).fetchall()
        }
        pending_tracks = int(connection.execute(
            """
            SELECT COUNT(1)
            FROM scan_results sr
            LEFT JOIN track_intelligence ti ON ti.rel_path = sr.rel_path
            WHERE sr.features IS NOT NULL AND (
                ti.rel_path IS NULL
                OR COALESCE(ti.mtime, -1) != COALESCE(sr.mtime, -1)
                OR ti.profile_version != ?
            )
            """,
            (PROFILE_VERSION,),
        ).fetchone()[0])
        refreshed_styles = int(connection.execute(
            "SELECT COUNT(1) FROM track_intelligence WHERE model_base_genre IS NOT NULL"
        ).fetchone()[0])
        refreshed_languages = int(connection.execute(
            "SELECT COUNT(1) FROM track_intelligence WHERE model_language IS NOT NULL"
        ).fetchone()[0])
        deep_row = connection.execute(
            """
            SELECT
                SUM(CASE WHEN de.status='completed' AND de.embedding IS NOT NULL
                          AND COALESCE(de.mtime, -1)=COALESCE(sr.mtime, -1) THEN 1 ELSE 0 END),
                SUM(CASE WHEN de.status='error' THEN 1 ELSE 0 END)
            FROM scan_results sr
            LEFT JOIN track_deep_embeddings de
              ON de.rel_path=sr.rel_path AND de.model_id=?
            """,
            (DEEP_MODEL_ID,),
        ).fetchone()
        deep_tracks = int(deep_row[0] or 0)
        return {
            "scan_tracks": total_scan,
            "profiled_tracks": int(row[0] or 0),
            "embedded_tracks": int(row[1] or 0),
            "semantic_tracks": int(row[2] or 0),
            "pending_tracks": pending_tracks,
            "model_style_tracks": refreshed_styles,
            "model_language_tracks": refreshed_languages,
            "deep_tracks": deep_tracks,
            "deep_pending_tracks": max(0, total_scan - deep_tracks),
            "deep_error_tracks": int(deep_row[1] or 0),
            "deep_coverage": float(deep_tracks / total_scan) if total_scan else 0.0,
            "coverage": float((row[0] or 0) / total_scan) if total_scan else 0.0,
            "average_energy": float(row[3] or 0.0),
            "average_density": float(row[4] or 0.0),
            "average_vocalness": float(row[5] or 0.0),
            "roles": roles,
            "moods": moods,
            "index_state": load_catalog_state("catalog_index", db_path, {}),
            "sync_state": load_catalog_state("catalog_sync", db_path, {}),
            "model_labels_state": load_catalog_state("catalog_model_labels", db_path, {}),
        }
    finally:
        connection.close()


def _cosine(left, right):
    left = _normalize_vector(left)
    right = _normalize_vector(right)
    if left.size != right.size or not left.size:
        return 0.0
    return float(np.clip(np.dot(left, right), -1.0, 1.0))


def _option_rows(connection, expression):
    rows = connection.execute(
        f"""
        SELECT {expression} AS value, COUNT(1) AS count
        FROM track_intelligence ti
        LEFT JOIN scan_results sr ON sr.rel_path = ti.rel_path
        GROUP BY value
        ORDER BY count DESC, value ASC
        """
    ).fetchall()
    return [
        {"value": row["value"] or "Unknown", "count": int(row["count"])}
        for row in rows
    ]


EFFECTIVE_STYLE_SQL = """
    CASE
        WHEN COALESCE(NULLIF(ti.model_base_genre, ''), NULLIF(sr.base_genre, ''), NULLIF(sr.genre, ''), 'Unknown') = 'Русские Ремиксы'
        THEN 'Club House'
        ELSE COALESCE(NULLIF(ti.model_base_genre, ''), NULLIF(sr.base_genre, ''), NULLIF(sr.genre, ''), 'Unknown')
    END
"""

EFFECTIVE_FAMILY_SQL = """
    COALESCE(
        NULLIF(ti.model_genre_family, ''),
        NULLIF(sr.genre_family, ''),
        'Unknown'
    )
"""


def catalog_filter_options(db_path=SCAN_DB_FILE):
    init_catalog_intelligence_db(db_path)
    connection = _connect(db_path)
    try:
        return {
            "styles": _option_rows(
                connection,
                EFFECTIVE_STYLE_SQL,
            ),
            "dj_categories": _option_rows(
                connection,
                "COALESCE(NULLIF(ti.model_genre, ''), NULLIF(sr.genre, ''), 'Unknown')",
            ),
            "languages": _option_rows(
                connection,
                "COALESCE(NULLIF(ti.model_language, ''), NULLIF(sr.language, ''), 'Unknown')",
            ),
            "roles": _option_rows(connection, "COALESCE(NULLIF(ti.role, ''), 'Unknown')"),
            "moods": _option_rows(connection, "COALESCE(NULLIF(ti.mood, ''), 'Unknown')"),
        }
    finally:
        connection.close()


def _catalog_filter_sql(filters=None, scope_prefix=None):
    filters = filters if isinstance(filters, dict) else {}
    clauses = []
    params = []
    effective_style = EFFECTIVE_STYLE_SQL
    effective_category = "COALESCE(NULLIF(ti.model_genre, ''), NULLIF(sr.genre, ''), 'Unknown')"
    effective_language = "COALESCE(NULLIF(ti.model_language, ''), NULLIF(sr.language, ''), 'Unknown')"
    mappings = (
        ("style", effective_style),
        ("dj_category", effective_category),
        ("language", effective_language),
        ("role", "COALESCE(NULLIF(ti.role, ''), 'Unknown')"),
        ("mood", "COALESCE(NULLIF(ti.mood, ''), 'Unknown')"),
    )
    for key, expression in mappings:
        value = str(filters.get(key) or "").strip()
        if value and value != "All":
            clauses.append(f"{expression} = ?")
            params.append(value)
    numeric_mappings = (
        ("bpm_min", "ti.bpm >= ?"), ("bpm_max", "ti.bpm <= ?"),
        ("energy_min", "ti.energy >= ?"), ("energy_max", "ti.energy <= ?"),
        ("personal_min", "COALESCE(ti.user_rating, ti.predicted_rating, 0) >= ?"),
    )
    for key, clause in numeric_mappings:
        value = filters.get(key)
        if value not in (None, ""):
            clauses.append(clause)
            params.append(float(value))
    vocal_mode = str(filters.get("vocal_mode") or "any")
    if vocal_mode == "vocal":
        clauses.append("ti.vocalness >= 0.70")
    elif vocal_mode == "instrumental":
        clauses.append("ti.vocalness <= 0.25")
    if bool(filters.get("clean_only", False)):
        clauses.append("ti.quality_flags = '[]'")
    if scope_prefix:
        normalized_scope = os.path.normpath(os.fspath(scope_prefix)).rstrip("\\/")
        if normalized_scope not in ("", "."):
            clauses.append("ti.rel_path LIKE ?")
            params.append(normalized_scope + os.sep + "%")
    return clauses, params


def _bpm_distance(left, right):
    left = float(left or 0)
    right = float(right or 0)
    if left <= 0 or right <= 0:
        return 30.0
    return min(abs(left - right), abs(left * 2.0 - right), abs(left - right * 2.0))


def _normalized_match_weights(weights=None):
    defaults = {
        "deep": 40.0,
        "acoustic": 25.0,
        "character": 15.0,
        "semantic": 5.0,
        "bpm": 5.0,
        "personal": 10.0,
    }
    source = weights or defaults
    parsed = {}
    for key, default_value in defaults.items():
        try:
            parsed[key] = max(0.0, float(source.get(key, default_value)))
        except (AttributeError, TypeError, ValueError):
            parsed[key] = default_value
    total = sum(parsed.values())
    if total <= 0:
        parsed, total = defaults, sum(defaults.values())
    return {key: value / total for key, value in parsed.items()}


def match_reference_tracks(
        reference_paths,
        *,
        limit=20,
        filters=None,
        scope_prefix=None,
        exclude_versions=True,
        weights=None,
        use_deep=True,
        db_path=SCAN_DB_FILE,
):
    """Rank a folder or the full catalog against one or more liked tracks."""
    references = []
    seen = set()
    for path in reference_paths or []:
        normalized = os.fspath(path or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            references.append(normalized)
    if not references:
        return {"items": [], "references_used": [], "missing_references": []}

    normalized_weights = _normalized_match_weights(weights)
    init_catalog_intelligence_db(db_path)
    connection = _connect(db_path)
    try:
        placeholders = ",".join("?" for _ in references)
        reference_rows = connection.execute(
            f"""
            SELECT ti.*, {EFFECTIVE_FAMILY_SQL} AS effective_family,
                   de.embedding AS deep_embedding,
                   de.embedding_dim AS deep_embedding_dim,
                   de.embedding_dtype AS deep_embedding_dtype
            FROM track_intelligence ti
            LEFT JOIN scan_results sr ON sr.rel_path = ti.rel_path
            LEFT JOIN track_deep_embeddings de
              ON de.rel_path=ti.rel_path AND de.model_id=? AND de.status='completed'
            WHERE ti.rel_path IN ({placeholders}) AND ti.embedding IS NOT NULL
            """,
            [DEEP_MODEL_ID, *references],
        ).fetchall()
        found_paths = {row["rel_path"] for row in reference_rows}
        missing = [path for path in references if path not in found_paths]
        if not reference_rows:
            return {"items": [], "references_used": [], "missing_references": missing}

        reference_embeddings = [
            _blob_to_vector(row["embedding"], row["embedding_dim"])
            for row in reference_rows
        ]
        reference_embeddings = [item for item in reference_embeddings if item is not None]
        centroid = _normalize_vector(np.mean(reference_embeddings, axis=0))
        semantic_vectors = [
            _blob_to_vector(row["semantic_embedding"], row["semantic_dim"])
            for row in reference_rows if row["semantic_embedding"] is not None
        ]
        semantic_vectors = [item for item in semantic_vectors if item is not None]
        semantic_centroid = _normalize_vector(np.mean(semantic_vectors, axis=0)) if semantic_vectors else None
        deep_vectors = [
            _deep_blob_to_vector(
                row["deep_embedding"], row["deep_embedding_dim"], row["deep_embedding_dtype"]
            )
            for row in reference_rows if row["deep_embedding"] is not None
        ]
        deep_vectors = [item for item in deep_vectors if item is not None]
        deep_centroid = (
            _normalize_vector(np.mean(deep_vectors, axis=0))
            if deep_vectors and use_deep else None
        )
        character_mean = {
            key: float(np.mean([float(row[key] or 0) for row in reference_rows]))
            for key in ("energy", "density", "brightness", "danceability", "vocalness", "valence", "bpm")
        }
        version_groups = {row["version_group"] for row in reference_rows if row["version_group"]}

        filter_clauses, filter_params = _catalog_filter_sql(filters, scope_prefix)
        sql = f"""
            SELECT ti.rel_path, ti.energy, ti.density, ti.brightness, ti.danceability,
                   ti.vocalness, ti.valence, ti.bpm, ti.mood, ti.role,
                   ti.embedding, ti.embedding_dim, ti.semantic_embedding, ti.semantic_dim,
                   ti.version_group, ti.audio_fingerprint, ti.quality_flags,
                   ti.user_rating, ti.predicted_rating, ti.rating_confidence, ti.personal_score,
                   ti.rating_source,
                   de.embedding AS deep_embedding,
                   de.embedding_dim AS deep_embedding_dim,
                   de.embedding_dtype AS deep_embedding_dtype,
                   COALESCE(NULLIF(ti.model_genre, ''), NULLIF(sr.genre, ''), 'Unknown') AS genre,
                   {EFFECTIVE_STYLE_SQL} AS effective_style,
                   COALESCE(NULLIF(ti.model_language, ''), NULLIF(sr.language, ''), 'Unknown') AS effective_language,
                   {EFFECTIVE_FAMILY_SQL} AS effective_family
            FROM track_intelligence ti
            LEFT JOIN scan_results sr ON sr.rel_path = ti.rel_path
            LEFT JOIN track_deep_embeddings de
              ON de.rel_path=ti.rel_path AND de.model_id=? AND de.status='completed'
            WHERE ti.embedding IS NOT NULL
              AND ti.rel_path NOT IN ({placeholders})
        """
        params = [DEEP_MODEL_ID, *references]
        if filter_clauses:
            sql += " AND " + " AND ".join(filter_clauses)
            params.extend(filter_params)
        energy = character_mean["energy"]
        half_limit = max(1, DEFAULT_CANDIDATE_LIMIT // 2)
        lower = connection.execute(
            sql + " AND ti.energy <= ? ORDER BY ti.energy DESC LIMIT ?",
            [*params, energy, half_limit],
        ).fetchall()
        upper = connection.execute(
            sql + " AND ti.energy > ? ORDER BY ti.energy ASC LIMIT ?",
            [*params, energy, half_limit],
        ).fetchall()

        scored = []
        for row in [*lower, *upper]:
            if exclude_versions and row["version_group"] in version_groups:
                continue
            embedding = _blob_to_vector(row["embedding"], row["embedding_dim"])
            acoustic_similarity = _cosine(centroid, embedding)
            deep_similarity = None
            if deep_centroid is not None and row["deep_embedding"] is not None:
                deep_embedding = _deep_blob_to_vector(
                    row["deep_embedding"], row["deep_embedding_dim"], row["deep_embedding_dtype"]
                )
                if deep_embedding is not None:
                    deep_similarity = _cosine(deep_centroid, deep_embedding)
            semantic_similarity = None
            if semantic_centroid is not None and row["semantic_embedding"] is not None:
                semantic = _blob_to_vector(row["semantic_embedding"], row["semantic_dim"])
                semantic_similarity = _cosine(semantic_centroid, semantic)
            character_similarity = _clip01(1.0 - (
                abs(character_mean["energy"] - float(row["energy"])) * 0.28
                + abs(character_mean["density"] - float(row["density"])) * 0.18
                + abs(character_mean["brightness"] - float(row["brightness"])) * 0.12
                + abs(character_mean["danceability"] - float(row["danceability"])) * 0.14
                + abs(character_mean["vocalness"] - float(row["vocalness"])) * 0.14
                + abs(character_mean["valence"] - float(row["valence"])) * 0.14
            ))
            bpm_difference = _bpm_distance(character_mean["bpm"], row["bpm"])
            bpm_similarity = math.exp(-((bpm_difference / 12.0) ** 2))
            semantic_component = semantic_similarity if semantic_similarity is not None else acoustic_similarity
            personal_component = (
                float(row["personal_score"])
                if row["personal_score"] is not None else 0.5
            )
            deep_component = deep_similarity if deep_similarity is not None else acoustic_similarity
            final_score = (
                deep_component * normalized_weights["deep"]
                + acoustic_similarity * normalized_weights["acoustic"]
                + character_similarity * normalized_weights["character"]
                + semantic_component * normalized_weights["semantic"]
                + bpm_similarity * normalized_weights["bpm"]
                + personal_component * normalized_weights["personal"]
            )
            reasons = []
            if deep_similarity is not None and deep_similarity >= 0.82:
                reasons.append("близкое глубокое звучание EffNet")
            if acoustic_similarity >= 0.85:
                reasons.append("очень близкое звучание")
            if abs(character_mean["energy"] - float(row["energy"])) <= 0.08:
                reasons.append("похожая энергия")
            if bpm_difference <= 4:
                reasons.append("близкий BPM")
            if semantic_similarity is not None and semantic_similarity >= 0.80:
                reasons.append("семантически близкий YAMNet-профиль")
            if row["user_rating"] is not None and float(row["user_rating"]) >= 4:
                reasons.append(f"ваша оценка {int(round(float(row['user_rating'])))}★")
            elif row["predicted_rating"] is not None and float(row["predicted_rating"]) >= 4.0:
                reasons.append("высокий прогноз по вашему вкусу")
            scored.append({
                "path": row["rel_path"],
                "genre": row["genre"],
                "base_genre": row["effective_style"],
                "genre_family": row["effective_family"],
                "language": row["effective_language"],
                "energy": float(row["energy"]),
                "density": float(row["density"]),
                "brightness": float(row["brightness"]),
                "danceability": float(row["danceability"]),
                "vocalness": float(row["vocalness"]),
                "valence": float(row["valence"]),
                "bpm": float(row["bpm"] or 0),
                "mood": row["mood"],
                "role": row["role"],
                "similarity": round(float(final_score), 6),
                "acoustic_similarity": round(float(acoustic_similarity), 6),
                "deep_similarity": round(float(deep_similarity), 6) if deep_similarity is not None else None,
                "semantic_similarity": round(float(semantic_similarity), 6) if semantic_similarity is not None else None,
                "character_similarity": round(float(character_similarity), 6),
                "bpm_similarity": round(float(bpm_similarity), 6),
                "bpm_difference": round(float(bpm_difference), 3),
                "quality_flags": _json_load(row["quality_flags"], []),
                "user_rating": float(row["user_rating"]) if row["user_rating"] is not None else None,
                "predicted_rating": float(row["predicted_rating"]) if row["predicted_rating"] is not None else None,
                "rating_confidence": float(row["rating_confidence"]) if row["rating_confidence"] is not None else None,
                "personal_score": personal_component,
                "rating_source": row["rating_source"],
                "reasons": reasons,
            })
        scored.sort(key=lambda item: item["similarity"], reverse=True)
        return {
            "items": scored[: max(1, min(int(limit), 100))],
            "references_used": sorted(found_paths),
            "missing_references": missing,
            "candidate_count": len(scored),
            "weights": normalized_weights,
        }
    finally:
        connection.close()


def find_similar_intelligent(
        rel_path, limit=20, same_family=False, db_path=SCAN_DB_FILE, use_deep=True
):
    init_catalog_intelligence_db(db_path)
    connection = _connect(db_path)
    try:
        current = connection.execute(
            f"""
            SELECT ti.*, {EFFECTIVE_FAMILY_SQL} AS effective_family
            FROM track_intelligence ti
            LEFT JOIN scan_results sr ON sr.rel_path = ti.rel_path
            WHERE ti.rel_path = ?
            """,
            (os.fspath(rel_path),),
        ).fetchone()
        if not current or current["embedding"] is None:
            return []
        current_embedding = _blob_to_vector(current["embedding"], current["embedding_dim"])
        current_deep_row = connection.execute(
            """
            SELECT embedding, embedding_dim, embedding_dtype
            FROM track_deep_embeddings
            WHERE rel_path=? AND model_id=? AND status='completed'
            """,
            (os.fspath(rel_path), DEEP_MODEL_ID),
        ).fetchone()
        current_deep = (
            _deep_blob_to_vector(
                current_deep_row["embedding"], current_deep_row["embedding_dim"],
                current_deep_row["embedding_dtype"],
            ) if current_deep_row and use_deep else None
        )
        sql = f"""
            SELECT ti.rel_path, ti.energy, ti.density, ti.brightness, ti.danceability,
                   ti.vocalness, ti.valence, ti.bpm, ti.mood, ti.role,
                   ti.embedding, ti.embedding_dim, ti.semantic_embedding, ti.semantic_dim,
                   ti.version_group, ti.audio_fingerprint, ti.quality_flags,
                   de.embedding AS deep_embedding,
                   de.embedding_dim AS deep_embedding_dim,
                   de.embedding_dtype AS deep_embedding_dtype,
                   sr.genre, sr.base_genre, {EFFECTIVE_FAMILY_SQL} AS effective_family,
                   sr.language
            FROM track_intelligence ti
            LEFT JOIN scan_results sr ON sr.rel_path = ti.rel_path
            LEFT JOIN track_deep_embeddings de
              ON de.rel_path=ti.rel_path AND de.model_id=? AND de.status='completed'
            WHERE ti.rel_path != ? AND ti.embedding IS NOT NULL
        """
        params = [DEEP_MODEL_ID, os.fspath(rel_path)]
        if same_family and current["effective_family"]:
            sql += f" AND {EFFECTIVE_FAMILY_SQL} = ?"
            params.append(current["effective_family"])
        # Two index-ordered halves are much faster than ORDER BY ABS(...),
        # which makes SQLite sort a large part of a 100k+ track catalog.
        energy = float(current["energy"])
        half_limit = max(1, DEFAULT_CANDIDATE_LIMIT // 2)
        lower = connection.execute(
            sql + " AND ti.energy <= ? ORDER BY ti.energy DESC LIMIT ?",
            [*params, energy, half_limit],
        ).fetchall()
        upper = connection.execute(
            sql + " AND ti.energy > ? ORDER BY ti.energy ASC LIMIT ?",
            [*params, energy, half_limit],
        ).fetchall()
        candidates = [*lower, *upper]
        current_semantic = _blob_to_vector(current["semantic_embedding"], current["semantic_dim"])
        scored = []
        for row in candidates:
            embedding = _blob_to_vector(row["embedding"], row["embedding_dim"])
            acoustic_similarity = _cosine(current_embedding, embedding)
            deep_similarity = None
            if current_deep is not None and row["deep_embedding"] is not None:
                deep_embedding = _deep_blob_to_vector(
                    row["deep_embedding"], row["deep_embedding_dim"], row["deep_embedding_dtype"]
                )
                if deep_embedding is not None:
                    deep_similarity = _cosine(current_deep, deep_embedding)
            semantic_similarity = None
            if current_semantic is not None and row["semantic_embedding"] is not None:
                semantic = _blob_to_vector(row["semantic_embedding"], row["semantic_dim"])
                semantic_similarity = _cosine(current_semantic, semantic)
            energy_difference = abs(float(current["energy"]) - float(row["energy"]))
            bpm_difference = _bpm_distance(current["bpm"], row["bpm"])
            bpm_similarity = math.exp(-((bpm_difference / 12.0) ** 2))
            character_similarity = _clip01(
                1.0
                - (
                    energy_difference * 0.34
                    + abs(float(current["density"]) - float(row["density"])) * 0.20
                    + abs(float(current["brightness"]) - float(row["brightness"])) * 0.12
                    + abs(float(current["vocalness"]) - float(row["vocalness"])) * 0.18
                    + abs(float(current["valence"]) - float(row["valence"])) * 0.16
                )
            )
            final_score = (
                (deep_similarity if deep_similarity is not None else acoustic_similarity) * 0.42
                + acoustic_similarity * 0.24
                + character_similarity * 0.24
                + (semantic_similarity if semantic_similarity is not None else acoustic_similarity) * 0.10
            )
            scored.append({
                "path": row["rel_path"],
                "genre": row["genre"],
                "base_genre": row["base_genre"],
                "genre_family": row["effective_family"],
                "language": row["language"],
                "energy": float(row["energy"]),
                "density": float(row["density"]),
                "vocalness": float(row["vocalness"]),
                "valence": float(row["valence"]),
                "bpm": float(row["bpm"] or 0),
                "mood": row["mood"],
                "role": row["role"],
                "similarity": round(float(final_score), 6),
                "acoustic_similarity": round(float(acoustic_similarity), 6),
                "deep_similarity": round(float(deep_similarity), 6) if deep_similarity is not None else None,
                "semantic_similarity": round(float(semantic_similarity), 6) if semantic_similarity is not None else None,
                "character_similarity": round(float(character_similarity), 6),
                "bpm_similarity": round(float(bpm_similarity), 6),
                "energy_difference": round(float(energy_difference), 6),
                "bpm_difference": round(float(bpm_difference), 3),
                "same_version_group": row["version_group"] == current["version_group"],
                "same_fingerprint": row["audio_fingerprint"] == current["audio_fingerprint"],
                "quality_flags": _json_load(row["quality_flags"], []),
            })
        scored.sort(key=lambda item: item["similarity"], reverse=True)
        return scored[: max(1, min(int(limit), 100))]
    finally:
        connection.close()


def find_track_versions(rel_path, limit=50, db_path=SCAN_DB_FILE):
    init_catalog_intelligence_db(db_path)
    connection = _connect(db_path)
    try:
        current = connection.execute(
            "SELECT version_group, audio_fingerprint FROM track_intelligence WHERE rel_path = ?",
            (os.fspath(rel_path),),
        ).fetchone()
        if not current:
            return []
        rows = connection.execute(
            """
            SELECT ti.rel_path, ti.version_group, ti.audio_fingerprint,
                   ti.energy, ti.bpm, ti.mood, ti.role,
                   sr.genre, sr.version_type
            FROM track_intelligence ti
            LEFT JOIN scan_results sr ON sr.rel_path = ti.rel_path
            WHERE ti.rel_path != ?
              AND (ti.version_group = ? OR ti.audio_fingerprint = ?)
            LIMIT ?
            """,
            (os.fspath(rel_path), current["version_group"], current["audio_fingerprint"], max(1, min(int(limit), 200))),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()


def list_smart_collections(db_path=SCAN_DB_FILE, *, filters=None, scope_prefix=None):
    init_catalog_intelligence_db(db_path)
    connection = _connect(db_path)
    try:
        filter_clauses, filter_params = _catalog_filter_sql(filters, scope_prefix)
        extra_where = ""
        if filter_clauses:
            extra_where = " AND " + " AND ".join(filter_clauses)
        result = []
        for slug, definition in SMART_COLLECTIONS.items():
            count = int(connection.execute(
                f"""
                SELECT COUNT(1)
                FROM track_intelligence ti
                LEFT JOIN scan_results sr ON sr.rel_path = ti.rel_path
                WHERE {definition['where']} {extra_where}
                """,
                filter_params,
            ).fetchone()[0])
            result.append({"slug": slug, "count": count, **definition})
        return result
    finally:
        connection.close()


def smart_collection_tracks(
        slug,
        limit=100,
        offset=0,
        db_path=SCAN_DB_FILE,
        *,
        filters=None,
        scope_prefix=None,
):
    definition = SMART_COLLECTIONS.get(slug)
    if definition is None:
        raise KeyError(slug)
    init_catalog_intelligence_db(db_path)
    connection = _connect(db_path)
    try:
        filter_clauses, filter_params = _catalog_filter_sql(filters, scope_prefix)
        extra_where = ""
        if filter_clauses:
            extra_where = " AND " + " AND ".join(filter_clauses)
        rows = connection.execute(
            f"""
            SELECT ti.rel_path, ti.energy, ti.density, ti.brightness, ti.danceability,
                   ti.vocalness, ti.valence, ti.bpm, ti.mood, ti.role, ti.quality_flags,
                   ti.user_rating, ti.predicted_rating, ti.rating_confidence, ti.personal_score,
                   COALESCE(NULLIF(ti.model_genre, ''), NULLIF(sr.genre, ''), 'Unknown') AS genre,
                   {EFFECTIVE_STYLE_SQL} AS base_genre,
                   {EFFECTIVE_FAMILY_SQL} AS genre_family,
                   COALESCE(NULLIF(ti.model_language, ''), NULLIF(sr.language, ''), 'Unknown') AS language,
                   sr.version_type
            FROM track_intelligence ti
            LEFT JOIN scan_results sr ON sr.rel_path = ti.rel_path
            WHERE {definition['where']} {extra_where}
            ORDER BY {definition['order']}
            LIMIT ? OFFSET ?
            """,
            (
                *filter_params,
                max(1, min(int(limit), 500)),
                max(0, int(offset)),
            ),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["quality_flags"] = _json_load(item.get("quality_flags"), [])
            result.append(item)
        return result
    finally:
        connection.close()
