# app/db.py 14-08-25 01-50
"""Модуль работы с базами данных сканирования и избранного для WebMusicPlayer."""
import json
import sqlite3
import logging
import os
import time
from pathlib import Path
from .paths import FAVORITE_DB_FILE, SCAN_DB_BACKUP_FILE, SCAN_DB_FILE
# Логирование
from .logging_config import (
    is_log_type_enabled,
    setup_model_logger,
    # (другие setup_ если понадобятся)
)

# model logger
model_logger = logging.getLogger("model")
setup_model_logger()

logger = logging.getLogger(__name__) # Логирование

SCAN_DB = str(SCAN_DB_FILE)
SCAN_DB_BACKUP = str(SCAN_DB_BACKUP_FILE)
FAVORITE_DB = str(FAVORITE_DB_FILE)


def _create_language_enrichment_table(cursor):
    """Create the durable second-stage Whisper queue."""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS language_enrichment (
            rel_path TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            language TEXT,
            confidence REAL,
            detector_status TEXT,
            error TEXT,
            updated_at REAL
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_language_enrichment_status
        ON language_enrichment(status, attempts, updated_at)
    ''')


def scan_result_count(db_path=SCAN_DB):
    """Return the scan row count without creating a missing database."""
    path = os.fspath(db_path)
    if not os.path.isfile(path):
        return 0
    try:
        connection = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
        try:
            return int(connection.execute("SELECT COUNT(*) FROM scan_results").fetchone()[0])
        finally:
            connection.close()
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return 0


def create_scan_db_backup(source_path=SCAN_DB, backup_path=SCAN_DB_BACKUP):
    """Atomically back up a non-empty scan database and preserve older backups on failure."""
    source = os.fspath(source_path)
    backup = os.fspath(backup_path)
    row_count = scan_result_count(source)
    if row_count <= 0:
        return {"backed_up": False, "reason": "empty_or_missing", "rows": 0, "path": backup}

    os.makedirs(os.path.dirname(os.path.abspath(backup)), exist_ok=True)
    temporary = f"{backup}.tmp"
    if os.path.exists(temporary):
        os.remove(temporary)
    source_connection = sqlite3.connect(
        f"file:{Path(source).resolve().as_posix()}?mode=ro",
        uri=True,
        timeout=30,
    )
    destination_connection = sqlite3.connect(temporary, timeout=30)
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
        integrity = destination_connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise sqlite3.DatabaseError(f"Backup integrity check failed: {integrity}")
    except Exception:
        destination_connection.close()
        source_connection.close()
        if os.path.exists(temporary):
            os.remove(temporary)
        raise
    else:
        destination_connection.close()
        source_connection.close()

    os.replace(temporary, backup)
    return {"backed_up": True, "reason": "ok", "rows": row_count, "path": backup}

def init_scan_db():
    """Инициализация базы данных для хранения результатов сканирования."""
    conn = sqlite3.connect(SCAN_DB)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS scan_results (
            id INTEGER PRIMARY KEY,
            rel_path TEXT UNIQUE,
            genre TEXT,
            mtime REAL,
            confidence REAL,
            features TEXT,
            rf_proba TEXT,
            yamnet_prior TEXT,
            fused_proba TEXT,
            base_genre TEXT,
            genre_family TEXT,
            language TEXT,
            version_type TEXT,
            mood TEXT,
            taxonomy_json TEXT
        )
    ''')
    _create_language_enrichment_table(c)
    conn.commit()
    conn.close()##3

def ensure_scan_results_yamnet_columns():
    """
    Добавляет недостающие колонки вероятностей и многомерной таксономии,
    если таблица scan_results была создана более старой версией приложения.
    """
    if not os.path.exists(SCAN_DB):
        return
    conn = sqlite3.connect(SCAN_DB)
    c = conn.cursor()
    try:
        c.execute("PRAGMA table_info(scan_results)")
        cols = {row[1] for row in c.fetchall()}
        alters = []
        if "rf_proba" not in cols:
            alters.append("ALTER TABLE scan_results ADD COLUMN rf_proba TEXT")
        if "yamnet_prior" not in cols:
            alters.append("ALTER TABLE scan_results ADD COLUMN yamnet_prior TEXT")
        if "fused_proba" not in cols:
            alters.append("ALTER TABLE scan_results ADD COLUMN fused_proba TEXT")
        for column in ("base_genre", "genre_family", "language", "version_type", "mood", "taxonomy_json"):
            if column not in cols:
                alters.append(f"ALTER TABLE scan_results ADD COLUMN {column} TEXT")
        for sql in alters:
            c.execute(sql)
        _create_language_enrichment_table(c)
        if alters and is_log_type_enabled("status"):
            model_logger.info(f"[DB][MIGRATION] Добавлены колонки: {alters}")
        conn.commit()
    except Exception as e:
        model_logger.error(f"[DB][MIGRATION] Ошибка миграции таблицы scan_results: {e}")
    finally:
        conn.close()

def get_unique_scan_count(): # Получение количества уникальных записей в таблице scan_results по полю rel_path.
    """
    Возвращает количество уникальных записей в таблице scan_results по полю rel_path.
    """
    conn = sqlite3.connect(SCAN_DB)
    c = conn.cursor()
    c.execute("SELECT COUNT(DISTINCT rel_path) FROM scan_results")
    count = c.fetchone()[0]
    conn.close()
    return count

def scan_table_exists():
    """Проверяет, существует ли таблица scan_results в базе данных SCAN_DB."""
    if not os.path.exists(SCAN_DB):
        return False
    conn = sqlite3.connect(SCAN_DB)
    c = conn.cursor()
    try:
        c.execute("SELECT 1 FROM scan_results LIMIT 1;")
        exists = True
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            exists = False
        else:
            raise
    finally:
        conn.close()
    return exists

def load_scan_result(rel_path): # Загружает запись о сканировании по rel_path.
    """
    Загружает запись о сканировании по rel_path.
    Всегда возвращает кортеж из 4 элементов: (genre, mtime, confidence, features).
    Если поле features сериализовано в json — возвращает dict/list.
    Если запись не найдена, возвращает None.
    """
    conn = sqlite3.connect(SCAN_DB)
    c = conn.cursor()
    c.execute("SELECT genre, mtime, confidence, features FROM scan_results WHERE rel_path = ?", (rel_path,))
    row = c.fetchone()
    conn.close()
    if row is None:
        return None
    # если вдруг в старой базе всего 3 поля — дополним до 4
    if len(row) == 3:
        row = row + (None,)
    genre, mtime, confidence, features = row
    # Десериализация features, если оно не None
    if features is not None:
        try:
            features = json.loads(features)
        except (json.JSONDecodeError, TypeError):
            pass  # оставим как есть, если это не json
    return (genre, mtime, confidence, features)

def _initial_language_enrichment_status(taxonomy):
    taxonomy = taxonomy if isinstance(taxonomy, dict) else {}
    language = str(taxonomy.get("language") or "Unknown")
    source = str(taxonomy.get("language_source") or "unknown")
    if source in {"manual_correction", "metadata", "vocal"} and language not in {"Unknown", "Foreign"}:
        return "not_needed"
    return "pending"


def _scan_result_columns(connection):
    """Возвращает схему scan_results для уже открытого соединения."""
    cursor = connection.execute("PRAGMA table_info(scan_results)")
    return {row[1] for row in cursor.fetchall()}


class ScanResultWriter:
    """Переиспользует соединение и схему БД в течение одного сканирования.

    Каждая запись по-прежнему фиксируется отдельным commit: при аварии уже
    обработанные треки не теряются, но исчезают открытия БД и PRAGMA на трек.
    """

    def __init__(self, db_path=SCAN_DB):
        self.db_path = os.fspath(db_path)
        self.connection = None
        self.columns = None

    def __enter__(self):
        self.connection = sqlite3.connect(self.db_path, timeout=30)
        self.connection.execute("PRAGMA busy_timeout = 30000")
        self.columns = _scan_result_columns(self.connection)
        return self

    def save(self, *args, **kwargs):
        if self.connection is None:
            raise RuntimeError("ScanResultWriter должен использоваться внутри with")
        kwargs["_connection"] = self.connection
        kwargs["_known_columns"] = self.columns
        return save_scan_result(*args, **kwargs)

    def __exit__(self, exc_type, exc_value, traceback_value):
        if self.connection is not None:
            self.connection.close()
        self.connection = None
        self.columns = None


def save_scan_result(rel_path, genre, mtime, confidence,
                     features=None, rf_proba=None, yamnet_prior=None, fused_proba=None,
                     taxonomy=None, defer_vocal_language=False,
                     _connection=None, _known_columns=None):
    """
    Сохраняет результат сканирования в базу данных.
    Новые поля: rf_proba, yamnet_prior, fused_proba (json-списки или None).
    """
    if isinstance(genre, dict):
        genre = genre.get("genre", str(genre))
    import json
    def _json_or_none(x):
        if x is None:
            return None
        if isinstance(x, (dict, list)):
            return json.dumps(x, ensure_ascii=False)
        return json.dumps(x, ensure_ascii=False)
    if isinstance(features, (dict, list)):
        features = json.dumps(features, ensure_ascii=False)
        if is_log_type_enabled("model"):
            model_logger.debug(
                "save_scan_result: path=%s, genre=%s, features_len=%s",
                rel_path, genre,
                len(json.loads(features)) if features else 0
            )
    rf_proba_json = _json_or_none(rf_proba)
    yamnet_prior_json = _json_or_none(yamnet_prior)
    fused_proba_json = _json_or_none(fused_proba)
    taxonomy = dict(taxonomy) if isinstance(taxonomy, dict) else {}
    enrichment_status = None
    if defer_vocal_language:
        enrichment_status = _initial_language_enrichment_status(taxonomy)
        taxonomy["language_enrichment_status"] = enrichment_status
    base_genre = taxonomy.get("base_genre")
    genre_family = taxonomy.get("genre_family")
    language = taxonomy.get("language")
    version_type = taxonomy.get("version_type")
    mood = taxonomy.get("mood")
    taxonomy_json = _json_or_none(taxonomy) if taxonomy else None

    owns_connection = _connection is None
    conn = _connection or sqlite3.connect(SCAN_DB, timeout=30)
    c = conn.cursor()
    # При одиночном вызове проверяем схему как раньше. Во время полного
    # сканирования ScanResultWriter передаёт уже известный набор колонок.
    cols = _known_columns if _known_columns is not None else _scan_result_columns(conn)
    taxonomy_columns = {"base_genre", "genre_family", "language", "version_type", "mood", "taxonomy_json"}
    if {"rf_proba", "yamnet_prior", "fused_proba"}.issubset(cols) and taxonomy_columns.issubset(cols):
        c.execute("""
            INSERT OR REPLACE INTO scan_results
            (rel_path, genre, mtime, confidence, features, rf_proba, yamnet_prior, fused_proba,
             base_genre, genre_family, language, version_type, mood, taxonomy_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            rel_path, genre, mtime, confidence, features,
            rf_proba_json, yamnet_prior_json, fused_proba_json,
            base_genre, genre_family, language, version_type, mood, taxonomy_json,
        ))
    else:
        # fallback (старые таблицы – но мы запускаем миграцию; теоретически не должно сюда попадать)
        c.execute("""
            INSERT OR REPLACE INTO scan_results
            (rel_path, genre, mtime, confidence, features)
            VALUES (?, ?, ?, ?, ?)
        """, (rel_path, genre, mtime, confidence, features))
    if enrichment_status:
        c.execute("""
            INSERT INTO language_enrichment
            (rel_path, status, attempts, language, confidence, detector_status, error, updated_at)
            VALUES (?, ?, 0, NULL, NULL, NULL, NULL, ?)
            ON CONFLICT(rel_path) DO UPDATE SET
                status=excluded.status,
                attempts=0,
                language=NULL,
                confidence=NULL,
                detector_status=NULL,
                error=NULL,
                updated_at=excluded.updated_at
        """, (rel_path, enrichment_status, time.time()))
    conn.commit()
    if owns_connection:
        conn.close()

def load_scan_result_extended(rel_path):
    """
    Расширенная версия: возвращает (genre, mtime, confidence, features, rf_proba, yamnet_prior, fused_proba)
    Отсутствующие колонки -> None.
    """
    conn = sqlite3.connect(SCAN_DB)
    c = conn.cursor()
    # Проверяем есть ли новые поля
    c.execute("PRAGMA table_info(scan_results)")
    cols = [row[1] for row in c.fetchall()]
    has_rf = "rf_proba" in cols
    has_yam = "yamnet_prior" in cols
    has_fused = "fused_proba" in cols

    select_cols = "genre, mtime, confidence, features"
    if has_rf:
        select_cols += ", rf_proba"
    if has_yam:
        select_cols += ", yamnet_prior"
    if has_fused:
        select_cols += ", fused_proba"

    c.execute(f"SELECT {select_cols} FROM scan_results WHERE rel_path = ?", (rel_path,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    # Базовые первые 4
    genre = row[0]
    mtime = row[1]
    confidence = row[2]
    features = row[3]
    idx = 4
    rf_proba = yamnet_prior = fused_proba = None
    if has_rf:
        rf_proba = row[idx]; idx += 1
    if has_yam:
        yamnet_prior = row[idx]; idx += 1 if has_fused else 0
        if has_fused:
            fused_proba = row[idx]
    # Десериализация features
    import json
    if features is not None:
        try:
            features = json.loads(features)
        except Exception:
            pass
    def _decode_vector(value):
        if value is None or isinstance(value, (list, tuple)):
            return value
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    rf_proba = _decode_vector(rf_proba)
    yamnet_prior = _decode_vector(yamnet_prior)
    fused_proba = _decode_vector(fused_proba)
    return (genre, mtime, confidence, features, rf_proba, yamnet_prior, fused_proba)


def load_scan_taxonomy(rel_path):
    """Возвращает многомерную таксономию трека или пустой словарь."""
    if not os.path.exists(SCAN_DB):
        return {}
    conn = sqlite3.connect(SCAN_DB)
    c = conn.cursor()
    try:
        c.execute("PRAGMA table_info(scan_results)")
        cols = {row[1] for row in c.fetchall()}
        if "taxonomy_json" not in cols:
            return {}
        c.execute("SELECT taxonomy_json FROM scan_results WHERE rel_path = ?", (rel_path,))
        row = c.fetchone()
        if not row or not row[0]:
            return {}
        return json.loads(row[0])
    except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
        return {}
    finally:
        conn.close()


def prepare_language_enrichment_queue(enabled=True):
    """Migrate unfinished rows into the durable Whisper queue and recover interrupted work."""
    ensure_scan_results_yamnet_columns()
    conn = sqlite3.connect(SCAN_DB, timeout=30)
    try:
        c = conn.cursor()
        c.execute("UPDATE language_enrichment SET status='pending' WHERE status='processing'")
        if enabled:
            rows = c.execute("""
                SELECT s.rel_path, s.taxonomy_json
                FROM scan_results s
                LEFT JOIN language_enrichment q ON q.rel_path = s.rel_path
                WHERE q.rel_path IS NULL
            """).fetchall()
            now = time.time()
            queue_rows = []
            for rel_path, taxonomy_json in rows:
                try:
                    taxonomy = json.loads(taxonomy_json) if taxonomy_json else {}
                except (TypeError, ValueError, json.JSONDecodeError):
                    taxonomy = {}
                queue_rows.append((
                    rel_path,
                    _initial_language_enrichment_status(taxonomy),
                    now,
                ))
            c.executemany("""
                INSERT OR IGNORE INTO language_enrichment
                (rel_path, status, attempts, updated_at)
                VALUES (?, ?, 0, ?)
            """, queue_rows)
        conn.commit()
    finally:
        conn.close()
    return get_language_enrichment_stats()


def get_language_enrichment_stats():
    if not os.path.isfile(SCAN_DB):
        return {
            "pending": 0, "processing": 0, "completed": 0,
            "failed": 0, "not_needed": 0, "total": 0, "processed": 0,
            "languages": {},
        }
    conn = sqlite3.connect(SCAN_DB, timeout=30)
    try:
        try:
            counts = dict(conn.execute(
                "SELECT status, COUNT(*) FROM language_enrichment GROUP BY status"
            ).fetchall())
            languages = dict(conn.execute("""
                SELECT COALESCE(language, 'Unknown'), COUNT(*)
                FROM language_enrichment
                WHERE status='completed'
                GROUP BY COALESCE(language, 'Unknown')
            """).fetchall())
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).lower():
                raise
            counts = {}
            languages = {}
    finally:
        conn.close()
    payload = {
        key: int(counts.get(key, 0))
        for key in ("pending", "processing", "completed", "failed", "not_needed")
    }
    payload["total"] = payload["pending"] + payload["processing"] + payload["completed"] + payload["failed"]
    payload["processed"] = payload["completed"] + payload["failed"]
    payload["languages"] = {str(key): int(value) for key, value in languages.items()}
    return payload


def claim_next_language_enrichment(max_attempts=3):
    """Atomically claim one pending track for the single persistent Whisper worker."""
    conn = sqlite3.connect(SCAN_DB, timeout=30, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("""
            SELECT q.rel_path
            FROM language_enrichment q
            LEFT JOIN scan_results s ON s.rel_path = q.rel_path
            WHERE q.status='pending' AND q.attempts < ?
            ORDER BY
                CASE WHEN COALESCE(s.language, 'Unknown') IN ('Unknown', 'Foreign') THEN 0 ELSE 1 END,
                COALESCE(q.updated_at, 0), q.rel_path
            LIMIT 1
        """, (int(max_attempts),)).fetchone()
        if not row:
            conn.commit()
            return None
        rel_path = row[0]
        conn.execute("""
            UPDATE language_enrichment
            SET status='processing', attempts=attempts + 1, error=NULL, updated_at=?
            WHERE rel_path=?
        """, (time.time(), rel_path))
        conn.commit()
        return rel_path
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def finish_language_enrichment(rel_path, result):
    """Merge a Whisper decision into taxonomy without recomputing RF/YAMNet features."""
    from .track_taxonomy import derive_dj_category

    result = dict(result or {})
    detector_status = str(result.get("status") or "error")
    candidate = str(result.get("language") or "Unknown")
    confidence = float(result.get("confidence") or 0.0)
    failed = detector_status in {"error", "unavailable", "disabled"}

    conn = sqlite3.connect(SCAN_DB, timeout=30)
    try:
        row = conn.execute("""
            SELECT taxonomy_json, base_genre, language, genre
            FROM scan_results WHERE rel_path=?
        """, (rel_path,)).fetchone()
        if not row:
            conn.execute("""
                UPDATE language_enrichment
                SET status='failed', error='scan row missing', updated_at=?
                WHERE rel_path=?
            """, (time.time(), rel_path))
            conn.commit()
            return
        taxonomy_json, base_genre, current_language, current_genre = row
        try:
            taxonomy = json.loads(taxonomy_json) if taxonomy_json else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            taxonomy = {}
        taxonomy["vocal_language"] = result
        taxonomy["language_enrichment_status"] = "failed" if failed else "completed"
        final_language = str(current_language or taxonomy.get("language") or "Unknown")
        current_language_source = str(taxonomy.get("language_source") or "unknown")
        has_provisional_rf_language = (
            current_language_source in {"rf", "pending_vocal"}
            or str(taxonomy.get("provisional_language_source") or "") == "rf"
        )
        if not failed and candidate != "Unknown":
            final_language = candidate
            taxonomy["language"] = candidate
            taxonomy["language_confidence"] = confidence
            taxonomy["language_source"] = "vocal"
        elif has_provisional_rf_language:
            # Не сохраняем предварительный RF-ответ как окончательный, если
            # Whisper не смог его подтвердить. Для DJ-категории безопаснее
            # Unknown: базовый стиль при этом остаётся неизменным.
            final_language = "Unknown"
            taxonomy["language"] = "Unknown"
            taxonomy["language_confidence"] = 0.0
            taxonomy["language_source"] = "vocal_failed" if failed else "vocal_inconclusive"
        final_base_genre = str(base_genre or taxonomy.get("base_genre") or "Unknown")
        final_genre = (
            derive_dj_category(final_base_genre, final_language)
            if final_base_genre != "Unknown" else str(current_genre or "Unknown")
        )
        taxonomy["dj_category"] = final_genre
        conn.execute("""
            UPDATE scan_results
            SET genre=?, language=?, taxonomy_json=?
            WHERE rel_path=?
        """, (final_genre, final_language, json.dumps(taxonomy, ensure_ascii=False), rel_path))
        has_intelligence = conn.execute("""
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='track_intelligence'
        """).fetchone()
        if has_intelligence:
            conn.execute("""
                UPDATE track_intelligence
                SET model_genre=?, model_base_genre=?,
                    model_language=?, model_language_confidence=?
                WHERE rel_path=?
            """, (
                final_genre,
                final_base_genre,
                final_language,
                confidence if candidate != "Unknown" and not failed else float(
                    taxonomy.get("language_confidence") or 0.0
                ),
                rel_path,
            ))
        conn.execute("""
            UPDATE language_enrichment
            SET status=?, language=?, confidence=?, detector_status=?, error=?, updated_at=?
            WHERE rel_path=?
        """, (
            "failed" if failed else "completed",
            candidate,
            confidence,
            detector_status,
            str(result.get("error") or "") or None,
            time.time(),
            rel_path,
        ))
        conn.commit()
    finally:
        conn.close()


def fail_language_enrichment(rel_path, error):
    conn = sqlite3.connect(SCAN_DB, timeout=30)
    try:
        conn.execute("""
            UPDATE language_enrichment
            SET status='failed', error=?, updated_at=?
            WHERE rel_path=?
        """, (str(error), time.time(), rel_path))
        conn.commit()
    finally:
        conn.close()


def retry_failed_language_enrichment():
    ensure_scan_results_yamnet_columns()
    conn = sqlite3.connect(SCAN_DB, timeout=30)
    try:
        conn.execute("""
            UPDATE language_enrichment
            SET status='pending', attempts=0, error=NULL, updated_at=?
            WHERE status='failed'
        """, (time.time(),))
        changed = conn.total_changes
        conn.commit()
        return int(changed)
    finally:
        conn.close()

def init_favorite_db(db_path=FAVORITE_DB): # Инициализация базы данных избранных файлов
    """Инициализирует базу данных для хранения избранных файлов."""
    conn = sqlite3.connect(os.fspath(db_path))
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS favorites (
            path TEXT PRIMARY KEY,
            genre TEXT,
            rating INTEGER DEFAULT 0
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS track_ratings (
            path TEXT PRIMARY KEY,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            updated_at REAL NOT NULL
        )
    ''')
    # Старые оценки находились только внутри favorites. Перенос сохраняет их,
    # после чего сердце и оценка могут существовать независимо.
    c.execute('''
        INSERT OR IGNORE INTO track_ratings(path, rating, updated_at)
        SELECT path, rating, ? FROM favorites WHERE rating BETWEEN 1 AND 5
    ''', (time.time(),))
    conn.commit()
    conn.close()


def get_track_ratings(db_path=FAVORITE_DB):
    """Return explicit player ratings, including legacy favorite-only values."""
    init_favorite_db(db_path)
    connection = sqlite3.connect(os.fspath(db_path), timeout=30)
    try:
        rows = connection.execute(
            "SELECT path, rating FROM track_ratings WHERE rating BETWEEN 1 AND 5"
        ).fetchall()
        result = {str(path): int(rating) for path, rating in rows}
        legacy = connection.execute(
            "SELECT path, rating FROM favorites WHERE rating BETWEEN 1 AND 5"
        ).fetchall()
        for path, rating in legacy:
            result.setdefault(str(path), int(rating))
        return result
    finally:
        connection.close()


def set_track_rating(path, rating, *, favorite_db_path=FAVORITE_DB, scan_db_path=SCAN_DB):
    """Persist a 1–5★ preference independently from the favorites heart."""
    value = str(path or "").strip()
    if not value:
        raise ValueError("Не указан путь трека")
    try:
        rating_value = int(rating)
    except (TypeError, ValueError) as exc:
        raise ValueError("Оценка должна быть целым числом от 0 до 5") from exc
    if rating_value < 0 or rating_value > 5:
        raise ValueError("Оценка должна быть от 0 до 5")

    init_favorite_db(favorite_db_path)
    connection = sqlite3.connect(os.fspath(favorite_db_path), timeout=30)
    try:
        if rating_value == 0:
            connection.execute("DELETE FROM track_ratings WHERE path=?", (value,))
        else:
            connection.execute(
                """
                INSERT INTO track_ratings(path, rating, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    rating=excluded.rating, updated_at=excluded.updated_at
                """,
                (value, rating_value, time.time()),
            )
        # Keep the old column synchronized only for already-favorited tracks.
        connection.execute(
            "UPDATE favorites SET rating=? WHERE path=?",
            (rating_value, value),
        )
        connection.commit()
        favorite = connection.execute(
            "SELECT 1 FROM favorites WHERE path=?", (value,)
        ).fetchone() is not None
    finally:
        connection.close()

    # The intelligent catalog can use a new explicit rating immediately,
    # without waiting for the next full personalization training pass.
    scan_path = os.fspath(scan_db_path)
    if os.path.isfile(scan_path):
        scan_connection = sqlite3.connect(scan_path, timeout=30)
        try:
            table_exists = scan_connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='track_intelligence'"
            ).fetchone()
            if table_exists:
                if rating_value:
                    scan_connection.execute(
                        """
                        UPDATE track_intelligence
                        SET user_rating=?, rating_source='player', personal_score=?
                        WHERE LOWER(REPLACE(rel_path, '\\', '/'))=LOWER(REPLACE(?, '\\', '/'))
                        """,
                        (rating_value, (rating_value - 1.0) / 4.0, value),
                    )
                else:
                    scan_connection.execute(
                        """
                        UPDATE track_intelligence
                        SET user_rating=NULL, rating_source=NULL, personal_score=NULL
                        WHERE LOWER(REPLACE(rel_path, '\\', '/'))=LOWER(REPLACE(?, '\\', '/'))
                          AND rating_source='player'
                        """,
                        (value,),
                    )
                scan_connection.commit()
        finally:
            scan_connection.close()
    return {"path": value, "rating": rating_value, "favorite": favorite}

def scan_db_is_ready():
    """Возвращает True, если таблица scan_results существует и содержит хотя бы одну запись."""
    if not scan_table_exists():
        return False
    conn = sqlite3.connect(SCAN_DB)
    c = conn.cursor()
    try:
        c.execute("SELECT COUNT(*) FROM scan_results")
        count = c.fetchone()[0]
    except sqlite3.OperationalError:
        count = 0
    finally:
        conn.close()
    return count > 0
