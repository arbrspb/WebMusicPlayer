# app/models.py 14-08-25 01-50
"""Модели и утилиты для работы с жанрами, обучением и анализом треков в WebMusicPlayer."""
# Перед новым обучением желательно удалить файл pkl
import os
import concurrent.futures
import psutil
import re
import json
import datetime
import pickle
import sqlite3
import zlib
import copy
import csv
import hashlib
import getpass
import logging
import math
import numpy as np
import pandas as pd
import librosa
import traceback
import shutil
import time
import threading
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns
import gc
from collections import Counter, defaultdict
# Импорты из librosa
from mutagen.easyid3 import EasyID3
from sklearn.ensemble import RandomForestClassifier
from sklearn.calibration import CalibratedClassifierCV
from sklearn.utils import resample
from sklearn.metrics import confusion_matrix, classification_report, f1_score
from sklearn.model_selection import RandomizedSearchCV, StratifiedGroupKFold
# Импорты из utils, db, config
from .db import ScanResultWriter, init_scan_db, load_scan_result, save_scan_result
from .genre_review import (
    find_training_override,
    get_manual_correction,
    iter_training_corrections,
    list_review_entries,
    training_override_index,
)
from .genre_fusion import (
    EffNetGenreHead,
    available_embedding_rows,
    fuse_available_rows,
    fuse_probabilities,
)
from .hierarchical_genre import family_decision, fit_hierarchical_classifier
from .librosa_settings import DEFAULT_LIBROSA_SETTINGS, load_librosa_settings
from .config import get_advanced_mode, get_model_pipeline_settings, load_config
from .deep_embeddings import (
    build_training_embedding_map,
    cached_library_embedding,
    extract_deep_embedding,
)
from .utils import normalize_audio_filename
from .utils import plot_learning_curve_for_genre_model # Импорт функции learning curve
from .utils import get_dynamic_max_workers_by_settings # Импорт функции для определения максимального количества потоков
from .utils import save_bad_file_info # Импорт функции для сохранения информации о плохих файлах
from .paths import (
    ACTIVE_MODEL_MANIFEST_FILE,
    BAD_FILES_FILE,
    GENRE_SETTINGS_EXAMPLE_FILE,
    GENRE_SETTINGS_FILE as GENRE_SETTINGS_PATH,
    LEARNING_CURVES_DIR,
    MODEL_FILE,
    PROJECT_DIR,
    REKORDBOX_OUTPUT_DIR,
    SAMPLES_DIR,
    SCAN_REPORT_FILE,
    TRAINING_DUPLICATES_FILE,
    TRAINING_CONFLICTS_FILE,
    TRAINING_ERRORS_FILE,
    TRAINING_FEATURE_CACHE_FILE,
    TRAINING_LANGUAGE_ERRORS_FILE,
    TRAINING_LABEL_CONFLICTS_FILE,
    TRAINING_SOURCE_LABEL_CONFLICTS_FILE,
    TRAINING_QUALITY_REPORT_FILE,
    TRAINING_REVIEW_QUEUE_FILE,
    TRAINING_RUN_REPORT_FILE,
    YAMNET_CLASS_MAP_FILE,
    YAMNET_CUDA_LOCK_FILE,
    YAMNET_GENRE_MAP_FILE,
    YAMNET_MODEL_FILE,
    resolve_mapped_music_path,
    resolve_project_path,
)
from .track_taxonomy import (
    FAMILY_FALLBACK_ONLY_STYLES,
    derive_dj_category,
    genre_family,
    parse_track_taxonomy,
    taxonomy_from_training_label,
    track_group_key,
)
from .training_dataset import (
    build_training_problem_folders,
    dataset_summary,
    get_training_dataset_settings,
    has_confirmed_training_tracks,
    iter_confirmed_training_tracks,
    update_training_folders,
)
from .vocal_language import detect_vocal_language

# Логирование
from .logging_config import (
    is_log_type_enabled,
    setup_model_logger,
    log_memory_error,
    setup_status_logger,
    setup_resource_logger,
    # (другие setup_ если понадобятся)
)

# model logger
model_logger = logging.getLogger("model")
setup_model_logger()

# status logger
status_logger = logging.getLogger("status")
setup_status_logger()

# resource logger
resource_logger = logging.getLogger("resource")
setup_resource_logger()

# # Проверочный вывод для status_logger
# status_logger.debug("=== STATUS LOGGER TEST: DEBUG LEVEL ===")
# status_logger.info("=== STATUS LOGGER TEST: INFO LEVEL ===")
# status_logger.warning("=== STATUS LOGGER TEST: WARNING LEVEL ===")
# status_logger.error("=== STATUS LOGGER TEST: ERROR LEVEL ===")
#
# # Проверочный вывод для resource_logger
# resource_logger.debug("=== RESOURCE LOGGER TEST: DEBUG LEVEL ===")
# resource_logger.info("=== RESOURCE LOGGER TEST: INFO LEVEL ===")
# resource_logger.warning("=== RESOURCE LOGGER TEST: WARNING LEVEL ===")
# resource_logger.error("=== RESOURCE LOGGER TEST: ERROR LEVEL ===")

global_state = None
_EXPECTED_FEATURE_LEN_CACHE = None
_GENRE_MODEL_META_CACHE = None
_GENRE_MODEL_META_MTIME_NS = None
_REKORDBOX_PREVIEW_COUNTS_CACHE = {"key": None, "counts": {}}
_REKORDBOX_PREVIEW_COUNTS_LOCK = threading.Lock()
_GENRE_MODEL_LOAD_LOCK = None
_SCAN_STOP_EVENT = None

logger = logging.getLogger(__name__)  # Логирование

MODEL_PATH = str(MODEL_FILE)


def _is_memory_related_scan_error(error):
    text = f"{type(error).__name__}: {error}".lower()
    return any(marker in text for marker in (
        "memoryerror",
        "outofmemory",
        "unable to allocate",
        "could not allocate",
        "недостаточно памяти",
    ))


def _is_fatal_scan_worker_error(error):
    return (
        _is_memory_related_scan_error(error)
        or type(error).__name__ == "BrokenProcessPool"
    )


def _windows_commit_headroom_bytes():
    """Возвращает доступный Windows commit (RAM + pagefile) без WMI."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return int(status.ullAvailPageFile)
    except (AttributeError, OSError, TypeError, ValueError):
        return None


def _safe_scan_worker_count(
        requested_workers,
        settings,
        model_path=MODEL_PATH,
        commit_headroom_bytes=None,
):
    """Ограничивает тяжёлый пул без изменения алгоритма анализа трека."""
    requested = max(1, int(requested_workers))
    internal_limit = settings.get("_scan_worker_limit") if hasattr(settings, "get") else None
    if internal_limit is not None:
        return min(requested, max(1, int(internal_limit))), "automatic_retry"

    try:
        model_size = os.path.getsize(model_path)
    except OSError:
        model_size = 0
    # На Windows spawn создаёт отдельную копию RF и библиотек в каждом процессе.
    # Эта модель занимает около 1.17 ГБ commit-памяти на воркер. Число процессов
    # выбираем по реальному свободному commit, сохраняя 4 ГБ для Flask, Windows
    # и кратковременных массивов. При большем pagefile/свободной памяти система
    # сможет безопасно вернуться к трём или четырём воркерам.
    if os.name == "nt" and model_size >= 128 * 1024 * 1024:
        if commit_headroom_bytes is None:
            commit_headroom_bytes = _windows_commit_headroom_bytes()
        commit_limit = 2
        if commit_headroom_bytes is not None:
            reserve = 4 * 1024 ** 3
            estimated_worker = int(1.25 * 1024 ** 3)
            commit_limit = max(1, int((commit_headroom_bytes - reserve) // estimated_worker))
        safe_workers = min(requested, 4, commit_limit)
        if safe_workers != requested:
            reason = (
                "large_windows_model_low_commit"
                if commit_limit < 2
                else "large_windows_model_commit_safe"
            )
            return safe_workers, reason
    return requested, "dynamic"


def _init_scan_worker(model_load_lock, scan_stop_event=None):
    """Передаёт воркеру lock модели и общий флаг остановки без Manager-процесса."""
    global _GENRE_MODEL_LOAD_LOCK, _SCAN_STOP_EVENT
    _GENRE_MODEL_LOAD_LOCK = model_load_lock
    _SCAN_STOP_EVENT = scan_stop_event


def _load_genre_model_meta_cached():
    """Загружает genre_model.pkl один раз на процесс и обновляет кэш после замены файла."""
    global _GENRE_MODEL_META_CACHE, _GENRE_MODEL_META_MTIME_NS

    def load_current_model():
        global _GENRE_MODEL_META_CACHE, _GENRE_MODEL_META_MTIME_NS
        current_mtime_ns = os.stat(MODEL_PATH).st_mtime_ns
        if (
            _GENRE_MODEL_META_CACHE is None
            or _GENRE_MODEL_META_MTIME_NS != current_mtime_ns
        ):
            with open(MODEL_PATH, "rb") as model_file:
                loaded_meta = pickle.load(model_file)
            _GENRE_MODEL_META_CACHE = loaded_meta
            _GENRE_MODEL_META_MTIME_NS = current_mtime_ns
        return _GENRE_MODEL_META_CACHE

    if _GENRE_MODEL_LOAD_LOCK is None:
        return load_current_model()
    # На Windows одновременная распаковка нескольких копий большого Random
    # Forest даёт кратковременный пик памяти. Загружаем копии последовательно.
    with _GENRE_MODEL_LOAD_LOCK:
        return load_current_model()

COLOR_MAP = {
    "Легкая": 0,
    "Кач": 1,
    "Танцевально/Поставить": 2,
    "Нейтральная": 3,
    "Orange": 4
    # ... добавьте остальные цвета, если нужно
}

SITUATION_MAP = {
    "": 0,
    "Light": 1,
    "Медляк": 2,
    "Грустная": 3,
    "Ставим": 4,
    "Веселая": 5
}

GENRE_SETTINGS_FILE = str(GENRE_SETTINGS_PATH)

DEFAULT_FOLDER_KEYWORDS = {
    "afro house": {"genre": "Afro House", "is_trainable": True},
    "bass house": {"genre": "Bass House", "is_trainable": True},
    "club house": {"genre": "Club House", "is_trainable": True},
    "clubhouse": {"genre": "Club House", "is_trainable": True},
    "deep house": {"genre": "Deep House", "is_trainable": True},
    "disco": {"genre": "Disco", "is_trainable": True},
    "drum & bass": {"genre": "Drum & Bass", "is_trainable": True},
    "drum and bass": {"genre": "Drum & Bass", "is_trainable": True},
    "future house": {"genre": "Future House", "is_trainable": True},
    "hip-hop": {"genre": "Hip-Hop", "is_trainable": True},
    "hip hop": {"genre": "Hip-Hop", "is_trainable": True},
    "house": {"genre": "House", "is_trainable": True},
    "lounge": {"genre": "Lounge", "is_trainable": True},
    "moombahton": {"genre": "Moombahton", "is_trainable": True},
    "pop": {"genre": "Pop", "is_trainable": True},
    "rnb": {"genre": "RnB", "is_trainable": True},
    "rock": {"genre": "Rock", "is_trainable": True},
    "tech house": {"genre": "Tech House", "is_trainable": True},
    "techno": {"genre": "Techno", "is_trainable": True},
    "trap": {"genre": "Trap", "is_trainable": True},
}


def _parse_segment_offsets(raw_offsets, fallback_offset=0.0):
    """Возвращает уникальные неотрицательные смещения аудиосегментов."""
    if isinstance(raw_offsets, (list, tuple)):
        parts = raw_offsets
    else:
        parts = str(raw_offsets or "").replace(";", ",").split(",")
    offsets = []
    for part in parts:
        try:
            value = max(0.0, float(str(part).strip()))
        except (TypeError, ValueError):
            continue
        if value not in offsets:
            offsets.append(value)
    if not offsets:
        offsets = [max(0.0, float(fallback_offset or 0.0))]
    return offsets


def _expected_audio_feature_length(librosa_params):
    """Возвращает фиксированную длину акустической части вектора."""
    enabled = librosa_params.get("features", {}) or {}
    n_mfcc = int(librosa_params.get("n_mfcc", 13))
    sizes = {
        "mfcc": n_mfcc,
        "chroma": 12,
        "spectral_contrast": 7,
        "zcr": 1,
        "tonnetz": 6,
        "spectral_centroid": 2,
        "spectral_bandwidth": 2,
        "spectral_rolloff": 1,
        "rms": 1,
        "onset_strength": 2,
        "tempogram": 3,
        "delta_mfcc": n_mfcc,
        "delta2_mfcc": n_mfcc,
        "spectral_flatness": 1,
        "pitch": 3,
        "silence_ratio": 1,
        "energy_entropy": 1,
        "spectral_skewness": 2,
        "harmonic_ratio": 1,
        "mfcc_std": n_mfcc,
        "energy_ratio": 2,
        "spectral_stats": 12,
    }
    total = sum(size for name, size in sizes.items() if enabled.get(name, False))
    if enabled.get("tempo", False) or enabled.get("bpm", False):
        total += 1
    return total


def _extract_multisegment_features(path, params, track=None):
    """Извлекает признаки из одного или нескольких участков и усредняет их."""
    enabled = bool(params.get("multi_segment_enabled", False))
    default_offset = float(params.get("offset", 0) or 0)
    offsets = _parse_segment_offsets(
        params.get("multi_segment_offsets", ""),
        fallback_offset=default_offset,
    ) if enabled else [default_offset]
    duration = float(
        params.get("multi_segment_duration", params.get("duration", 30))
        if enabled else params.get("duration", 30)
    )
    duration = max(1.0, duration)
    sample_rate = int(params.get("sample_rate", 22050))

    feature_rows = []
    audio_segments = []
    errors = []
    for segment_offset in offsets:
        try:
            y, sr = librosa.load(
                path,
                sr=sample_rate,
                offset=segment_offset,
                duration=duration,
            )
            if y is None or not getattr(y, "size", 0):
                errors.append(f"offset={segment_offset:g}: empty audio")
                continue
            base_features = extract_features(y, sr, params, path=path)
            if isinstance(base_features, tuple):
                base_features, feature_error = base_features
                if feature_error:
                    if _is_memory_related_scan_error(RuntimeError(feature_error)):
                        raise MemoryError(feature_error)
                    errors.append(f"offset={segment_offset:g}: {feature_error}")
                    continue
            full_features = extract_features_from_track(track, base_features)
            if full_features is None or not np.size(full_features):
                errors.append(f"offset={segment_offset:g}: empty features")
                continue
            feature_rows.append(np.asarray(full_features, dtype=float).reshape(-1))
            audio_segments.append((y, sr, segment_offset))
        except Exception as exc:
            if _is_memory_related_scan_error(exc):
                raise
            errors.append(f"offset={segment_offset:g}: {exc}")

    if not feature_rows:
        raise ValueError("Не удалось извлечь ни одного аудиосегмента: " + "; ".join(errors[:3]))
    lengths = {row.shape[0] for row in feature_rows}
    if len(lengths) != 1:
        raise ValueError(f"Сегменты дали признаки разной длины: {sorted(lengths)}")

    averaged_features = np.mean(np.vstack(feature_rows), axis=0)
    return averaged_features, feature_rows, audio_segments, errors

def iter_mp3_files(MUSIC_DIR, librosa_params, settings, rekordbox_data, scan_stop_event, scan_mode):
    from .db import load_scan_result
    for root, dirs, files in os.walk(MUSIC_DIR):
        if scan_stop_event is not None and scan_stop_event.is_set():
            if is_log_type_enabled("status"):
                status_logger.info("[SCAN] Прерывание итератора: stop_event выставлен при os.walk")
            return
        for file in files:
            if scan_stop_event is not None and scan_stop_event.is_set():
                if is_log_type_enabled("status"):
                    status_logger.info("[SCAN] Прерывание итератора: stop_event выставлен внутри директории")
                return
            if file.lower().endswith(".mp3"):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, MUSIC_DIR)
                if scan_mode != "new" and load_scan_result(rel_path) is not None:
                    if is_log_type_enabled("status"):
                        status_logger.debug(f"[SCAN] Пропуск файла {rel_path}: уже есть в базе (режим continue)")
                    continue
                # Event передаётся процессам один раз через initializer. Его нельзя
                # сериализовать заново вместе с каждым заданием ProcessPoolExecutor.
                yield (full_path, rel_path, librosa_params, settings, rekordbox_data)

def process_one_sample(args): # Функция для multiprocessing ( добвляет использование многоядерного процессинга для обучения по Samples)
    # Важно: эта функция должна быть на верхнем уровне, не внутри другой функции!
    path, genre, sample_rate, offset, duration, librosa_params = args
    if is_log_type_enabled("model"):
        model_logger.info(f"[DATA] Начата обработка файла: {path}")
    try:
        import librosa  # импорт внутри функции для multiprocessing
        full_features, segment_features, _audio_segments, segment_errors = _extract_multisegment_features(
            path,
            librosa_params,
        )
        if is_log_type_enabled("model"):
            model_logger.debug(
                f"[FEATURES DEBUG] [TRAIN] segments={len(segment_features)}, errors={segment_errors[:2]}, "
                f"shape={full_features.shape}, первые 10 признаков: {full_features[:10]}")

        if full_features is None or \
                (isinstance(full_features, np.ndarray) and full_features.size == 0) or \
                (isinstance(full_features, list) and len(full_features) == 0):
            if is_log_type_enabled("model"):
                model_logger.warning(f"No features extracted from track: {path}")
            return None
        if is_log_type_enabled("model"):
            model_logger.info(f"Успешно обработан файл: {path}")
        return (full_features, segment_features, genre, path)
    except Exception as e:
        if isinstance(e, MemoryError) or "Unable to allocate" in str(e) or "OutOfMemory" in str(e):
            log_memory_error(e, context="process_one_sample", track_path=path)
        if is_log_type_enabled("model"):
            model_logger.error("Error processing %s: %s", path, e)
            model_logger.error(f"Feature extraction failed for {path}: {e}", exc_info=True)
        # Ошибка признаков (особенно временный OOM) не доказывает, что MP3 битый.
        # Файлы пользователя автоматически больше не перемещаем.
        return ("__FAIL__", path, str(e))


class TrainingPoolStalledError(RuntimeError):
    """Пул обучения не вернул ни одного результата за допустимое время."""

    def __init__(self, timeout_seconds, pending_paths):
        self.timeout_seconds = timeout_seconds
        self.pending_paths = list(pending_paths)
        preview = "; ".join(self.pending_paths[:4]) or "<unknown>"
        super().__init__(
            f"Нет результатов от воркеров {timeout_seconds:.0f} сек. "
            f"Текущие задания: {preview}"
        )


def _force_shutdown_process_pool(executor):
    """Аварийно завершает зависшие дочерние процессы ProcessPoolExecutor."""
    processes = list((getattr(executor, "_processes", None) or {}).values())
    for process in processes:
        try:
            if process.is_alive():
                process.terminate()
        except Exception:
            pass
    try:
        executor.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass
    for process in processes:
        try:
            process.join(timeout=1.0)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
        except Exception:
            pass


def _iter_bounded_executor_results(
        tasks,
        worker_fn,
        max_workers,
        *,
        pending_multiplier=2,
        stall_timeout_seconds=180,
        executor_factory=None,
):
    """Выполняет задачи с ограниченной очередью и watchdog по отсутствию прогресса."""
    import concurrent.futures

    if max_workers < 1:
        raise ValueError("max_workers должен быть не меньше 1")
    executor_factory = executor_factory or concurrent.futures.ProcessPoolExecutor
    executor = executor_factory(max_workers=max_workers)
    task_iter = iter(tasks)
    pending = {}
    queue_limit = max(max_workers, max_workers * max(1, pending_multiplier))
    force_shutdown = True

    def submit_until_full():
        while len(pending) < queue_limit:
            try:
                task_args = next(task_iter)
            except StopIteration:
                break
            future = executor.submit(worker_fn, task_args)
            pending[future] = task_args

    try:
        submit_until_full()
        last_result_at = time.monotonic()
        poll_seconds = min(5.0, max(0.05, float(stall_timeout_seconds)))

        while pending:
            done, _ = concurrent.futures.wait(
                pending,
                timeout=poll_seconds,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            if not done:
                if time.monotonic() - last_result_at >= stall_timeout_seconds:
                    pending_paths = [
                        str(task_args[0]) if isinstance(task_args, tuple) and task_args else "<unknown>"
                        for task_args in pending.values()
                    ]
                    raise TrainingPoolStalledError(stall_timeout_seconds, pending_paths)
                continue

            last_result_at = time.monotonic()
            for future in done:
                task_args = pending.pop(future)
                try:
                    yield task_args, future.result(), None
                except Exception as exc:
                    yield task_args, None, exc
            submit_until_full()

        force_shutdown = False
    finally:
        if force_shutdown:
            _force_shutdown_process_pool(executor)
        else:
            executor.shutdown(wait=True, cancel_futures=False)


def _select_training_batch_workers(requested_workers, available_bytes):
    """Снижает число воркеров, если после резерва Windows остаётся мало RAM."""
    gib = 1024 ** 3
    reserved_bytes = 4 * gib
    estimated_worker_bytes = 1 * gib
    usable_bytes = max(0, int(available_bytes) - reserved_bytes)
    workers_by_memory = max(1, usable_bytes // estimated_worker_bytes)
    return max(1, min(int(requested_workers), int(workers_by_memory)))


def _training_memory_decision(
        requested_workers,
        physical_available_bytes,
        commit_headroom_bytes=None,
):
    """Choose a safe worker count from both RAM and Windows commit headroom."""
    candidates = [max(0, int(physical_available_bytes))]
    if commit_headroom_bytes is not None:
        candidates.append(max(0, int(commit_headroom_bytes)))
    effective = min(candidates)
    gib = 1024 ** 3
    if effective < 3 * gib:
        return "pause", 0, effective
    return "run", _select_training_batch_workers(requested_workers, effective), effective


_TRAINING_FEATURE_CACHE_VERSION = 1


def _training_feature_signature(librosa_params):
    feature_keys = (
        "sample_rate", "duration", "offset", "n_mfcc", "hop_length",
        "n_fft", "win_length", "window", "multi_segment_enabled",
        "multi_segment_offsets", "multi_segment_duration", "features",
    )
    payload = {
        "version": _TRAINING_FEATURE_CACHE_VERSION,
        "librosa": {
            key: librosa_params.get(key)
            for key in feature_keys
        },
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, default=str,
    ).encode("utf-8", "surrogatepass")
    return hashlib.sha256(encoded).hexdigest()


def _training_cache_key(task_args, signature):
    path, genre = task_args[0], task_args[1]
    raw = "\0".join((
        signature,
        os.path.normcase(os.path.abspath(path)),
        str(genre),
    ))
    return hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()


def _training_file_fingerprint(path):
    try:
        stat = os.stat(path)
        return int(stat.st_size), int(stat.st_mtime_ns)
    except OSError:
        return None


def _open_training_feature_cache(path=TRAINING_FEATURE_CACHE_FILE):
    path = os.fspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS training_features (
            cache_key TEXT PRIMARY KEY,
            signature TEXT NOT NULL,
            path TEXT NOT NULL,
            genre TEXT NOT NULL,
            file_size INTEGER,
            file_mtime_ns INTEGER,
            payload BLOB NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_training_features_signature "
        "ON training_features(signature)"
    )
    connection.commit()
    return connection


def _load_training_feature_cache(tasks, signature, cache_path=TRAINING_FEATURE_CACHE_FILE):
    if not os.path.isfile(cache_path):
        return {}
    task_keys = {
        _training_cache_key(task_args, signature): task_args
        for task_args in tasks
    }
    if not task_keys:
        return {}
    connection = _open_training_feature_cache(cache_path)
    try:
        rows = []
        keys = list(task_keys)
        for start in range(0, len(keys), 500):
            batch = keys[start:start + 500]
            placeholders = ",".join("?" for _ in batch)
            rows.extend(connection.execute(
                "SELECT cache_key, file_size, file_mtime_ns, payload "
                f"FROM training_features WHERE signature=? AND cache_key IN ({placeholders})",
                (signature, *batch),
            ).fetchall())
    finally:
        connection.close()
    stored = {row[0]: row[1:] for row in rows}
    results = {}
    for task_args in tasks:
        cache_key = _training_cache_key(task_args, signature)
        row = stored.get(cache_key)
        if row is None:
            continue
        current = _training_file_fingerprint(task_args[0])
        if current is None or current != (int(row[0]), int(row[1])):
            continue
        try:
            results[cache_key] = pickle.loads(zlib.decompress(row[2]))
        except (OSError, ValueError, TypeError, pickle.PickleError, zlib.error):
            continue
    return results


def _save_training_feature_cache(
        entries,
        signature,
        cache_path=TRAINING_FEATURE_CACHE_FILE,
):
    rows = []
    updated_at = datetime.datetime.now().isoformat()
    for task_args, result in entries:
        fingerprint = _training_file_fingerprint(task_args[0])
        if fingerprint is None or result is None:
            continue
        cache_key = _training_cache_key(task_args, signature)
        payload = zlib.compress(pickle.dumps(result, protocol=pickle.HIGHEST_PROTOCOL), 1)
        rows.append((
            cache_key,
            signature,
            os.fspath(task_args[0]),
            str(task_args[1]),
            fingerprint[0],
            fingerprint[1],
            sqlite3.Binary(payload),
            updated_at,
        ))
    if not rows:
        return 0
    connection = _open_training_feature_cache(cache_path)
    try:
        connection.executemany(
            """
            INSERT INTO training_features(
                cache_key, signature, path, genre, file_size, file_mtime_ns,
                payload, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                file_size=excluded.file_size,
                file_mtime_ns=excluded.file_mtime_ns,
                payload=excluded.payload,
                updated_at=excluded.updated_at
            """,
            rows,
        )
        connection.commit()
    finally:
        connection.close()
    return len(rows)

def process_one_scan_file(args):
    """
    Обработка одного трека для multiprocessing в scan_library_async.
    Позволяет корректно прерывать обработку при выставлении scan_stop_event.
    Добавлен мониторинг памяти воркера и расширенное логирование.

    Аргументы:
      args: tuple из (full_path, rel_path, librosa_params, settings, rekordbox_data)
    Возвращает:
      (rel_path, genre, current_mtime, conf, features_to_save, error)
    """
    try:
        import numpy as np
        import librosa
    except MemoryError as e:
        if is_log_type_enabled("resource"):
            resource_logger.error(f"[SCAN][MEMORY] Ошибка памяти при импорте numpy/librosa: {e}")
        # Можно вернуть специальный result с ошибкой
        return (None, None, None, None, None, f"MemoryError при импорте numpy/librosa: {e}")
    except Exception as e:
        logger.error(f"Ошибка при импорте numpy/librosa: {e}")
        return (None, None, None, None, None, f"Ошибка при импорте numpy/librosa: {e}")
    if len(args) == 6:
        # Совместимость с прямыми/старыми вызовами функции.
        full_path, rel_path, librosa_params, settings, rekordbox_data, scan_stop_event = args
    else:
        full_path, rel_path, librosa_params, settings, rekordbox_data = args
        scan_stop_event = _SCAN_STOP_EVENT

    RAM_LIMIT_MB = 2048  # лимит для одного воркера (можно сделать настраиваемым)
    proc = psutil.Process()

    # Логирование источника пути (опционально, можно раскомментировать)
    # if is_log_type_enabled("model"):
    #     source_type = "oswalk"
    #     if "json" in str(rel_path).lower():
    #         source_type = "json"
    #     if str(rel_path).startswith("\\\\"):
    #         source_type = "UNC"
    #     model_logger.debug(
    #         f"[PATH][SCAN] process_one_scan_file: rel_path={rel_path}, full_path={full_path}, source_type={source_type}, os.path.exists={os.path.exists(full_path)}, os.path.isfile={os.path.isfile(full_path)}"
    #     )

    # Проверка на остановку до начала
    if scan_stop_event is not None and scan_stop_event.is_set():
        if is_log_type_enabled("status"):
            status_logger.info(f"[SCAN] Остановка воркера до начала обработки: {rel_path}")
        return (rel_path, None, None, None, None, "stopped by user before processing")
    try:
        # Проверка после первой тяжелой операции
        if scan_stop_event is not None and scan_stop_event.is_set():
            if is_log_type_enabled("status"):
                status_logger.info(f"[SCAN] Остановка воркера после проверки файла: {rel_path}")
            return (rel_path, None, None, None, None, "stopped by user before mtime")

        current_mtime = os.path.getmtime(full_path)
        # if is_log_type_enabled("model"):
        # model_logger.debug(
        #     f"[PATH][SCAN] process_one_scan_file: full_path={full_path}, os.path.exists={os.path.exists(full_path)}, os.path.isfile={os.path.isfile(full_path)}")
        # Мониторинг памяти после получения mtime
        used_mb = proc.memory_info().rss / (1024 * 1024)
        if used_mb > RAM_LIMIT_MB:
            if is_log_type_enabled("resource"):
                resource_logger.warning(
                    f"[SCAN][MEMORY] Воркер превысил лимит RAM после mtime: {used_mb:.1f} MB > {RAM_LIMIT_MB} MB. Завершаем.")
            return (rel_path, None, None, None, None, "Memory limit exceeded after mtime")

        if scan_stop_event is not None and scan_stop_event.is_set():
            if is_log_type_enabled("status"):
                status_logger.info(f"[SCAN] Остановка воркера после mtime: {rel_path}")
            return (rel_path, None, None, None, None, "stopped by user after mtime")

        use_rekordbox = settings.get("use_rekordbox", False)
        rekordbox_meta = get_rekordbox_meta(full_path, rekordbox_data) if use_rekordbox else {}
        genre_meta = get_genre(
            full_path,
            librosa_params=librosa_params,
            return_meta=True,
            track_metadata=rekordbox_meta,
            defer_vocal_language=bool(settings.get("defer_vocal_language", False)),
            defer_deep_embedding=True,
        )
        # ОБРАТНАЯ СОВМЕСТИМОСТЬ: genre_meta может быть длины 3 или 4
        if len(genre_meta) == 4:
            genre, conf, audio_features, proba_meta = genre_meta
        else:
            genre, conf, audio_features = genre_meta
            proba_meta = None
        if isinstance(audio_features, tuple):
            arr, error = audio_features
            if error and "MemoryError" in error:
                return (rel_path, None, current_mtime, None, None, error)
            audio_features = arr
        if audio_features is None:
            try:
                save_bad_file_info(full_path, rel_path, json_path=str(BAD_FILES_FILE))
            except Exception:
                pass
            return (rel_path, None, current_mtime, None, None, "no base features")
        # Мониторинг памяти после анализа жанра
        used_mb = proc.memory_info().rss / (1024 * 1024)
        if used_mb > RAM_LIMIT_MB:
            status_logger.warning(f"[SCAN][MEMORY] Воркер превысил лимит RAM после get_genre: {used_mb:.1f} MB > {RAM_LIMIT_MB} MB. Завершаем.")
            return (rel_path, None, current_mtime, None, None, "Memory limit exceeded after get_genre")

        if scan_stop_event is not None and scan_stop_event.is_set():
            if is_log_type_enabled("status"):
                status_logger.info(f"[SCAN] Остановка воркера после get_genre: {rel_path}")
            return (rel_path, None, current_mtime, None, None, "stopped by user after get_genre")
        # --- SKIP SECONDARY EXPANSION (кэшируем expected_feature_len) ---
        global _EXPECTED_FEATURE_LEN_CACHE
        if _EXPECTED_FEATURE_LEN_CACHE is None:
            try:
                _meta_exp = _load_genre_model_meta_cached()
                _EXPECTED_FEATURE_LEN_CACHE = _meta_exp.get("expected_feature_len")
            except Exception:
                _EXPECTED_FEATURE_LEN_CACHE = None

        if _EXPECTED_FEATURE_LEN_CACHE and isinstance(audio_features, np.ndarray):
            af = audio_features
            if af.ndim > 1:
                af = af.reshape(-1)
            if af.shape[0] == _EXPECTED_FEATURE_LEN_CACHE:
                if is_log_type_enabled("model"):
                    model_logger.debug(f"[SCAN] (no re-expand) features already length={af.shape[0]}")
                features_to_save = af.tolist()
                if proba_meta:
                    return (rel_path, genre, current_mtime, conf, features_to_save, None, proba_meta)
                return (rel_path, genre, current_mtime, conf, features_to_save, None)
        track_dict = {"path": full_path}
        meta = get_rekordbox_meta(track_dict["path"], rekordbox_data) if use_rekordbox else {}
        full_features = extract_features_from_track(
            track_dict, audio_features, rekordbox_data, use_rekordbox
        )

        # Мониторинг памяти после извлечения признаков
        used_mb = proc.memory_info().rss / (1024 * 1024)
        if used_mb > RAM_LIMIT_MB:
            status_logger.warning(f"[SCAN][MEMORY] Воркер превысил лимит RAM после extract_features_from_track: {used_mb:.1f} MB > {RAM_LIMIT_MB} MB. Завершаем.")
            return (rel_path, None, current_mtime, None, None, "Memory limit exceeded after extract_features_from_track")

        if scan_stop_event is not None and scan_stop_event.is_set():
            if is_log_type_enabled("status"):
                status_logger.info(f"[SCAN] Остановка воркера после extract_features_from_track: {rel_path}")
            return (rel_path, None, current_mtime, None, None, "stopped by user after extract_features_from_track")

        # --- Audit-лог только если признаки не извлечены ---
        # if full_features is None or \
        #         (isinstance(full_features, np.ndarray) and full_features.size == 0) or \
        #         (isinstance(full_features, list) and len(full_features) == 0):
        #     if is_log_type_enabled("model"):
        #         model_logger.error(
        #             f"[SCAN ERROR][AUDIT] Признаки не извлечены для {rel_path} | "
        #             f"audio_features={audio_features} | full_features={full_features} | "
        #             f"os.path.exists={os.path.exists(full_path)}, os.path.isfile={os.path.isfile(full_path)}"
        #         )
        #         if isinstance(audio_features, np.ndarray):
        #             model_logger.error(
        #                 f"[SCAN ERROR][AUDIT] audio_features.shape={audio_features.shape}, size={audio_features.size}, dtype={audio_features.dtype}"
        #             )
        #         if isinstance(full_features, np.ndarray):
        #             model_logger.error(
        #                 f"[SCAN ERROR][AUDIT] full_features.shape={full_features.shape}, size={full_features.size}, dtype={full_features.dtype}"
        #             )
        #     return (rel_path, None, current_mtime, None, None, "failed feature extraction")
        if full_features is None or \
                (isinstance(full_features, np.ndarray) and full_features.size == 0) or \
                (isinstance(full_features, list) and len(full_features) == 0):
            if is_log_type_enabled("model"):
                model_logger.error(
                    f"[TRACK REJECT][SCAN] Не удалось извлечь признаки для трека: {rel_path} | "
                    f"full_path={full_path} | "
                    f"os.path.exists={os.path.exists(full_path)}, os.path.isfile={os.path.isfile(full_path)} | "
                    f"audio_features_type={type(audio_features)} | "
                    f"audio_features_shape={getattr(audio_features, 'shape', 'N/A')} | "
                    f"full_features_type={type(full_features)} | "
                    f"full_features_shape={getattr(full_features, 'shape', 'N/A')}"
                )
            return (rel_path, None, current_mtime, None, None, "failed feature extraction")

        # --- Признаки извлечены, сохраняем ---
        features_to_save = full_features.tolist()
        if proba_meta:
            return (rel_path, genre, current_mtime, conf, features_to_save, None, proba_meta)
        return (rel_path, genre, current_mtime, conf, features_to_save, None)
    except Exception as e:
        logger.error(f"[BAD FILE] Ошибка обработки {full_path}: {e}")
        if isinstance(e, MemoryError) or "Unable to allocate" in str(e) or "OutOfMemory" in str(e):
            # Нехватка памяти не означает, что пользовательский MP3 повреждён.
            return (rel_path, None, None, None, None, f"MemoryError: {e}")
        else:
            save_bad_file_info(full_path, rel_path)
            status_logger.error(f"[SCAN][ERROR] Ошибка в воркере: {str(e)}")
            return (rel_path, None, None, None, None, str(e))

def get_rekordbox_meta(track_path, rekordbox_data):
    if not rekordbox_data:
        return {}
    meta = rekordbox_data.get(track_path)
    if meta:
        return meta
    fname = os.path.basename(track_path)
    for k, v in rekordbox_data.items():
        if fname and fname in k:
            return v
    return {}

# Флаг для однократного логирования использования функции
normalize_for_genre_compare_used = False

def get_genre_and_trainable(genre_settings, key):
    """Возвращает жанр и флаг обучаемости по ключу из genre_settings."""
    val = genre_settings.get(key)
    if isinstance(val, dict):
        return val.get("genre", "Other"), val.get("is_trainable", False)
    elif isinstance(val, str):
        return val, True
    else:
        return "Other", False

def normalize_genre_rekordbox(raw_genre, genre_settings, logger=None):

    """
    Для Reckordbox: берёт только первый подходящий жанр из строки вида "A, B, C".
    Если ни один не найден — возвращает строку 'Other'.
    """
    if not raw_genre or not isinstance(raw_genre, str):
        if logger:
            if is_log_type_enabled("model"):
                model_logger.debug(f"normalize_genre_rekordbox: пустой или нестроковый raw_genre={raw_genre} -> 'Other'")
        return "Other"
    tokens = [t.strip() for t in raw_genre.split(",") if t.strip()]
    if logger:
        if is_log_type_enabled("model"):
            model_logger.debug(f"normalize_genre_rekordbox: tokens: {tokens}")
    # Сначала точное совпадение
    for token in tokens:
        token_norm = normalize_for_genre_compare(token)
        for key in sorted(genre_settings, key=len, reverse=True):
            key_norm = normalize_for_genre_compare(key)
            if key_norm == token_norm:
                val = genre_settings[key]
                genre = val["genre"] if isinstance(val, dict) else val
                if logger:
                    if is_log_type_enabled("model"):
                        model_logger.debug(f"  -> ТОЧНО: '{token}' == '{key}' -> {genre}")
                return genre
    # Потом частичное совпадение только для длинных ключей (>4)
    for token in tokens:
        token_norm = normalize_for_genre_compare(token)
        for key in sorted(genre_settings, key=len, reverse=True):
            key_norm = normalize_for_genre_compare(key)
            if len(key_norm) > 4 and (key_norm in token_norm or token_norm in key_norm):
                val = genre_settings[key]
                genre = val["genre"] if isinstance(val, dict) else val
                if logger:
                    if is_log_type_enabled("model"):
                        model_logger.debug(f"  -> ПОДСТРОКА: '{token}' ~ '{key}' -> {genre}")
                return genre
    if logger:
        if is_log_type_enabled("model"):
            model_logger.debug(f"normalize_genre_rekordbox: ни один жанр не подошёл -> 'Other'")
    return "Other"

def normalize_for_genre_compare(s):
    """
    Универсальная нормализация строки для сравнения жанров:
    - переводит в нижний регистр
    - заменяет все апострофы/кавычки на n
    - удаляет все не-буквенно-цифровые символы (заменяя их на пробел)
    - сводит множественные пробелы к одному
    """
    global normalize_for_genre_compare_used
    if not normalize_for_genre_compare_used:
        logger.info("Функция normalize_for_genre_compare используется")
        normalize_for_genre_compare_used = True
    s = str(s).lower()
    s = re.sub(r"[’'`]", "n", s)  # апострофы и кавычки = n
    s = re.sub(r"[^a-zа-я0-9]+", " ", s)  # все не буквы/цифры -> пробел
    s = re.sub(r"\s+", " ", s)  # несколько пробелов -> один
    return s.strip()

def extract_relevant_tokens(s):
    """
    Делит путь или строку на отдельные части (папки/слова), убирает расширения и номера, нормализует.
    Возвращает список токенов.
    """
    s = re.sub(r"^[a-z]:[\\/]", "", str(s), flags=re.IGNORECASE)
    parts = re.split(r"[\\/]", s)
    tokens = []
    for part in parts:
        part = re.sub(r"\.[a-z0-9]{2,5}$", "", part, flags=re.IGNORECASE)
        part = re.sub(r"^\d+\s*[\-a-z]*", "", part, flags=re.IGNORECASE)
        part = part.strip()
        if part:
            tokens.append(normalize_for_genre_compare(part))
    return tokens

def normalize_genre(raw_genre_or_path, genre_settings, logger=None):
    """
    Нормализует жанр или путь, возвращая каноническое имя жанра из genre_settings.
    Если совпадение не найдено — возвращает 'Other'.
    """
    if not raw_genre_or_path:
        if logger:
            if is_log_type_enabled("model"):
                model_logger.debug(f"normalize_genre: пустой raw_genre_or_path -> 'Other'")
        return "Other"
    tokens = extract_relevant_tokens(raw_genre_or_path)
    if logger:
        if is_log_type_enabled("model"):
            model_logger.debug(f"normalize_genre: tokens extracted: {tokens}")
    # Сначала точное совпадение
    for key in sorted(genre_settings, key=len, reverse=True):
        key_norm = normalize_for_genre_compare(key)
        for token in tokens:
            if key_norm == token:
                val = genre_settings[key]
                genre = val["genre"] if isinstance(val, dict) else val
                if logger:
                    if is_log_type_enabled("model"):
                        model_logger.debug(
                        f"    -> Найдено ТОЧНОЕ совпадение по ключу '{key}' (normalized '{key_norm}') c токеном '{token}' -> '{genre}'"
                    )
                return genre
    # Потом частичное совпадение
    for key in sorted(genre_settings, key=len, reverse=True):
        key_norm = normalize_for_genre_compare(key)
        for token in tokens:
            if key_norm in token or token in key_norm:
                val = genre_settings[key]
                genre = val["genre"] if isinstance(val, dict) else val
                if logger:
                    if is_log_type_enabled("model"):
                        model_logger.debug(
                        f"    -> Найдено ПОДСТРОЧНОЕ совпадение по ключу '{key}' (normalized '{key_norm}') c токеном '{token}' -> '{genre}'"
                    )
                return genre
    if logger:
        if is_log_type_enabled("model"):
            model_logger.debug(f"normalize_genre: ничего не найдено в '{raw_genre_or_path}' -> 'Other'")
    return "Other"

def load_genre_settings():
    source_file = None
    if os.path.exists(GENRE_SETTINGS_FILE):
        source_file = GENRE_SETTINGS_FILE
    elif os.path.exists(GENRE_SETTINGS_EXAMPLE_FILE):
        source_file = str(GENRE_SETTINGS_EXAMPLE_FILE)

    if source_file:
        try:
            with open(source_file, "r", encoding="utf-8") as f:
                settings = json.load(f)
                migrated = False
                for k, v in list(settings.items()):
                    if isinstance(v, str):
                        settings[k] = {"genre": v, "is_trainable": True}
                        migrated = True
                if migrated and source_file == GENRE_SETTINGS_FILE:
                    save_genre_settings(settings)
                    logger.info("Migrated old genre settings to new format.")
                # if is_log_type_enabled("model"):
                #     model_logger.debug("Loaded genre settings: %s", settings)
                return settings
        except Exception as e:
            if is_log_type_enabled("model"):
                model_logger.error("Error loading genre settings: %s", e)
    return copy.deepcopy(DEFAULT_FOLDER_KEYWORDS)

def save_genre_settings(settings_dict):
    try:
        with open(GENRE_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings_dict, f, indent=4, ensure_ascii=False)
        logger.info("Genre settings saved: %s", settings_dict)
    except Exception as e:
        if is_log_type_enabled("model"):
            model_logger.error("Error saving genre settings: %s", e)

def get_trainable_genres(genre_settings):
    return set(
        v["genre"] for v in genre_settings.values() if v.get("is_trainable", False)
    )

def get_meta_value(meta, key, default=None):
    # Сначала ищет в исходном виде, потом в верхнем регистре, потом с заглавной буквы для ф-ии def extract_features_from_track
    return meta.get(key) or meta.get(key.upper()) or meta.get(key.capitalize()) or default


def _raise_feature_memory_error(error):
    """Не позволяет подменять нулями признаки, не вычисленные из-за OOM."""
    if _is_memory_related_scan_error(error):
        raise MemoryError(str(error)) from error


def extract_features(y, sr, librosa_params, path=None):
    """
    Извлекает базовые аудио признаки.
    Плейсхолдеры (нули) добавляются в случае падения отдельных блоков, чтобы итоговая длина признакового вектора была стабильной.
    Возможные форматы возврата:
      - np.ndarray признаков
      - (np.ndarray, "MemoryError: ...") при фатальной ошибке памяти
    """
    try:
        features = []

        # === MFCC ===
        if librosa_params["features"].get("mfcc", True):
            try:
                mfcc = librosa.feature.mfcc(
                    y=y, sr=sr,
                    n_mfcc=librosa_params.get("n_mfcc", 13),
                    hop_length=librosa_params.get("hop_length", 512),
                    n_fft=librosa_params.get("n_fft", 2048),
                    win_length=librosa_params.get("win_length", 2048),
                    window=librosa_params.get("window", "hann")
                )
                features.extend(np.mean(mfcc.T, axis=0))
            except Exception as e:
                _raise_feature_memory_error(e)
                model_logger.error(f"[FEATURES ERROR] MFCC extraction failed: {e}")
                features.extend([0.0] * librosa_params.get("n_mfcc", 13))

        # === Chroma ===
        if librosa_params["features"].get("chroma", False):
            try:
                chroma = librosa.feature.chroma_stft(
                    y=y, sr=sr,
                    hop_length=librosa_params.get("hop_length", 512),
                    n_fft=librosa_params.get("n_fft", 2048)
                )
                features.extend(np.mean(chroma, axis=1))
            except Exception as e:
                _raise_feature_memory_error(e)
                model_logger.error(f"[FEATURES ERROR] Chroma extraction failed: {e}")
                features.extend([0.0] * 12)

        # === Spectral Contrast ===
        if librosa_params["features"].get("spectral_contrast", False):
            try:
                contrast = librosa.feature.spectral_contrast(
                    y=y, sr=sr,
                    hop_length=librosa_params.get("hop_length", 512),
                    n_fft=librosa_params.get("n_fft", 2048)
                )
                features.extend(np.mean(contrast, axis=1))
            except Exception as e:
                _raise_feature_memory_error(e)
                model_logger.error(f"[FEATURES ERROR] Spectral Contrast extraction failed: {e}")
                features.extend([0.0] * 7)

        # === ZCR ===
        if librosa_params["features"].get("zcr", False):
            try:
                zcr = librosa.feature.zero_crossing_rate(y)
                features.append(float(np.mean(zcr)))
            except Exception as e:
                _raise_feature_memory_error(e)
                model_logger.error(f"[FEATURES ERROR] ZCR extraction failed: {e}")
                features.append(0.0)

        # === Tonnetz ===
        if librosa_params["features"].get("tonnetz", False):
            try:
                tonnetz = librosa.feature.tonnetz(y=librosa.effects.harmonic(y), sr=sr)
                features.extend(np.mean(tonnetz, axis=1))
            except Exception as e:
                _raise_feature_memory_error(e)
                model_logger.warning(f"Tonnetz extraction failed: {e}")
                features.extend([0.0] * 6)

        # --- Spectral Centroid ---
        if librosa_params["features"].get("spectral_centroid", False):
            try:
                centroid = librosa.feature.spectral_centroid(y=y, sr=sr)
                features.append(float(np.mean(centroid)))
                features.append(float(np.std(centroid)))
            except Exception as e:
                _raise_feature_memory_error(e)
                model_logger.error(f"[FEATURES ERROR] Spectral Centroid extraction failed: {e}")
                features.extend([0.0, 0.0])

        # --- Spectral Bandwidth ---
        if librosa_params["features"].get("spectral_bandwidth", False):
            try:
                bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sr)
                features.append(float(np.mean(bandwidth)))
                features.append(float(np.std(bandwidth)))
            except Exception as e:
                _raise_feature_memory_error(e)
                model_logger.error(f"[FEATURES ERROR] Spectral Bandwidth extraction failed: {e}")
                features.extend([0.0, 0.0])

        # --- Spectral Rolloff ---
        if librosa_params["features"].get("spectral_rolloff", False):
            try:
                rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)
                features.append(float(np.mean(rolloff)))
            except Exception as e:
                _raise_feature_memory_error(e)
                model_logger.error(f"[FEATURES ERROR] Spectral Rolloff extraction failed: {e}")
                features.append(0.0)

        # --- RMS ---
        if librosa_params["features"].get("rms", False):
            try:
                rms = librosa.feature.rms(y=y)
                features.append(float(np.mean(rms)))
            except Exception as e:
                _raise_feature_memory_error(e)
                model_logger.error(f"[FEATURES ERROR] RMS extraction failed: {e}")
                features.append(0.0)

        # --- Onset Strength ---
        if librosa_params["features"].get("onset_strength", False):
            try:
                onset_env = librosa.onset.onset_strength(y=y, sr=sr)
                features.append(float(np.mean(onset_env)))
                features.append(float(np.std(onset_env)))
            except Exception as e:
                _raise_feature_memory_error(e)
                model_logger.error(f"[FEATURES ERROR] Onset Strength extraction failed: {e}")
                features.extend([0.0, 0.0])

        # --- Audio BPM ---
        if (librosa_params["features"].get("tempo", False)
                or librosa_params["features"].get("bpm", False)):
            try:
                tempo_val, _ = librosa.beat.beat_track(y=y, sr=sr)
                tempo_scalar = float(tempo_val[0]) if isinstance(tempo_val, (np.ndarray, list)) else float(tempo_val)
                features.append(tempo_scalar)
            except Exception as e:
                _raise_feature_memory_error(e)
                if is_log_type_enabled("model"):
                    model_logger.error(f"[FEATURES ERROR] Audio BPM extraction failed: {e}")
                features.append(0.0)

        # --- Tempogram ---
        if librosa_params["features"].get("tempogram", False):
            try:
                tempogram = librosa.feature.tempogram(y=y, sr=sr)
                features.append(float(np.mean(tempogram)))
                features.append(float(np.std(tempogram)))
                features.append(float(np.max(tempogram)))
            except Exception as e:
                _raise_feature_memory_error(e)
                model_logger.error(f"[FEATURES ERROR] Tempogram extraction failed: {e}")
                features.extend([0.0, 0.0, 0.0])

        # --- Delta MFCC ---
        if librosa_params["features"].get("delta_mfcc", False):
            if 'mfcc' in locals():
                try:
                    delta_mfcc = librosa.feature.delta(mfcc)
                    features.extend(np.mean(delta_mfcc, axis=1))
                except Exception as e:
                    _raise_feature_memory_error(e)
                    model_logger.error(f"[FEATURES ERROR] Delta MFCC extraction failed: {e}")
                    features.extend([0.0] * librosa_params.get("n_mfcc", 13))
            else:
                features.extend([0.0] * librosa_params.get("n_mfcc", 13))

        # --- Delta-Delta MFCC ---
        if librosa_params["features"].get("delta2_mfcc", False):
            if 'mfcc' in locals():
                try:
                    delta2_mfcc = librosa.feature.delta(mfcc, order=2)
                    features.extend(np.mean(delta2_mfcc, axis=1))
                except Exception as e:
                    _raise_feature_memory_error(e)
                    model_logger.error(f"[FEATURES ERROR] Delta-Delta MFCC extraction failed: {e}")
                    features.extend([0.0] * librosa_params.get("n_mfcc", 13))
            else:
                features.extend([0.0] * librosa_params.get("n_mfcc", 13))

        # --- Spectral Flatness ---
        if librosa_params["features"].get("spectral_flatness", False):
            try:
                flatness = librosa.feature.spectral_flatness(y=y)
                features.append(float(np.mean(flatness)))
            except Exception as e:
                _raise_feature_memory_error(e)
                model_logger.error(f"[FEATURES ERROR] Spectral Flatness extraction failed: {e}")
                features.append(0.0)

        # --- Pitch ---
        if librosa_params["features"].get("pitch", False):
            try:
                f0, voiced_flag, voiced_probs = librosa.pyin(
                    y, fmin=librosa.note_to_hz('C2'), fmax=librosa.note_to_hz('C7')
                )
                pitch_mean = float(np.nanmean(f0)) if f0 is not None else 0.0
                pitch_std = float(np.nanstd(f0)) if f0 is not None else 0.0
                pitch_conf = float(np.nanmean(voiced_probs)) if voiced_probs is not None else 0.0
                features.extend([pitch_mean, pitch_std, pitch_conf])
            except Exception as e:
                _raise_feature_memory_error(e)
                model_logger.warning(f"Pitch feature extraction failed: {e}")
                features.extend([0.0, 0.0, 0.0])

        # --- Silence Ratio ---
        if librosa_params["features"].get("silence_ratio", False):
            if 'rms' in locals():
                try:
                    rms_local = rms
                    silence_threshold = 0.05 * np.max(rms_local)
                    silence_ratio = float(np.sum(rms_local < silence_threshold) / len(rms_local))
                    features.append(silence_ratio)
                except Exception as e:
                    _raise_feature_memory_error(e)
                    model_logger.error(f"[FEATURES ERROR] Silence Ratio extraction failed: {e}")
                    features.append(0.0)
            else:
                features.append(0.0)

        # --- Energy Entropy ---
        if librosa_params["features"].get("energy_entropy", False):
            if 'rms' in locals():
                try:
                    rms_norm = rms / (np.sum(rms) + 1e-10)
                    rms_entropy = float(-np.sum(rms_norm * np.log2(rms_norm + 1e-10)))
                    features.append(rms_entropy)
                except Exception as e:
                    _raise_feature_memory_error(e)
                    model_logger.error(f"[FEATURES ERROR] Energy Entropy extraction failed: {e}")
                    features.append(0.0)
            else:
                features.append(0.0)

        # --- Spectral Skewness / Kurtosis ---
        if librosa_params["features"].get("spectral_skewness", False):
            try:
                import scipy.stats
                S, _ = librosa.magphase(librosa.stft(y))
                skewness = float(np.mean(scipy.stats.skew(S, axis=1)))
                kurtosis = float(np.mean(scipy.stats.kurtosis(S, axis=1)))
                features.extend([skewness, kurtosis])
            except Exception as e:
                _raise_feature_memory_error(e)
                model_logger.error(f"[FEATURES ERROR] Spectral Skewness/Kurtosis extraction failed: {e}")
                features.extend([0.0, 0.0])

        # --- Harmonic Ratio ---
        if librosa_params["features"].get("harmonic_ratio", False):
            try:
                harmonic, percussive = librosa.effects.hpss(y)
                harm_val = np.mean(np.abs(harmonic))
                perc_val = np.mean(np.abs(percussive))
                harmonic_ratio = harm_val / (harm_val + perc_val + 1e-6)
                features.append(float(harmonic_ratio))
            except Exception as e:
                _raise_feature_memory_error(e)
                model_logger.error(f"[FEATURES ERROR] Harmonic Ratio extraction failed: {e}")
                features.append(0.0)

        # --- MFCC std ---
        if librosa_params["features"].get("mfcc_std", False):
            if 'mfcc' in locals():
                try:
                    mfcc_std = np.std(mfcc, axis=1)
                    features.extend(mfcc_std)
                except Exception as e:
                    _raise_feature_memory_error(e)
                    model_logger.error(f"[FEATURES ERROR] MFCC std extraction failed: {e}")
                    features.extend([0.0] * librosa_params.get("n_mfcc", 13))
            else:
                features.extend([0.0] * librosa_params.get("n_mfcc", 13))

        # --- High / Low Energy Ratio ---
        if librosa_params["features"].get("energy_ratio", False):
            if 'rms' in locals():
                try:
                    median_rms = np.median(rms)
                    high_energy = rms[rms >= median_rms]
                    low_energy = rms[rms < median_rms]
                    high_ratio = np.sum(high_energy) / (np.sum(rms) + 1e-10)
                    low_ratio = np.sum(low_energy) / (np.sum(rms) + 1e-10)
                    features.extend([float(high_ratio), float(low_ratio)])
                except Exception as e:
                    _raise_feature_memory_error(e)
                    model_logger.error(f"[FEATURES ERROR] Energy Ratio extraction failed: {e}")
                    features.extend([0.0, 0.0])
            else:
                features.extend([0.0, 0.0])

        # --- Spectral Stats (фиксированный порядок) ---
        if librosa_params["features"].get("spectral_stats", False):
            try:
                names = ["centroid", "bandwidth", "rolloff"]
                local_map = {}
                if 'centroid' in locals():
                    local_map["centroid"] = centroid
                if 'bandwidth' in locals():
                    local_map["bandwidth"] = bandwidth
                if 'rolloff' in locals():
                    local_map["rolloff"] = rolloff
                for nm in names:
                    arr_ = local_map.get(nm)
                    if arr_ is None:
                        features.extend([0.0, 0.0, 0.0, 0.0])
                    else:
                        features.extend([
                            float(np.min(arr_)),
                            float(np.max(arr_)),
                            float(np.median(arr_)),
                            float(np.std(arr_))
                        ])
            except Exception as e:
                _raise_feature_memory_error(e)
                model_logger.error(f"[FEATURES ERROR] Spectral Stats extraction failed: {e}")
                features.extend([0.0] * 12)

        arr = np.array(features, dtype=float)
        expected_length = _expected_audio_feature_length(librosa_params)
        if arr.shape[0] != expected_length:
            raise ValueError(
                f"Нестабильная длина признаков: получено {arr.shape[0]}, ожидалось {expected_length}"
            )

        if is_log_type_enabled("model"):
            if arr.size == 0:
                model_logger.warning(f"[FEATURES WARNING] Итоговый массив признаков пуст! path={path}")
            elif arr.shape[0] < 5:
                model_logger.warning(f"[FEATURES WARNING] Слишком мало признаков ({arr.shape[0]}) path={path}")

        return arr
    except Exception as e:
        # Фатальная ошибка уровня всей функции
        if isinstance(e, MemoryError) or "Unable to allocate" in str(e) or "OutOfMemory" in str(e):
            log_memory_error(e, context="extract_features")
            if is_log_type_enabled("model"):
                model_logger.error(f"[FEATURES ERROR] Признаки не извлечены (OOM) path={path}")
            return np.array([]), f"MemoryError: {e}"
        if is_log_type_enabled("model"):
            model_logger.error(f"extract_features: Unhandled error: {e}", exc_info=True)
        return np.array([]), None

# def extract_features(y, sr, librosa_params, path=None):
#     try:
#         model_logger.debug(
#             f"[FEATURES-AUDIT] Входные параметры: path={path}, y.shape={getattr(y, 'shape', 'N/A')}, sr={sr}, librosa_params={librosa_params}")
#         features = []
#         # === MFCC (тембральные коэффициенты) ===
#         if librosa_params["features"].get("mfcc", True):
#             mfcc = librosa.feature.mfcc(
#                 y=y,
#                 sr=sr,
#                 n_mfcc=librosa_params.get("n_mfcc", 13),
#                 hop_length=librosa_params.get("hop_length", 512),
#                 n_fft=librosa_params.get("n_fft", 2048),
#                 win_length=librosa_params.get("win_length", 2048),
#                 window=librosa_params.get("window", "hann")
#             )
#             vals = np.mean(mfcc.T, axis=0)
#             features.extend(vals)
#             # if is_log_type_enabled("model"):
#             #     model_logger.debug(f"Добавлен MFCC: shape={vals.shape}, values={vals}")
#         # === Chroma (гармония) ===
#         if librosa_params["features"].get("chroma", False):
#             chroma = librosa.feature.chroma_stft(
#                 y=y,
#                 sr=sr,
#                 hop_length=librosa_params.get("hop_length", 512),
#                 n_fft=librosa_params.get("n_fft", 2048)
#             )
#             vals = np.mean(chroma, axis=1)
#             features.extend(vals)
#             # if is_log_type_enabled("model"):
#             #     model_logger.debug(f"Добавлен Chroma: shape={vals.shape}, values={vals}")
#         # === Spectral Contrast (контрасты спектра) ===
#         if librosa_params["features"].get("spectral_contrast", False):
#             contrast = librosa.feature.spectral_contrast(
#                 y=y,
#                 sr=sr,
#                 hop_length=librosa_params.get("hop_length", 512),
#                 n_fft=librosa_params.get("n_fft", 2048)
#             )
#             vals = np.mean(contrast, axis=1)
#             features.extend(vals)
#             # if is_log_type_enabled("model"):
#             #     model_logger.debug(f"Добавлен Spectral Contrast: shape={vals.shape}, values={vals}")
#         # === Zero Crossing Rate (количество переходов через ноль) ===
#         if librosa_params["features"].get("zcr", False):
#             zcr = librosa.feature.zero_crossing_rate(y)
#             val = np.mean(zcr)
#             features.append(float(val))
#             # if is_log_type_enabled("model"):
#             #     model_logger.debug(f"Добавлен ZCR: value={val}")
#         # === Tonnetz (тональное пространство) ===
#         if librosa_params["features"].get("tonnetz", False):
#             try:
#                 tonnetz = librosa.feature.tonnetz(y=librosa.effects.harmonic(y), sr=sr)
#                 vals = np.mean(tonnetz, axis=1)
#                 features.extend(vals)
#                 # if is_log_type_enabled("model"):
#                 #     model_logger.debug(f"Добавлен Tonnetz: shape={vals.shape}, values={vals}")
#             except Exception as e:
#                 if is_log_type_enabled("model"):
#                     model_logger.warning(f"Tonnetz extraction failed: {e}")
#         # === Дополнительные признаки ===
#         # --- Spectral Centroid (яркость звучания) ---
#         if librosa_params["features"].get("spectral_centroid", False):
#             centroid = librosa.feature.spectral_centroid(y=y, sr=sr)
#             mean_centroid = np.mean(centroid)
#             std_centroid = np.std(centroid)
#             features.append(float(mean_centroid))
#             features.append(float(std_centroid))
#         # if is_log_type_enabled("model"):
#         #     model_logger.debug(f"Добавлен Spectral Centroid: mean={mean_centroid}, std={std_centroid}")
#         # --- Spectral Bandwidth (ширина спектра) ---
#         if librosa_params["features"].get("spectral_bandwidth", False):
#             bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sr)
#             mean_bandwidth = np.mean(bandwidth)
#             std_bandwidth = np.std(bandwidth)
#             features.append(float(mean_bandwidth))
#             features.append(float(std_bandwidth))
#         # if is_log_type_enabled("model"):
#         #     model_logger.debug(f"Добавлен Spectral Bandwidth: mean={mean_bandwidth}, std={std_bandwidth}")
#         # --- Spectral Rolloff (частота rolloff, 85% энергии) ---
#         if librosa_params["features"].get("spectral_rolloff", False):
#             rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)
#             mean_rolloff = np.mean(rolloff)
#             features.append(float(mean_rolloff))
#         # if is_log_type_enabled("model"):
#         #     model_logger.debug(f"Добавлен Spectral Rolloff: mean={mean_rolloff}")
#         # --- RMS Energy (громкость) ---
#         if librosa_params["features"].get("rms", False):
#             rms = librosa.feature.rms(y=y)
#             mean_rms = np.mean(rms)
#             features.append(float(mean_rms))
#         # if is_log_type_enabled("model"):
#         #     model_logger.debug(f"Добавлен RMS: mean={mean_rms}")
#         # --- Onset Strength (сила атак) ---
#         if librosa_params["features"].get("onset_strength", False):
#             onset_env = librosa.onset.onset_strength(y=y, sr=sr)
#             mean_onset = np.mean(onset_env)
#             std_onset = np.std(onset_env)
#             features.append(float(mean_onset))
#             features.append(float(std_onset))
#         # if is_log_type_enabled("model"):
#         #     model_logger.debug(f"Добавлен Onset Strength: mean={mean_onset}, std={std_onset}")
#         # --- BPM (темп, вычисленный) ---
#         if librosa_params["features"].get("tempo", False):
#             tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
#             tempo_val = float(tempo[0]) if isinstance(tempo, (np.ndarray, list)) else float(tempo)
#             features.append(tempo_val)
#         # if is_log_type_enabled("model"):
#         #     model_logger.debug(f"Добавлен BPM (tempo): {tempo_val}")
#         # --- Tempogram (динамика ритма) ---
#         if librosa_params["features"].get("tempogram", False):
#             tempogram = librosa.feature.tempogram(y=y, sr=sr)
#             tempogram_mean = float(np.mean(tempogram))
#             tempogram_std = float(np.std(tempogram))
#             tempogram_max = float(np.max(tempogram))
#             features.append(tempogram_mean)
#             features.append(tempogram_std)
#             features.append(tempogram_max)
#         # if is_log_type_enabled("model"):
#         #     model_logger.debug(f"Добавлен Tempogram: mean={tempogram_mean}, std={tempogram_std}, max={tempogram_max}")
#         # --- Delta-Delta MFCC (динамика тембра) ---
#         if librosa_params["features"].get("delta_mfcc", False) and 'mfcc' in locals():
#             delta_mfcc = librosa.feature.delta(mfcc)
#             vals_delta = np.mean(delta_mfcc, axis=1)
#             features.extend(vals_delta)
#         # --- Delta- (динамика тембра) ---
#         if librosa_params["features"].get("delta2_mfcc", False) and 'mfcc' in locals():
#             delta2_mfcc = librosa.feature.delta(mfcc, order=2)
#             vals_delta2 = np.mean(delta2_mfcc, axis=1)
#             features.extend(vals_delta2)
#             # if is_log_type_enabled("model"):
#             #     model_logger.debug(f"Добавлен Delta MFCC: shape={vals_delta.shape}, values={vals_delta}")
#             #     model_logger.debug(f"Добавлен Delta-Delta MFCC: shape={vals_delta2.shape}, values={vals_delta2}")
#         # --- Spectral Flatness (шумность/тональность) ---
#         if librosa_params["features"].get("spectral_flatness", False):
#             flatness = librosa.feature.spectral_flatness(y=y)
#             mean_flatness = np.mean(flatness)
#             features.append(float(mean_flatness))
#         # if is_log_type_enabled("model"):
#         #     model_logger.debug(f"Добавлен Spectral Flatness: mean={mean_flatness}")
#         # --- Pitch features (основная частота и доверие) ---
#         if librosa_params["features"].get("pitch", False):
#             try:
#                 f0, voiced_flag, voiced_probs = librosa.pyin(y, fmin=librosa.note_to_hz('C2'), fmax=librosa.note_to_hz('C7'))
#                 pitch_mean = float(np.nanmean(f0)) if f0 is not None else 0
#                 pitch_std = float(np.nanstd(f0)) if f0 is not None else 0
#                 pitch_conf = float(np.nanmean(voiced_probs)) if voiced_probs is not None else 0
#             except Exception as e:
#                 if is_log_type_enabled("model"):
#                     model_logger.warning(f"Pitch feature extraction failed: {e}")
#                 pitch_mean, pitch_std, pitch_conf = 0, 0, 0
#             features.extend([pitch_mean, pitch_std, pitch_conf])
#         # if is_log_type_enabled("model"):
#         #     model_logger.debug(f"Добавлен Pitch: mean={pitch_mean}, std={pitch_std}, conf={pitch_conf}")
#         # --- Silence Ratio (доля тишины) ---
#         # Тишиной считаем RMS < 5% max(rms)
#         if librosa_params["features"].get("silence_ratio", False) and 'rms' in locals():
#             silence_threshold = 0.05 * np.max(rms)
#             silence_ratio = float(np.sum(rms < silence_threshold) / len(rms))
#             features.append(silence_ratio)
#         # if is_log_type_enabled("model"):
#         #     model_logger.debug(f"Добавлен Silence Ratio: {silence_ratio}")
#         # --- Energy Entropy (энтропия энергии) ---
#         # Логарифмируем, нормируем, считаем энтропию
#         if librosa_params["features"].get("energy_entropy", False) and 'rms' in locals():
#             rms_norm = rms / (np.sum(rms) + 1e-10)
#             rms_entropy = float(-np.sum(rms_norm * np.log2(rms_norm + 1e-10)))
#             features.append(rms_entropy)
#         # if is_log_type_enabled("model"):
#         #     model_logger.debug(f"Добавлен Energy Entropy: {rms_entropy}")
#         # === Spectral Skewness и Kurtosis ===
#         if librosa_params["features"].get("spectral_skewness", False):
#             import scipy.stats
#             S, _ = librosa.magphase(librosa.stft(y))
#             # По частотам (axis=1), усреднение по каналам
#             skewness = float(np.mean(scipy.stats.skew(S, axis=1)))
#             kurtosis = float(np.mean(scipy.stats.kurtosis(S, axis=1)))
#             features.extend([skewness, kurtosis])
#             # if is_log_type_enabled("model"):
#             #     model_logger.debug(f"Добавлен Spectral Skewness: {skewness}, Kurtosis: {kurtosis}")
#         # === Harmonic/Percussive Ratio ===
#         if librosa_params["features"].get("harmonic_ratio", False):
#             harmonic, percussive = librosa.effects.hpss(y)
#             harm_val = np.mean(np.abs(harmonic))
#             perc_val = np.mean(np.abs(percussive))
#             harmonic_ratio = harm_val / (harm_val + perc_val + 1e-6)
#             features.append(harmonic_ratio)
#             if is_log_type_enabled("model"):
#                 model_logger.debug(f"Добавлен Harmonic Ratio: {harmonic_ratio}")
#         # === MFCC std по времени ===
#         if librosa_params["features"].get("mfcc_std", False) and 'mfcc' in locals():
#             mfcc_std = np.std(mfcc, axis=1)
#             features.extend(mfcc_std)
#             # if is_log_type_enabled("model"):
#             #     model_logger.debug(f"Добавлен MFCC std: {mfcc_std}")
#         # === High/Low Energy Ratio ===
#         if librosa_params["features"].get("energy_ratio", False) and 'rms' in locals():
#             median_rms = np.median(rms)
#             high_energy = rms[rms >= median_rms]
#             low_energy = rms[rms < median_rms]
#             high_ratio = np.sum(high_energy) / (np.sum(rms) + 1e-10)
#             low_ratio = np.sum(low_energy) / (np.sum(rms) + 1e-10)
#             features.extend([high_ratio, low_ratio])
#             # if is_log_type_enabled("model"):
#             #     model_logger.debug(f"Добавлен High/Low Energy Ratio: {high_ratio}/{low_ratio}")
#         # === Spectral Stats (min/max/median/std по спектральным признакам) ===
#         if librosa_params["features"].get("spectral_stats", False):
#             spectral_features = {}
#             if 'centroid' in locals():
#                 spectral_features['centroid'] = centroid
#             if 'bandwidth' in locals():
#                 spectral_features['bandwidth'] = bandwidth
#             if 'rolloff' in locals():
#                 spectral_features['rolloff'] = rolloff
#             for name, arr in spectral_features.items():
#                 min_val = float(np.min(arr))
#                 max_val = float(np.max(arr))
#                 median_val = float(np.median(arr))
#                 std_val = float(np.std(arr))
#                 features.extend([min_val, max_val, median_val, std_val])
#                 # if is_log_type_enabled("model"):
#                 #     model_logger.debug(
#                 #         f"Добавлен Spectral Stats для {name}: min={min_val}, max={max_val}, median={median_val}, std={std_val}")
#
#         # === Проверка типов ===
#         # if is_log_type_enabled("model"):
#         #     model_logger.debug(f"features len: {len(features)}, types: {[type(x) for x in features]}")
#             for i, v in enumerate(features):
#                 if isinstance(v, np.ndarray):
#                     model_logger.debug(f"NDARRAY at features[{i}]: shape={v.shape}, value={v}")
#         arr = np.array(features)
#         model_logger.error("[DEBUG] arr.size = %d" % arr.size)
#         print("DEBUG: arr.size =", arr.size)
#         if is_log_type_enabled("model"):
#             if arr.size == 0:
#                 model_logger.warning(
#                     f"[FEATURES WARNING] Итоговый массив признаков пуст! path={path if path else 'N/A'}, параметры features: {librosa_params.get('features', {})}")
#                 # Дополнительное логирование причин пустого массива признаков для отладки!
#                 # Проверим длину аудиомассива, параметры анализа
#                 if y is not None and hasattr(y, "shape"):
#                     model_logger.error(
#                         f"[FEATURES ERROR][AUDIT] Пустой массив признаков для файла: {path if path else 'N/A'} | "
#                         f"y.shape={y.shape}, sr={sr}, duration={librosa_params.get('duration', 'N/A')}, "
#                         f"offset={librosa_params.get('offset', 'N/A')}, "
#                         f"features_enabled={list(librosa_params['features'].keys())} | "
#                         f"librosa_params={librosa_params}"
#                     )
#                 else:
#                     model_logger.error(
#                         f"[FEATURES ERROR][AUDIT] Не удалось получить аудиомассив для файла: {path if path else 'N/A'} | "
#                         f"sr={sr}, duration={librosa_params.get('duration', 'N/A')}, offset={librosa_params.get('offset', 'N/A')}, "
#                         f"features_enabled={list(librosa_params['features'].keys())} | "
#                         f"librosa_params={librosa_params}"
#                     )
#             elif arr.shape[0] < 5:
#                 model_logger.warning(
#                     f"[FEATURES WARNING] Мало признаков ({arr.shape[0]}), возможно проблема с параметрами features или аудиофайлом. path={path if path else 'N/A'}")
#         if is_log_type_enabled("model"):
#             # Вспомогательная функция для безопасного форматирования признаков
#             def arr_to_str(val):
#                 if isinstance(val, (np.ndarray, list)):
#                     return np.array(val).round(4).tolist()
#                 return round(float(val), 4) if isinstance(val, float) else val
#
#             enabled_features = [k for k, v in librosa_params["features"].items() if v]
#             model_logger.debug(f"Включённые признаки: {enabled_features}")
#
#             model_logger.debug(
#                 "extract_features: summary:\n"
#                 f"  MFCC: {arr_to_str(np.mean(mfcc.T, axis=0)) if 'mfcc' in locals() else 'off'}\n"
#                 f"  Delta MFCC: {arr_to_str(np.mean(delta_mfcc, axis=1)) if 'delta_mfcc' in locals() else 'off'}\n"
#                 f"  Delta-Delta MFCC: {arr_to_str(np.mean(delta2_mfcc, axis=1)) if 'delta2_mfcc' in locals() else 'off'}\n"
#                 f"  Chroma: {arr_to_str(np.mean(chroma, axis=1)) if 'chroma' in locals() else 'off'}\n"
#                 f"  Spectral Contrast: {arr_to_str(np.mean(contrast, axis=1)) if 'contrast' in locals() else 'off'}\n"
#                 f"  ZCR: {arr_to_str(np.mean(zcr)) if 'zcr' in locals() else 'off'}\n"
#                 f"  Tonnetz: {arr_to_str(np.mean(tonnetz, axis=1)) if 'tonnetz' in locals() else 'off'}\n"
#                 f"  Spectral Centroid (mean, std): "
#                     f"{mean_centroid:.2f}, {std_centroid:.2f}" if 'mean_centroid' in locals() and 'std_centroid' in locals() else 'off' + "\n"
#                 f"  Spectral Bandwidth (mean, std): "
#                     f"{mean_bandwidth:.2f}, {std_bandwidth:.2f}" if 'mean_bandwidth' in locals() and 'std_bandwidth' in locals() else 'off' + "\n"
#                 f"  Spectral Rolloff: {mean_rolloff:.2f}\n" if 'mean_rolloff' in locals() else "  Spectral Rolloff: off\n"
#                 f"  RMS: {mean_rms:.2f}\n" if 'mean_rms' in locals() else "  RMS: off\n"
#                 f"  Onset Strength (mean, std): "
#                     f"{mean_onset:.2f}, {std_onset:.2f}" if 'mean_onset' in locals() and 'std_onset' in locals() else 'off' + "\n"
#                 f"  BPM (tempo): {tempo_val:.2f}\n" if 'tempo_val' in locals() else "  BPM (tempo): off\n"
#                 f"  Tempogram (mean, std, max): "
#                     f"{tempogram_mean:.4f}, {tempogram_std:.4f}, {tempogram_max:.4f}" if 'tempogram_mean' in locals() and 'tempogram_std' in locals() and 'tempogram_max' in locals() else 'off' + "\n"
#                 f"  Pitch (mean, std, conf): "
#                     f"{pitch_mean:.2f}, {pitch_std:.2f}, {pitch_conf:.2f}" if 'pitch_mean' in locals() and 'pitch_std' in locals() and 'pitch_conf' in locals() else 'off' + "\n"
#                 f"  Silence Ratio: {silence_ratio:.4f}\n" if 'silence_ratio' in locals() else "  Silence Ratio: off\n"
#                 f"  Energy Entropy: {rms_entropy:.4f}\n" if 'rms_entropy' in locals() else "  Energy Entropy: off\n"
#                 f"  Spectral Skewness/Kurtosis: {skewness:.4f}, {kurtosis:.4f}\n" if 'skewness' in locals() and 'kurtosis' in locals() else "  Spectral Skewness/Kurtosis: off\n"
#                 f"  Harmonic Ratio: {harmonic_ratio:.4f}\n" if 'harmonic_ratio' in locals() else "  Harmonic Ratio: off\n"
#                 f"  MFCC std: {arr_to_str(mfcc_std) if 'mfcc_std' in locals() else 'off'}\n"
#                 f"  High/Low Energy Ratio: {high_ratio:.4f}/{low_ratio:.4f}\n" if 'high_ratio' in locals() and 'low_ratio' in locals() else "  High/Low Energy Ratio: off\n"
#                 f"  Spectral Stats (min/max/median/std): centroid=({min_val:.2f},{max_val:.2f},{median_val:.2f},{std_val:.2f})\n" if 'min_val' in locals() else "  Spectral Stats: off\n"
#                 f"  Вектор признаков (первые 10): {arr[:10]}\n"
#                 f"  Shape: {arr.shape}, ndim: {arr.ndim}"
#             )
#         return arr
#     except Exception as e:
#         if isinstance(e, MemoryError) or "Unable to allocate" in str(e) or "OutOfMemory" in str(e):
#             log_memory_error(e, context="extract_features")
#             if is_log_type_enabled("model"):
#                 model_logger.error(f"[FEATURES ERROR] Признаки не извлечены, вероятно ошибка памяти! path={path if 'path' in locals() else 'N/A'}")
#             return np.array([]), f"MemoryError: {e}"
#         if is_log_type_enabled("model"):
#             model_logger.error(f"extract_features: Unhandled error: {e}", exc_info=True)
#             model_logger.error(f"extract_features: error for audio, exception: {e}", exc_info=True)
#             # Расширенное логирование причин ошибки с деталями аудиомассива и параметров
#             if 'y' in locals() and y is not None and hasattr(y, "shape"):
#                 model_logger.error(
#                     f"[FEATURES ERROR][AUDIT] Исключение при извлечении признаков: {e}, файл: {path if 'path' in locals() else 'N/A'}, "
#                     f"y.shape={y.shape if 'y' in locals() else 'None'}, sr={sr if 'sr' in locals() else 'None'}, "
#                     f"duration={librosa_params.get('duration', 'N/A')}, offset={librosa_params.get('offset', 'N/A')}, "
#                     f"features_enabled={list(librosa_params['features'].keys())} | "
#                     f"librosa_params={librosa_params}"
#                 )
#             else:
#                 model_logger.error(
#                     f"[FEATURES ERROR][AUDIT] Исключение при извлечении признаков: {e}, файл: {path if 'path' in locals() else 'N/A'}, "
#                     f"y.shape=None, sr={sr if 'sr' in locals() else 'None'}, duration={librosa_params.get('duration', 'N/A')}, "
#                     f"offset={librosa_params.get('offset', 'N/A')}, features_enabled={list(librosa_params['features'].keys())} | "
#                     f"librosa_params={librosa_params}"
#                 )
#             # --- Расширенное информативное логирование ---
#             model_logger.error(f"[extract_features] Параметры: librosa_params={librosa_params}")
#             model_logger.error(f"[extract_features] Тип ошибки: {type(e).__name__}")
#             model_logger.error(
#                 f"[extract_features] Входные y.shape: {y.shape if 'y' in locals() else 'None'}, sr: {sr if 'sr' in locals() else 'None'}")
#             enabled_features = [k for k, v in librosa_params.get("features", {}).items() if v]
#             model_logger.error(f"[extract_features] Включённые признаки: {enabled_features}")
#             if 'path' in locals():
#                 model_logger.error(f"[extract_features] Анализируемый файл: {path}")
#         return np.array([]), None

def extract_features_from_track(
        track,
        audio_features,
        rekordbox_data=None,
        use_rekordbox=False,
        include_metadata=False,
):
    # if is_log_type_enabled("model"):
    #     model_logger.debug(f"extract_features_from_track: ВХОД audio_features shape={np.shape(audio_features)}, ndim={np.ndim(audio_features)}")
    # --- БЛОК ЗАЩИТЫ ОТ None И ПУСТЫХ ---
    if audio_features is None or np.size(audio_features) == 0:
        if is_log_type_enabled("model"):
            model_logger.error("audio_features is None or empty, skipping this track")
        return None
    audio_features = np.asarray(audio_features)
    if audio_features.ndim > 1:
        if is_log_type_enabled("model"):
            model_logger.debug(f"extract_features_from_track: Преобразуем audio_features из shape={audio_features.shape} в 1D")
        audio_features = audio_features.flatten()
        if is_log_type_enabled("model"):
            model_logger.debug(f"extract_features_from_track: audio_features shape={audio_features.shape}, ndim={audio_features.ndim}")
    # Rating/Color/Situation описывают личные предпочтения пользователя, а не
    # акустический жанр. При обычном распознавании они неизвестны, поэтому для
    # жанровой модели всегда оставляем одинаковые нейтральные значения. Параметр
    # include_metadata сохранён для будущей отдельной модели рекомендаций.
    meta = {}
    if include_metadata:
        if use_rekordbox and rekordbox_data and track and 'path' in track:
            meta = get_rekordbox_meta(track['path'], rekordbox_data)
        if not meta and track:
            meta = track
    rating = float(get_meta_value(meta, "rating", 0)) / 5.0 if get_meta_value(meta, "rating") else 0.0
    bpm = float(get_meta_value(meta, "bpm", 0)) / 200.0 if get_meta_value(meta, "bpm") else 0.0
    color_idx = COLOR_MAP.get(get_meta_value(meta, "color", ""), -1)
    situation_idx = SITUATION_MAP.get(get_meta_value(meta, "situation", ""), 0)
    audio_features = np.asarray(audio_features)
    if is_log_type_enabled("model"):
        model_logger.debug(f"extract_features_from_track: audio_features shape={audio_features.shape}, ndim={audio_features.ndim}")
    if audio_features.ndim > 1:
        audio_features = np.mean(audio_features, axis=-1)
        if is_log_type_enabled("model"):
            model_logger.debug(f"extract_features_from_track: audio_features after mean shape={audio_features.shape}, ndim={audio_features.ndim}")
    feature_vector = np.concatenate([audio_features, [rating, bpm, color_idx, situation_idx]])
    if is_log_type_enabled("model"):
        model_logger.debug(f"extract_features_from_track: feature_vector shape={feature_vector.shape}, values={feature_vector[:7]}...")
    return feature_vector

def get_genre(path, librosa_params=None, return_meta=False, track_metadata=None,
              defer_vocal_language=False, defer_deep_embedding=False):
    """
    Определение жанра + (опционально) YAMNet fusion.
    Возвращает:
      - если return_meta=False: (genre, proba, features_or_None)
      - если return_meta=True: (genre, proba, features_1D, meta_vectors)
        meta_vectors = {
            "labels": [...],
            "rf_proba": [...]/None,
            "yamnet_prior": [...]/None,
            "fused_proba": [...]/None
        }
    """
    if not get_advanced_mode():
        return ("Unknown", 1.0, None) if not return_meta else ("Unknown", 1.0, None, None)

    original_input_path = path

    # --- 1. Нормализация пути ---
    if not os.path.exists(path):
        try:
            from .config import load_config, DEFAULT_CONFIG
            from .utils import safe_join_music_dir
            cfg = load_config()
            music_dir = cfg.get("music_dir", DEFAULT_CONFIG["music_dir"])
            path = safe_join_music_dir(music_dir, path)
        except Exception as e:
            if is_log_type_enabled("model"):
                model_logger.error(f"[PATH][GENRE] Не удалось нормализовать путь '{original_input_path}': {e}")
    else:
        if is_log_type_enabled("model"):
            model_logger.debug(f"[GENRE] Локальный путь используется напрямую: {path}")

    if is_log_type_enabled("model"):
        model_logger.debug(f"[GENRE] Итоговый путь для анализа: {path}, exists={os.path.exists(path)}, isfile={os.path.isfile(path)}")

    # --- 2. Загрузка модели ---
    full_proba_vec = None     # RF predict_proba
    effnet_proba_vec = None   # optional Discogs Multi-EffNet genre head
    effnet_embedding_source = None
    yam_prior = None          # YAMNet prior
    fused_proba_vec = None    # Фьюжн (если применён)
    try:
        model_meta = _load_genre_model_meta_cached()
    except Exception as e:
        if is_log_type_enabled("model"):
            model_logger.error(f"[GENRE] Не удалось загрузить модель '{MODEL_PATH}': {e}")
        if isinstance(e, MemoryError) or "could not allocate" in str(e).lower():
            raise MemoryError(f"Не удалось загрузить модель жанров: {e}") from e
        return ("Unknown", 0.0, None) if not return_meta else ("Unknown", 0.0, None, None)

    model = model_meta.get("model")
    if model is None:
        if is_log_type_enabled("model"):
            model_logger.error("[GENRE] model_meta['model'] отсутствует")
        return ("Unknown", 0.0, None) if not return_meta else ("Unknown", 0.0, None, None)

    model_librosa_params = model_meta.get("librosa_params", {}) or {}
    taxonomy_model_active = str(model_meta.get("version", "")).startswith(("2.", "3.", "4."))

    # --- 3. Runtime overrides ---
    try:
        from .librosa_settings import load_librosa_settings
        runtime_settings = load_librosa_settings()
    except Exception:
        runtime_settings = {}

    runtime_keys = (
        "genre_threshold", "yamnet_enabled", "yamnet_use_cuda",
        "yamnet_alpha", "yamnet_model_path", "auto_class_thresholds_enabled",
        "min_genre_margin", "segment_disagreement_penalty", "language_threshold",
        "vocal_language_enabled", "vocal_language_model", "vocal_language_device",
        "vocal_language_compute_type", "vocal_language_min_probability",
        "vocal_language_min_speech_seconds", "vocal_language_detection_segments",
        "vocal_language_cpu_threads", "vocal_language_mark_instrumental",
        "vocal_language_music_fallback_enabled", "vocal_language_music_min_probability",
        "vocal_language_segment_consensus_enabled",
        "vocal_language_rf_fallback",
        "family_fallback_enabled", "family_fallback_threshold",
        "family_fallback_margin",
    )
    for rk in runtime_keys:
        if rk in runtime_settings and rk in model_librosa_params:
            if model_librosa_params.get(rk) != runtime_settings[rk] and is_log_type_enabled("model"):
                model_logger.info(f"[RUNTIME OVERRIDE] {rk}: model={model_librosa_params.get(rk)} -> runtime={runtime_settings[rk]}")

    # --- 4. Сбор params ---
    default_params = {
        "sample_rate": 22050,
        "duration": 30,
        "offset": 45,
        "n_mfcc": 40,
        "hop_length": 512,
        "n_fft": 4096,
        "win_length": 2048,
        "window": "hann",
        "use_id3": True,
        "use_folder": True,
        "genre_threshold": 0.55,
        "auto_class_thresholds_enabled": True,
        "min_genre_margin": 0.1,
        "segment_disagreement_penalty": 0.1,
        "language_threshold": 0.6,
        "vocal_language_enabled": False,
        "vocal_language_model": "base",
        "vocal_language_device": "cpu",
        "vocal_language_compute_type": "int8",
        "vocal_language_min_probability": 0.70,
        "vocal_language_min_speech_seconds": 2.0,
        "vocal_language_mark_instrumental": False,
        "vocal_language_music_fallback_enabled": True,
        "vocal_language_music_min_probability": 0.80,
        "vocal_language_segment_consensus_enabled": True,
        "vocal_language_detection_segments": 3,
        "vocal_language_cpu_threads": 4,
        "vocal_language_rf_fallback": True,
        "family_fallback_enabled": True,
        "family_fallback_threshold": 0.68,
        "family_fallback_margin": 0.15,
        # Старые модели не содержат эти ключи и продолжают анализировать один
        # сегмент. Новая модель сохранит значения, использованные при обучении.
        "multi_segment_enabled": False,
        "multi_segment_offsets": "",
        "multi_segment_duration": 30,
        "features": {},
        "yamnet_enabled": False,
        "yamnet_use_cuda": False,
        "yamnet_alpha": 0.35,
        "yamnet_model_path": str(YAMNET_MODEL_FILE)
    }
    params = default_params.copy()
    for k, v in model_librosa_params.items():
        if k == "features":
            continue
        if k in params:
            params[k] = v
    if "features" in model_librosa_params:
        params["features"] = copy.deepcopy(model_librosa_params["features"])

    # Runtime override критичных ключей
    if "genre_threshold" in runtime_settings:
        params["genre_threshold"] = runtime_settings["genre_threshold"]
    params["auto_class_thresholds_enabled"] = runtime_settings.get(
        "auto_class_thresholds_enabled", params["auto_class_thresholds_enabled"]
    )
    params["min_genre_margin"] = runtime_settings.get("min_genre_margin", params["min_genre_margin"])
    params["segment_disagreement_penalty"] = runtime_settings.get(
        "segment_disagreement_penalty",
        params["segment_disagreement_penalty"],
    )
    params["language_threshold"] = runtime_settings.get("language_threshold", params["language_threshold"])
    for key in (
            "vocal_language_enabled", "vocal_language_model", "vocal_language_device",
            "vocal_language_compute_type", "vocal_language_min_probability",
            "vocal_language_min_speech_seconds", "vocal_language_detection_segments",
            "vocal_language_cpu_threads", "vocal_language_mark_instrumental",
            "vocal_language_music_fallback_enabled", "vocal_language_music_min_probability",
            "vocal_language_segment_consensus_enabled",
            "vocal_language_rf_fallback",
            "family_fallback_enabled", "family_fallback_threshold",
            "family_fallback_margin",
    ):
        params[key] = runtime_settings.get(key, params[key])
    params["yamnet_enabled"] = runtime_settings.get("yamnet_enabled", params["yamnet_enabled"])
    params["yamnet_use_cuda"] = runtime_settings.get("yamnet_use_cuda", params["yamnet_use_cuda"])
    params["yamnet_alpha"] = runtime_settings.get("yamnet_alpha", params["yamnet_alpha"])
    params["yamnet_model_path"] = runtime_settings.get("yamnet_model_path", params["yamnet_model_path"])
    if defer_vocal_language:
        # Library indexing keeps RF + YAMNet parallel. Whisper is applied later
        # by the durable single-worker enrichment queue.
        params["vocal_language_enabled"] = False

    yamnet_active = bool(params.get("yamnet_enabled", False))

    if is_log_type_enabled("model"):
        model_logger.debug(
            f"[GENRE] Итоговые параметры: sr={params['sample_rate']} dur={params['duration']} off={params['offset']} "
            f"thr={params['genre_threshold']} yamnet={yamnet_active} alpha={params['yamnet_alpha']} "
            f"margin={params['min_genre_margin']} segment_penalty={params['segment_disagreement_penalty']} "
            f"multi={params['multi_segment_enabled']} vocal_lang={params['vocal_language_enabled']} "
            f"segments={params['multi_segment_offsets']} yam_path={params['yamnet_model_path']}"
        )

    # --- 5. Candidate genre по папке ---
    folder_name = os.path.basename(os.path.dirname(path)) if path else ""
    genre_settings = load_genre_settings()
    candidate_genre = normalize_genre(folder_name, genre_settings, logger)
    if is_log_type_enabled("model"):
        model_logger.debug(f"[GENRE] candidate_genre='{candidate_genre}'")

    # --- 6. Попытка ID3 shortcut ---
    id3_genre = None
    id3_title = None
    id3_artist = None
    if params.get("use_id3", True):
        try:
            tags = EasyID3(path)
            genre_from_tags = tags.get("genre", [None])[0]
            id3_genre = genre_from_tags
            id3_title = tags.get("title", [None])[0]
            id3_artist = tags.get("artist", [None])[0]
            if is_log_type_enabled("model"):
                model_logger.debug(f"[GENRE][ID3] genre_from_tags={genre_from_tags}")
            if genre_from_tags and isinstance(genre_from_tags, str):
                trainable_genres_lower = {
                    (v["genre"] if isinstance(v, dict) else v).lower()
                    for v in genre_settings.values()
                    if (isinstance(v, dict) and v.get("is_trainable", False)) or isinstance(v, str)
                }
                if genre_from_tags.strip().lower() in trainable_genres_lower and not taxonomy_model_active:
                    if is_log_type_enabled("model"):
                        model_logger.info(f"[GENRE][ID3] Быстрый жанр: {genre_from_tags}")
                    # ID3 shortcut — НЕ строим meta (сохраняем прежнюю семантику).
                    return (genre_from_tags, 1.0, None) if not (yamnet_active and return_meta) else (genre_from_tags, 1.0, None, None)
        except Exception as e:
            if is_log_type_enabled("model"):
                model_logger.debug(f"[GENRE][ID3] Ошибка чтения: {e}")

    # --- 7-8. Загрузка одного/нескольких сегментов и извлечение признаков ---
    try:
        full_features, segment_feature_rows, audio_segments, segment_errors = _extract_multisegment_features(
            path,
            params,
        )
        if is_log_type_enabled("model"):
            used_offsets = [segment[2] for segment in audio_segments]
            model_logger.debug(
                f"[AUDIO][SEGMENTS] used={used_offsets}, count={len(audio_segments)}, "
                f"skipped={segment_errors[:3]}"
            )
    except MemoryError as me:
        log_memory_error(me, context="get_genre_feature_extraction", track_path=path)
        return ((candidate_genre or "Unknown"), 0.0, None) if not (yamnet_active and return_meta) else ((candidate_genre or "Unknown"), 0.0, None, None)
    except Exception as e:
        if is_log_type_enabled("model"):
            model_logger.error(f"[AUDIO][SEGMENTS] Ошибка: {e}")
        save_bad_file_info(path, None)
        return ((candidate_genre or "Unknown"), 0.0, None) if not (yamnet_active and return_meta) else ((candidate_genre or "Unknown"), 0.0, None, None)

    # --- 9. Сравнение с train_features_dict (диагностика) ---
    try:
        train_features_dict = model_meta.get("train_features_dict") or {}
        if train_features_dict and is_log_type_enabled("model"):
            norm_key = normalize_audio_filename(os.path.basename(path))
            if norm_key in train_features_dict:
                train_vec = np.array(train_features_dict[norm_key])
                diff = np.abs(np.array(full_features) - train_vec)
                if diff.size > 0:
                    model_logger.debug(f"[FEATURES][COMPARE] max_diff={diff.max():.6f} mean_diff={diff.mean():.6f}")
    except Exception as e:
        if is_log_type_enabled("model"):
            model_logger.debug(f"[FEATURES][COMPARE] Ошибка сравнения: {e}")

    # --- 9a. ALIGN ---
    try:
        expected_len = model_meta.get("expected_feature_len") or getattr(model, "n_features_in_", None)
        aligned_rows = []
        for feature_row in segment_feature_rows:
            feature_row = np.asarray(feature_row, dtype=float).reshape(-1)
            cur_len = feature_row.shape[0]
            if expected_len and cur_len < expected_len:
                feature_row = np.concatenate([
                    feature_row,
                    np.zeros(expected_len - cur_len, dtype=feature_row.dtype),
                ])
            elif expected_len and cur_len > expected_len:
                feature_row = feature_row[:expected_len]
            aligned_rows.append(feature_row)
        X_segments = np.vstack(aligned_rows)
        full_features = np.mean(X_segments, axis=0)
    except Exception as e_align:
        if is_log_type_enabled("model"):
            model_logger.error(f"[FEATURES][ALIGN] Ошибка: {e_align}")
        return ((candidate_genre or "Unknown"), 0.0, None) if not (yamnet_active and return_meta) else ((candidate_genre or "Unknown"), 0.0, None, None)

    # --- 10. Предсказание RF ---
    segment_disagreement = False
    hierarchy_family_meta = family_decision(model, X_segments)
    family_fallback_applied = False
    try:
        X = np.asarray(full_features, dtype=float).reshape(1, -1)
        proba = 0.0
        try:
            if hasattr(model, "predict_proba"):
                segment_probabilities = np.asarray(model.predict_proba(X_segments), dtype=float)
                full_proba_vec = np.mean(segment_probabilities, axis=0)
                if len(segment_probabilities) > 1:
                    segment_winners = np.argmax(segment_probabilities, axis=1)
                    segment_disagreement = len(set(segment_winners.tolist())) > 1
                if full_proba_vec is not None and full_proba_vec.size:
                    best_index = int(np.argmax(full_proba_vec))
                    predicted_genre = model.classes_[best_index]
                    proba = float(full_proba_vec[best_index])
                else:
                    raise ValueError("predict_proba вернул пустой результат")
            else:
                predicted_genre = model.predict(X)[0]
        except Exception as pe:
            if is_log_type_enabled("model"):
                model_logger.debug(f"[PREDICT] predict_proba error: {pe}")
            return ((candidate_genre or "Unknown"), 0.0, None) if not (yamnet_active and return_meta) else ((candidate_genre or "Unknown"), 0.0, None, None)
        if not np.isfinite(proba):
            proba = 0.0
        if is_log_type_enabled("model"):
            labels_for_log = list(getattr(model, "classes_", []))
            probabilities_for_log = {
                str(label): round(float(value), 4)
                for label, value in zip(labels_for_log, full_proba_vec)
            }
            model_logger.debug(
                f"[PREDICT] RF genre='{predicted_genre}', conf={proba:.3f}, "
                f"segments={len(X_segments)}, all={probabilities_for_log}"
            )
    except Exception as e:
        if is_log_type_enabled("model"):
            model_logger.error(f"[PREDICT] Общая ошибка: {e}")
        return ((candidate_genre or "Unknown"), 0.0, None) if not (yamnet_active and return_meta) else ((candidate_genre or "Unknown"), 0.0, None, None)

    # --- 11. YAMNet Fusion (только если активен) ---
    global_threshold = float(params.get("genre_threshold", 0.55))
    decision_proba_vec = np.asarray(full_proba_vec, dtype=float).copy() if full_proba_vec is not None else None

    # The deep head is optional and never runs inside the multiprocessing RF
    # scan.  For an individual track it first reuses the independent catalog
    # index and only then performs one live ONNX extraction if needed.
    effnet_head = model_meta.get("effnet_genre_head")
    if (
            not defer_deep_embedding
            and effnet_head is not None
            and decision_proba_vec is not None
    ):
        try:
            config_value = load_config()
            pipeline = get_model_pipeline_settings(config_value)
            if (
                    pipeline.get("effnet_enabled", False)
                    and pipeline.get("effnet_genre_fusion_enabled", True)
            ):
                deep_vector = cached_library_embedding(
                    path,
                    config_value.get("music_dir"),
                )
                if deep_vector is not None:
                    effnet_embedding_source = "catalog_cache"
                else:
                    deep_vector = extract_deep_embedding(path, pipeline)
                    if deep_vector is not None:
                        effnet_embedding_source = "live"
                if deep_vector is not None:
                    deep_matrix = np.asarray(
                        deep_vector, dtype=np.float32,
                    ).reshape(1, -1)
                    effnet_proba_vec = effnet_head.aligned_probabilities(
                        deep_matrix,
                        getattr(model, "classes_", []),
                    )[0]
                    alpha = float(
                        model_meta.get("effnet_genre_fusion_alpha", 0.35) or 0.35
                    )
                    decision_proba_vec = fuse_probabilities(
                        decision_proba_vec.reshape(1, -1),
                        effnet_proba_vec.reshape(1, -1),
                        alpha,
                    )[0]
                    deep_index = int(np.argmax(decision_proba_vec))
                    predicted_genre = model.classes_[deep_index]
                    proba = float(decision_proba_vec[deep_index])
                    if is_log_type_enabled("model"):
                        model_logger.debug(
                            "[EFFNET][FUSION] source=%s alpha=%.2f genre='%s' conf=%.3f",
                            effnet_embedding_source, alpha, predicted_genre, proba,
                        )
        except Exception as effnet_error:
            if is_log_type_enabled("model"):
                model_logger.warning(
                    "[EFFNET][FUSION] skipped; acoustic fallback is used: %s",
                    effnet_error,
                )

    global _yamnet_disabled, _yamnet_cuda_failed, _yamnet_logged_cuda_switch
    if _yamnet_cuda_failed and not _yamnet_logged_cuda_switch and is_log_type_enabled("model"):
        model_logger.info("[YAMNET] Переключено на CPU-only после CUDA ошибки.")
        _yamnet_logged_cuda_switch = True

    if yamnet_active and not _yamnet_disabled and decision_proba_vec is not None:
        try:
            yam_alpha = float(params.get("yamnet_alpha", 0.35))
            yam_model_raw = params.get("yamnet_model_path", str(YAMNET_MODEL_FILE))
            resolved_path = str(resolve_project_path(yam_model_raw, YAMNET_MODEL_FILE))
            if os.path.isfile(resolved_path):
                sess, inp_name, labels_521 = _yamnet_get_session(
                    resolved_path,
                    allow_cuda=bool(params.get("yamnet_use_cuda", False)),
                )
                if sess and inp_name and labels_521:
                    my_labels = list(getattr(model, "classes_", []))
                    try:
                        track_mtime = os.path.getmtime(path)
                        cache_key = (
                            os.path.abspath(path),
                            track_mtime,
                            16000,
                            tuple(round(float(segment[2]), 3) for segment in audio_segments),
                            float(params.get("multi_segment_duration", params.get("duration", 30))),
                        )
                    except Exception:
                        cache_key = None

                    if cache_key and cache_key in _yamnet_prior_cache:
                        yam_prior = _yamnet_prior_cache[cache_key]
                        if is_log_type_enabled("model"):
                            model_logger.debug("[YAMNET][CACHE] hit")
                    else:
                        segment_yam_priors = []
                        for segment_y, segment_sr, _segment_offset in audio_segments:
                            segment_prior = _yamnet_infer_prior_from_audio(
                                segment_y,
                                segment_sr,
                                sess,
                                inp_name,
                                labels_521,
                                my_labels,
                            )
                            if segment_prior is not None:
                                segment_yam_priors.append(np.asarray(segment_prior, dtype=float))
                        if segment_yam_priors:
                            yam_prior = np.mean(np.vstack(segment_yam_priors), axis=0)
                        if cache_key and yam_prior is not None:
                            _yamnet_prior_cache[cache_key] = yam_prior
                            if is_log_type_enabled("model"):
                                model_logger.debug("[YAMNET][CACHE] store")

                    if (
                        yam_prior is not None
                        and yam_prior.shape == decision_proba_vec.shape
                        and float(np.max(yam_prior)) >= 0.02
                    ):
                        yam_alpha = min(max(yam_alpha, 0.0), 0.5)
                        # YAMNet только мягко усиливает RF. Жанры без
                        # AudioSet-маппинга не обнуляются и не штрафуются.
                        support = yam_prior / max(float(np.max(yam_prior)), 1e-9)
                        fused = decision_proba_vec * (1.0 + yam_alpha * support)
                        s = fused.sum()
                        if s > 1e-9:
                            fused /= s
                        fused_proba_vec = fused
                        f_idx = int(np.argmax(fused))
                        fused_label = my_labels[f_idx] if f_idx < len(my_labels) else predicted_genre
                        fused_conf = float(fused[f_idx])

                        rf_label = predicted_genre
                        rf_conf = float(decision_proba_vec.max())

                        apply_fusion = True
                        reason = ""
                        fused_threshold = float(
                            model_meta.get("class_thresholds", {}).get(fused_label, global_threshold)
                            if params.get("auto_class_thresholds_enabled", True)
                            else global_threshold
                        )
                        if fused_conf < fused_threshold:
                            apply_fusion = False
                            reason = f"fused_conf {fused_conf:.3f} < threshold {fused_threshold:.3f}"
                        elif fused_label == rf_label and fused_conf < rf_conf:
                            apply_fusion = False
                            reason = "no improvement"
                        elif fused_label != rf_label and fused_conf < (fused_threshold + 0.05):
                            apply_fusion = False
                            reason = "label change low margin"

                        if apply_fusion:
                            predicted_genre = fused_label
                            proba = fused_conf
                            decision_proba_vec = np.asarray(fused_proba_vec, dtype=float)
                            if is_log_type_enabled("model"):
                                model_logger.debug(f"[YAMNET][FUSION][APPLIED] alpha={yam_alpha:.2f} '{rf_label}'->{fused_label} conf={fused_conf:.3f}")
                        else:
                            if is_log_type_enabled("model"):
                                model_logger.debug(f"[YAMNET][FUSION][SKIP] {reason} rf_conf={rf_conf:.3f} fused_conf={fused_conf:.3f}")
                    else:
                        if is_log_type_enabled("model"):
                            model_logger.debug("[YAMNET] prior None/shape mismatch — skip fusion")
            else:
                if is_log_type_enabled("model"):
                    model_logger.debug(f"[YAMNET] Файл не найден: {resolved_path}")
        except Exception as fe:
            if is_log_type_enabled("model"):
                model_logger.error(f"[YAMNET] Исключение fusion: {fe}")

    # --- 12. Индивидуальный порог класса и отрыв top-1/top-2 ---
    margin = 1.0
    second_genre = None
    second_proba = 0.0
    if decision_proba_vec is not None and decision_proba_vec.size > 1:
        ranking = np.argsort(decision_proba_vec)[::-1]
        top_index = int(ranking[0])
        second_index = int(ranking[1])
        labels = list(getattr(model, "classes_", []))
        predicted_genre = labels[top_index]
        proba = float(decision_proba_vec[top_index])
        second_genre = labels[second_index]
        second_proba = float(decision_proba_vec[second_index])
        margin = proba - second_proba

    class_thresholds = model_meta.get("class_thresholds", {}) or {}
    use_class_thresholds = bool(params.get("auto_class_thresholds_enabled", True))
    class_threshold = float(class_thresholds.get(str(predicted_genre), global_threshold)) \
        if use_class_thresholds else global_threshold
    # Автокалибровка не может ослабить заданный пользователем базовый порог.
    threshold = max(class_threshold, global_threshold)
    segment_penalty = min(max(float(params.get("segment_disagreement_penalty", 0.1)), 0.0), 0.4)
    if segment_disagreement:
        threshold += segment_penalty
    threshold = min(max(threshold, 0.05), 0.99)

    min_margin = min(max(float(params.get("min_genre_margin", 0.1)), 0.0), 0.95)
    rejected_reasons = []
    if proba < threshold:
        rejected_reasons.append(f"conf {proba:.3f} < threshold {threshold:.3f}")
    if margin < min_margin:
        rejected_reasons.append(f"margin {margin:.3f} < min_margin {min_margin:.3f}")
    if segment_disagreement and proba < threshold:
        rejected_reasons.append(f"segment disagreement penalty +{segment_penalty:.3f}")

    if rejected_reasons:
        prev = predicted_genre
        predicted_genre = "Unknown"
        if is_log_type_enabled("model"):
            model_logger.debug(
                f"[DECISION][REJECT] {'; '.join(rejected_reasons)}; "
                f"top1='{prev}' {proba:.3f}, top2='{second_genre}' {second_proba:.3f} → 'Unknown'"
            )
    elif is_log_type_enabled("model"):
        model_logger.debug(
            f"[DECISION][ACCEPT] genre='{predicted_genre}' conf={proba:.3f} threshold={threshold:.3f} "
            f"top2='{second_genre}' {second_proba:.3f} margin={margin:.3f}"
        )

    # House is both a useful parent category and a valid acoustic fallback.
    # If the family is clear but its very similar substyles are not, returning
    # House is more informative than either a wrong subtype or a full Unknown.
    if (
            predicted_genre == "Unknown"
            and bool(params.get("family_fallback_enabled", True))
            and isinstance(hierarchy_family_meta, dict)
            and hierarchy_family_meta.get("family") == "House"
            and float(hierarchy_family_meta.get("confidence", 0.0))
            >= float(params.get("family_fallback_threshold", 0.68))
            and float(hierarchy_family_meta.get("margin", 0.0))
            >= float(params.get("family_fallback_margin", 0.15))
    ):
        predicted_genre = "House"
        proba = float(hierarchy_family_meta["confidence"])
        family_fallback_applied = True
        if is_log_type_enabled("model"):
            model_logger.debug(
                "[HIERARCHY][FALLBACK] subtype rejected, family=House conf=%.3f margin=%.3f",
                hierarchy_family_meta["confidence"], hierarchy_family_meta["margin"],
            )

    # --- 13. (РЕФАКТОР) Fallback логика: Жанр остаётся 'Unknown'. Мы НЕ подменяем на candidate_genre.
    # Раньше тут был ранний return; теперь единый return ниже.

    taxonomy_result = None
    if taxonomy_model_active:
        manual_correction = get_manual_correction(path)
        base_genre_prediction = predicted_genre
        metadata = track_metadata if isinstance(track_metadata, dict) else {}
        raw_metadata_genre = get_track_val(metadata, "Genre") or id3_genre or ""
        metadata_taxonomy = parse_track_taxonomy(
            raw_genre=raw_metadata_genre,
            fallback_genre=None,
            title=get_track_val(metadata, "Title") or id3_title or "",
            artist=get_track_val(metadata, "Artist") or id3_artist or "",
            path=path,
        )

        # Явная ручная разметка Rekordbox/ID3 важнее акустического прогноза.
        explicit_base_genre = metadata_taxonomy.base_genre
        if raw_metadata_genre and explicit_base_genre not in {"", "Other"}:
            base_genre_prediction = explicit_base_genre

        language = metadata_taxonomy.language
        language_source = "metadata" if language != "Unknown" else "unknown"
        language_confidence = 1.0 if language != "Unknown" else 0.0
        language_probabilities = None
        vocal_language_result = None
        language_model = model_meta.get("language_model")

        # Foreign означает только широкую ручную категорию. Whisper может
        # уточнить её до English/Other/Instrumental. Явные Russian/English не
        # перепроверяем, чтобы ручная разметка оставалась приоритетной.
        if (
                bool(params.get("vocal_language_enabled", False))
                and language in {"Unknown", "Foreign"}
        ):
            vocal_language_result = detect_vocal_language(
                path,
                settings=params,
                audio_segments=audio_segments,
            )
            vocal_candidate = str(vocal_language_result.get("language", "Unknown"))
            if vocal_candidate != "Unknown":
                language = vocal_candidate
                language_confidence = float(vocal_language_result.get("confidence", 0.0))
                language_source = "vocal"

        if (
                language == "Unknown"
                and bool(params.get("vocal_language_rf_fallback", True))
                and language_model is not None
                and base_genre_prediction != "Unknown"
        ):
            try:
                segment_language_probabilities = np.asarray(
                    language_model.predict_proba(X_segments),
                    dtype=float,
                )
                language_probabilities = np.mean(segment_language_probabilities, axis=0)
                language_index = int(np.argmax(language_probabilities))
                language_candidate = str(language_model.classes_[language_index])
                language_confidence = float(language_probabilities[language_index])
                language_thresholds = model_meta.get("language_class_thresholds", {}) or {}
                language_threshold = float(language_thresholds.get(
                    language_candidate,
                    params.get("language_threshold", 0.6),
                ))
                if language_confidence >= language_threshold:
                    language = language_candidate
                    language_source = "rf"
                else:
                    language = "Unknown"
                    language_source = "rf_rejected"
            except Exception as language_error:
                if is_log_type_enabled("model"):
                    model_logger.debug(f"[TAXONOMY][LANGUAGE] Ошибка: {language_error}")

        version_type = metadata_taxonomy.version_type
        base_genre_source = "metadata" if (
            raw_metadata_genre and explicit_base_genre not in {"", "Other"}
        ) else "audio_model"
        version_type_source = "metadata" if version_type != "Unknown" else "unknown"
        if manual_correction:
            base_genre_prediction = manual_correction["corrected_base_genre"]
            base_genre_source = "manual_correction"
            corrected_language = manual_correction.get("corrected_language", "Auto")
            if corrected_language != "Auto":
                language = corrected_language
                language_confidence = 1.0
                language_source = "manual_correction"
            corrected_version = manual_correction.get("corrected_version_type", "Auto")
            if corrected_version != "Auto":
                version_type = corrected_version
                version_type_source = "manual_correction"

        # Во время основного сканирования RF-язык является только быстрым
        # предварительным кандидатом: окончательное решение примет отдельный
        # Whisper-проход. Не превращаем House в «Русские Ремиксы» до такого
        # подтверждения, но сохраняем RF-кандидата для диагностики.
        provisional_language = None
        provisional_language_confidence = None
        provisional_language_source = None
        if defer_vocal_language and language_source == "rf":
            provisional_language = language
            provisional_language_confidence = language_confidence
            provisional_language_source = language_source
            language = "Unknown"
            language_confidence = 0.0
            language_source = "pending_vocal"

        dj_category = (
            derive_dj_category(base_genre_prediction, language)
            if base_genre_prediction != "Unknown" else "Unknown"
        )
        taxonomy_result = {
            "base_genre": base_genre_prediction,
            "base_genre_source": base_genre_source,
            "genre_family": parse_track_taxonomy(
                fallback_genre=base_genre_prediction
            ).genre_family,
            "language": language,
            "language_confidence": language_confidence,
            "language_source": language_source,
            "vocal_language": vocal_language_result,
            "language_labels": list(getattr(language_model, "classes_", [])) if language_model is not None else [],
            "language_probabilities": language_probabilities.tolist() if language_probabilities is not None else None,
            "provisional_language": provisional_language,
            "provisional_language_confidence": provisional_language_confidence,
            "provisional_language_source": provisional_language_source,
            "version_type": version_type,
            "version_type_source": version_type_source,
            "mood": metadata_taxonomy.mood,
            "dj_category": dj_category,
            "base_genre_confidence": proba,
            "manual_correction": manual_correction,
        }
        predicted_genre = dj_category
        if is_log_type_enabled("model"):
            model_logger.debug(
                f"[TAXONOMY] base='{base_genre_prediction}' language='{language}' source='{language_source}' "
                f"language_conf={language_confidence:.3f} version='{version_type}' "
                f"mood='{metadata_taxonomy.mood}' dj_category='{dj_category}'"
            )

    # --- 14. Формирование meta_vectors при return_meta (YAMNet может быть выключен) ---
    if return_meta:
        import numpy as _np
        labels = list(getattr(model, "classes_", []))
        rf_list = None
        effnet_list = None
        yam_list = None
        fused_list = None
        try:
            if full_proba_vec is not None:
                v = _np.asarray(full_proba_vec, dtype=float)
                s = v.sum()
                rf_list = (v / s).tolist() if s > 1e-12 else v.tolist()
        except Exception:
            pass
        try:
            if effnet_proba_vec is not None:
                v = _np.asarray(effnet_proba_vec, dtype=float)
                s = v.sum()
                effnet_list = (v / s).tolist() if s > 1e-12 else v.tolist()
        except Exception:
            pass
        try:
            if yam_prior is not None:
                v = _np.asarray(yam_prior, dtype=float)
                s = v.sum()
                yam_list = (v / s).tolist() if s > 1e-12 else v.tolist()
        except Exception:
            pass
        try:
            if fused_proba_vec is not None:
                v = _np.asarray(fused_proba_vec, dtype=float)
                s = v.sum()
                fused_list = (v / s).tolist() if s > 1e-12 else v.tolist()
        except Exception:
            pass

        meta_vectors = {
            "labels": labels,
            "rf_proba": rf_list,
            "effnet_proba": effnet_list,
            "effnet_embedding_source": effnet_embedding_source,
            "yamnet_prior": yam_list,
            "fused_proba": fused_list,
            "taxonomy": taxonomy_result,
            "decision_threshold": threshold,
            "segment_disagreement": segment_disagreement,
            "segment_disagreement_penalty": segment_penalty if segment_disagreement else 0.0,
            "rejected_reasons": rejected_reasons,
            "decision_margin": margin,
            "acoustic_prediction": None,
            "top_candidates": [],
            "genre_family_prediction": hierarchy_family_meta,
            "family_fallback_applied": family_fallback_applied,
        }
        try:
            decision_values = _np.asarray(decision_proba_vec, dtype=float)
            if decision_values.size == len(labels):
                decision_sum = float(decision_values.sum())
                if decision_sum > 1e-12:
                    decision_values = decision_values / decision_sum
                ranking = _np.argsort(decision_values)[::-1]
                meta_vectors["top_candidates"] = [
                    {
                        "genre": str(labels[int(index)]),
                        "confidence": float(decision_values[int(index)]),
                    }
                    for index in ranking[:3]
                ]
                if meta_vectors["top_candidates"]:
                    meta_vectors["acoustic_prediction"] = meta_vectors["top_candidates"][0]["genre"]
                meta_vectors["decision_proba"] = decision_values.tolist()
        except Exception:
            meta_vectors["decision_proba"] = None
        if is_log_type_enabled("model"):
            model_logger.debug(
                f"[GENRE][META] saved rf={bool(rf_list)} yamnet_prior={bool(yam_list)} fused={bool(fused_list)} genre={predicted_genre} conf={proba:.3f}"
            )
        return predicted_genre, proba, X, meta_vectors

    # --- 15. Возврат без meta (YAMNet выключен или не запросили meta) ---
    if is_log_type_enabled("model"):
        model_logger.debug(f"[GENRE][RESULT] genre={predicted_genre} conf={proba:.3f} yamnet_active={yamnet_active}")
    return predicted_genre, proba, X

def scan_library_async(MUSIC_DIR, scan_mode, scan_stop_event, scan_progress, settings, rekordbox_data):
    import multiprocessing
    import json
    import os

    use_rekordbox = settings.get("use_rekordbox", False)
    if not os.path.exists(MODEL_PATH):
        if is_log_type_enabled("model"):
            model_logger.error("genre_model.pkl отсутствует! Необходимо обучить модель.")
        if is_log_type_enabled("status"):
            status_logger.error("Сканирование прервано: отсутствует модель жанров (genre_model.pkl).")
        scan_progress.clear()  # сбросить прогресс, если вдруг был
        scan_progress["status"] = "error"
        scan_progress["error_message"] = "Файл модели жанров не найден. Сначала обучите модель!"
        return  # <-- ВАЖНО! Прерываем функцию до инициализации и запуска сканирования!


    with open(MODEL_PATH, "rb") as f:
        model_meta = pickle.load(f)
    librosa_params = dict(model_meta["librosa_params"])
    model_labels = list(model_meta.get("labels", []))
    yam_path_model = (model_meta.get("librosa_params") or {}).get("yamnet_model_path")
    # Сам RandomForest здесь не используется: предсказания выполняют дочерние
    # воркеры. Не держим лишнюю копию ~214 МБ в управляющем процессе.
    del model_meta
    gc.collect()
    logger.info("=== СТАРТ scan_library_async === MUSIC_DIR=%r scan_mode=%r", MUSIC_DIR, scan_mode)
    # --- YAMNet: предчек для UI-ошибки при отсутствии файла ---
    try:
        yam_enabled = bool(settings.get("yamnet_enabled", False))
        # Путь берём из настроек, если нет — из модели (на всякий случай), далее дефолт
        yam_path_cfg = settings.get("yamnet_model_path")
        yam_path = yam_path_cfg or yam_path_model or str(YAMNET_MODEL_FILE)

        if yam_enabled:
            resolved_path = str(resolve_project_path(yam_path, YAMNET_MODEL_FILE))
            if not os.path.isfile(resolved_path):
                if is_log_type_enabled("model"):
                    model_logger.error(f"[YAMNET] Включён, но файл не найден: {resolved_path}")
                if is_log_type_enabled("status"):
                    status_logger.error(f"Сканирование прервано: включён YAMNet, но файл не найден: {resolved_path}.")
                # Сообщаем в UI — как для отсутствия основной жанровой модели
                scan_progress.clear()
                scan_progress["status"] = "error"
                scan_progress["error_message"] = (
                    f"Файл YAMNet не найден: {resolved_path}. "
                    f"Выключите YAMNet в настройках или укажите корректный путь."
                )
                return
    except Exception as e:
        # Не роняем сканирование, просто логируем проблему предчека
        if is_log_type_enabled("model"):
            model_logger.error(f"[YAMNET] Ошибка предчека наличия файла: {e}")

    # --- ДОБАВЛЕНО ---
    skipped_tracks = []  # [(rel_path, причина)]
    error_tracks = []    # [(rel_path, str(exception))]
    # -----------------

    try:
        if scan_mode == "new":
            init_scan_db()

        file_iter = iter_mp3_files(MUSIC_DIR, librosa_params, settings, rekordbox_data, scan_stop_event, scan_mode)

        # Подсчет общего числа mp3-файлов (без формирования all_tasks)
        total = 0
        for root, dirs, files in os.walk(MUSIC_DIR):
            if scan_stop_event is not None and scan_stop_event.is_set():
                break
            for file in files:
                if scan_stop_event is not None and scan_stop_event.is_set():
                    break
                if file.lower().endswith(".mp3"):
                    total += 1

        logger.info("Total mp3 files: %d", total)
        scan_progress["total"] = total
        from .db import get_unique_scan_count

        # --- ИНИЦИАЛИЗАЦИЯ СЧЁТЧИКА ПРОГРЕССА ---
        # Правила:
        #   - new: всегда начинаем с 0
        #   - continue: всегда пересчитываем фактическое число записей в БД (DISTINCT rel_path)
        try:
            if scan_mode == "new":
                prev_val = scan_progress.get("scanned", 0)
                if prev_val != 0 and is_log_type_enabled("status"):
                    status_logger.info(f"[SCAN][INIT] Перезапись scanned {prev_val} -> 0 (режим new)")
                scan_progress["scanned"] = 0
            else:
                try:
                    scanned_db = get_unique_scan_count()
                except Exception as e_gidc:
                    scanned_db = 0
                    if is_log_type_enabled("status"):
                        status_logger.warning(f"[SCAN][INIT] get_unique_scan_count() ошибка: {e_gidc}")
                scan_progress["scanned"] = scanned_db
                if is_log_type_enabled("status"):
                    status_logger.info(f"[SCAN][INIT] CONTINUE: scanned из БД = {scanned_db} / total={total}")
            if is_log_type_enabled("status"):
                status_logger.info(
                    f"[SCAN][INIT SUMMARY] mode={scan_mode} scanned_start={scan_progress['scanned']} total={scan_progress['total']}"
                )
        except Exception as init_ex:
            if is_log_type_enabled("status"):
                status_logger.error(f"[SCAN][INIT ERROR] Ошибка инициализации прогресса: {init_ex}")
        results = {}

        if scan_stop_event is not None and scan_stop_event.is_set():
            if is_log_type_enabled("status"):
                status_logger.info(
                    "[SCAN] Остановка сканирования: stop_event выставлен до подачи задач в executor, выход из scan_library_async.")
            scan_progress["status"] = "stopped"
            return

        # Определяем приоритет (можно вынести в конфиг или settings)
        priority = settings.get("scan_priority", "medium") if "settings" in locals() and hasattr(settings,"get") else "medium"
        max_workers, resource_warning, resource_critical = get_dynamic_max_workers_by_settings(librosa_params,priority)
        requested_workers = max_workers
        commit_headroom = _windows_commit_headroom_bytes()
        max_workers, worker_limit_reason = _safe_scan_worker_count(
            max_workers,
            settings,
            commit_headroom_bytes=commit_headroom,
        )
        if commit_headroom is not None:
            scan_progress["commit_headroom_gb"] = round(commit_headroom / (1024 ** 3), 2)
            resource_logger.info(
                "[SCAN][MEMORY] Свободный Windows commit перед запуском: %.2f ГБ.",
                commit_headroom / (1024 ** 3),
            )
        if max_workers != requested_workers:
            resource_logger.warning(
                "[SCAN][WORKERS] Безопасный предел: %s -> %s воркеров (%s).",
                requested_workers,
                max_workers,
                worker_limit_reason,
            )
            scan_progress["worker_limit_reason"] = worker_limit_reason
        scan_progress["max_workers"] = max_workers
        logger.info(f"Multiprocessing scan: CPU total={multiprocessing.cpu_count()}, max_workers={max_workers}")
        if resource_critical:
            scan_progress["status"] = "error"
            scan_progress["error_message"] = resource_warning
            return

        # Основной блок multiprocessing — динамическая подача задач по мере освобождения воркеров
        import psutil
        available_mem = psutil.virtual_memory().available  # bytes
        # Практический пик включает Python/librosa/ONNX и распакованную RF-модель.
        estimated_worker_mb = max(700, int(os.path.getsize(MODEL_PATH) / (1024 * 1024) * 3.2))
        needed_mem = max_workers * estimated_worker_mb * 1024 * 1024
        if available_mem < needed_mem:
            import traceback
            resource_logger.warning(
                f"[RESOURCE][MEMORY] Недостаточно памяти для запуска {max_workers} воркеров. Доступно: {available_mem // (1024 * 1024)} МБ, требуется минимум: {needed_mem // (1024 * 1024)} МБ"
            )
            if 'global_state' in locals() and global_state is not None:
                global_state["training_error"] = (
                    f"Недостаточно памяти для запуска обучения ({available_mem // (1024 * 1024)} МБ). "
                    f"Уменьшите количество процессов или закройте лишние программы."
                )
        worker_context = multiprocessing.get_context("spawn")
        model_load_lock = worker_context.Lock()
        with ScanResultWriter() as scan_writer, concurrent.futures.ProcessPoolExecutor(
            max_workers=max_workers,
            mp_context=worker_context,
            initializer=_init_scan_worker,
            initargs=(model_load_lock, scan_stop_event),
        ) as executor:
            future_to_task = {}

            # Стартуем max_workers задач (или меньше, если файлов мало)
            for _ in range(max_workers):
                try:
                    args = next(file_iter)
                    full_path = args[0] if isinstance(args, tuple) and len(args) > 0 else None
                    if full_path and is_log_type_enabled("model"):
                        model_logger.debug(
                            f"[PATH][SCAN] scan_library_async: full_path={full_path}, os.path.exists={os.path.exists(full_path)}, os.path.isfile={os.path.isfile(full_path)}")
                except StopIteration:
                    break
                try:
                    future = executor.submit(process_one_scan_file, args)
                    future_to_task[future] = args
                except MemoryError as e:
                    import traceback
                    if is_log_type_enabled("resource"):
                        resource_logger.error(f"[SCAN][MEMORY] Ошибка памяти при запуске воркера: {e}")
                    scan_progress["error_message"] = f"Ошибка памяти при запуске воркера: {e}"
                    break
                except Exception as e:
                    logger.error(f"Ошибка при запуске воркера: {e}")
                    scan_progress["error_message"] = f"Ошибка при запуске воркера: {e}"
                    break


            trainable_genres = set(model_labels)
            results = {}

            while future_to_task:
                # Дожидаемся хотя бы одного завершения
                done, _ = concurrent.futures.wait(
                    future_to_task.keys(),
                    return_when=concurrent.futures.FIRST_COMPLETED
                )
                for future in done:
                    args = future_to_task.pop(future)
                    if scan_stop_event.is_set():
                        scan_progress["status"] = "stopped"
                        if is_log_type_enabled("status"):
                            status_logger.debug("[SCAN] Scanning stopped by user (dynamic submit)")
                        logger.info("Scanning stopped by user (dynamic submit)")
                        continue  # Не подаем новые задачи, просто дорабатываем уже запущенные

                    try:
                        fut_res = future.result()
                        proba_meta = None
                        if isinstance(fut_res, tuple):
                            if len(fut_res) == 7:
                                rel_path, genre, current_mtime, conf, features_to_save, err, proba_meta = fut_res
                            else:
                                rel_path, genre, current_mtime, conf, features_to_save, err = fut_res
                        else:
                            # Непредвиденный формат
                            rel_path = None
                            genre = None
                            current_mtime = None
                            conf = None
                            features_to_save = None
                            err = f"unexpected_result_type={type(fut_res)}"
                    except Exception as e_fut:
                        # Воркер упал (MemoryError / OOM / crash) — фиксируем и подставляем новую задачу,
                        # чтобы пул не сокращался
                        rel_path = args[1] if isinstance(args, tuple) and len(args) > 1 else "<unknown>"
                        err_msg = f"worker_crash: {type(e_fut).__name__}: {e_fut}"
                        error_tracks.append((rel_path, err_msg))
                        logger.error(f"[SCAN][WORKER CRASH] {rel_path}: {err_msg}")
                        if is_log_type_enabled("model"):
                            model_logger.error(f"[SCAN][WORKER CRASH] {rel_path}: {err_msg}")
                        fatal_worker_error = _is_fatal_scan_worker_error(e_fut)
                        if fatal_worker_error:
                            # Сломанный пул нельзя пополнять: иначе оставшаяся библиотека
                            # мгновенно превратится в список ошибок. Обёртка маршрута
                            # перезапустит режим continue с двумя воркерами.
                            scan_progress["error_message"] = err_msg
                            for pending_future in future_to_task:
                                pending_future.cancel()
                            future_to_task.clear()
                            break
                        # Подать новую задачу вместо упавшей (если не нажали стоп)
                        if not scan_stop_event.is_set():
                            try:
                                new_args = next(file_iter)
                                new_future = executor.submit(process_one_scan_file, new_args)
                                future_to_task[new_future] = new_args
                            except StopIteration:
                                pass
                            except Exception as submit_e:
                                logger.error(f"[SCAN] Ошибка при подаче новой задачи после крэша воркера: {submit_e}")
                        continue
                    scan_progress["scanned"] += 1
                    save_interval = 100  # Можно вынести в начало функции, если хочешь менять в одном месте

                    if scan_progress["scanned"] % save_interval == 0 or scan_progress["scanned"] == scan_progress["total"]:
                        if is_log_type_enabled("status"):
                            status_logger.info(
                                f"[SCAN] Промежуточное сохранение отчета: обработано {scan_progress['scanned']} файлов из {scan_progress['total']}")
                        with open(SCAN_REPORT_FILE, "w", encoding="utf-8") as f:
                            json.dump({
                                "skipped_tracks": skipped_tracks,
                                "error_tracks": error_tracks
                            }, f, ensure_ascii=False, indent=2)
                    if scan_stop_event.is_set():
                        if is_log_type_enabled("status"):
                            status_logger.info(f"[SCAN] Результат для {rel_path} после остановки — не сохраняется в базу.")
                        continue  # Пропустить сохранение результата в базу после остановки
                    if err:
                        # Плохой трек — добавляем и продолжаем, НО пул не сокращаем (не делаем continue)
                        error_tracks.append((rel_path, err))
                        logger.error(f"[BAD FILE] Ошибка обработки {rel_path}: {err}")
                        if is_log_type_enabled("model"):
                            model_logger.error(f"[TRACK REJECT][SCAN] {rel_path} | Причина: {err}")
                        # Не делаем continue — ниже подставится новая задача
                    if features_to_save is not None:
                        try:
                            if proba_meta:
                                scan_writer.save(
                                    rel_path,
                                    genre,
                                    current_mtime,
                                    conf,
                                    features_to_save,
                                    rf_proba=proba_meta.get("rf_proba"),
                                    yamnet_prior=proba_meta.get("yamnet_prior"),
                                    fused_proba=proba_meta.get("fused_proba"),
                                    taxonomy=proba_meta.get("taxonomy"),
                                    defer_vocal_language=bool(settings.get("defer_vocal_language", False)),
                                )
                            else:
                                scan_writer.save(rel_path, genre, current_mtime, conf, features_to_save)
                            if is_log_type_enabled("model"):
                                model_logger.info(
                                    f"[DB] Сохранён трек: {rel_path} (жанр: {genre}) — признаки shape: {len(features_to_save) if features_to_save else 'None'}")
                                if genre not in trainable_genres:
                                    model_logger.info(f"[DB] Сохранён трек с НЕобучаемым жанром: {rel_path} (жанр: {genre})")
                            if is_log_type_enabled("model"):
                                model_logger.debug(f"Saved features for {rel_path} (genre={genre})")
                            results.setdefault(genre, []).append(rel_path)
                        except Exception as db_e:
                            error_tracks.append((rel_path, f"DB save error: {db_e}"))
                            if is_log_type_enabled("model"):
                                model_logger.error(f"DB error for {rel_path}: {db_e}")
                    # Можно добавить промежуточный лог по прогрессу
                    if scan_progress["scanned"] % 100 == 0 or scan_progress["scanned"] == total:
                        logger.info(f"Progress: {scan_progress['scanned']} / {total}")
                    if is_log_type_enabled("status"):
                        status_logger.info("[SCAN] Цикл сканирования завершён с флагом stop_event: %r", scan_stop_event.is_set())
                        status_logger.info("[SCAN] Итоговый статус scan_progress: %r", scan_progress)
                    # Подаём новую задачу только если не нажали стоп
                    if not scan_stop_event.is_set():
                        try:
                            new_args = next(file_iter)
                            new_future = executor.submit(process_one_scan_file, new_args)
                            future_to_task[new_future] = new_args
                        except StopIteration:
                            pass
                        except Exception as submit_error:
                            submit_error_text = (
                                f"worker_submit_crash: {type(submit_error).__name__}: "
                                f"{submit_error}"
                            )
                            logger.error("[SCAN][WORKER POOL] %s", submit_error_text)
                            scan_progress["error_message"] = submit_error_text
                            for pending_future in future_to_task:
                                pending_future.cancel()
                            future_to_task.clear()
                            break
            # Если сканирование не было остановлено
            # Обработка ошибок в error_tracks
            memory_errors = [
                err for _, err in error_tracks
                if _is_memory_related_scan_error(RuntimeError(err))
            ]
            if memory_errors:
                scan_progress["error_message"] = memory_errors[0]
                import traceback
                resource_logger.error(f"[SCAN][MEMORY] scan_progress.error_message: {scan_progress['error_message']}")
                resource_logger.error(f"[SCAN][MEMORY] Stacktrace:\n{traceback.format_exc()}")
                scan_progress["status"] = "error"
            elif scan_progress.get("error_message"):
                import traceback
                resource_logger.error(f"[SCAN][MEMORY] scan_progress.error_message: {scan_progress['error_message']}")
                resource_logger.error(f"[SCAN][MEMORY] Stacktrace:\n{traceback.format_exc()}")
                scan_progress["status"] = "error"
            elif not scan_stop_event.is_set():
                scan_progress["status"] = "completed"
            scan_progress["results"] = results
            scan_progress["error_tracks"] = [rel_path for rel_path, err in error_tracks]  # только имена файлов
            scan_progress["error_count"] = len(error_tracks)
            # Контекст ProcessPoolExecutor сам завершает только принадлежащие ему
            # процессы. Нельзя убивать все дочерние Python-процессы: среди них
            # находится Manager, чей Event нужен для безопасного автоповтора.

        # Новые/изменённые строки синхронизируются с интеллектуальным каталогом
        # из уже сохранённых признаков. Аудиофайлы повторно не открываются.
        if scan_progress.get("status") == "completed":
            try:
                from .catalog_intelligence import (
                    refresh_catalog_model_labels,
                    sync_catalog_index,
                )
                sync_progress = {}
                scan_progress["intelligence_sync"] = sync_progress
                sync_result = sync_catalog_index(progress=sync_progress)
                if sync_result.get("status") == "completed":
                    label_progress = {}
                    scan_progress["model_label_refresh"] = label_progress
                    refresh_catalog_model_labels(progress=label_progress)
                    from .personalization import apply_personal_rating_model
                    personal_progress = {}
                    scan_progress["personalization_refresh"] = personal_progress
                    apply_personal_rating_model(progress=personal_progress)
                if is_log_type_enabled("status"):
                    status_logger.info(
                        "[SCAN][CATALOG SYNC] %s",
                        scan_progress.get("intelligence_sync"),
                    )
            except Exception as intelligence_error:
                scan_progress["intelligence_sync"] = {
                    "status": "error",
                    "error": str(intelligence_error),
                }
                logger.exception(
                    "Сканирование завершено, но синхронизация интеллектуального каталога не удалась: %s",
                    intelligence_error,
                )

        # Итоговый отчёт
        logger.info("Scanning finished. Results: %s", results)
        logger.info("=== Scan Report ===")
        logger.info("Total scanned: %d", scan_progress['scanned'])
        logger.info("Skipped tracks: %d", len(skipped_tracks))
        logger.info("Tracks with errors: %d", len(error_tracks))
        if skipped_tracks:
            logger.info("Skipped tracks list (first 10): %s", skipped_tracks[:10])
        if error_tracks:
            logger.info("Error tracks list (first 10): %s", error_tracks[:10])
        if is_log_type_enabled("status"):
            # Диагностика: дошли ли до total
            status_logger.info(f"[SCAN][FINAL] scanned={scan_progress.get('scanned')} total={scan_progress.get('total')} mode={scan_mode}")
            remaining = scan_progress.get('total', 0) - scan_progress.get('scanned', 0)
            if remaining == 0:
                status_logger.info("[SCAN][FINAL] Прогресс достиг total (100%).")
            elif remaining > 0:
                status_logger.warning(f"[SCAN][FINAL] Осталось неохваченных файлов (по счётчику): {remaining}")
            else:
                status_logger.warning(f"[SCAN][FINAL] scanned > total (аномалия): {remaining}")
        # Сохраняем в файл по желанию:
        with open(SCAN_REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "skipped_tracks": skipped_tracks,
                "error_tracks": error_tracks
            }, f, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.exception("Ошибка в scan_library_async: %s", e)
        scan_progress["status"] = "error"
        if not scan_progress.get("error_message"):
            scan_progress["error_message"] = f"{type(e).__name__}: {e}"

def balance_rekordbox_tracks(tracks, genres, max_per_genre, logger=None):
    """
    Балансировка списка треков по жанрам.
    tracks: список dict, каждый с полями 'genre', 'path' и др.
    genres: список жанров, которые нужно оставить.
    max_per_genre: максимальное количество треков на жанр.
    """
    df = pd.DataFrame(tracks)
    balanced = pd.DataFrame()
    for genre in genres:
        genre_df = df[df['genre'] == genre]
        n = min(len(genre_df), max_per_genre)
        if n == 0:
            if logger:
                logger.info(f"[BALANCE] Пропускаем жанр '{genre}', треков нет.")
            continue
        balanced = pd.concat([
            balanced,
            resample(genre_df, replace=False, n_samples=n, random_state=42)
        ])
        if logger:
            logger.info(f"[BALANCE] Жанр '{genre}': взято {n} треков из {len(genre_df)}")
    return balanced.to_dict(orient='records')


def _build_probability_model(rf_params, settings, y):
    """Создаёт RF либо RF с калибровкой вероятностей."""
    if not bool(settings.get("calibrate_probabilities", True)):
        return RandomForestClassifier(**rf_params), {
            "enabled": False,
            "method": None,
            "cv": None,
        }

    method = str(settings.get("calibration_method", "sigmoid") or "sigmoid").lower()
    if method not in {"sigmoid", "isotonic"}:
        method = "sigmoid"
    requested_cv = max(2, int(settings.get("calibration_cv", 3) or 3))
    min_class_count = min(Counter(y).values()) if len(y) else 0
    calibration_cv = min(requested_cv, int(min_class_count))
    if calibration_cv < 2:
        logger.warning("Калибровка отключена: недостаточно примеров в одном из классов.")
        return RandomForestClassifier(**rf_params), {
            "enabled": False,
            "method": None,
            "cv": None,
            "reason": "not_enough_samples",
        }

    # CalibratedClassifierCV сам распараллеливает folds. Вложенный n_jobs=-1
    # внутри каждого RF резко увеличивал расход RAM при сегментном обучении.
    base_params = dict(rf_params)
    base_params["n_jobs"] = 1
    base_model = RandomForestClassifier(**base_params)
    calibrated_model = CalibratedClassifierCV(
        estimator=base_model,
        method=method,
        cv=calibration_cv,
        n_jobs=-1,
    )
    return calibrated_model, {
        "enabled": True,
        "method": method,
        "cv": calibration_cv,
    }


def _tune_rf_params(X, y, groups, rf_params, settings):
    """Подбирает RF по macro F1 только на train-части."""
    if not bool(settings.get("auto_tune_model", True)):
        return dict(rf_params), {"enabled": False}
    cv_splits = max(2, min(5, int(settings.get("auto_tune_cv", 3) or 3)))
    classes = np.unique(y)
    min_class_group_count = min(
        len(set(np.asarray(groups)[np.asarray(y) == class_name].tolist()))
        for class_name in classes
    )
    cv_splits = min(cv_splits, int(min_class_group_count), len(set(groups.tolist())))
    if cv_splits < 2:
        return dict(rf_params), {"enabled": False, "reason": "not_enough_groups"}

    search_space = {
        "n_estimators": [200, 300, 500, 700],
        "max_depth": [12, 18, 24, 32, None],
        "min_samples_leaf": [1, 2, 3, 5],
        "max_features": ["sqrt", "log2", 0.5],
        "class_weight": ["balanced", "balanced_subsample"],
    }
    base_params = dict(rf_params)
    base_params["n_jobs"] = 1
    estimator = RandomForestClassifier(**base_params)
    cv = StratifiedGroupKFold(n_splits=cv_splits, shuffle=True, random_state=rf_params["random_state"])
    search = RandomizedSearchCV(
        estimator,
        param_distributions=search_space,
        n_iter=max(4, min(30, int(settings.get("auto_tune_iterations", 12) or 12))),
        scoring="f1_macro",
        cv=cv,
        random_state=rf_params["random_state"],
        n_jobs=-1,
        refit=False,
        verbose=0,
    )
    try:
        search.fit(X, y, groups=groups)
    except Exception as tuning_error:
        logger.warning("[AUTO TUNE] Подбор пропущен после ошибки: %s", tuning_error)
        return dict(rf_params), {
            "enabled": False,
            "reason": "search_failed",
            "error": str(tuning_error),
        }
    tuned = dict(rf_params)
    tuned.update(search.best_params_)
    tuned["n_jobs"] = -1
    return tuned, {
        "enabled": True,
        "scoring": "f1_macro",
        "cv": cv_splits,
        "iterations": search.n_iter,
        "best_score": float(search.best_score_),
        "best_params": search.best_params_,
    }


def _select_progressive_training_styles(
        threshold_labels,
        predicted_labels,
        candidate_styles,
        active_styles,
        settings,
):
    """Evaluate new styles without removing requested targets from the candidate.

    The precheck is diagnostic only.  The final quality gate is the authority
    that accepts or rejects a complete candidate.  Dropping weak new styles at
    this point silently turns an extended run back into the active model's old
    class set and makes ``candidate_classes`` describe a different experiment
    from the one requested by the user.
    """
    candidate_styles = sorted(set(str(value) for value in candidate_styles if value))
    active_styles = set(str(value) for value in (active_styles or []) if value)
    if not bool(settings.get("progressive_style_admission_enabled", True)):
        return set(candidate_styles), {
            "enabled": False,
            "reason": "disabled",
            "admitted_styles": candidate_styles,
            "deferred_styles": [],
        }
    minimum_f1 = float(settings.get("training_new_style_min_f1", 0.60) or 0.60)
    minimum_recall = float(settings.get("training_new_style_min_recall", 0.50) or 0.50)
    minimum_support = max(
        2, int(settings.get("training_new_style_min_support", 15) or 15)
    )
    metrics = classification_report(
        np.asarray(threshold_labels, dtype=object),
        np.asarray(predicted_labels, dtype=object),
        labels=np.asarray(candidate_styles, dtype=object),
        output_dict=True,
        zero_division=0,
    )
    admitted = set(candidate_styles)
    flagged = set()
    rows = {}
    for style in candidate_styles:
        style_metrics = metrics.get(style) or {}
        support = int(style_metrics.get("support", 0) or 0)
        f1_value = float(style_metrics.get("f1-score", 0.0) or 0.0)
        recall = float(style_metrics.get("recall", 0.0) or 0.0)
        retained = style in active_styles
        passed = retained or (
            support >= minimum_support
            and f1_value >= minimum_f1
            and recall >= minimum_recall
        )
        if not passed:
            flagged.add(style)
        reasons = []
        if not retained and support < minimum_support:
            reasons.append(f"support {support} < {minimum_support}")
        if not retained and f1_value < minimum_f1:
            reasons.append(f"F1 {f1_value:.3f} < {minimum_f1:.3f}")
        if not retained and recall < minimum_recall:
            reasons.append(f"recall {recall:.3f} < {minimum_recall:.3f}")
        rows[style] = {
            "status": (
                "retained" if retained
                else ("precheck_passed" if passed else "quality_warning")
            ),
            "support": support,
            "precision": float(style_metrics.get("precision", 0.0) or 0.0),
            "recall": recall,
            "f1": f1_value,
            "reasons": reasons,
        }

    return admitted, {
        "enabled": True,
        "mode": "diagnostic_only",
        "minimum_f1": minimum_f1,
        "minimum_recall": minimum_recall,
        "minimum_support": minimum_support,
        "fallback_used": False,
        "admitted_styles": sorted(admitted),
        "deferred_styles": [],
        "quality_warning_styles": sorted(flagged),
        "rows": rows,
    }


def _calculate_class_thresholds(
        y_true,
        probabilities,
        classes,
        target_precision=0.9,
        fallback_threshold=0.5,
        min_margin=0.1,
        min_predictions=5,
        segment_disagreement=None,
        segment_disagreement_penalty=0.0,
):
    """Подбирает самый мягкий порог класса, сохраняющий заданную precision."""
    y_true = np.asarray(y_true)
    probabilities = np.asarray(probabilities, dtype=float)
    classes = np.asarray(classes)
    if probabilities.ndim != 2 or probabilities.shape[1] != len(classes):
        raise ValueError("Некорректная матрица вероятностей для подбора порогов")

    target_precision = min(max(float(target_precision), 0.5), 0.99)
    fallback_threshold = min(max(float(fallback_threshold), 0.05), 0.99)
    min_margin = min(max(float(min_margin), 0.0), 0.95)
    segment_disagreement_penalty = min(max(float(segment_disagreement_penalty), 0.0), 0.4)
    top_indices = np.argmax(probabilities, axis=1)
    top_scores = probabilities[np.arange(len(probabilities)), top_indices]
    if probabilities.shape[1] > 1:
        second_scores = np.partition(probabilities, -2, axis=1)[:, -2]
    else:
        second_scores = np.zeros(len(probabilities), dtype=float)
    margins = top_scores - second_scores
    if segment_disagreement is None:
        disagreement = np.zeros(len(probabilities), dtype=bool)
    else:
        disagreement = np.asarray(segment_disagreement, dtype=bool)
        if len(disagreement) != len(probabilities):
            raise ValueError("Размер segment_disagreement не совпадает с вероятностями")
    effective_scores = top_scores - disagreement.astype(float) * segment_disagreement_penalty

    thresholds = {}
    diagnostics = {}
    for class_index, class_name in enumerate(classes):
        predicted_as_class = (top_indices == class_index) & (margins >= min_margin)
        class_scores = effective_scores[predicted_as_class]
        class_correct = y_true[predicted_as_class] == class_name
        chosen = None
        chosen_precision = 0.0
        chosen_count = 0
        raw_candidates = np.unique(class_scores)
        # Search the thresholds that will actually be used at runtime.  Merely
        # clamping a lower winning threshold afterwards can invalidate its
        # measured precision because a different subset is then accepted.
        candidates = sorted(np.unique(np.maximum(raw_candidates, fallback_threshold)))

        for candidate in candidates:
            accepted = class_scores >= candidate
            accepted_count = int(np.sum(accepted))
            if accepted_count < min_predictions:
                continue
            precision = float(np.mean(class_correct[accepted]))
            if precision >= target_precision:
                chosen = float(candidate)
                chosen_precision = precision
                chosen_count = accepted_count
                break

        status = "target_reached"
        if chosen is None:
            best_available = None
            for candidate in candidates:
                accepted = class_scores >= candidate
                accepted_count = int(np.sum(accepted))
                if accepted_count < min_predictions:
                    continue
                precision = float(np.mean(class_correct[accepted]))
                row = (precision, accepted_count, float(candidate))
                if best_available is None or row[:2] > best_available[:2]:
                    best_available = row
            # Do not silently pretend that an arbitrary 0.75 is calibrated.
            # A candidate with this status will fail per-class quality checks;
            # 0.99 keeps accidental runtime acceptance conservative.
            chosen = 0.99
            accepted = class_scores >= chosen
            chosen_count = int(np.sum(accepted))
            chosen_precision = float(np.mean(class_correct[accepted])) if chosen_count else 0.0
            status = "precision_unreachable"
        else:
            best_available = None

        floor_applied = bool(
            status == "target_reached"
            and chosen == fallback_threshold
            and np.any(raw_candidates < fallback_threshold)
        )
        chosen = min(chosen, 0.99)
        accepted = class_scores >= chosen
        chosen_count = int(np.sum(accepted))
        chosen_precision = float(np.mean(class_correct[accepted])) if chosen_count else 0.0
        if floor_applied and status == "target_reached":
            status = "target_reached_with_floor"
        key = str(class_name)
        thresholds[key] = round(chosen, 6)
        diagnostics[key] = {
            "threshold": round(chosen, 6),
            "validation_precision": round(chosen_precision, 6),
            "accepted_validation_tracks": chosen_count,
            "predicted_validation_tracks": int(np.sum(predicted_as_class)),
            "target_precision": target_precision,
            "min_margin": min_margin,
            "minimum_threshold": fallback_threshold,
            "segment_disagreement_penalty": segment_disagreement_penalty,
            "status": status,
        }
        if best_available is not None:
            diagnostics[key].update({
                "best_available_precision": round(best_available[0], 6),
                "best_available_tracks": int(best_available[1]),
                "best_available_threshold": round(best_available[2], 6),
            })
    return thresholds, diagnostics


def _evaluate_rejection_policy(
        y_true,
        probabilities,
        classes,
        class_thresholds,
        fallback_threshold=0.5,
        min_margin=0.1,
        segment_disagreement=None,
        segment_disagreement_penalty=0.0,
):
    """Оценивает точность только тех результатов, которые не стали Unknown."""
    y_true = np.asarray(y_true)
    probabilities = np.asarray(probabilities, dtype=float)
    classes = np.asarray(classes)
    top_indices = np.argmax(probabilities, axis=1)
    top_scores = probabilities[np.arange(len(probabilities)), top_indices]
    second_scores = np.partition(probabilities, -2, axis=1)[:, -2] \
        if probabilities.shape[1] > 1 else np.zeros(len(probabilities))
    margins = top_scores - second_scores
    predicted = classes[top_indices]
    thresholds = np.asarray([
        max(float(class_thresholds.get(str(label), fallback_threshold)), float(fallback_threshold))
        for label in predicted
    ])
    if segment_disagreement is None:
        disagreement = np.zeros(len(probabilities), dtype=bool)
    else:
        disagreement = np.asarray(segment_disagreement, dtype=bool)
        if len(disagreement) != len(probabilities):
            raise ValueError("Размер segment_disagreement не совпадает с вероятностями")
    segment_disagreement_penalty = min(max(float(segment_disagreement_penalty), 0.0), 0.4)
    thresholds = thresholds + disagreement.astype(float) * segment_disagreement_penalty
    thresholds = np.clip(thresholds, 0.05, 0.99)
    accepted = (top_scores >= thresholds) & (margins >= float(min_margin))
    accepted_count = int(np.sum(accepted))
    correct_count = int(np.sum(predicted[accepted] == y_true[accepted])) if accepted_count else 0
    result = {
        "total_tracks": int(len(y_true)),
        "accepted_tracks": accepted_count,
        "unknown_tracks": int(len(y_true) - accepted_count),
        "coverage": float(accepted_count / len(y_true)) if len(y_true) else 0.0,
        "accepted_precision": float(correct_count / accepted_count) if accepted_count else 0.0,
        "segment_disagreement_penalty": segment_disagreement_penalty,
        "disagreement_tracks": int(np.sum(disagreement)),
        "per_class": {},
    }
    for class_name in classes:
        class_mask = accepted & (predicted == class_name)
        count = int(np.sum(class_mask))
        correct = int(np.sum(y_true[class_mask] == class_name)) if count else 0
        result["per_class"][str(class_name)] = {
            "accepted_tracks": count,
            "accepted_precision": float(correct / count) if count else 0.0,
        }
    return result


def _evaluate_training_quality_gate(
        validation_report,
        rejection_policy_report,
    settings,
    protected_styles=None,
    active_style_metrics=None,
):
    """Decide whether a candidate is safe to replace the active genre model."""
    enabled = bool(settings.get("training_quality_gate_enabled", True))
    macro_f1 = float(validation_report.get("macro avg", {}).get("f1-score", 0.0))
    accepted_precision = float(rejection_policy_report.get("accepted_precision", 0.0))
    coverage = float(rejection_policy_report.get("coverage", 0.0))
    minimum_macro_f1 = float(settings.get("training_min_macro_f1", 0.65))
    minimum_accepted_precision = float(
        settings.get(
            "training_min_accepted_precision",
            settings.get("target_class_precision", 0.90),
        )
    )
    minimum_coverage = float(settings.get("training_min_coverage", 0.45))
    minimum_class_tracks = max(
        1, int(settings.get("training_min_class_accepted_tracks", 2) or 2)
    )
    minimum_class_precision = float(
        settings.get("training_min_class_accepted_precision", 0.75)
    )
    minimum_retained_f1 = float(
        settings.get("training_min_retained_style_f1", 0.70)
    )
    maximum_retained_recall_drop = float(
        settings.get(
            "training_max_retained_style_recall_drop",
            settings.get("training_max_retained_style_f1_drop", 0.05),
        )
    )
    active_style_metrics = active_style_metrics if isinstance(active_style_metrics, dict) else {}

    reasons = []
    if enabled and macro_f1 < minimum_macro_f1:
        reasons.append(
            f"macro F1 {macro_f1:.3f} ниже минимума {minimum_macro_f1:.3f}"
        )
    if enabled and accepted_precision < minimum_accepted_precision:
        reasons.append(
            "точность принятых результатов "
            f"{accepted_precision:.3f} ниже минимума {minimum_accepted_precision:.3f}"
        )
    if enabled and coverage < minimum_coverage:
        reasons.append(
            f"покрытие {coverage:.3f} ниже минимума {minimum_coverage:.3f}"
        )

    per_class_failures = {}
    for class_name, class_report in rejection_policy_report.get("per_class", {}).items():
        accepted_tracks = int(class_report.get("accepted_tracks", 0))
        class_precision = float(class_report.get("accepted_precision", 0.0))
        class_reasons = []
        if accepted_tracks < minimum_class_tracks:
            class_reasons.append(
                f"принято {accepted_tracks}, требуется минимум {minimum_class_tracks}"
            )
        if accepted_tracks and class_precision < minimum_class_precision:
            class_reasons.append(
                f"precision {class_precision:.3f} ниже {minimum_class_precision:.3f}"
            )
        if class_reasons:
            per_class_failures[str(class_name)] = class_reasons

    if enabled and per_class_failures:
        failed_classes = ", ".join(sorted(per_class_failures))
        reasons.append(f"не прошли классы: {failed_classes}")

    retained_style_failures = {}
    for style in sorted(set(str(value) for value in (protected_styles or []) if value)):
        style_report = validation_report.get(style)
        if not isinstance(style_report, dict):
            retained_style_failures[style] = ["стиль отсутствует в кандидате"]
            continue
        f1_score = float(style_report.get("f1-score", 0.0))
        candidate_recall = float(style_report.get("recall", 0.0) or 0.0)
        old_recall = float((active_style_metrics.get(style) or {}).get("recall", 0.0) or 0.0)
        style_reasons = []
        if f1_score < minimum_retained_f1:
            style_reasons.append(
                f"F1 {f1_score:.3f} ниже защитного минимума {minimum_retained_f1:.3f}"
            )
        # Recall is comparable even though the old model knew fewer classes:
        # it is measured only on true tracks of this retained style.  Precision
        # and F1 would be distorted because the old model cannot predict any of
        # the newly introduced labels.
        if old_recall and candidate_recall + maximum_retained_recall_drop < old_recall:
            style_reasons.append(
                f"recall {candidate_recall:.3f} ниже активной модели {old_recall:.3f} "
                f"больше чем на {maximum_retained_recall_drop:.3f}"
            )
        if style_reasons:
            retained_style_failures[style] = style_reasons
    if enabled and retained_style_failures:
        reasons.append(
            "ухудшены ранее активные стили: "
            + ", ".join(sorted(retained_style_failures))
        )

    return {
        "enabled": enabled,
        "passed": (not enabled) or not reasons,
        "metrics": {
            "macro_f1": macro_f1,
            "accepted_precision": accepted_precision,
            "coverage": coverage,
        },
        "requirements": {
            "minimum_macro_f1": minimum_macro_f1,
            "minimum_accepted_precision": minimum_accepted_precision,
            "minimum_coverage": minimum_coverage,
            "minimum_class_accepted_tracks": minimum_class_tracks,
            "minimum_class_accepted_precision": minimum_class_precision,
            "minimum_retained_style_f1": minimum_retained_f1,
            "maximum_retained_style_recall_drop": maximum_retained_recall_drop,
        },
        "per_class_failures": per_class_failures,
        "retained_style_failures": retained_style_failures,
        "reasons": reasons,
    }


def _feature_fingerprint(feature_row):
    """Приближённый аудиоотпечаток уже извлечённого вектора признаков."""
    row = np.nan_to_num(np.asarray(feature_row, dtype=float).reshape(-1))
    median = float(np.median(row))
    scale = float(np.median(np.abs(row - median))) or float(np.std(row)) or 1.0
    quantized = np.clip(np.round((row - median) / scale, 1) * 10, -127, 127).astype(np.int8)
    return hashlib.sha1(quantized.tobytes()).hexdigest()[:20]


def _strict_deduplicate_training_rows(
        paths, samples, labels, taxonomies, *, write_report=True,
):
    """Drop true acoustic copies and quarantine copies with conflicting labels.

    The broad fingerprint is only used to find candidates.  Rows are considered
    copies only when their actual feature vectors are numerically equal, which
    avoids deleting merely similar remixes.
    """
    paths = list(paths)
    samples = [np.asarray(row, dtype=float).reshape(-1) for row in samples]
    labels = [str(value) for value in labels]
    taxonomies = list(taxonomies)
    if not (len(paths) == len(samples) == len(labels) == len(taxonomies)):
        raise ValueError("Размеры массивов для дедупликации не совпадают")

    candidates = {}
    for index, row in enumerate(samples):
        candidates.setdefault(_feature_fingerprint(row), []).append(index)

    source_priority = {
        "manual_review": 0,
        "dataset_builder": 1,
        "rekordbox": 2,
        "samples": 3,
        "unknown": 4,
    }
    keep = np.ones(len(paths), dtype=bool)
    report_rows = []
    duplicate_groups = 0
    conflict_groups = 0
    dropped_duplicates = 0
    dropped_conflicts = 0

    for fingerprint, bucket in candidates.items():
        if len(bucket) < 2:
            continue
        unassigned = list(bucket)
        exact_clusters = []
        while unassigned:
            anchor = unassigned.pop(0)
            cluster = [anchor]
            remaining = []
            for candidate in unassigned:
                if np.allclose(
                    samples[anchor], samples[candidate], rtol=1e-6, atol=1e-5,
                    equal_nan=True,
                ):
                    cluster.append(candidate)
                else:
                    remaining.append(candidate)
            exact_clusters.append(cluster)
            unassigned = remaining

        for cluster_number, cluster in enumerate(exact_clusters, start=1):
            if len(cluster) < 2:
                continue
            cluster_id = f"{fingerprint}:{cluster_number}"
            styles = sorted({labels[index] for index in cluster})
            manual = [
                index for index in cluster
                if str((taxonomies[index] or {}).get("training_source", "")) == "manual_review"
            ]
            trusted_manual_styles = {labels[index] for index in manual}
            if len(styles) > 1 and len(trusted_manual_styles) != 1:
                conflict_groups += 1
                for index in cluster:
                    keep[index] = False
                    dropped_conflicts += 1
                    report_rows.append((
                        cluster_id, "conflicting_labels_excluded", "|".join(styles),
                        "", labels[index],
                        str((taxonomies[index] or {}).get("training_source", "unknown")),
                        paths[index],
                    ))
                continue

            winning_style = next(iter(trusted_manual_styles), styles[0])
            eligible = [index for index in cluster if labels[index] == winning_style]
            winner = min(
                eligible,
                key=lambda index: (
                    source_priority.get(
                        str((taxonomies[index] or {}).get("training_source", "unknown")), 9
                    ),
                    os.path.normcase(os.path.abspath(paths[index])),
                ),
            )
            duplicate_groups += 1
            for index in cluster:
                if index == winner:
                    decision = "kept"
                else:
                    keep[index] = False
                    dropped_duplicates += 1
                    decision = (
                        "manual_label_overrode_duplicate"
                        if len(styles) > 1 else "duplicate_excluded"
                    )
                report_rows.append((
                    cluster_id, decision, "|".join(styles), paths[winner], labels[index],
                    str((taxonomies[index] or {}).get("training_source", "unknown")),
                    paths[index],
                ))

    if write_report:
        TRAINING_LABEL_CONFLICTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(TRAINING_LABEL_CONFLICTS_FILE, "w", newline="", encoding="utf-8-sig") as output:
            writer = csv.writer(output)
            writer.writerow([
                "fingerprint_group", "decision", "group_labels", "kept_path",
                "row_label", "training_source", "path",
            ])
            writer.writerows(report_rows)

    selected = np.flatnonzero(keep)
    return selected, {
        "input_tracks": len(paths),
        "output_tracks": int(selected.size),
        "duplicate_groups": duplicate_groups,
        "conflict_groups": conflict_groups,
        "dropped_duplicates": dropped_duplicates,
        "dropped_conflicts": dropped_conflicts,
        "report_file": str(TRAINING_LABEL_CONFLICTS_FILE),
    }


def _build_training_groups(paths, samples, labels, *, write_report=True):
    """Группирует версии одного трека и одинаковые аудиоотпечатки."""
    name_groups = [track_group_key(path) for path in paths]
    fingerprints = [_feature_fingerprint(row) for row in samples]

    # Связанные компоненты по двум признакам сразу. Например, если A и B имеют
    # одно имя, а B и C — один аудиоотпечаток, все A/B/C обязаны быть в одном split.
    parents = list(range(len(paths)))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left, right):
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    seen_names = {}
    seen_fingerprints = {}
    for index, (name_group, fingerprint) in enumerate(zip(name_groups, fingerprints)):
        if name_group in seen_names:
            union(index, seen_names[name_group])
        else:
            seen_names[name_group] = index
        if fingerprint in seen_fingerprints:
            union(index, seen_fingerprints[fingerprint])
        else:
            seen_fingerprints[fingerprint] = index

    component_ids = {}
    groups = []
    for index in range(len(paths)):
        root = find(index)
        component_id = component_ids.setdefault(root, len(component_ids) + 1)
        groups.append(f"track-group:{component_id:06d}")

    if write_report:
        TRAINING_DUPLICATES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(TRAINING_DUPLICATES_FILE, "w", newline="", encoding="utf-8-sig") as report_file:
            writer = csv.writer(report_file)
            writer.writerow(["group", "audio_fingerprint", "base_genre", "path"])
            group_counts = Counter(groups)
            for group, fingerprint, label, path in zip(groups, fingerprints, labels, paths):
                if group_counts[group] > 1:
                    writer.writerow([group, fingerprint, label, path])
    return np.asarray(groups, dtype=object)


def _three_way_grouped_indices(X, y, groups, holdout_fraction=0.2, random_state=42):
    """Train / threshold-tuning / final-test без пересечения групп треков."""
    y = np.asarray(y)
    groups = np.asarray(groups, dtype=object)
    indices = np.arange(len(y))
    classes = set(y.tolist())
    class_group_counts = {
        str(class_name): len(set(groups[y == class_name].tolist()))
        for class_name in classes
    }

    # validation_size задаёт суммарную долю двух контрольных частей.
    # Например, 0.2 -> два независимых fold примерно по 10% каждый.
    desired_splits = max(3, int(round(2.0 / max(holdout_fraction, 0.1))))
    max_splits = min(desired_splits, min(class_group_counts.values()))
    for n_splits in range(max_splits, 2, -1):
        splitter = StratifiedGroupKFold(
            n_splits=n_splits,
            shuffle=True,
            random_state=random_state,
        )
        complete_folds = []
        for _fold_train, fold_test in splitter.split(X, y, groups):
            if set(y[fold_test].tolist()) == classes:
                complete_folds.append(np.asarray(fold_test, dtype=int))
        if len(complete_folds) < 2:
            continue

        threshold_idx = complete_folds[0]
        validation_idx = complete_folds[1]
        holdout_idx = np.concatenate([threshold_idx, validation_idx])
        train_idx = np.setdiff1d(indices, holdout_idx, assume_unique=False)
        if set(y[train_idx].tolist()) == classes:
            return train_idx, threshold_idx, validation_idx

    raise ValueError(
        "Не удалось создать три групповые выборки. "
        f"Число независимых групп по классам: {class_group_counts}. "
        "Добавьте разнообразные треки или отключите слишком редкий жанр."
    )


def _eligible_base_genres(base_labels, trainable_genres, min_tracks_per_genre):
    """Выбирает только включённые и достаточно представленные базовые стили."""
    counts = Counter(np.asarray(base_labels).tolist())
    allowed = {
        taxonomy_from_training_label(str(genre)).base_genre
        for genre in trainable_genres
    }
    minimum = max(3, int(min_tracks_per_genre or 3))
    eligible = {
        genre for genre, count in counts.items()
        if genre in allowed and count >= minimum
    }
    skipped = {
        genre: count for genre, count in counts.items()
        if genre not in eligible
    }
    return eligible, skipped, counts


def _balanced_track_indices(labels, max_class_ratio=1.5, random_state=42):
    """Ограничивает крупный класс относительно минимального на уровне треков."""
    labels = np.asarray(labels)
    counts = Counter(labels.tolist())
    if not counts:
        return np.asarray([], dtype=int), 0
    ratio = min(max(float(max_class_ratio or 1.5), 1.0), 5.0)
    min_count = min(counts.values())
    max_count_per_class = max(min_count, int(round(min_count * ratio)))
    rng = np.random.default_rng(int(random_state))
    selected = []
    for genre in sorted(counts):
        indices = np.where(labels == genre)[0]
        if len(indices) > max_count_per_class:
            indices = rng.choice(indices, size=max_count_per_class, replace=False)
        selected.extend(indices.tolist())
    selected = np.asarray(selected, dtype=int)
    rng.shuffle(selected)
    return selected, max_count_per_class


def _independent_class_cap_indices(labels, max_per_class=800, random_state=42):
    """Ограничивает каждый класс независимо, не привязывая все стили к самому редкому."""
    labels = np.asarray(labels)
    maximum = max(1, int(max_per_class or 800))
    rng = np.random.default_rng(int(random_state))
    selected = []
    for genre in sorted(set(labels.tolist())):
        indices = np.where(labels == genre)[0]
        if len(indices) > maximum:
            indices = rng.choice(indices, size=maximum, replace=False)
        selected.extend(indices.tolist())
    selected = np.asarray(selected, dtype=int)
    rng.shuffle(selected)
    return selected, maximum


def _expand_track_segments(segment_features_by_track, labels, groups, track_indices):
    """Разворачивает выбранные треки в сегменты, сохраняя метку и группу трека."""
    labels = np.asarray(labels)
    groups = np.asarray(groups, dtype=object)
    rows = []
    expanded_labels = []
    expanded_groups = []
    for track_index in np.asarray(track_indices, dtype=int):
        segment_rows = segment_features_by_track[track_index]
        if isinstance(segment_rows, np.ndarray) and segment_rows.ndim == 1:
            segment_rows = [segment_rows]
        for row in segment_rows:
            rows.append(np.asarray(row, dtype=float).reshape(-1))
            expanded_labels.append(labels[track_index])
            expanded_groups.append(groups[track_index])
    if not rows:
        raise ValueError("Не найдено сегментных признаков для выбранных треков")
    lengths = {row.shape[0] for row in rows}
    if len(lengths) != 1:
        raise ValueError(f"Сегментные признаки имеют разную длину: {sorted(lengths)}")
    return (
        np.vstack(rows),
        np.asarray(expanded_labels),
        np.asarray(expanded_groups, dtype=object),
    )


def _predict_track_probabilities(model, segment_features_by_track, track_indices):
    """Усредняет вероятности сегментов отдельно для каждого трека."""
    probabilities = []
    segment_disagreement = []
    for track_index in np.asarray(track_indices, dtype=int):
        segment_rows = np.asarray(segment_features_by_track[track_index], dtype=float)
        if segment_rows.ndim == 1:
            segment_rows = segment_rows.reshape(1, -1)
        segment_probabilities = np.asarray(model.predict_proba(segment_rows), dtype=float)
        probabilities.append(np.mean(segment_probabilities, axis=0))
        segment_winners = np.argmax(segment_probabilities, axis=1)
        winner_counts = Counter(segment_winners.tolist())
        # A single different fragment is normal for an intro/break/drop and
        # must not add a blanket +0.10 rejection penalty.  Mark the track as
        # unstable only when no style wins a two-thirds majority.
        majority_share = max(winner_counts.values()) / max(1, len(segment_winners))
        segment_disagreement.append(
            len(segment_winners) > 1 and majority_share < (2.0 / 3.0)
        )
    return np.vstack(probabilities), np.asarray(segment_disagreement, dtype=bool)


def _calibrated_feature_importances(model):
    """Усредняет важности внутренних RF после CalibratedClassifierCV."""
    if hasattr(model, "flat_model"):
        return _calibrated_feature_importances(model.flat_model)
    if hasattr(model, "feature_importances_"):
        return model.feature_importances_.tolist()
    importances = []
    for calibrated in getattr(model, "calibrated_classifiers_", []):
        estimator = getattr(calibrated, "estimator", None)
        if estimator is not None and hasattr(estimator, "feature_importances_"):
            importances.append(np.asarray(estimator.feature_importances_, dtype=float))
    if not importances:
        return None
    return np.mean(np.vstack(importances), axis=0).tolist()


def _fit_hierarchy_safe(flat_model, features, labels, rf_params, settings):
    """Add hierarchy without making a failed optional head lose the flat model."""
    if not bool(settings.get("hierarchical_genre_enabled", True)):
        return flat_model, {"enabled": False, "reason": "disabled"}
    hierarchy_weight = float(settings.get("hierarchical_genre_weight", 0.72) or 0.72)

    def model_factory(target_labels):
        # The flat model still carries the main decision.  Family/subtype
        # heads only refine it, so duplicating a 500-tree calibrated forest
        # for every family wastes RAM and makes training unnecessarily long.
        head_params = dict(rf_params)
        head_params["n_estimators"] = min(
            int(head_params.get("n_estimators", 300)),
            int(settings.get("hierarchical_genre_estimators", 180) or 180),
        )
        if head_params.get("max_depth") is None:
            head_params["max_depth"] = 18
        else:
            head_params["max_depth"] = min(int(head_params["max_depth"]), 18)
        classifier, _calibration = _build_probability_model(
            head_params, settings, np.asarray(target_labels),
        )
        return classifier

    try:
        return fit_hierarchical_classifier(
            flat_model,
            features,
            labels,
            model_factory,
            hierarchy_weight=hierarchy_weight,
        )
    except Exception as exc:
        logger.warning("[HIERARCHY] Не удалось обучить дополнительный уровень: %s", exc)
        return flat_model, {
            "enabled": False,
            "reason": f"{type(exc).__name__}: {exc}",
        }


def _select_safe_hierarchy_weight(
        model, segment_features_by_track, labels, threshold_idx, validation_idx,
        protected_styles=None,
):
    """Tune hierarchy on one split and keep it only after an untouched check."""
    if not hasattr(model, "hierarchy_weight") or not hasattr(model, "flat_model"):
        threshold_probabilities, threshold_disagreement = _predict_track_probabilities(
            model, segment_features_by_track, threshold_idx,
        )
        validation_probabilities, validation_disagreement = _predict_track_probabilities(
            model, segment_features_by_track, validation_idx,
        )
        return (
            threshold_probabilities, threshold_disagreement,
            validation_probabilities, validation_disagreement,
            {"enabled": False, "reason": "flat_model"},
        )

    classes = np.asarray(model.classes_, dtype=object)
    threshold_labels = np.asarray(labels, dtype=object)[np.asarray(threshold_idx, dtype=int)]
    validation_labels = np.asarray(labels, dtype=object)[np.asarray(validation_idx, dtype=int)]
    configured = min(max(float(model.hierarchy_weight), 0.0), 1.0)
    candidates = sorted({0.0, 0.25, 0.50, configured, 0.85})
    tuning = []
    threshold_cache = {}
    for weight in candidates:
        model.hierarchy_weight = weight
        probabilities, disagreement = _predict_track_probabilities(
            model, segment_features_by_track, threshold_idx,
        )
        score, _ = _probability_macro_f1(threshold_labels, probabilities, classes)
        threshold_cache[weight] = (probabilities, disagreement)
        tuning.append({"weight": weight, "macro_f1": score})
    selected = max(tuning, key=lambda row: (row["macro_f1"], -row["weight"]))["weight"]

    model.hierarchy_weight = 0.0
    base_validation, base_disagreement = _predict_track_probabilities(
        model, segment_features_by_track, validation_idx,
    )
    base_macro, base_prediction = _probability_macro_f1(
        validation_labels, base_validation, classes,
    )
    model.hierarchy_weight = selected
    selected_validation, selected_disagreement = _predict_track_probabilities(
        model, segment_features_by_track, validation_idx,
    )
    selected_macro, selected_prediction = _probability_macro_f1(
        validation_labels, selected_validation, classes,
    )
    base_report = classification_report(
        validation_labels, base_prediction, labels=classes,
        output_dict=True, zero_division=0,
    )
    selected_report = classification_report(
        validation_labels, selected_prediction, labels=classes,
        output_dict=True, zero_division=0,
    )
    regressions = []
    for style in sorted(set(str(value) for value in (protected_styles or []) if value)):
        base_f1 = float((base_report.get(style) or {}).get("f1-score", 0.0))
        selected_f1 = float((selected_report.get(style) or {}).get("f1-score", 0.0))
        if selected_f1 + 0.03 < base_f1:
            regressions.append({
                "style": style,
                "flat_f1": base_f1,
                "hierarchical_f1": selected_f1,
            })
    accepted = bool(selected > 0 and selected_macro + 0.005 >= base_macro and not regressions)
    if not accepted:
        selected = 0.0
        model.hierarchy_weight = 0.0
        selected_validation = base_validation
        selected_disagreement = base_disagreement
    threshold_probabilities, threshold_disagreement = threshold_cache[selected]

    # The family report distinguishes cross-family errors from close subtypes.
    family_true = np.asarray([genre_family(value) for value in validation_labels], dtype=object)
    family_predicted = []
    for source_index in np.asarray(validation_idx, dtype=int):
        decision = family_decision(model, np.asarray(segment_features_by_track[source_index]))
        family_predicted.append((decision or {}).get("family", "Other"))
    family_predicted = np.asarray(family_predicted, dtype=object)
    family_macro = float(f1_score(
        family_true, family_predicted, average="macro", zero_division=0,
    )) if len(family_true) else 0.0
    report = {
        "enabled": accepted,
        "selected_weight": selected,
        "flat_validation_macro_f1": base_macro,
        "hierarchical_validation_macro_f1": selected_macro,
        "family_validation_macro_f1": family_macro,
        "family_validation_accuracy": float(np.mean(family_true == family_predicted))
        if len(family_true) else 0.0,
        "protected_style_regressions": regressions,
        "tuning": tuning,
        "reason": "accepted" if accepted else "validation_regression",
    }
    return (
        threshold_probabilities, threshold_disagreement,
        selected_validation, selected_disagreement, report,
    )


def _fit_effnet_head_for_indices(
        training_paths, labels, embedding_map, track_indices, pipeline_settings,
):
    """Fit a compact deep head only on paths with a valid cached vector."""
    track_indices = np.asarray(track_indices, dtype=int)
    positions, vectors = available_embedding_rows(
        training_paths, embedding_map, track_indices,
    )
    coverage = float(positions.size / max(1, track_indices.size))
    minimum_coverage = float(
        pipeline_settings.get("effnet_genre_min_coverage", 0.50) or 0.50
    )
    report = {
        "enabled": False,
        "available_tracks": int(positions.size),
        "requested_tracks": int(track_indices.size),
        "coverage": coverage,
        "minimum_coverage": minimum_coverage,
    }
    if positions.size < 20 or coverage < minimum_coverage:
        report["reason"] = "insufficient_embedding_coverage"
        return None, report
    source_indices = track_indices[positions]
    targets = np.asarray(labels, dtype=object)[source_indices]
    if len(set(targets.tolist())) < 2:
        report["reason"] = "one_class"
        return None, report
    try:
        head = EffNetGenreHead(
            pca_dimensions=int(
                pipeline_settings.get("effnet_genre_pca_dimensions", 48) or 48
            ),
            random_state=int(pipeline_settings.get("random_state", 42) or 42),
            n_estimators=int(
                pipeline_settings.get("effnet_genre_estimators", 220) or 220
            ),
        ).fit(vectors, targets)
    except Exception as exc:
        report["reason"] = f"{type(exc).__name__}: {exc}"
        return None, report
    report.update({
        "enabled": True,
        "classes": [str(value) for value in head.classes_],
        "embedding_dimensions": int(head.embedding_dim_),
        "pca_dimensions": int(head.pca.n_components_),
    })
    return head, report


def _fuse_effnet_split(
        base_probabilities,
        base_classes,
        head,
        training_paths,
        embedding_map,
        track_indices,
        alpha,
):
    if head is None:
        return np.asarray(base_probabilities, dtype=float), 0
    positions, vectors = available_embedding_rows(
        training_paths, embedding_map, np.asarray(track_indices, dtype=int),
    )
    fused = fuse_available_rows(
        base_probabilities, base_classes, head, vectors, positions, alpha,
    )
    return fused, int(positions.size)


def _probability_macro_f1(labels, probabilities, classes):
    probabilities = np.asarray(probabilities, dtype=float)
    predicted = np.asarray(classes, dtype=object)[np.argmax(probabilities, axis=1)]
    return float(f1_score(labels, predicted, average="macro", zero_division=0)), predicted


def _select_safe_effnet_fusion(
        head,
        training_paths,
        labels,
        embedding_map,
        threshold_idx,
        threshold_probabilities,
        validation_idx,
        validation_probabilities,
        classes,
        pipeline_settings,
        protected_styles=None,
):
    """Tune on threshold split, then reject the head if validation regresses."""
    configured = float(
        pipeline_settings.get("effnet_genre_fusion_alpha", 0.35) or 0.35
    )
    candidates = sorted(set([
        0.15, 0.25, configured, 0.35, 0.45,
    ]))
    threshold_labels = np.asarray(labels, dtype=object)[np.asarray(threshold_idx, dtype=int)]
    validation_labels = np.asarray(labels, dtype=object)[np.asarray(validation_idx, dtype=int)]
    tuning = []
    for alpha in candidates:
        fused, available = _fuse_effnet_split(
            threshold_probabilities, classes, head, training_paths,
            embedding_map, threshold_idx, alpha,
        )
        score, _predicted = _probability_macro_f1(
            threshold_labels, fused, classes,
        )
        tuning.append({
            "alpha": round(float(alpha), 4),
            "macro_f1": score,
            "available_tracks": available,
        })
    best = max(tuning, key=lambda row: (row["macro_f1"], -row["alpha"]))
    alpha = float(best["alpha"])
    hybrid_validation, available_validation = _fuse_effnet_split(
        validation_probabilities, classes, head, training_paths,
        embedding_map, validation_idx, alpha,
    )
    base_macro, base_prediction = _probability_macro_f1(
        validation_labels, validation_probabilities, classes,
    )
    hybrid_macro, hybrid_prediction = _probability_macro_f1(
        validation_labels, hybrid_validation, classes,
    )
    base_report = classification_report(
        validation_labels, base_prediction, labels=classes,
        output_dict=True, zero_division=0,
    )
    hybrid_report = classification_report(
        validation_labels, hybrid_prediction, labels=classes,
        output_dict=True, zero_division=0,
    )
    regressions = []
    for style in sorted(set(str(value) for value in (protected_styles or []) if value)):
        base_f1 = float((base_report.get(style) or {}).get("f1-score", 0.0))
        hybrid_f1 = float((hybrid_report.get(style) or {}).get("f1-score", 0.0))
        if hybrid_f1 + 0.03 < base_f1:
            regressions.append({
                "style": style,
                "base_f1": base_f1,
                "hybrid_f1": hybrid_f1,
            })
    minimum_macro_f1 = float(
        pipeline_settings.get("effnet_genre_min_macro_f1", 0.65) or 0.65
    )
    passed = (
        hybrid_macro >= minimum_macro_f1
        and hybrid_macro + 0.005 >= base_macro
        and not regressions
    )
    if hybrid_macro < minimum_macro_f1:
        reason = "below_absolute_quality_floor"
    elif regressions:
        reason = "protected_style_regression"
    elif hybrid_macro + 0.005 < base_macro:
        reason = "validation_regression"
    else:
        reason = "accepted"
    report = {
        "enabled": bool(passed),
        "selected_alpha": alpha,
        "base_validation_macro_f1": base_macro,
        "hybrid_validation_macro_f1": hybrid_macro,
        "minimum_macro_f1": minimum_macro_f1,
        "validation_available_tracks": available_validation,
        "validation_total_tracks": int(len(validation_idx)),
        "protected_style_regressions": regressions,
        "tuning": tuning,
        "reason": reason,
    }
    return hybrid_validation if passed else np.asarray(validation_probabilities), alpha, report


def _write_genre_conflict_reports(
        training_paths,
        training_groups,
        true_labels,
        probabilities,
        classes,
        validation_indices,
        *,
        max_pairs=10,
        tracks_per_pair=40,
):
    """Create a compact active-learning queue instead of thousands of rows."""
    probabilities = np.asarray(probabilities, dtype=float)
    classes = np.asarray(classes, dtype=object)
    true_labels = np.asarray(true_labels, dtype=object)
    validation_indices = np.asarray(validation_indices, dtype=int)
    rankings = np.argsort(probabilities, axis=1)[:, ::-1]
    conflicts = {}
    pair_population = Counter()
    for true_label in true_labels:
        for other in classes:
            if str(other) != str(true_label):
                pair_population[tuple(sorted((str(true_label), str(other))))] += 1
    for local_index, source_index in enumerate(validation_indices):
        top_index = int(rankings[local_index, 0])
        second_index = int(rankings[local_index, 1]) if rankings.shape[1] > 1 else top_index
        predicted = str(classes[top_index])
        true_label = str(true_labels[local_index])
        if predicted == true_label:
            continue
        pair = tuple(sorted((true_label, predicted)))
        top_probability = float(probabilities[local_index, top_index])
        second_probability = float(probabilities[local_index, second_index])
        conflicts.setdefault(pair, []).append({
            "path": str(training_paths[source_index]),
            "group": str(training_groups[source_index]),
            "true_style": true_label,
            "predicted_style": predicted,
            "true_family": genre_family(true_label),
            "predicted_family": genre_family(predicted),
            "confidence": top_probability,
            "second_style": str(classes[second_index]),
            "second_probability": second_probability,
            "margin": top_probability - second_probability,
        })

    ranked_pairs = sorted(
        conflicts,
        key=lambda pair: (-len(conflicts[pair]), pair),
    )
    TRAINING_CONFLICTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TRAINING_CONFLICTS_FILE, "w", newline="", encoding="utf-8-sig") as output:
        writer = csv.writer(output)
        writer.writerow([
            "style_a", "style_b", "family_a", "family_b", "errors",
            "validation_pair_population", "conflict_rate", "priority",
        ])
        for priority, pair in enumerate(ranked_pairs, start=1):
            population = max(1, int(pair_population.get(pair, 0)))
            writer.writerow([
                pair[0], pair[1], genre_family(pair[0]), genre_family(pair[1]),
                len(conflicts[pair]), population,
                round(len(conflicts[pair]) / population, 6), priority,
            ])

    review_count = 0
    with open(TRAINING_REVIEW_QUEUE_FILE, "w", newline="", encoding="utf-8-sig") as output:
        writer = csv.writer(output)
        writer.writerow([
            "pair_priority", "path", "group", "true_style", "predicted_style",
            "true_family", "predicted_family", "confidence", "second_style",
            "second_probability", "margin", "review_action",
        ])
        for pair_priority, pair in enumerate(ranked_pairs[:max(1, int(max_pairs))], start=1):
            # Confident contradictions often reveal bad folder labels; small
            # margins reveal genuinely overlapping styles.  Alternate both.
            rows = conflicts[pair]
            uncertain = sorted(rows, key=lambda row: (row["margin"], -row["confidence"]))
            confident = sorted(rows, key=lambda row: (-row["confidence"], row["margin"]))
            selected = []
            seen = set()
            for left, right in zip(uncertain, confident):
                for row in (left, right):
                    if row["path"] in seen:
                        continue
                    selected.append(row)
                    seen.add(row["path"])
                    if len(selected) >= max(1, int(tracks_per_pair)):
                        break
                if len(selected) >= max(1, int(tracks_per_pair)):
                    break
            for row in selected:
                writer.writerow([
                    pair_priority, row["path"], row["group"], row["true_style"],
                    row["predicted_style"], row["true_family"], row["predicted_family"],
                    round(row["confidence"], 6), row["second_style"],
                    round(row["second_probability"], 6), round(row["margin"], 6),
                    "confirm_true_or_correct",
                ])
                review_count += 1
    return {
        "conflict_pairs": len(ranked_pairs),
        "review_tracks": review_count,
        "conflicts_file": str(TRAINING_CONFLICTS_FILE),
        "review_queue_file": str(TRAINING_REVIEW_QUEUE_FILE),
    }


def _model_file_identity():
    try:
        stat = os.stat(MODEL_PATH)
    except OSError:
        return None
    return {
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
    }


def _compact_validation_metrics(model_meta):
    report = model_meta.get("classification_report", {}) if isinstance(model_meta, dict) else {}
    compact = {}
    if not isinstance(report, dict):
        return compact
    for key, value in report.items():
        if isinstance(value, dict):
            compact[str(key)] = {
                metric: float(value.get(metric, 0.0) or 0.0)
                for metric in ("precision", "recall", "f1-score", "support")
            }
        elif key == "accuracy":
            compact[key] = float(value or 0.0)
    return compact


def _write_active_model_manifest(model_meta):
    """Persist small trusted metadata so a training worker needn't unpickle 200+ MB."""
    identity = _model_file_identity()
    if identity is None:
        return None
    if isinstance(model_meta, dict):
        model = model_meta.get("base_genre_model") or model_meta.get("model")
        version = str(model_meta.get("version", ""))
        expected_feature_len = model_meta.get("expected_feature_len")
    else:
        model = model_meta
        version = "legacy"
        expected_feature_len = getattr(model, "n_features_in_", None)
    classes = sorted(str(value) for value in getattr(model, "classes_", []) if value)
    if not classes:
        return None
    manifest = {
        "schema_version": 1,
        "updated_at": datetime.datetime.now().isoformat(),
        "model": identity,
        "version": version,
        "classes": classes,
        "expected_feature_len": int(expected_feature_len) if expected_feature_len else None,
        "classification_report": _compact_validation_metrics(model_meta),
    }
    ACTIVE_MODEL_MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = ACTIVE_MODEL_MANIFEST_FILE.with_suffix(".json.tmp")
    with open(temporary, "w", encoding="utf-8") as target:
        json.dump(manifest, target, ensure_ascii=False, indent=2)
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, ACTIVE_MODEL_MANIFEST_FILE)
    return manifest


def _load_active_model_manifest():
    identity = _model_file_identity()
    if identity is None:
        return None
    try:
        with open(ACTIVE_MODEL_MANIFEST_FILE, "r", encoding="utf-8") as source:
            manifest = json.load(source)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(manifest, dict) or manifest.get("model") != identity:
        return None
    if not isinstance(manifest.get("classes"), list) or not manifest["classes"]:
        return None
    return manifest


def _active_model_snapshot():
    """Return active classes and lazily migrate old models to a small manifest."""
    if not os.path.isfile(MODEL_PATH):
        return [], True

    manifest = _load_active_model_manifest()
    if manifest is not None:
        return sorted(str(value) for value in manifest["classes"] if value), True

    if _GENRE_MODEL_META_CACHE is not None:
        meta = _GENRE_MODEL_META_CACHE
        manifest = _write_active_model_manifest(meta)
        if manifest is not None:
            return manifest["classes"], True

    # One-time migration for models made before active_genre_model.json existed.
    # The object is deliberately not placed in the process cache: the training
    # worker only needs metadata and can release the old forest before features.
    try:
        with open(MODEL_PATH, "rb") as source:
            meta = pickle.load(source)
        manifest = _write_active_model_manifest(meta)
        del meta
        gc.collect()
        if manifest is not None:
            return manifest["classes"], True
    except Exception as exc:
        logger.warning("[TRAIN][ACTIVE MODEL] Не удалось создать манифест: %s", exc)

    # Reports are only a fallback.  A rejected last run must never be treated
    # as proof that its candidate classes are active.
    report = load_training_run_report()
    if report.get("status") == "accepted" and isinstance(report.get("active_after"), list):
        return sorted(str(value) for value in report["active_after"] if value), True
    try:
        with open(TRAINING_QUALITY_REPORT_FILE, "r", encoding="utf-8") as source:
            quality = json.load(source)
        if quality.get("passed") and isinstance(quality.get("candidate_classes"), list):
            return sorted(str(value) for value in quality["candidate_classes"] if value), True
    except (OSError, ValueError, TypeError):
        pass
    return [], False


def _active_model_style_metrics():
    manifest = _load_active_model_manifest() or {}
    report = manifest.get("classification_report", {})
    return {
        str(style): dict(metrics)
        for style, metrics in report.items()
        if isinstance(metrics, dict) and style not in {"macro avg", "weighted avg"}
    }


def _evaluate_active_model_on_current_validation(
        segment_features_by_track, labels, validation_idx, protected_styles,
):
    """Score the active model on the candidate's retained-style holdout.

    Metrics from an old three-class report cannot be compared directly with a
    new many-class report.  This evaluates both generations on the same current
    tracks and feature schema instead.
    """
    protected = sorted(set(str(value) for value in (protected_styles or []) if value))
    validation_idx = np.asarray(validation_idx, dtype=int)
    labels = np.asarray(labels, dtype=object)
    relevant_idx = np.asarray([
        index for index in validation_idx if str(labels[index]) in protected
    ], dtype=int)
    report = {
        "available": False,
        "protected_styles": protected,
        "tracks": int(relevant_idx.size),
    }
    if not protected or not relevant_idx.size or not os.path.isfile(MODEL_PATH):
        report["reason"] = "no_comparable_tracks"
        return {}, report
    try:
        with open(MODEL_PATH, "rb") as source:
            meta = pickle.load(source)
        if isinstance(meta, dict):
            model = meta.get("base_genre_model") or meta.get("model")
            expected = meta.get("expected_feature_len")
        else:
            model = meta
            expected = getattr(model, "n_features_in_", None)
        first_rows = np.asarray(segment_features_by_track[int(relevant_idx[0])])
        actual = int(first_rows.shape[-1])
        if expected and int(expected) != actual:
            report.update({
                "reason": "feature_schema_changed",
                "expected_features": int(expected),
                "actual_features": actual,
            })
            del meta, model
            gc.collect()
            return {}, report
        probabilities, _disagreement = _predict_track_probabilities(
            model, segment_features_by_track, relevant_idx,
        )
        model_classes = np.asarray(model.classes_, dtype=object)
        predicted = model_classes[np.argmax(probabilities, axis=1)]
        current_labels = labels[relevant_idx]
        metrics = classification_report(
            current_labels,
            predicted,
            labels=np.asarray(protected, dtype=object),
            output_dict=True,
            zero_division=0,
        )
        compact = {
            style: {
                key: float((metrics.get(style) or {}).get(key, 0.0) or 0.0)
                for key in ("precision", "recall", "f1-score", "support")
            }
            for style in protected
        }
        report.update({
            "available": True,
            "reason": "evaluated_on_candidate_holdout",
            "macro_f1": float((metrics.get("macro avg") or {}).get("f1-score", 0.0)),
            "accuracy": float(metrics.get("accuracy", 0.0) or 0.0),
            "per_style": compact,
        })
        del meta, model
        gc.collect()
        return compact, report
    except Exception as exc:
        report["reason"] = f"{type(exc).__name__}: {exc}"
        logger.warning("[TRAIN][ACTIVE MODEL] Сравнение на текущем holdout пропущено: %s", exc)
        gc.collect()
        return {}, report


def _active_model_classes():
    return _active_model_snapshot()[0]


def _active_model_classes_readonly():
    """Read active classes without creating or updating any model metadata."""
    manifest = _load_active_model_manifest() or {}
    classes = manifest.get("classes") or manifest.get("active_classes")
    if isinstance(classes, list) and classes:
        return sorted(str(value) for value in classes if value), True
    if not os.path.isfile(MODEL_PATH):
        return [], False
    try:
        with open(MODEL_PATH, "rb") as source:
            meta = pickle.load(source)
        model = (
            (meta.get("base_genre_model") or meta.get("model"))
            if isinstance(meta, dict) else meta
        )
        model_classes = getattr(model, "classes_", None)
        if model_classes is None:
            return [], False
        return sorted(str(value) for value in model_classes if value), True
    except (OSError, ValueError, TypeError, pickle.PickleError):
        return [], False


def _stable_task_order(task):
    path = os.path.normcase(os.path.abspath(task[0]))
    return hashlib.sha1(path.encode("utf-8", "surrogatepass")).hexdigest()


def _deduplicate_items_by_exact_path(items, get_path_fn, *, prefer_last=False):
    """Remove the same file twice without merging equal names from other folders."""
    by_path = {}
    order = []
    for item in items:
        key = os.path.normcase(os.path.abspath(get_path_fn(item)))
        if key not in by_path:
            order.append(key)
            by_path[key] = item
        elif prefer_last:
            by_path[key] = item
    return [by_path[key] for key in order]


def _training_library_path_key(path, music_dir=None, *, mapped_source=False):
    """Return one identity for a library file referenced through UNC or a drive.

    Confirmed folders normally use the configured UNC root, while Rekordbox may
    retain the mapped-drive spelling (for example ``Z:/2025/...``). The key is
    deliberately limited to the configured library and explicit mapped-source
    paths; unrelated absolute paths are never collapsed by their suffix alone.
    """
    raw_path = os.fspath(path or "").strip()
    if not raw_path:
        return ""

    def normalise(value):
        return re.sub(r"/+", "/", str(value).replace("\\", "/")).rstrip("/").casefold()

    normalised_path = normalise(raw_path)
    normalised_root = normalise(os.fspath(music_dir or ""))
    if normalised_root and (
            normalised_path == normalised_root
            or normalised_path.startswith(normalised_root + "/")
    ):
        relative = normalised_path[len(normalised_root):].lstrip("/")
        return f"library:{relative}"
    if mapped_source and re.match(r"^[a-z]:/", normalised_path):
        return f"library:{normalised_path[3:].lstrip('/')}"
    return f"absolute:{normalised_path}"


def _reconcile_confirmed_and_rekordbox_labels(
        dataset_rows, rekordbox_tracks, genre_settings, music_dir=None,
        *, effective_styles=None, write_report=True,
):
    """Prevent a confirmed folder and Rekordbox teaching opposite labels.

    When the same physical library file has another Rekordbox base style, the
    conflict is quarantined before cap/balancing. This preserves the existing
    late fingerprint-dedup policy: neither source silently wins without a
    per-track manual review. No stored label or music file is changed.
    """
    effective = set(effective_styles) if effective_styles is not None else None
    confirmed_by_key = {}
    for row in dataset_rows or []:
        key = _training_library_path_key(row.get("path"), music_dir)
        label = taxonomy_from_training_label(
            str(row.get("base_genre") or "")
        ).base_genre
        if key and label and (effective is None or label in effective):
            confirmed_by_key[key] = {
                "label": label,
                "path": str(row.get("path") or ""),
                "folder_id": str((row.get("taxonomy") or {}).get("training_folder_id") or ""),
            }

    selected = []
    conflicts = []
    overlap_tracks = 0
    pair_counts = Counter()
    for track in rekordbox_tracks or []:
        source_path = track.get("source_path") or track.get("path") or ""
        key = _training_library_path_key(
            source_path,
            music_dir,
            mapped_source=bool(track.get("source_path")),
        )
        confirmed = confirmed_by_key.get(key)
        if confirmed is None:
            resolved_key = _training_library_path_key(track.get("path"), music_dir)
            confirmed = confirmed_by_key.get(resolved_key)
        if confirmed is None:
            selected.append(track)
            continue

        overlap_tracks += 1
        rekordbox_label = _rekordbox_track_base_style(track, genre_settings)
        confirmed_label = confirmed["label"]
        if effective is not None and rekordbox_label not in effective:
            selected.append(track)
            continue
        if not rekordbox_label or rekordbox_label == confirmed_label:
            selected.append(track)
            continue

        pair_counts[(confirmed_label, rekordbox_label)] += 1
        conflicts.append({
            "decision": "conflicting_source_labels_quarantined",
            "confirmed_style": confirmed_label,
            "rekordbox_style": rekordbox_label,
            "rekordbox_raw_genre": str(
                track.get("raw_genre") or get_track_val(track, "Genre") or ""
            ),
            "confirmed_path": confirmed["path"],
            "rekordbox_path": str(track.get("path") or source_path),
            "rekordbox_source_path": str(source_path),
            "training_folder_id": confirmed["folder_id"],
        })

    if write_report:
        TRAINING_SOURCE_LABEL_CONFLICTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(
                TRAINING_SOURCE_LABEL_CONFLICTS_FILE,
                "w", newline="", encoding="utf-8-sig",
        ) as output:
            writer = csv.DictWriter(output, fieldnames=[
                "decision", "confirmed_style", "rekordbox_style",
                "rekordbox_raw_genre", "confirmed_path", "rekordbox_path",
                "rekordbox_source_path", "training_folder_id",
            ])
            writer.writeheader()
            writer.writerows(conflicts)

    conflicting_confirmed_paths = sorted({
        item["confirmed_path"] for item in conflicts if item["confirmed_path"]
    })
    return selected, {
        "policy": "quarantine_confirmed_rekordbox_conflicts",
        "input_rekordbox_tracks": len(rekordbox_tracks or []),
        "confirmed_overlap_tracks": overlap_tracks,
        "conflict_tracks": len(conflicts),
        "excluded_rekordbox_tracks": len(conflicts),
        "excluded_confirmed_tracks": len(conflicting_confirmed_paths),
        "conflicting_confirmed_paths": conflicting_confirmed_paths,
        "conflict_pairs": [
            {
                "confirmed_style": confirmed_style,
                "rekordbox_style": rekordbox_style,
                "count": count,
            }
            for (confirmed_style, rekordbox_style), count in sorted(
                pair_counts.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "items": conflicts,
        "report_file": str(TRAINING_SOURCE_LABEL_CONFLICTS_FILE),
    }


def _training_source_mix_targets(local_available, rekordbox_available, max_per_style):
    maximum = max(1, int(max_per_style))
    local_available = max(0, int(local_available))
    rekordbox_available = max(0, int(rekordbox_available))
    if not rekordbox_available:
        return min(local_available, maximum), 0
    if not local_available:
        return 0, min(rekordbox_available, maximum)
    rekordbox_target = min(rekordbox_available, max(1, maximum // 3))
    local_target = min(local_available, maximum - rekordbox_target)
    rekordbox_target = min(rekordbox_available, maximum - local_target)
    local_target = min(local_available, maximum - rekordbox_target)
    return local_target, rekordbox_target


def _effective_trainable_styles(genre_settings, builder_styles, excluded_styles=None):
    """Return the exact style set allowed to contribute to one training run."""
    styles = set()
    for value in (genre_settings or {}).values():
        if isinstance(value, dict):
            if not value.get("is_trainable", False):
                continue
            label = value.get("genre")
        else:
            label = value
        base_style = taxonomy_from_training_label(str(label or "")).base_genre
        if base_style:
            styles.add(base_style)

    if isinstance(builder_styles, dict):
        builder_values = (
            style for style, count in builder_styles.items() if int(count or 0) > 0
        )
    else:
        builder_values = builder_styles or []
    for label in builder_values:
        base_style = taxonomy_from_training_label(str(label or "")).base_genre
        if base_style:
            styles.add(base_style)

    excluded = {
        taxonomy_from_training_label(str(label or "")).base_genre
        for label in (excluded_styles or [])
    }
    return {style for style in styles if style and style not in excluded}


def _prepare_rekordbox_training_tracks(
        tracks, genre_settings, effective_styles, excluded_styles=None,
):
    """Normalize and retain only Rekordbox tracks allowed by effective styles."""
    effective = set(effective_styles or [])
    excluded = set(excluded_styles or [])
    prepared_tracks = []
    for track in tracks or []:
        base_style = _rekordbox_track_base_style(track, genre_settings)
        if not base_style or base_style in excluded or base_style not in effective:
            continue
        prepared = dict(track)
        prepared["genre"] = base_style
        prepared["_training_base_genre"] = base_style
        prepared_tracks.append(prepared)
    return prepared_tracks


def _select_training_pool_sources(
        local_counts, rekordbox_by_style, max_per_style, min_per_style,
):
    """Apply the existing source-mix and minimum-size rules without ML work."""
    local_counts = Counter(local_counts or {})
    rekordbox_by_style = {
        str(style): list(rows or [])
        for style, rows in (rekordbox_by_style or {}).items()
    }
    local_limits = {}
    selected_rekordbox_tracks = []
    source_mix_report = {}
    for style in sorted(set(local_counts) | set(rekordbox_by_style)):
        local_target, rekordbox_target = _training_source_mix_targets(
            local_counts[style],
            len(rekordbox_by_style.get(style, [])),
            max_per_style,
        )
        if local_target + rekordbox_target < int(min_per_style):
            local_target = 0
            rekordbox_target = 0
        local_limits[style] = local_target
        ranked_rekordbox = sorted(
            rekordbox_by_style.get(style, []),
            key=lambda track: hashlib.sha1(
                os.path.normcase(os.path.abspath(track.get("path", ""))).encode(
                    "utf-8", "surrogatepass"
                )
            ).hexdigest(),
        )
        selected_rekordbox_tracks.extend(ranked_rekordbox[:rekordbox_target])
        source_mix_report[style] = {
            "local_available": int(local_counts[style]),
            "rekordbox_available": len(rekordbox_by_style.get(style, [])),
            "local_target": local_target,
            "rekordbox_target": rekordbox_target,
            "total_target": local_target + rekordbox_target,
            "minimum_required": int(min_per_style),
            "status": (
                "ready"
                if local_target + rekordbox_target >= int(min_per_style)
                else "insufficient"
            ),
        }
    return local_limits, selected_rekordbox_tracks, source_mix_report


def _build_training_pool_preflight(
        effective_styles,
        dataset_builder_counts,
        local_counts,
        rekordbox_counts,
        capped_local_counts,
        capped_rekordbox_counts,
        expected_rows=None,
        minimum_required=200,
        samples_counts=None,
        capped_samples_counts=None,
        manual_counts=None,
        capped_manual_counts=None,
):
    """Describe and validate the final pool immediately before extraction."""
    effective = set(effective_styles or [])
    builder = Counter(dataset_builder_counts or {})
    local = Counter(local_counts or {})
    samples = Counter(samples_counts or {})
    manual = Counter(manual_counts or {})
    rekordbox = Counter(rekordbox_counts or {})
    capped_local = Counter(capped_local_counts or {})
    capped_samples = Counter(capped_samples_counts or {})
    capped_manual = Counter(capped_manual_counts or {})
    capped_rekordbox = Counter(capped_rekordbox_counts or {})
    expected = {
        str(row.get("style")): row
        for row in (expected_rows or [])
        if row.get("style")
    }
    styles = sorted(
        effective | set(builder) | set(local) | set(rekordbox)
        | set(capped_local) | set(capped_rekordbox) | set(expected)
    )
    rows = []
    issues = []
    for style in styles:
        expected_row = expected.get(style, {})
        after_cap = int(capped_local[style] + capped_rekordbox[style])
        row = {
            "style": style,
            "effective": style in effective,
            "dataset_builder": int(builder[style]),
            "samples": int(samples[style]),
            "manual_review": int(manual[style]),
            "samples_and_builder": int(local[style]),
            "rekordbox": int(rekordbox[style]),
            "combined": int(local[style] + rekordbox[style]),
            "after_cap": after_cap,
            "after_cap_local": int(capped_local[style]),
            "after_cap_samples": int(capped_samples[style]),
            "after_cap_manual_review": int(capped_manual[style]),
            "after_cap_builder": max(
                0, int(
                    capped_local[style]
                    - capped_samples[style]
                    - capped_manual[style]
                )
            ),
            "after_cap_rekordbox": int(capped_rekordbox[style]),
            "preview_selected": int(expected_row.get("selected_tracks", 0) or 0),
            "preview_builder": int(expected_row.get("builder_tracks", 0) or 0),
            "preview_samples": int(expected_row.get("samples_tracks", 0) or 0),
            "preview_rekordbox": int(
                expected_row.get("selected_rekordbox_tracks", 0) or 0
            ),
        }
        rows.append(row)
        preview_enabled = bool(expected_row.get("enabled", True))
        if preview_enabled and row["preview_selected"] > 0:
            if style not in effective:
                issues.append(
                    f"{style}: preview выбрал стиль, но его нет в effective trainable styles"
                )
            if row["after_cap"] == 0:
                issues.append(
                    f"{style}: preview обещал {row['preview_selected']} треков, "
                    "но после фактической подготовки pool осталось 0"
                )
        if (
            preview_enabled
            and row["preview_builder"] > 0
            and row["dataset_builder"] == 0
        ):
            issues.append(
                f"{style}: preview обещал dataset_builder "
                f"({row['preview_builder']}), но фактический источник дал 0"
            )
        if (
            preview_enabled
            and row["preview_samples"] > 0
            and row["samples"] == 0
        ):
            issues.append(
                f"{style}: preview обещал Samples "
                f"({row['preview_samples']}), но фактический источник дал 0"
            )
        if (
            preview_enabled
            and row["preview_rekordbox"] > 0
            and row["rekordbox"] == 0
        ):
            issues.append(
                f"{style}: preview обещал Rekordbox "
                f"({row['preview_rekordbox']}), но фактический источник дал 0"
            )
        if (
            style in effective
            and row["combined"] >= int(minimum_required)
            and row["after_cap"] == 0
        ):
            issues.append(
                f"{style}: доступно {row['combined']} треков, но cap сформировал 0"
            )
    return {
        "checked_at": datetime.datetime.now().isoformat(),
        "passed": not issues,
        "minimum_required": int(minimum_required),
        "effective_styles": sorted(effective),
        "rows": rows,
        "issues": issues,
    }


def _rekordbox_track_base_style(track, genre_settings):
    raw_genre = get_track_val(track, "raw_genre") or get_track_val(track, "Genre")
    fallback = track.get("genre") or normalize_genre_rekordbox(raw_genre, genre_settings)
    return parse_track_taxonomy(
        raw_genre=raw_genre,
        fallback_genre=fallback,
        title=get_track_val(track, "title"),
        artist=get_track_val(track, "artist"),
        path=track.get("path", ""),
    ).base_genre


def _merge_counts_by_base_style(counts):
    merged = Counter()
    for label, count in Counter(counts or {}).items():
        base_style = taxonomy_from_training_label(str(label)).base_genre
        if base_style:
            merged[base_style] += int(count)
    return merged


def _cap_training_tasks_by_style(
        tasks,
        max_per_style=1200,
        priority_path_keys=None,
        per_style_limits=None,
):
    """До извлечения признаков равномерно выбирает треки из разных папок каждого стиля."""
    maximum = max(100, min(5000, int(max_per_style or 1200)))
    priority_path_keys = {
        os.path.normcase(os.path.abspath(path)) for path in (priority_path_keys or set())
    }
    per_style_limits = per_style_limits if isinstance(per_style_limits, dict) else {}
    grouped = {}
    for task in tasks:
        grouped.setdefault(str(task[1]), []).append(task)

    selected = []
    per_style = {}
    for style in sorted(grouped):
        style_tasks = grouped[style]
        style_limit = max(0, min(maximum, int(per_style_limits.get(style, maximum))))
        priorities = [
            task for task in style_tasks
            if os.path.normcase(os.path.abspath(task[0])) in priority_path_keys
        ]
        priorities.sort(key=_stable_task_order)
        chosen = priorities[:style_limit]
        chosen_paths = {os.path.normcase(os.path.abspath(task[0])) for task in chosen}

        buckets = {}
        for task in style_tasks:
            key = os.path.normcase(os.path.abspath(task[0]))
            if key in chosen_paths:
                continue
            folder = os.path.normcase(os.path.abspath(os.path.dirname(task[0])))
            buckets.setdefault(folder, []).append(task)
        for bucket in buckets.values():
            bucket.sort(key=_stable_task_order)

        folder_order = sorted(buckets)
        cursor = 0
        while len(chosen) < style_limit and folder_order:
            next_order = []
            for folder in folder_order:
                bucket = buckets[folder]
                if cursor < len(bucket) and len(chosen) < style_limit:
                    chosen.append(bucket[cursor])
                if cursor + 1 < len(bucket):
                    next_order.append(folder)
            cursor += 1
            folder_order = next_order

        selected.extend(chosen)
        per_style[style] = {
            "available": len(style_tasks),
            "selected": len(chosen),
            "dropped": max(0, len(style_tasks) - len(chosen)),
            "limit": style_limit,
            "folders": len({os.path.normcase(os.path.abspath(os.path.dirname(task[0]))) for task in chosen}),
            "priority_tracks": min(len(priorities), len(chosen)),
        }

    selected.sort(key=lambda task: (str(task[1]), _stable_task_order(task)))
    return selected, {
        "max_tracks_per_style": maximum,
        "before": len(tasks),
        "after": len(selected),
        "dropped": max(0, len(tasks) - len(selected)),
        "per_style": per_style,
    }


def _reference_samples_dir(dataset_settings=None):
    """Resolve the optional curated reference-samples source."""
    settings = dataset_settings or get_training_dataset_settings()
    configured = str(settings.get("reference_samples_path", "") or "").strip()
    return Path(resolve_project_path(configured)) if configured else Path(SAMPLES_DIR)


def _rekordbox_training_enabled(dataset_settings, librosa_params):
    value = (dataset_settings or {}).get("use_rekordbox_training")
    return bool(librosa_params.get("use_rekordbox")) if value is None else bool(value)


def _sample_style_counts(genre_settings, samples_dir=None):
    counts = Counter()
    samples_dir = os.fspath(samples_dir or SAMPLES_DIR)
    if not os.path.isdir(samples_dir):
        return counts
    for folder in os.listdir(samples_dir):
        folder_path = os.path.join(samples_dir, folder)
        if not os.path.isdir(folder_path):
            continue
        style = normalize_genre(folder, genre_settings)
        is_trainable = any(
            (value.get("genre") == style and value.get("is_trainable", True))
            if isinstance(value, dict) else value == style
            for value in genre_settings.values()
        )
        if style == "Other" or not is_trainable:
            continue
        try:
            counts[style] += sum(name.lower().endswith(".mp3") for name in os.listdir(folder_path))
        except OSError:
            continue
    return counts


def _rekordbox_preview_style_counts(json_path, genre_settings):
    """Count labelled Rekordbox styles without probing audio or network paths.

    The dataset-plan endpoint is informational and must stay cheap.  Actual
    path validation is deliberately retained in ``load_rekordbox_json_tracks``
    and therefore still runs before feature extraction starts.
    """
    try:
        stat = os.stat(json_path)
    except OSError:
        return Counter()
    try:
        settings_payload = json.dumps(
            genre_settings or {}, ensure_ascii=False, sort_keys=True, default=str,
        )
    except (TypeError, ValueError):
        settings_payload = repr(genre_settings)
    cache_key = (
        os.path.abspath(os.fspath(json_path)),
        int(stat.st_mtime_ns),
        int(stat.st_size),
        hashlib.sha1(settings_payload.encode("utf-8", "surrogatepass")).hexdigest(),
    )

    with _REKORDBOX_PREVIEW_COUNTS_LOCK:
        if _REKORDBOX_PREVIEW_COUNTS_CACHE.get("key") == cache_key:
            return Counter(_REKORDBOX_PREVIEW_COUNTS_CACHE.get("counts") or {})

        with open(json_path, "r", encoding="utf-8") as source:
            rows = json.load(source)
        if not isinstance(rows, list):
            rows = []

        counts = Counter()
        for track in rows:
            if not isinstance(track, dict):
                continue
            raw_genre = str(get_track_val(track, "Genre") or "").strip()
            if not raw_genre:
                continue
            base_style = _rekordbox_track_base_style(track, genre_settings)
            if base_style and base_style != "Other":
                counts[base_style] += 1

        _REKORDBOX_PREVIEW_COUNTS_CACHE.update({
            "key": cache_key,
            "counts": dict(counts),
        })
        return Counter(counts)


def load_training_run_report():
    try:
        with open(TRAINING_RUN_REPORT_FILE, "r", encoding="utf-8") as source:
            data = json.load(source)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _save_training_run_report(report):
    TRAINING_RUN_REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = TRAINING_RUN_REPORT_FILE.with_suffix(".json.tmp")
    with open(temporary, "w", encoding="utf-8") as target:
        json.dump(report, target, ensure_ascii=False, indent=2)
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, TRAINING_RUN_REPORT_FILE)


def get_training_preflight_report():
    """Возвращает быстрый прогноз состава обучения без извлечения аудиопризнаков."""
    summary = dataset_summary()
    settings = get_training_dataset_settings()
    try:
        librosa_params = load_librosa_settings()
    except Exception:
        librosa_params = copy.deepcopy(DEFAULT_LIBROSA_SETTINGS)
    genre_settings = load_genre_settings()
    use_builder = bool(settings.get("use_dataset_builder", True))
    use_samples = bool(settings.get("use_reference_samples", True))
    use_rekordbox_training = _rekordbox_training_enabled(settings, librosa_params)
    samples_dir = _reference_samples_dir(settings)
    builder_counts = _merge_counts_by_base_style(
        (summary.get("style_track_counts") or {}) if use_builder else {}
    )
    if use_builder:
        for entry in list_review_entries():
            original_style = str(entry.get("review_true_style") or "")
            if not original_style or not entry.get("review_folder_id"):
                continue
            if entry.get("exclude_from_training"):
                builder_counts[original_style] = max(0, int(builder_counts.get(original_style, 0)) - 1)
                continue
            override_style = str(entry.get("style_override") or "")
            if override_style and override_style != original_style:
                builder_counts[original_style] = max(0, int(builder_counts.get(original_style, 0)) - 1)
                builder_counts[override_style] = int(builder_counts.get(override_style, 0)) + 1
    builder_folders = _merge_counts_by_base_style(
        (summary.get("style_folder_counts") or {}) if use_builder else {}
    )
    sample_counts = _merge_counts_by_base_style(
        _sample_style_counts(genre_settings, samples_dir) if use_samples else {}
    )
    maximum = settings["max_tracks_per_style"]
    minimum = settings["min_tracks_per_style"]
    excluded_styles = set(settings.get("excluded_styles") or [])
    effective_styles = _effective_trainable_styles(
        genre_settings,
        Counter(builder_counts) + Counter(sample_counts),
        excluded_styles,
    )
    rekordbox_counts = Counter()
    rekordbox_error = ""
    if use_rekordbox_training:
        try:
            rk_path = str(REKORDBOX_OUTPUT_DIR / "parsed_rekordbox.json")
            raw_counts = _rekordbox_preview_style_counts(rk_path, genre_settings)
            rk_cap = max(1, int(librosa_params.get("max_tracks_per_genre", 130) or 130))
            rekordbox_counts.update({
                style: min(count, rk_cap)
                for style, count in raw_counts.items()
                if style in effective_styles and style not in excluded_styles
            })
        except Exception as exc:
            rekordbox_error = str(exc)

    active_values, active_known = _active_model_snapshot()
    active = set(active_values)
    mandatory_excluded = {
        "Other",
        "Новогодние",
        *FAMILY_FALLBACK_ONLY_STYLES,
    }
    styles = sorted(
        active | effective_styles | set(builder_counts) | set(sample_counts)
        | set(rekordbox_counts) | excluded_styles
    )
    rows = []
    for style in styles:
        local_available = int(builder_counts[style] + sample_counts[style])
        selected_local, selected_rekordbox = _training_source_mix_targets(
            local_available,
            rekordbox_counts[style],
            maximum,
        )
        candidate_total = selected_local + selected_rekordbox
        if style in excluded_styles or style not in effective_styles:
            readiness = "disabled"
        elif candidate_total >= minimum:
            readiness = "ready"
        elif candidate_total:
            readiness = "insufficient"
        else:
            readiness = "no_data"
        selected_total = candidate_total if readiness == "ready" else 0
        rows.append({
            "style": style,
            "enabled": style in effective_styles and style not in excluded_styles,
            "mandatory_excluded": style in mandatory_excluded,
            "fallback_only": style in FAMILY_FALLBACK_ONLY_STYLES,
            "class_change": "retained" if style in active else ("new" if active_known else "unknown"),
            "confirmed_folders": int(builder_folders[style]),
            "builder_tracks": int(builder_counts[style]),
            "samples_tracks": int(sample_counts[style]),
            "rekordbox_tracks": int(rekordbox_counts[style]),
            "selected_local_tracks": selected_local if readiness == "ready" else 0,
            "selected_rekordbox_tracks": selected_rekordbox if readiness == "ready" else 0,
            "available_tracks": local_available + int(rekordbox_counts[style]),
            "candidate_tracks": candidate_total,
            "selected_tracks": selected_total,
            "readiness": readiness,
            "minimum_required": minimum,
        })
    return {
        "generated_at": datetime.datetime.now().isoformat(),
        "estimated": True,
        "max_tracks_per_style": maximum,
        "minimum_tracks_per_style": minimum,
        "excluded_styles": sorted(excluded_styles),
        "effective_trainable_styles": sorted(effective_styles),
        "mandatory_excluded_styles": sorted(mandatory_excluded),
        "active_classes": sorted(active),
        "active_classes_known": active_known,
        "rows": rows,
        "selected_total": sum(row["selected_tracks"] for row in rows),
        "source_settings": {
            "dataset_builder_enabled": use_builder,
            "reference_samples_enabled": use_samples,
            "reference_samples_path": str(samples_dir),
            "reference_samples_exists": samples_dir.is_dir(),
            "rekordbox_enabled": use_rekordbox_training,
        },
        "rekordbox_enabled": use_rekordbox_training,
        "rekordbox_error": rekordbox_error,
        "last_run": load_training_run_report(),
    }


def _prepare_quick_quality_pool():
    """Build the current capped pool without extracting any audio features."""
    librosa_params = load_librosa_settings()
    dataset_settings = get_training_dataset_settings()
    genre_settings = load_genre_settings()
    excluded_styles = set(dataset_settings.get("excluded_styles") or [])
    maximum = int(dataset_settings.get("max_tracks_per_style", 800) or 800)
    minimum = int(dataset_settings.get("min_tracks_per_style", 200) or 200)
    summary = dataset_summary()
    use_builder = bool(dataset_settings.get("use_dataset_builder", True))
    use_samples = bool(dataset_settings.get("use_reference_samples", True))
    use_rekordbox_training = _rekordbox_training_enabled(
        dataset_settings, librosa_params,
    )
    samples_dir = _reference_samples_dir(dataset_settings)
    builder_preview = _merge_counts_by_base_style(
        (summary.get("style_track_counts") or {}) if use_builder else {}
    )
    sample_preview = _merge_counts_by_base_style(
        _sample_style_counts(genre_settings, samples_dir) if use_samples else {}
    )
    effective = _effective_trainable_styles(
        genre_settings,
        Counter(builder_preview) + Counter(sample_preview),
        excluded_styles,
    )
    sample_rate = int(librosa_params.get("sample_rate", 22050) or 22050)
    offset = float(librosa_params.get("offset", 0) or 0)
    duration = float(librosa_params.get("duration", 30) or 30)

    local_tasks = []
    training_overrides = training_override_index()
    samples_dir = os.fspath(samples_dir)
    if use_samples and os.path.isdir(samples_dir):
        for folder in os.listdir(samples_dir):
            folder_path = os.path.join(samples_dir, folder)
            if not os.path.isdir(folder_path):
                continue
            style = taxonomy_from_training_label(
                normalize_genre(folder, genre_settings)
            ).base_genre
            is_legacy_trainable = any(
                (
                    value.get("genre") == style
                    and value.get("is_trainable", True)
                ) if isinstance(value, dict) else value == style
                for value in genre_settings.values()
            )
            if not style or style not in effective or not is_legacy_trainable:
                continue
            for filename in os.listdir(folder_path):
                if filename.lower().endswith(".mp3"):
                    local_tasks.append((
                        os.path.join(folder_path, filename), style, sample_rate,
                        offset, duration, librosa_params,
                    ))

    dataset_rows = list(iter_confirmed_training_tracks()) if use_builder else []
    for row in dataset_rows:
        style = taxonomy_from_training_label(
            str(row.get("base_genre") or "")
        ).base_genre
        if style and style in effective:
            local_tasks.append((
                row["path"], style, sample_rate, offset, duration,
                librosa_params,
            ))
    local_tasks = _deduplicate_items_by_exact_path(
        local_tasks, get_path_fn=lambda item: item[0], prefer_last=True,
    )

    task_index = {
        os.path.normcase(os.path.abspath(task[0])): index
        for index, task in enumerate(local_tasks)
    }
    for correction in iter_training_corrections(effective):
        style = taxonomy_from_training_label(
            str(correction.get("corrected_base_genre") or "")
        ).base_genre
        if not style or style not in effective:
            continue
        task = (
            correction["path"], style, sample_rate, offset, duration,
            librosa_params,
        )
        key = os.path.normcase(os.path.abspath(task[0]))
        if key in task_index:
            local_tasks[task_index[key]] = task
        else:
            task_index[key] = len(local_tasks)
            local_tasks.append(task)

    local_tasks = [
        task for task in local_tasks
        if str(task[1]) in effective and str(task[1]) not in excluded_styles
        and not (
            (find_training_override(training_overrides, task[0]) or {})
            .get("exclude_from_training")
        )
    ]
    prepared_rekordbox = []
    source_label_consistency = {
        "policy": "quarantine_confirmed_rekordbox_conflicts",
        "conflict_tracks": 0,
        "excluded_rekordbox_tracks": 0,
        "excluded_confirmed_tracks": 0,
        "conflicting_confirmed_paths": [],
    }
    if use_rekordbox_training:
        rk_path = str(REKORDBOX_OUTPUT_DIR / "parsed_rekordbox.json")
        rows = load_rekordbox_json_tracks(rk_path, genre_settings)
        rows, source_label_consistency = _reconcile_confirmed_and_rekordbox_labels(
            dataset_rows,
            rows,
            genre_settings,
            load_config().get("music_dir"),
            effective_styles=effective,
            write_report=False,
        )
        conflicting_confirmed_paths = {
            os.path.normcase(os.path.abspath(path))
            for path in source_label_consistency["conflicting_confirmed_paths"]
        }
        if conflicting_confirmed_paths:
            local_tasks = [
                task for task in local_tasks
                if os.path.normcase(os.path.abspath(task[0]))
                not in conflicting_confirmed_paths
            ]
        prepared_rekordbox = _prepare_rekordbox_training_tracks(
            rows, genre_settings, effective, excluded_styles,
        )
        counts = Counter(track["genre"] for track in prepared_rekordbox)
        prepared_rekordbox = balance_rekordbox_tracks(
            prepared_rekordbox,
            list(counts),
            max_per_genre=int(
                librosa_params.get("max_tracks_per_genre", 130) or 130
            ),
        )
        limit = int(librosa_params.get("rekordbox_track_limit", 0) or 0)
        if limit > 0:
            prepared_rekordbox = prepared_rekordbox[:limit]
        prepared_rekordbox = _deduplicate_items_by_exact_path(
            prepared_rekordbox, get_path_fn=lambda item: item["path"],
        )
        prepared_rekordbox = [
            track for track in prepared_rekordbox
            if not (
                (find_training_override(training_overrides, track["path"]) or {})
                .get("exclude_from_training")
            )
        ]

    local_counts = Counter(str(task[1]) for task in local_tasks)
    rekordbox_by_style = defaultdict(list)
    for track in prepared_rekordbox:
        style = str(track.get("_training_base_genre") or "")
        if style in effective:
            rekordbox_by_style[style].append(track)
    local_limits, selected_rekordbox, source_mix = _select_training_pool_sources(
        local_counts, rekordbox_by_style, maximum, minimum,
    )
    local_tasks, cap_report = _cap_training_tasks_by_style(
        local_tasks,
        max_per_style=maximum,
        per_style_limits=local_limits,
    )
    rekordbox_tasks = [(
        track["path"], track["_training_base_genre"], sample_rate, offset,
        duration, librosa_params,
    ) for track in selected_rekordbox]
    tasks = _deduplicate_items_by_exact_path(
        [*local_tasks, *rekordbox_tasks],
        get_path_fn=lambda item: item[0],
        prefer_last=False,
    )
    return {
        "tasks": tasks,
        "librosa_params": librosa_params,
        "effective_styles": sorted(effective),
        "source_mix": source_mix,
        "source_label_consistency": source_label_consistency,
        "cap": cap_report,
        "dataset_builder_counts": dict(Counter(
            row.get("base_genre") for row in dataset_rows
            if row.get("base_genre")
        )),
    }


def quick_training_quality_assessment(progress_callback=None):
    """Estimate class quality from cached 134D features without model writes."""
    started_at = datetime.datetime.now()

    def progress(value, message):
        if progress_callback:
            progress_callback(int(value), str(message))

    progress(5, "Формирование текущего training pool")
    pool = _prepare_quick_quality_pool()
    tasks = pool["tasks"]
    if not tasks:
        raise ValueError("Текущая обучающая выборка пуста.")

    progress(20, "Проверка кэша 134D-признаков")
    signature = _training_feature_signature(pool["librosa_params"])
    cached = _load_training_feature_cache(tasks, signature)
    selected_counts = Counter(str(task[1]) for task in tasks)
    samples = []
    segment_rows = []
    labels = []
    paths = []
    for task in tasks:
        result = cached.get(_training_cache_key(task, signature))
        if not (isinstance(result, tuple) and len(result) == 4):
            continue
        features, segments, _cached_genre, cached_path = result
        features = np.asarray(features, dtype=float).reshape(-1)
        if features.size != 134:
            continue
        valid_segments = [
            np.asarray(row, dtype=float).reshape(-1)
            for row in (segments or [features])
            if np.asarray(row).size == 134
        ]
        if not valid_segments:
            valid_segments = [features]
        samples.append(features)
        segment_rows.append(valid_segments)
        labels.append(str(task[1]))
        paths.append(str(cached_path or task[0]))

    cached_counts = Counter(labels)
    cache_rows = []
    insufficient = []
    for style in sorted(selected_counts):
        selected = int(selected_counts[style])
        available = int(cached_counts[style])
        coverage = available / max(1, selected)
        cache_rows.append({
            "style": style,
            "selected": selected,
            "cached": available,
            "missing": max(0, selected - available),
            "coverage": coverage,
        })
        if available < 30 or coverage < 0.25:
            insufficient.append(style)
    if insufficient or len(cached_counts) < 2:
        return {
            "status": "cache_insufficient",
            "diagnostic_only": True,
            "generated_at": datetime.datetime.now().isoformat(),
            "message": (
                "Быстрая оценка не запущена: в кэше недостаточно 134D-"
                "признаков. Полное извлечение автоматически не запускалось."
            ),
            "insufficient_styles": insufficient,
            "cache": {"rows": cache_rows, "cached_total": len(samples),
                      "selected_total": len(tasks)},
        }

    progress(38, "Безопасная дедупликация кэшированных признаков")
    taxonomies = [
        {"training_source": "diagnostic_cache"} for _ in paths
    ]
    keep, dedup_report = _strict_deduplicate_training_rows(
        paths, samples, labels, taxonomies, write_report=False,
    )
    samples = np.asarray(samples, dtype=float)[keep]
    labels = np.asarray(labels, dtype=object)[keep]
    paths = np.asarray(paths, dtype=object)[keep].tolist()
    segment_rows = np.asarray(segment_rows, dtype=object)[keep].tolist()
    remaining = Counter(labels.tolist())
    too_small = sorted(style for style, count in remaining.items() if count < 30)
    if too_small or len(remaining) < 2:
        return {
            "status": "cache_insufficient",
            "diagnostic_only": True,
            "generated_at": datetime.datetime.now().isoformat(),
            "message": "После безопасной дедупликации недостаточно кэшированных данных.",
            "insufficient_styles": too_small,
            "cache": {"rows": cache_rows, "cached_total": len(samples),
                      "selected_total": len(tasks)},
            "dedup": dedup_report,
        }

    progress(50, "Group-aware разбиение train/validation")
    groups = _build_training_groups(paths, samples, labels, write_report=False)
    train_idx, threshold_idx, validation_idx = _three_way_grouped_indices(
        samples,
        labels,
        groups,
        holdout_fraction=float(
            pool["librosa_params"].get("validation_size", 0.2) or 0.2
        ),
        random_state=int(
            pool["librosa_params"].get("random_state", 42) or 42
        ),
    )
    validation_idx = np.concatenate([threshold_idx, validation_idx])

    progress(65, "Обучение диагностического lightweight RF")
    model = RandomForestClassifier(
        n_estimators=160,
        max_depth=18,
        min_samples_leaf=3,
        max_features="sqrt",
        class_weight="balanced_subsample",
        random_state=int(
            pool["librosa_params"].get("random_state", 42) or 42
        ),
        n_jobs=1,
    )
    model.fit(samples[train_idx], labels[train_idx])
    predicted = model.predict(samples[validation_idx])
    class_names = sorted(str(value) for value in model.classes_)
    report = classification_report(
        labels[validation_idx],
        predicted,
        labels=class_names,
        output_dict=True,
        zero_division=0,
    )

    progress(86, "Сравнение защищённых стилей с активной моделью")
    protected_styles, protected_known = _active_model_classes_readonly()
    protected = sorted(set(protected_styles) & set(class_names))
    active_metrics, active_comparison = _evaluate_active_model_on_current_validation(
        segment_rows, labels, validation_idx, protected,
    )
    per_class = []
    weak_styles = []
    for style in class_names:
        metrics = report.get(style) or {}
        row = {
            "style": style,
            "precision": float(metrics.get("precision", 0.0) or 0.0),
            "recall": float(metrics.get("recall", 0.0) or 0.0),
            "f1": float(metrics.get("f1-score", 0.0) or 0.0),
            "support": int(metrics.get("support", 0) or 0),
            "cached_tracks": int(remaining[style]),
            "protected": style in protected,
        }
        if style in active_metrics:
            row["active_recall"] = float(
                active_metrics[style].get("recall", 0.0) or 0.0
            )
            row["recall_delta"] = row["recall"] - row["active_recall"]
        if row["f1"] < 0.60 or row["recall"] < 0.55:
            weak_styles.append(style)
        per_class.append(row)

    finished_at = datetime.datetime.now()
    progress(100, "Быстрая диагностическая оценка завершена")
    return {
        "status": "completed",
        "diagnostic_only": True,
        "generated_at": finished_at.isoformat(),
        "started_at": started_at.isoformat(),
        "duration_seconds": (finished_at - started_at).total_seconds(),
        "message": "Оценка завершена; рабочая модель и quality gate не изменены.",
        "macro_f1": float(
            (report.get("macro avg") or {}).get("f1-score", 0.0) or 0.0
        ),
        "accuracy": float(report.get("accuracy", 0.0) or 0.0),
        "classes": class_names,
        "per_class": per_class,
        "weak_styles": weak_styles,
        "protected_styles_known": protected_known,
        "protected_comparison": active_comparison,
        "cache": {"rows": cache_rows, "cached_total": len(samples),
                  "selected_total": len(tasks)},
        "dedup": dedup_report,
        "split": {
            "train_tracks": int(len(train_idx)),
            "validation_tracks": int(len(validation_idx)),
            "group_aware": True,
        },
        "pipeline": {
            "auto_tune": False,
            "calibration": False,
            "hierarchy": False,
            "effnet": False,
            "feature_extraction": False,
            "model_saved": False,
            "quality_gate_changed": False,
        },
    }


def get_training_preparation_assistant():
    """Build a read-only cleanup preview from existing pipeline diagnostics."""
    preflight = get_training_preflight_report()
    summary = dataset_summary()
    problem_report = build_training_problem_folders()
    minimum = int(preflight.get("minimum_tracks_per_style", 200) or 200)
    warning_floor = max(minimum, int(round(minimum * 1.5)))
    folders = list(problem_report.get("items") or [])

    recommendations = []
    proposed_by_style = Counter()
    style_problem_stats = defaultdict(lambda: {
        "problem_folders": 0, "review_errors": 0, "leave": 0,
        "recommend_exclude": 0, "needs_review": 0,
    })
    for row in folders:
        style = str(row.get("base_style") or "")
        historical_review_errors = int(row.get("review_queue_tracks", 0) or 0) + int(row.get("validation_errors", 0) or 0)
        unresolved_review = int(row.get("pending_disputed_tracks", 0) or 0)
        persistent_pairs = sum(
            int(pair.get("count", 0) or 0) >= 3
            for pair in (row.get("confusion_pairs") or [])
        )
        strong_signals = sum((
            unresolved_review >= 12,
            float(row.get("disputed_percent", 0) or 0) >= 8.0,
            persistent_pairs >= 2,
            bool(row.get("mixed_name_warning")),
            int(row.get("label_conflicts", 0) or 0) >= 2,
        ))
        if bool(row.get("review_complete")):
            recommendation = "leave"
            explanation = "Все связанные спорные треки получили пользовательское решение; историческая диагностика сохранена."
        elif strong_signals >= 2:
            recommendation = "recommend_exclude"
            explanation = "Несколько сильных признаков смешанного источника."
        elif row.get("risk") == "low" and unresolved_review <= 1 and not row.get("mixed_name_warning"):
            recommendation = "leave"
            explanation = "Сигнал слабый; существующий pipeline уже обработает дубликаты и конфликты треков."
        else:
            recommendation = "needs_review"
            explanation = "Есть диагностические сигналы, но их недостаточно для безопасного исключения папки."
        item = {
            "id": row.get("id"),
            "path": row.get("path"),
            "relative_path": row.get("relative_path"),
            "style": style,
            "tracks": int(row.get("training_tracks", 0) or 0),
            "risk": row.get("risk"),
            "review_errors": unresolved_review,
            "historical_review_errors": historical_review_errors,
            "review_complete": bool(row.get("review_complete")),
            "disputed_percent": float(row.get("disputed_percent", 0) or 0),
            "recommendation": recommendation,
            "explanation": explanation,
        }
        recommendations.append(item)
        if style:
            style_problem_stats[style]["problem_folders"] += 1
            style_problem_stats[style]["review_errors"] += unresolved_review
            style_problem_stats[style][recommendation] += 1
            if recommendation == "recommend_exclude":
                proposed_by_style[style] += item["tracks"]

    preflight_rows = {str(row.get("style")): row for row in preflight.get("rows") or []}
    confirmed_tracks = Counter(summary.get("style_track_counts") or {})
    styles = []
    blocked_styles = set()
    for style in sorted(set(preflight_rows) | set(confirmed_tracks) | set(style_problem_stats)):
        plan_row = preflight_rows.get(style, {})
        before = int(confirmed_tracks.get(style, 0))
        proposed = min(before, int(proposed_by_style.get(style, 0)))
        after = max(0, before - proposed)
        enabled = bool(plan_row.get("enabled", True))
        style_floor = max(warning_floor, int(math.ceil(before * 0.5)))
        too_small = bool(enabled and proposed and after < style_floor)
        if too_small:
            blocked_styles.add(style)
        stats = style_problem_stats[style]
        styles.append({
            "style": style,
            "enabled": enabled,
            "training_tracks_before": before,
            "training_tracks_after": after,
            "problem_folders": stats["problem_folders"],
            "clean_folders": max(0, int(plan_row.get("confirmed_folders", 0) or 0) - stats["problem_folders"]),
            "review_errors": stats["review_errors"],
            "leave_folders": stats["leave"],
            "recommended_exclusions": stats["recommend_exclude"],
            "needs_review": stats["needs_review"],
            "warning": (
                f"После исключения останется только {after} из {before} треков; автоматическое применение для этого стиля заблокировано."
                if too_small else ""
            ),
        })

    safe_ids = [
        row["id"] for row in recommendations
        if row["recommendation"] == "recommend_exclude"
        and row["style"] not in blocked_styles
        and row.get("id")
    ]
    safe_set = set(safe_ids)
    for row in recommendations:
        row["safe_to_apply"] = row.get("id") in safe_set

    status_tracks = summary.get("status_track_counts") or {}
    selection = (preflight.get("last_run") or {}).get("selection") or {}
    strict = selection.get("strict_dedup") or {}
    last_run = preflight.get("last_run") or {}
    expected_feature_tracks = int(selection.get("after", 0) or 0) + int(selection.get("rekordbox_selected", 0) or 0)
    pipeline_automatic = {
        "unconfirmed_tracks": sum(int(status_tracks.get(key, 0) or 0) for key in ("suggested", "ambiguous", "unmapped")),
        "excluded_tracks": int(status_tracks.get("excluded", 0) or 0),
        "dedup_tracks_last_run": int(strict.get("dropped_duplicates", 0) or 0),
        "fingerprint_conflicts_last_run": int(strict.get("dropped_conflicts", 0) or 0),
        "processing_errors_last_run": max(0, expected_feature_tracks - int(strict.get("input_tracks", 0) or 0)),
    }
    preview_payload = [
        (row["id"], row["style"], row["tracks"])
        for row in recommendations if row.get("id") in safe_set
    ]
    return {
        "generated_at": datetime.datetime.now().isoformat(),
        "preview_token": hashlib.sha256(
            json.dumps(preview_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:20],
        "safe_folder_ids": safe_ids,
        "blocked_styles": sorted(blocked_styles),
        "recommendations": recommendations,
        "styles": styles,
        "pipeline_automatic": pipeline_automatic,
        "summary": {
            "tracks_before": sum(int(row.get("selected_tracks", 0) or 0) for row in preflight.get("rows") or []),
            "confirmed_tracks_before": sum(confirmed_tracks.values()),
            "recommended_leave_folders": sum(row["recommendation"] == "leave" for row in recommendations),
            "recommended_exclude_folders": sum(row["recommendation"] == "recommend_exclude" for row in recommendations),
            "safe_to_apply_folders": len(safe_ids),
            "needs_review_folders": sum(row["recommendation"] == "needs_review" for row in recommendations),
            "automatically_not_participating_tracks": sum(pipeline_automatic.values()),
        },
        "rules": {
            "folder_statuses": "В обучение допускаются только confirmed-папки с базовым стилем.",
            "dedup": "Точные аудиокопии удаляются существующим strict dedup после извлечения признаков.",
            "fingerprint_conflicts": "Одинаковое аудио с противоречащими метками карантинится существующим pipeline.",
            "processing_errors": "Файлы без корректных признаков пропускаются существующим обработчиком.",
        },
    }


def apply_training_preparation_assistant(folder_ids, preview_token):
    """Apply only the safe folder exclusions listed by a fresh preview."""
    preview = get_training_preparation_assistant()
    if str(preview_token or "") != preview["preview_token"]:
        raise ValueError("План изменился. Обновите preview перед применением.")
    requested = {str(value) for value in (folder_ids or []) if value}
    allowed = set(preview["safe_folder_ids"])
    if not requested:
        raise ValueError("В безопасном плане нет выбранных папок для исключения")
    if not requested <= allowed:
        raise ValueError("Запрошены папки, которых нет в подтверждённом безопасном preview")
    result = update_training_folders(sorted(requested), status="excluded")
    return {
        **result,
        "applied_folder_ids": sorted(requested),
        "message": "Безопасные рекомендации применены. Музыкальные файлы не изменялись.",
    }


def _build_training_run_report(
        passed,
        active_before,
        active_before_known,
        candidate_classes,
        validation_report,
        rejection_policy_report,
        class_thresholds,
        skipped_styles,
        raw_counts,
    selection_report,
    quality_gate_report,
    threshold_diagnostics=None,
    hierarchy_validation_report=None,
):
    active_before = set(str(value) for value in active_before)
    candidates = set(str(value) for value in candidate_classes)
    per_class_rejection = rejection_policy_report.get("per_class", {}) if isinstance(rejection_policy_report, dict) else {}
    threshold_diagnostics = threshold_diagnostics if isinstance(threshold_diagnostics, dict) else {}
    rows = []
    for style in sorted(active_before | candidates | set(skipped_styles or {})):
        metrics = validation_report.get(style, {}) if isinstance(validation_report, dict) else {}
        accepted = per_class_rejection.get(style, {})
        if style in candidates:
            if not active_before_known:
                change = "activated" if passed else "candidate"
            else:
                change = "retained" if style in active_before else ("added" if passed else "candidate_new")
        elif style in active_before:
            change = "kept_old_model" if not passed else "removed"
        else:
            change = "skipped"
        rows.append({
            "style": style,
            "change": change,
            "tracks_before_balance": int((raw_counts or {}).get(style, 0)),
            "skipped_tracks": int((skipped_styles or {}).get(style, 0)),
            "precision": float(metrics.get("precision", 0.0) or 0.0),
            "recall": float(metrics.get("recall", 0.0) or 0.0),
            "f1": float(metrics.get("f1-score", 0.0) or 0.0),
            "validation_support": int(metrics.get("support", 0) or 0),
            "accepted_precision": float(accepted.get("accepted_precision", 0.0) or 0.0),
            "accepted_tracks": int(accepted.get("accepted_tracks", 0) or 0),
            "threshold": float((class_thresholds or {}).get(style, 0.0) or 0.0),
            "threshold_status": str(
                (threshold_diagnostics.get(style) or {}).get("status", "")
            ),
        })
    return {
        "completed_at": datetime.datetime.now().isoformat(),
        "status": "accepted" if passed else "rejected",
        "working_model_changed": bool(passed),
        "active_before_known": bool(active_before_known),
        "active_before": sorted(active_before),
        "candidate_classes": sorted(candidates),
        "active_after": sorted(candidates if passed else active_before)
        if passed or active_before_known else None,
        "added_styles": sorted(candidates - active_before) if passed and active_before_known else [],
        "candidate_new_styles": sorted(candidates - active_before) if not passed and active_before_known else [],
        "activated_styles": sorted(candidates) if passed and not active_before_known else [],
        "candidate_styles": sorted(candidates) if not passed and not active_before_known else [],
        "retained_styles": sorted(candidates & active_before) if active_before_known else [],
        "removed_styles": sorted(active_before - candidates) if passed else [],
        "skipped_styles": dict(skipped_styles or {}),
        "selection": selection_report or {},
        "quality_gate": quality_gate_report or {},
        "hierarchy_validation": hierarchy_validation_report or {},
        "rows": rows,
    }

def train_genre_model(force=False, global_state=None):
    skipped_tracks = []
    active_classes_before, active_classes_before_known = _active_model_snapshot()
    active_style_metrics_before = {}
    active_model_comparison = {"available": False, "reason": "not_evaluated"}
    training_selection_report = {}

    logger.info("=== DEBUG INFO ===")
    logger.info("Current working directory: %s", os.getcwd())
    logger.info("Running as user: %s", getpass.getuser())

    def set_progress(
            x=None, *, phase=None, message=None, processed=None, total=None,
            status=None,
    ):
        if global_state is None:
            return
        detail = global_state.setdefault("training_detail", {})
        if x is not None:
            progress_value = max(0, min(100, int(x)))
            global_state["training_progress"] = progress_value
            detail["progress"] = progress_value
        if phase is not None:
            detail["phase"] = phase
        if message is not None:
            detail["message"] = message
        if processed is not None:
            detail["processed"] = max(0, int(processed))
        if total is not None:
            detail["total"] = max(0, int(total))
        if status is not None:
            detail["status"] = status
        detail["updated_at"] = datetime.datetime.now().isoformat()

    def stop_requested():
        if global_state is None:
            return False
        stop_event = global_state.get("training_stop_event")
        return bool(stop_event is not None and stop_event.is_set())

    def finish_stopped():
        current_progress = 0
        if global_state is not None:
            current_progress = int(global_state.get("training_progress", 0) or 0)
        logger.info("[TRAIN] Обучение остановлено пользователем; рабочая модель не изменена.")
        set_progress(
            current_progress,
            phase="stopped",
            message="Обучение остановлено; рабочая модель не изменена",
            status="stopped",
        )

    def set_feature_progress(processed_tracks, total_tracks):
        ratio = processed_tracks / max(1, total_tracks)
        set_progress(
            5 + int(ratio * 70),
            phase="features",
            message=f"Извлечение аудиопризнаков: {processed_tracks} из {total_tracks}",
            processed=processed_tracks,
            total=total_tracks,
            status="running",
        )

    set_progress(
        0,
        phase="preparing",
        message="Проверка источников и настроек обучения",
        processed=0,
        total=0,
        status="running",
    )
    if os.path.exists(MODEL_PATH) and not force:
        logger.info("Model exists; skipping retraining.")
        set_progress(100)
        return

    # --- Проверка наличия папки Samples и треков ---
    try:
        preliminary_librosa_params = load_librosa_settings()
    except Exception:
        preliminary_librosa_params = copy.deepcopy(DEFAULT_LIBROSA_SETTINGS)
    training_dataset_settings = get_training_dataset_settings()
    use_builder = bool(training_dataset_settings.get("use_dataset_builder", True))
    use_samples = bool(training_dataset_settings.get("use_reference_samples", True))
    samples_dir_path = _reference_samples_dir(training_dataset_settings)
    has_dataset_tracks = use_builder and has_confirmed_training_tracks()
    use_rekordbox_training = _rekordbox_training_enabled(
        training_dataset_settings, preliminary_librosa_params,
    )
    has_rekordbox_tracks = bool(
        use_rekordbox_training
        and (REKORDBOX_OUTPUT_DIR / "parsed_rekordbox.json").is_file()
    )
    has_external_training_source = has_dataset_tracks or has_rekordbox_tracks

    samples_dir = os.fspath(samples_dir_path)
    has_reference_source = use_samples and samples_dir_path.is_dir()
    if not has_reference_source and not has_external_training_source:
        logger.error("Не найден ни один включённый источник обучения.")
        if global_state is not None:
            global_state["training_error"] = (
                "Не найден ни один включённый источник обучения. Включите "
                "подтверждённые папки, эталонную выборку или Rekordbox."
            )
        set_progress(100)
        return

    folders = (
        [f for f in os.listdir(samples_dir) if os.path.isdir(os.path.join(samples_dir, f))]
        if has_reference_source else []
    )
    mp3_found = any(
        any(file.lower().endswith(".mp3") for file in os.listdir(os.path.join(samples_dir, folder)))
        for folder in folders
    )
    if use_samples and not mp3_found and not has_external_training_source:
        logger.error("В эталонной выборке нет ни одного трека для обучения!")
        if global_state is not None:
            global_state["training_error"] = (
                "В эталонной выборке нет MP3. Выберите другой источник обучения."
            )
        set_progress(100)
        return

    # Загружаем актуальные настройки librosa из файла
    librosa_params = preliminary_librosa_params

    if stop_requested():
        finish_stopped()
        return
    set_progress(
        1,
        phase="preparing",
        message="Загрузка источников и формирование списка треков",
        status="running",
    )

    logger.info("Librosa settings loaded: %s", librosa_params)
    logger.info("librosa_params for training: %s", librosa_params)
    logger.info("Rekordbox use for genre training: %s", use_rekordbox_training)

    # Извлекаем все нужные параметры из настроек
    offset = librosa_params.get("offset", 0)
    duration = librosa_params.get("duration", 30)
    rekordbox_track_limit = librosa_params.get("rekordbox_track_limit", 10000)
    min_tracks_per_genre = librosa_params.get("min_tracks_per_genre", 130)
    max_tracks_per_genre = librosa_params.get("max_tracks_per_genre", 130)
    sample_rate = librosa_params.get("sample_rate", 22050)
    style_maximum = training_dataset_settings.get("max_tracks_per_style", 800)
    style_minimum = training_dataset_settings.get("min_tracks_per_style", 200)
    excluded_styles = set(training_dataset_settings.get("excluded_styles") or [])
    genre_settings = load_genre_settings()
    training_dataset_summary = dataset_summary()
    builder_style_counts_preview = _merge_counts_by_base_style(
        (training_dataset_summary.get("style_track_counts") or {}) if use_builder else {}
    )
    reference_style_counts_preview = _merge_counts_by_base_style(
        _sample_style_counts(genre_settings, samples_dir_path) if use_samples else {}
    )
    effective_trainable_styles = _effective_trainable_styles(
        genre_settings,
        Counter(builder_style_counts_preview) + Counter(reference_style_counts_preview),
        excluded_styles,
    )
    # Reuse one confirmed-row snapshot for task creation and source consistency.
    dataset_rows = list(iter_confirmed_training_tracks()) if use_builder else []
    expected_preflight = get_training_preflight_report()

    # === Сбор признаков из папок ===
    samples = []
    segment_features_by_track = []
    labels = []
    norm_keys_list = []
    training_paths = []
    training_taxonomies = []
    genre_counter = {}
    samples_dir = os.fspath(samples_dir_path)
    train_features_dict = {}
    trainable_genres = set(effective_trainable_styles)
    logger.info("Trainable genres: %s", trainable_genres)

    folders = (
        [f for f in os.listdir(samples_dir) if os.path.isdir(os.path.join(samples_dir, f))]
        if use_samples and os.path.isdir(samples_dir) else []
    )
    # Посчитаем общее количество файлов в samples
    total_files = sum(
        len([file for file in os.listdir(os.path.join(samples_dir, folder)) if file.lower().endswith(".mp3")])
        for folder in folders
    )

    # Плейсхолдер для balanced_rk_tracks (чтобы посчитать общее число заранее)
    balanced_rk_tracks = []
    source_label_consistency = {
        "policy": "quarantine_confirmed_rekordbox_conflicts",
        "input_rekordbox_tracks": 0,
        "confirmed_overlap_tracks": 0,
        "conflict_tracks": 0,
        "excluded_rekordbox_tracks": 0,
        "excluded_confirmed_tracks": 0,
        "conflicting_confirmed_paths": [],
        "conflict_pairs": [],
        "items": [],
        "report_file": str(TRAINING_SOURCE_LABEL_CONFLICTS_FILE),
    }
    if use_rekordbox_training:
        set_progress(
            2,
            phase="preparing",
            message="Подготовка и нормализация данных Rekordbox",
            status="running",
        )
        rk_json = str(REKORDBOX_OUTPUT_DIR / "parsed_rekordbox.json")
        genre_settings = load_genre_settings()  # на всякий случай
        rk_tracks = []
        if os.path.exists(rk_json):
            try:
                rk_tracks = load_rekordbox_json_tracks(rk_json, genre_settings)
                logger.info(f"Начинаю обработку Reckordbox, всего треков: {len(rk_tracks)}")
            except Exception as e:
                logger.error(f"[Rekordbox] Не удалось загрузить JSON: {e}")
        # Сначала переводим многомерные категории Rekordbox в базовый акустический стиль.
        # Russian Remixes становится Club House + Russian, а служебные категории не
        # создают отдельные классы RF.
        rk_tracks, source_label_consistency = _reconcile_confirmed_and_rekordbox_labels(
            dataset_rows,
            rk_tracks,
            genre_settings,
            load_config().get("music_dir"),
            effective_styles=effective_trainable_styles,
        )
        if source_label_consistency["conflict_tracks"]:
            conflicting_confirmed_paths = {
                os.path.normcase(os.path.abspath(path))
                for path in source_label_consistency["conflicting_confirmed_paths"]
            }
            dataset_rows = [
                row for row in dataset_rows
                if os.path.normcase(os.path.abspath(row["path"]))
                not in conflicting_confirmed_paths
            ]
            logger.warning(
                "[TRAIN][SOURCE LABEL CONFLICT] До ручной проверки исключены обе "
                "противоречащие метки: %s Rekordbox и %s подтверждённых треков. "
                "Сохранённые данные не изменены. Отчёт: %s",
                source_label_consistency["conflict_tracks"],
                source_label_consistency["excluded_confirmed_tracks"],
                source_label_consistency["report_file"],
            )
        prepared_rk_tracks = _prepare_rekordbox_training_tracks(
            rk_tracks,
            genre_settings,
            effective_trainable_styles,
            excluded_styles,
        )
        genre_counts = pd.Series([track["genre"] for track in prepared_rk_tracks]).value_counts()
        top_genres = list(genre_counts.index)
        balanced_rk_tracks = balance_rekordbox_tracks(
            prepared_rk_tracks,
            top_genres,
            max_per_genre=max_tracks_per_genre,
            logger=logger
        )
        # Лимитируем итоговый список треков после балансировки
        if rekordbox_track_limit and rekordbox_track_limit > 0:
            balanced_rk_tracks = balanced_rk_tracks[:rekordbox_track_limit]
        # At this stage only the exact same path is a certain duplicate.
        # Equal filenames in different DJ pools may be different edits/remixes;
        # acoustic deduplication below decides those after feature extraction.
        balanced_rk_tracks = _deduplicate_items_by_exact_path(
            balanced_rk_tracks,
            get_path_fn=lambda item: item["path"],
        )

    if stop_requested():
        finish_stopped()
        return

    total_rekordbox = len(balanced_rk_tracks)
    total = total_files + total_rekordbox
    processed = 0

    # --- обработка папочных треков ---
    import concurrent.futures
    import multiprocessing

    # Собираем все треки для параллельной обработки
    all_sample_files = []
    dataset_training_taxonomies = {}
    for folder in folders:
        genre = normalize_genre(folder, genre_settings)
        if genre == "Other":
            if is_log_type_enabled("model"):
                model_logger.warning(f"Пропускаю папку '{folder}': не удалось определить жанр.")
            continue

        # Проверяем, является ли жанр обучаемым (по trainable_genres)
        is_trainable = False
        for key, val in genre_settings.items():
            val_genre = val["genre"] if isinstance(val, dict) else val
            if val_genre == genre:
                is_trainable = val.get("is_trainable", True) if isinstance(val, dict) else True
                break
        if not is_trainable:
            logger.info(f"Пропускаю папку '{folder}' (жанр '{genre}'), не отмечен для обучения")
            continue
        folder_path = os.path.join(samples_dir, folder)
        if is_log_type_enabled("model"):
            model_logger.debug("folder: %s → genre: %s", folder, genre)
        for file in os.listdir(folder_path):
            if file.lower().endswith(".mp3"):
                path = os.path.join(folder_path, file)
                all_sample_files.append((path, genre, sample_rate, offset, duration, librosa_params))

    reference_path_keys = {
        os.path.normcase(os.path.abspath(task[0])) for task in all_sample_files
    }

    # Подтверждённые папки из конструктора дополняют legacy Samples. Файлы
    # используются на месте: ничего не копируется и не перемещается.
    set_progress(
        3,
        phase="preparing",
        message="Чтение подтверждённой обучающей выборки",
        status="running",
    )
    for dataset_row in dataset_rows:
        dataset_path = dataset_row["path"]
        dataset_genre = dataset_row["base_genre"]
        all_sample_files.append((
            dataset_path,
            dataset_genre,
            sample_rate,
            offset,
            duration,
            librosa_params,
        ))
        dataset_training_taxonomies[
            os.path.normcase(os.path.abspath(dataset_path))
        ] = dataset_row["taxonomy"]
        trainable_genres.add(dataset_genre)
    if dataset_rows:
        logger.info(
            "[TRAINING DATASET] Добавлено подтверждённых треков: %s; стили: %s",
            len(dataset_rows),
            dict(Counter(row["base_genre"] for row in dataset_rows)),
        )

    builder_path_keys = {
        os.path.normcase(os.path.abspath(row["path"])) for row in dataset_rows
    }

    # A confirmed builder row overrides a legacy Samples row for the same
    # physical path.  Same-looking names elsewhere remain until acoustic dedup.
    all_sample_files = _deduplicate_items_by_exact_path(
        all_sample_files,
        get_path_fn=lambda item: item[0],
        prefer_last=True,
    )
    training_overrides = training_override_index()
    all_sample_files = [
        task for task in all_sample_files
        if not (
            (find_training_override(training_overrides, task[0]) or {})
            .get("exclude_from_training")
        )
    ]
    balanced_rk_tracks = [
        track for track in balanced_rk_tracks
        if not (
            (find_training_override(training_overrides, track.get("path", "")) or {})
            .get("exclude_from_training")
        )
    ]

    if stop_requested():
        finish_stopped()
        return

    # Параллельно извлекаем признаки
    # Confirmed review entries have priority over a folder label and may also
    # add an existing uploaded/library file to the next training run.
    trainable_base_genres = {
        taxonomy_from_training_label(str(genre)).base_genre
        for genre in trainable_genres
    }
    manual_training_taxonomies = dict(dataset_training_taxonomies)
    correction_path_keys = set()
    sample_index_by_path = {
        os.path.normcase(os.path.abspath(task[0])): index
        for index, task in enumerate(all_sample_files)
    }
    rekordbox_path_keys = {
        os.path.normcase(os.path.abspath(track["path"]))
        for track in balanced_rk_tracks
        if track.get("path")
    }
    for correction in iter_training_corrections(trainable_base_genres):
        correction_path = correction["path"]
        correction_key = os.path.normcase(os.path.abspath(correction_path))
        correction_path_keys.add(correction_key)
        corrected_genre = correction["corrected_base_genre"]
        corrected_task = (
            correction_path,
            corrected_genre,
            sample_rate,
            offset,
            duration,
            librosa_params,
        )
        if correction_key in sample_index_by_path:
            all_sample_files[sample_index_by_path[correction_key]] = corrected_task
        elif correction_key not in rekordbox_path_keys:
            sample_index_by_path[correction_key] = len(all_sample_files)
            all_sample_files.append(corrected_task)

        corrected_language = correction.get("corrected_language", "Auto")
        taxonomy = parse_track_taxonomy(
            fallback_genre=corrected_genre,
            path=correction_path,
            fallback_language=None if corrected_language == "Auto" else corrected_language,
        ).to_dict()
        taxonomy["base_genre"] = corrected_genre
        taxonomy["genre_family"] = parse_track_taxonomy(
            fallback_genre=corrected_genre
        ).genre_family
        if corrected_language != "Auto":
            taxonomy["language"] = corrected_language
        corrected_version = correction.get("corrected_version_type", "Auto")
        if corrected_version != "Auto":
            taxonomy["version_type"] = corrected_version
        taxonomy["dj_category"] = derive_dj_category(
            taxonomy["base_genre"], taxonomy["language"]
        )
        taxonomy["training_source"] = "manual_review"
        manual_training_taxonomies[correction_key] = taxonomy

    # Ограничиваем огромные классы до извлечения аудиопризнаков. Это сокращает
    # часы обработки и RAM, сохраняя разнообразие папок и ручные исправления.
    normalised_sample_files = []
    for task in all_sample_files:
        base_style = taxonomy_from_training_label(str(task[1])).base_genre
        if (
            not base_style
            or base_style in excluded_styles
            or base_style not in effective_trainable_styles
        ):
            continue
        normalised_sample_files.append((task[0], base_style, *task[2:]))
        trainable_genres.add(base_style)
    all_sample_files = normalised_sample_files
    local_counts = Counter(str(task[1]) for task in all_sample_files)
    samples_counts = Counter(
        str(task[1]) for task in all_sample_files
        if os.path.normcase(os.path.abspath(task[0])) in reference_path_keys
        and os.path.normcase(os.path.abspath(task[0])) not in builder_path_keys
    )
    manual_counts = Counter(
        str(task[1]) for task in all_sample_files
        if os.path.normcase(os.path.abspath(task[0])) in correction_path_keys
        and os.path.normcase(os.path.abspath(task[0])) not in builder_path_keys
        and os.path.normcase(os.path.abspath(task[0])) not in reference_path_keys
    )
    rekordbox_by_style = {}
    for track in balanced_rk_tracks:
        track_key = os.path.normcase(os.path.abspath(track.get("path", "")))
        manual_taxonomy = manual_training_taxonomies.get(track_key)
        base_style = (
            manual_taxonomy.get("base_genre")
            if isinstance(manual_taxonomy, dict) and manual_taxonomy.get("base_genre")
            else _rekordbox_track_base_style(track, genre_settings)
        )
        if (
            not base_style
            or base_style in excluded_styles
            or base_style not in effective_trainable_styles
        ):
            continue
        prepared_track = dict(track)
        prepared_track["_training_base_genre"] = base_style
        rekordbox_by_style.setdefault(base_style, []).append(prepared_track)
    local_limits, selected_rekordbox_tracks, source_mix_report = (
        _select_training_pool_sources(
            local_counts,
            rekordbox_by_style,
            style_maximum,
            style_minimum,
        )
    )
    trainable_genres.update(
        style for style, row in source_mix_report.items()
        if row.get("status") == "ready"
    )
    balanced_rk_tracks = selected_rekordbox_tracks
    total_rekordbox = len(balanced_rk_tracks)
    all_sample_files, training_selection_report = _cap_training_tasks_by_style(
        all_sample_files,
        max_per_style=style_maximum,
        priority_path_keys=correction_path_keys,
        per_style_limits=local_limits,
    )
    training_selection_report["source_mix"] = source_mix_report
    training_selection_report["rekordbox_selected"] = total_rekordbox
    training_selection_report["minimum_tracks_per_style"] = style_minimum
    training_selection_report["excluded_styles"] = sorted(excluded_styles)
    training_selection_report["source_label_consistency"] = source_label_consistency
    dataset_builder_counts = Counter(
        taxonomy_from_training_label(str(row.get("base_genre") or "")).base_genre
        for row in dataset_rows
        if taxonomy_from_training_label(str(row.get("base_genre") or "")).base_genre
    )
    capped_local_counts = Counter(str(task[1]) for task in all_sample_files)
    capped_samples_counts = Counter(
        str(task[1]) for task in all_sample_files
        if os.path.normcase(os.path.abspath(task[0])) in reference_path_keys
        and os.path.normcase(os.path.abspath(task[0])) not in builder_path_keys
    )
    capped_manual_counts = Counter(
        str(task[1]) for task in all_sample_files
        if os.path.normcase(os.path.abspath(task[0])) in correction_path_keys
        and os.path.normcase(os.path.abspath(task[0])) not in builder_path_keys
        and os.path.normcase(os.path.abspath(task[0])) not in reference_path_keys
    )
    rekordbox_counts = Counter({
        style: len(rows) for style, rows in rekordbox_by_style.items()
    })
    capped_rekordbox_counts = Counter(
        str(track.get("_training_base_genre") or "")
        for track in balanced_rk_tracks
        if track.get("_training_base_genre")
    )
    pool_preflight = _build_training_pool_preflight(
        effective_trainable_styles,
        dataset_builder_counts,
        local_counts,
        rekordbox_counts,
        capped_local_counts,
        capped_rekordbox_counts,
        expected_rows=expected_preflight.get("rows") or [],
        minimum_required=style_minimum,
        samples_counts=samples_counts,
        capped_samples_counts=capped_samples_counts,
        manual_counts=manual_counts,
        capped_manual_counts=capped_manual_counts,
    )
    training_selection_report["preflight_consistency"] = pool_preflight
    if global_state is not None:
        global_state["training_preflight"] = pool_preflight
    logger.info("[TRAIN][PREFLIGHT] %s", pool_preflight)
    if not pool_preflight["passed"]:
        error_message = (
            "Проверка обучающей выборки остановила запуск до анализа аудио: "
            + "; ".join(pool_preflight["issues"][:6])
        )
        logger.error("[TRAIN][PREFLIGHT] %s", error_message)
        if global_state is not None:
            global_state["training_error"] = error_message
        set_progress(
            100,
            phase="error",
            message=error_message,
            status="error",
        )
        return
    logger.info(
        "[TRAINING CAP] max_per_style=%s; before=%s; after=%s; dropped=%s; per_style=%s",
        training_selection_report.get("max_tracks_per_style"),
        training_selection_report.get("before"),
        training_selection_report.get("after"),
        training_selection_report.get("dropped"),
        training_selection_report.get("per_style"),
    )

    total = len(all_sample_files) + total_rekordbox
    if stop_requested():
        finish_stopped()
        return
    set_progress(
        5,
        phase="features",
        message=f"Извлечение аудиопризнаков: 0 из {total}",
        processed=0,
        total=total,
        status="running",
    )

    def record_sample_result(task_args, result, worker_error=None):
        nonlocal processed
        processed += 1
        set_feature_progress(processed, total)
        if worker_error is not None:
            fail_path = task_args[0]
            fail_reason = f"worker_error: {type(worker_error).__name__}: {worker_error}"
            logger.error(
                "Ошибка при запуске/работе воркера для %s: %s",
                fail_path, worker_error,
            )
            skipped_tracks.append((fail_path, fail_reason))
            return
        if (
            result is not None
            and isinstance(result, tuple)
            and isinstance(result[0], str)
            and result[0] == "__FAIL__"
        ):
            _, fail_path, fail_reason = result
            if is_log_type_enabled("model"):
                model_logger.warning(
                    "[DATA] Признаки не извлечены, трек пропущен при обучении: "
                    f"{fail_path}. Причина: {fail_reason}"
                )
            skipped_tracks.append((fail_path, fail_reason))
            return
        if result is not None and isinstance(result, tuple):
            full_features, segment_features, genre, path = result
            samples.append(full_features)
            segment_features_by_track.append(segment_features)
            labels.append(genre)
            training_paths.append(path)
            path_key = os.path.normcase(os.path.abspath(path))
            training_taxonomies.append(
                manual_training_taxonomies.get(path_key)
                or {
                    **taxonomy_from_training_label(genre, path).to_dict(),
                    "training_source": "samples",
                }
            )
            genre_counter[genre] = genre_counter.get(genre, 0) + 1
            norm_key = normalize_audio_filename(path)
            norm_keys_list.append(norm_key)
            train_features_dict[norm_key] = full_features.tolist()
            if is_log_type_enabled("model"):
                model_logger.debug(
                    "[train] Добавлен трек в train_features_dict: %s", norm_key
                )
            return
        fail_path = task_args[0]
        if is_log_type_enabled("model"):
            model_logger.warning(
                "[DATA] Признаки не извлечены, трек пропущен при обучении: %s",
                fail_path,
            )
        skipped_tracks.append((fail_path, "Unknown error"))

    feature_cache_signature = _training_feature_signature(librosa_params)
    cached_sample_results = _load_training_feature_cache(
        all_sample_files,
        feature_cache_signature,
    )
    pending_sample_files = []
    for task_args in all_sample_files:
        cached_result = cached_sample_results.get(
            _training_cache_key(task_args, feature_cache_signature)
        )
        if cached_result is None:
            pending_sample_files.append(task_args)
            continue
        record_sample_result(task_args, cached_result)
    if cached_sample_results:
        logger.info(
            "[TRAIN][CHECKPOINT] Восстановлено %s треков; осталось извлечь %s.",
            len(all_sample_files) - len(pending_sample_files),
            len(pending_sample_files),
        )
    del cached_sample_results

    priority = "medium"  # Или из конфига/настроек, если нужно
    max_workers, resource_warning, resource_critical = get_dynamic_max_workers_by_settings(librosa_params, priority)
    logger.info(f"Feature extraction: CPU total={multiprocessing.cpu_count()}, max_workers={max_workers}")
    if resource_critical:
        logger.error(f"[TRAIN][RESOURCES] {resource_warning}")
        set_progress(100)
        return
    available_mem = psutil.virtual_memory().available
    commit_headroom = _windows_commit_headroom_bytes()
    memory_mode, safe_workers, effective_memory = _training_memory_decision(
        max_workers,
        available_mem,
        commit_headroom,
    )
    if safe_workers and safe_workers < max_workers:
        resource_logger.warning(
            "[RESOURCE][MEMORY] Стартовый предел обучения снижен: %s -> %s "
            "воркеров; эффективный запас %.2f ГБ.",
            max_workers, safe_workers, effective_memory / (1024 ** 3),
        )
        max_workers = safe_workers
    elif memory_mode == "pause":
        resource_logger.warning(
            "[RESOURCE][MEMORY] На старте свободно %.2f ГБ; обучение будет "
            "ожидать освобождения виртуальной памяти.",
            effective_memory / (1024 ** 3),
        )
    if librosa_params.get("multi_segment_enabled", False):
        max_workers = min(max_workers, 2)
        logger.info("[TRAIN][MEMORY] Multi-segment: ограничиваем число воркеров до %s", max_workers)
    tasks_per_worker_batch = 50
    stall_timeout_seconds = 180
    batch_cursor = 0
    batch_number = 0

    while batch_cursor < len(pending_sample_files):
        batch_number += 1
        physical_before = psutil.virtual_memory().available
        commit_before = _windows_commit_headroom_bytes()
        memory_mode, batch_workers, free_before = _training_memory_decision(
            max_workers,
            physical_before,
            commit_before,
        )
        while memory_mode == "pause":
            set_progress(
                phase="memory_pause",
                message=(
                    "Обучение приостановлено: системе требуется минимум 3 ГБ "
                    "свободной виртуальной памяти"
                ),
                processed=processed,
                total=total,
                status="running",
            )
            logger.warning(
                "[TRAIN][MEMORY] Свободно %.2f ГБ: ожидаем освобождения памяти.",
                free_before / (1024 ** 3),
            )
            for _ in range(10):
                if stop_requested():
                    finish_stopped()
                    return
                time.sleep(0.5)
            physical_before = psutil.virtual_memory().available
            commit_before = _windows_commit_headroom_bytes()
            memory_mode, batch_workers, free_before = _training_memory_decision(
                max_workers,
                physical_before,
                commit_before,
            )
        set_feature_progress(processed, total)
        if batch_workers < max_workers:
            logger.warning(
                "[TRAIN][MEMORY] Свободно %.2f ГБ: пакет %s будет обработан %s воркером вместо %s",
                free_before / (1024 ** 3), batch_number, batch_workers, max_workers,
            )
        batch_size = max(1, batch_workers * tasks_per_worker_batch)
        batch = pending_sample_files[batch_cursor:batch_cursor + batch_size]
        batch_started_at = time.monotonic()
        batch_pending = {id(task_args): task_args for task_args in batch}
        batch_cache_entries = []
        attempt = 0

        logger.info(
            "[TRAIN][BATCH] Старт пакета %s: треки %s-%s из %s, workers=%s, queue_limit=%s, RAM=%.2f ГБ",
            batch_number,
            batch_cursor + 1,
            batch_cursor + len(batch),
            len(pending_sample_files),
            batch_workers,
            batch_workers * 2,
            free_before / (1024 ** 3),
        )

        while batch_pending:
            attempt += 1
            current_tasks = list(batch_pending.values())
            attempt_workers = batch_workers if attempt == 1 else 1
            try:
                for task_args, res, worker_error in _iter_bounded_executor_results(
                        current_tasks,
                        process_one_sample,
                        attempt_workers,
                        pending_multiplier=2,
                        stall_timeout_seconds=stall_timeout_seconds,
                ):
                    batch_pending.pop(id(task_args), None)
                    record_sample_result(task_args, res, worker_error)
                    if worker_error is None and res is not None:
                        batch_cache_entries.append((task_args, res))

                    if stop_requested():
                        finish_stopped()
                        return
            except TrainingPoolStalledError as exc:
                logger.error("[TRAIN][WATCHDOG] Пакет %s, попытка %s: %s", batch_number, attempt, exc)
                if attempt == 1:
                    logger.warning(
                        "[TRAIN][WATCHDOG] Повторяем %s незавершённых треков пакета %s на одном воркере",
                        len(batch_pending), batch_number,
                    )
                    gc.collect()
                    continue

                error_message = (
                    f"Извлечение признаков зависло повторно в пакете {batch_number}. "
                    f"Текущие файлы: {', '.join(exc.pending_paths[:4])}"
                )
                logger.error("[TRAIN][WATCHDOG] %s", error_message)
                if global_state is not None:
                    global_state["training_error"] = error_message
                set_progress(100)
                return

        batch_cursor += len(batch)
        cached_count = _save_training_feature_cache(
            batch_cache_entries,
            feature_cache_signature,
        )
        gc.collect()
        free_after = psutil.virtual_memory().available
        logger.info(
            "[TRAIN][BATCH] Пакет %s завершён: %s треков, %.1f сек, RAM до/после %.2f/%.2f ГБ",
            batch_number,
            len(batch),
            time.monotonic() - batch_started_at,
            free_before / (1024 ** 3),
            free_after / (1024 ** 3),
        )
        if cached_count:
            logger.info(
                "[TRAIN][CHECKPOINT] Сохранено результатов пакета: %s.",
                cached_count,
            )


    # --- обработка Reckordbox треков ---
    for track in balanced_rk_tracks:
        genre = get_track_val(track, "Genre")
        genre_norm = track.get("_training_base_genre") or _rekordbox_track_base_style(
            track, genre_settings
        )
        path = track["path"]
        path_key = os.path.normcase(os.path.abspath(path))
        manual_taxonomy = manual_training_taxonomies.get(path_key)
        if manual_taxonomy:
            genre_norm = manual_taxonomy["base_genre"]
        if is_log_type_enabled("model"):
            model_logger.debug("raw_genre: %s -> genre_norm: %s", genre, genre_norm)
        if not genre_norm or genre_norm == "Other":
            processed += 1
            set_feature_progress(processed, total)
            if stop_requested():
                finish_stopped()
                return
            continue
        genre_counter[genre_norm] = genre_counter.get(genre_norm, 0) + 1
        try:
            full_features, segment_features, _audio_segments, segment_errors = _extract_multisegment_features(
                path,
                librosa_params,
                track=track,
            )
            if is_log_type_enabled("model"):
                model_logger.debug(
                    f"[train_genre_model] Rekordbox {path}: segments={len(segment_features)}, "
                    f"errors={segment_errors[:2]}, feature_shape={full_features.shape}")
            # --- ВСТАВКА ЗАЩИТЫ ---
            if full_features is None or \
                    (isinstance(full_features, np.ndarray) and full_features.size == 0) or \
                    (isinstance(full_features, list) and len(full_features) == 0):
                logger.warning(f"No features extracted from Rekordbox track: {path}")
                processed += 1
                set_feature_progress(processed, total)
                if stop_requested():
                    finish_stopped()
                    return
                continue
            samples.append(full_features)
            segment_features_by_track.append(segment_features)
            labels.append(genre_norm)
            training_paths.append(path)
            raw_rekordbox_genre = get_track_val(track, "raw_genre") or genre
            rekordbox_taxonomy = (
                manual_taxonomy
                or parse_track_taxonomy(
                    raw_genre=raw_rekordbox_genre,
                    fallback_genre=genre_norm,
                    title=get_track_val(track, "title"),
                    artist=get_track_val(track, "artist"),
                    path=path,
                    fallback_language=None,
                ).to_dict()
            )
            if not rekordbox_taxonomy.get("training_source"):
                rekordbox_taxonomy["training_source"] = "rekordbox"
            training_taxonomies.append(rekordbox_taxonomy)
            logger.info(f"Rekordbox track added for train: {path} (genre: {genre_norm})")
            norm_key = normalize_audio_filename(path)
            norm_keys_list.append(norm_key)
            train_features_dict[norm_key] = full_features.tolist()
            if is_log_type_enabled("model"):
                model_logger.debug(f"[train] Добавлен трек в train_features_dict: {norm_key}")
        except Exception as e:
            logger.error(f"Error processing Rekordbox track {path}: {e}")
        processed += 1
        set_feature_progress(processed, total)
        if stop_requested():
            finish_stopped()
            return

    # === Блок обучения (когда все samples и labels собраны) ===
    set_progress(
        76,
        phase="preparing_model",
        message="Подготовка матриц и разбиение train/validation",
        processed=processed,
        total=total,
        status="running",
    )
    logger.info("\n====== Статистика по жанрам ======")
    total_count = sum(genre_counter.values())
    all_genres = set(v["genre"] if isinstance(v, dict) else v for v in genre_settings.values())
    for genre in sorted(all_genres):
        count = genre_counter.get(genre, 0)
        logger.info(f"Genre: {genre} найдено треков: {count}")
    logger.info(f"ВСЕГО треков: {total_count}")
    logger.info(
        f"[SUMMARY] Всего обработано треков: {len(samples)}, меток: {len(labels)}, пропущено: {len(skipped_tracks)}")
    if skipped_tracks:
        logger.info(f"[SUMMARY] Список пропущенных треков (первые 10): {skipped_tracks[:10]}")
    if not samples:
        logger.error("Нет обучающих примеров! Проверьте папки с треками и JSON.")
        set_progress(100)
        return
    # === Проверка: все ли признаки одной длины ===
    if len(samples) > 0:
        feat_len = len(samples[0])
        wrong_samples = [(i, len(s), labels[i], norm_keys_list[i]) for i, s in enumerate(samples) if len(s) != feat_len]
        if wrong_samples:
            logger.error(f"[TRAIN ERROR] Найдены признаки разной длины! Всего неверных: {len(wrong_samples)}")
            for idx, slen, genre, norm_key in wrong_samples[:20]:
                logger.error(f"[TRAIN ERROR] Индекс: {idx}, Длина: {slen}, Жанр: {genre}, norm_key: {norm_key}")
            # Удаляем такие сэмплы из обучения
            samples_fixed = []
            labels_fixed = []
            norm_keys_fixed = []
            paths_fixed = []
            taxonomies_fixed = []
            segment_features_fixed = []
            for i, s in enumerate(samples):
                if len(s) == feat_len:
                    samples_fixed.append(s)
                    segment_features_fixed.append(segment_features_by_track[i])
                    labels_fixed.append(labels[i])
                    norm_keys_fixed.append(norm_keys_list[i])
                    paths_fixed.append(training_paths[i])
                    taxonomies_fixed.append(training_taxonomies[i])
            samples = samples_fixed
            segment_features_by_track = segment_features_fixed
            labels = labels_fixed
            norm_keys_list = norm_keys_fixed
            training_paths = paths_fixed
            training_taxonomies = taxonomies_fixed
            logger.error(f"[TRAIN] После фильтрации осталось {len(samples)} треков")

    logger.info("=== Баланс классов в обучении ===")
    logger.info(Counter(labels))
    logger.info("=== Уникальные классы в обучении ===")
    logger.info(set(labels))
    logger.info(
        f"[SUMMARY] Всего обработано треков: {len(samples)}, меток: {len(labels)}, пропущено: {len(skipped_tracks)}")
    if skipped_tracks:
        logger.info(f"[SUMMARY] Список пропущенных треков (первые 10): {skipped_tracks[:10]}")
    try:

        strict_indices, strict_dedup_report = _strict_deduplicate_training_rows(
            training_paths,
            samples,
            labels,
            training_taxonomies,
        )
        samples = [samples[index] for index in strict_indices]
        segment_features_by_track = [segment_features_by_track[index] for index in strict_indices]
        labels = [labels[index] for index in strict_indices]
        norm_keys_list = [norm_keys_list[index] for index in strict_indices]
        training_paths = [training_paths[index] for index in strict_indices]
        training_taxonomies = [training_taxonomies[index] for index in strict_indices]
        training_selection_report["strict_dedup"] = strict_dedup_report
        logger.info("[STRICT DEDUP] %s", strict_dedup_report)

        # DJ-категория остаётся для интерфейса, но акустическая модель учится
        # только базовому стилю. Например, Club House и Русские Ремиксы имеют
        # общий base_genre=Club House, а язык определяется отдельно.
        # Итоговую DJ-категорию берём из многомерной разметки, а не из первого
        # совпавшего токена старого normalize_genre_rekordbox(). Поэтому строка
        # "Russian, DrumNBass" корректно остаётся Drum & Bass.
        dj_labels_np = np.asarray([item["dj_category"] for item in training_taxonomies])
        base_labels_np = np.asarray([item["base_genre"] for item in training_taxonomies])
        # Акустическая RF остаётся бинарным резервом Russian/Foreign. Явная
        # метка English сохраняется в taxonomy, но для старой RF относится к
        # широкому классу Foreign.
        language_labels_np = np.asarray([
            "Foreign" if item["language"] in {"English", "Other"} else item["language"]
            for item in training_taxonomies
        ])
        samples_np = np.asarray(samples)
        segment_features_np = np.asarray(segment_features_by_track, dtype=object)
        paths_np = np.asarray(training_paths, dtype=object)
        taxonomies_np = np.asarray(training_taxonomies, dtype=object)
        norm_keys_np = np.asarray(norm_keys_list, dtype=object)

        eligible_base_genres, skipped_base_genres, raw_base_counts = _eligible_base_genres(
            base_labels_np,
            trainable_genres,
            style_minimum,
        )
        logger.info("[BASE STYLE] Распределение до фильтра: %s", raw_base_counts)
        if skipped_base_genres:
            logger.warning(
                "[BASE STYLE] Пропущены выключенные/редкие стили: %s. "
                "Минимум треков на включённый стиль: %s",
                skipped_base_genres,
                style_minimum,
            )
        if len(eligible_base_genres) < 2:
            logger.error(
                "Недостаточно базовых стилей для обучения: %s. "
                "Нужно минимум два включённых стиля с достаточным числом треков.",
                sorted(eligible_base_genres),
            )
            set_progress(100)
            return

        eligible_mask = np.isin(base_labels_np, list(eligible_base_genres))
        samples_np = samples_np[eligible_mask]
        segment_features_np = segment_features_np[eligible_mask]
        base_labels_np = base_labels_np[eligible_mask]
        dj_labels_np = dj_labels_np[eligible_mask]
        language_labels_np = language_labels_np[eligible_mask]
        paths_np = paths_np[eligible_mask]
        taxonomies_np = taxonomies_np[eligible_mask]
        norm_keys_np = norm_keys_np[eligible_mask]

        random_state = int(librosa_params.get("random_state", 42))
        balanced_indices, max_count_per_class = _independent_class_cap_indices(
            base_labels_np,
            max_per_class=style_maximum,
            random_state=random_state,
        )

        samples = samples_np[balanced_indices]
        segment_features_by_track = segment_features_np[balanced_indices].tolist()
        labels = base_labels_np[balanced_indices]
        dj_labels = dj_labels_np[balanced_indices]
        language_labels = language_labels_np[balanced_indices]
        training_paths = paths_np[balanced_indices].tolist()
        training_taxonomies = taxonomies_np[balanced_indices].tolist()
        norm_keys_list = norm_keys_np[balanced_indices].tolist()
        logger.info(
            "[BALANCE] Независимый лимит %s треков на стиль; веса классов balanced_subsample. Итог: %s",
            max_count_per_class,
            Counter(labels),
        )
        logger.info("[LANGUAGE LABELS] После фильтра/баланса: %s", Counter(language_labels))

        try:
            X = np.stack(samples)
        except Exception as e:
            if isinstance(e, MemoryError) or "Unable to allocate" in str(e) or "OutOfMemory" in str(e):
                log_memory_error(e, context="train_genre_model")
            logger.error(f"Ошибка при формировании матрицы признаков: {e}")
            if is_log_type_enabled("model"):
                model_logger.debug(f"Форматы features: {[s.shape for s in samples]}")
            set_progress(100)
            if is_log_type_enabled("model"):
                model_logger.error(f"Ошибка при формировании матрицы признаков: {e}", exc_info=True)
            return

        if X.shape[1] == 0:
            logger.error("Нет достаточных признаков для обучения! Измените настройки и попробуйте снова.")
            set_progress(100)
            return

        train_features_dict = {
            norm_keys_list[i]: samples[i] if isinstance(samples[i], list) else samples[i].tolist()
            for i in range(len(samples))
        }

        n_estimators = int(librosa_params.get("n_estimators", 300))
        test_size = float(librosa_params.get("validation_size", 0.2))
        test_size = min(max(test_size, 0.1), 0.4)
        max_depth_raw = librosa_params.get("max_depth", 24)
        max_depth = int(max_depth_raw) if max_depth_raw not in (None, "", 0, "0") else None
        min_samples_leaf = max(1, int(librosa_params.get("min_samples_leaf", 2)))
        max_features = librosa_params.get("max_features", "sqrt") or "sqrt"

        rf_params = {
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "min_samples_leaf": min_samples_leaf,
            "max_features": max_features,
            "class_weight": "balanced_subsample",
            "random_state": random_state,
            "n_jobs": -1,
        }

        # Разные версии одного оригинала и совпадающие аудиоотпечатки не могут
        # оказаться одновременно в train и test.
        training_groups = _build_training_groups(training_paths, X, labels)
        train_idx, threshold_idx, validation_idx = _three_way_grouped_indices(
            X,
            labels,
            training_groups,
            holdout_fraction=test_size,
            random_state=random_state,
        )
        training_selection_report["split_counts"] = {
            partition_name: dict(Counter(labels[partition_indices]))
            for partition_name, partition_indices in (
                ("train", train_idx),
                ("threshold", threshold_idx),
                ("validation", validation_idx),
            )
        }
        X_train, y_train = X[train_idx], labels[train_idx]
        X_threshold, y_threshold = X[threshold_idx], labels[threshold_idx]
        X_validation, y_validation = X[validation_idx], labels[validation_idx]
        X_train_segments, y_train_segments, train_segment_groups = _expand_track_segments(
            segment_features_by_track,
            labels,
            training_groups,
            train_idx,
        )
        progressive_admission_report = {
            "enabled": False,
            "reason": "not_needed",
            "admitted_styles": sorted(set(str(value) for value in labels)),
            "deferred_styles": [],
        }
        active_for_admission = (
            set(active_classes_before) - set(excluded_styles)
            if active_classes_before_known else set()
        )
        new_style_candidates = set(str(value) for value in labels) - active_for_admission
        if (
            bool(librosa_params.get("progressive_style_admission_enabled", True))
            and new_style_candidates
        ):
            admission_params = dict(rf_params)
            admission_params.update({
                "n_estimators": min(int(admission_params.get("n_estimators", 300)), 180),
                "min_samples_leaf": max(2, int(admission_params.get("min_samples_leaf", 2))),
                "n_jobs": -1,
            })
            if admission_params.get("max_depth") is None:
                admission_params["max_depth"] = 18
            else:
                admission_params["max_depth"] = min(
                    int(admission_params["max_depth"]), 18
                )
            admission_model = RandomForestClassifier(**admission_params)
            admission_model.fit(X_train_segments, y_train_segments)
            admission_probabilities, _admission_disagreement = _predict_track_probabilities(
                admission_model,
                segment_features_by_track,
                threshold_idx,
            )
            admission_classes = np.asarray(admission_model.classes_, dtype=object)
            admission_predicted = admission_classes[
                np.argmax(admission_probabilities, axis=1)
            ]
            admitted_styles, progressive_admission_report = (
                _select_progressive_training_styles(
                    y_threshold,
                    admission_predicted,
                    set(str(value) for value in labels),
                    active_for_admission,
                    librosa_params,
                )
            )
            del admission_model, admission_probabilities
            gc.collect()
            deferred_styles = set(str(value) for value in labels) - admitted_styles
            if deferred_styles:
                original_labels = np.asarray(labels, dtype=object)
                admission_mask = np.isin(original_labels, sorted(admitted_styles))
                partition = np.full(len(original_labels), "", dtype=object)
                partition[np.asarray(train_idx, dtype=int)] = "train"
                partition[np.asarray(threshold_idx, dtype=int)] = "threshold"
                partition[np.asarray(validation_idx, dtype=int)] = "validation"

                samples = np.asarray(samples)[admission_mask]
                X = np.asarray(X)[admission_mask]
                segment_features_by_track = np.asarray(
                    segment_features_by_track, dtype=object
                )[admission_mask].tolist()
                labels = original_labels[admission_mask]
                dj_labels = np.asarray(dj_labels, dtype=object)[admission_mask]
                language_labels = np.asarray(language_labels, dtype=object)[admission_mask]
                training_paths = np.asarray(training_paths, dtype=object)[admission_mask].tolist()
                training_taxonomies = np.asarray(
                    training_taxonomies, dtype=object
                )[admission_mask].tolist()
                norm_keys_list = np.asarray(norm_keys_list, dtype=object)[admission_mask].tolist()
                training_groups = np.asarray(training_groups, dtype=object)[admission_mask]
                filtered_partition = partition[admission_mask]
                train_idx = np.flatnonzero(filtered_partition == "train")
                threshold_idx = np.flatnonzero(filtered_partition == "threshold")
                validation_idx = np.flatnonzero(filtered_partition == "validation")
                X_train, y_train = X[train_idx], labels[train_idx]
                X_threshold, y_threshold = X[threshold_idx], labels[threshold_idx]
                X_validation, y_validation = X[validation_idx], labels[validation_idx]
                X_train_segments, y_train_segments, train_segment_groups = (
                    _expand_track_segments(
                        segment_features_by_track,
                        labels,
                        training_groups,
                        train_idx,
                    )
                )
                train_features_dict = {
                    norm_keys_list[index]: samples[index].tolist()
                    for index in range(len(samples))
                }
                for style in deferred_styles:
                    skipped_base_genres[style] = int(raw_base_counts.get(style, 0))
                logger.warning(
                    "[PROGRESSIVE ADMISSION] Отложены слабые новые стили: %s; "
                    "в этом кандидате остались: %s",
                    sorted(deferred_styles), sorted(admitted_styles),
                )
        training_selection_report["progressive_admission"] = (
            progressive_admission_report
        )
        if stop_requested():
            finish_stopped()
            return
        set_progress(
            80,
            phase="tuning",
            message="Автоподбор параметров Random Forest",
            processed=processed,
            total=total,
            status="running",
        )
        rf_params, tuning_report = _tune_rf_params(
            X_train_segments,
            y_train_segments,
            train_segment_groups,
            rf_params,
            librosa_params,
        )
        logger.info("[AUTO TUNE] %s", tuning_report)
        if stop_requested():
            finish_stopped()
            return
        set_progress(
            87,
            phase="validation",
            message="Обучение контрольной модели и калибровка порогов",
            processed=processed,
            total=total,
            status="running",
        )
        validation_model, validation_calibration = _build_probability_model(
            rf_params,
            librosa_params,
            y_train_segments,
        )
        validation_model.fit(X_train_segments, y_train_segments)
        validation_model, hierarchy_validation_report = _fit_hierarchy_safe(
            validation_model,
            X_train_segments,
            y_train_segments,
            rf_params,
            librosa_params,
        )
        (
            threshold_probabilities,
            threshold_segment_disagreement,
            validation_probabilities,
            validation_segment_disagreement,
            hierarchy_selection_report,
        ) = _select_safe_hierarchy_weight(
            validation_model,
            segment_features_by_track,
            labels,
            threshold_idx,
            validation_idx,
            protected_styles=(
                set(active_classes_before) - set(excluded_styles)
                if active_classes_before_known else None
            ),
        )
        hierarchy_validation_report = {
            **hierarchy_validation_report,
            "selection": hierarchy_selection_report,
        }
        logger.info("[HIERARCHY][VALIDATION] %s", hierarchy_validation_report)
        if active_classes_before_known:
            active_style_metrics_before, active_model_comparison = (
                _evaluate_active_model_on_current_validation(
                    segment_features_by_track,
                    labels,
                    validation_idx,
                    set(active_classes_before) - set(excluded_styles),
                )
            )
            logger.info("[TRAIN][ACTIVE MODEL COMPARISON] %s", active_model_comparison)
        validation_classes = np.asarray(validation_model.classes_)
        effnet_embedding_map = {}
        effnet_validation_head = None
        effnet_genre_head = None
        effnet_fusion_alpha = 0.0
        effnet_training_report = {
            "enabled": False,
            "reason": "pipeline_disabled",
        }
        pipeline_settings = get_model_pipeline_settings()
        if (
                bool(pipeline_settings.get("effnet_enabled", False))
                and bool(pipeline_settings.get("effnet_genre_fusion_enabled", True))
        ):
            try:
                set_progress(
                    88,
                    phase="effnet_genre",
                    message="Discogs EffNet: подготовка глубоких признаков для проверки жанров",
                    processed=0,
                    total=len(training_paths),
                    status="running",
                )

                def update_effnet_progress(info):
                    deep_total = int(info.get("total", len(training_paths)) or len(training_paths))
                    deep_processed = int(info.get("processed", 0) or 0)
                    ratio = deep_processed / max(1, deep_total)
                    set_progress(
                        88 + int(min(max(ratio, 0.0), 1.0) * 2),
                        phase="effnet_genre",
                        message=(
                            "Discogs EffNet: глубокие признаки "
                            f"{deep_processed} из {deep_total}"
                        ),
                        processed=deep_processed,
                        total=deep_total,
                        status="running",
                    )

                stop_event = (
                    global_state.get("training_stop_event")
                    if global_state is not None else None
                )
                effnet_embedding_map, extraction_report = build_training_embedding_map(
                    training_paths,
                    pipeline_settings,
                    progress_callback=update_effnet_progress,
                    stop_event=stop_event,
                )
                if stop_requested():
                    finish_stopped()
                    return
                effnet_validation_head, head_report = _fit_effnet_head_for_indices(
                    training_paths,
                    labels,
                    effnet_embedding_map,
                    train_idx,
                    pipeline_settings,
                )
                effnet_training_report = {
                    "enabled": False,
                    "extraction": extraction_report,
                    "validation_head": head_report,
                }
                if effnet_validation_head is not None:
                    validation_probabilities, effnet_fusion_alpha, fusion_report = (
                        _select_safe_effnet_fusion(
                            effnet_validation_head,
                            training_paths,
                            labels,
                            effnet_embedding_map,
                            threshold_idx,
                            threshold_probabilities,
                            validation_idx,
                            validation_probabilities,
                            validation_classes,
                            pipeline_settings,
                            protected_styles=(
                                set(active_classes_before) - set(excluded_styles)
                                if active_classes_before_known else None
                            ),
                        )
                    )
                    effnet_training_report["fusion"] = fusion_report
                    effnet_training_report["enabled"] = bool(
                        fusion_report.get("enabled", False)
                    )
                    effnet_training_report["reason"] = fusion_report.get("reason")
                    if effnet_training_report["enabled"]:
                        threshold_probabilities, _available_threshold = _fuse_effnet_split(
                            threshold_probabilities,
                            validation_classes,
                            effnet_validation_head,
                            training_paths,
                            effnet_embedding_map,
                            threshold_idx,
                            effnet_fusion_alpha,
                        )
                logger.info("[EFFNET][GENRE] %s", effnet_training_report)
            except Exception as effnet_error:
                logger.exception(
                    "[EFFNET][GENRE] Дополнительная голова пропущена: %s",
                    effnet_error,
                )
                effnet_training_report = {
                    "enabled": False,
                    "reason": f"{type(effnet_error).__name__}: {effnet_error}",
                }
        segment_disagreement_penalty = float(
            librosa_params.get("segment_disagreement_penalty", 0.1) or 0.0
        )
        class_thresholds, threshold_diagnostics = _calculate_class_thresholds(
            y_threshold,
            threshold_probabilities,
            validation_classes,
            target_precision=float(librosa_params.get("target_class_precision", 0.9)),
            fallback_threshold=float(librosa_params.get("genre_threshold", 0.55)),
            min_margin=float(librosa_params.get("min_genre_margin", 0.1)),
            segment_disagreement=threshold_segment_disagreement,
            segment_disagreement_penalty=segment_disagreement_penalty,
        )
        y_validation_pred = validation_classes[np.argmax(validation_probabilities, axis=1)]
        rejection_policy_report = _evaluate_rejection_policy(
            y_validation,
            validation_probabilities,
            validation_classes,
            class_thresholds,
            fallback_threshold=float(librosa_params.get("genre_threshold", 0.55)),
            min_margin=float(librosa_params.get("min_genre_margin", 0.1)),
            segment_disagreement=validation_segment_disagreement,
            segment_disagreement_penalty=segment_disagreement_penalty,
        )
        validation_report_text = classification_report(
            y_validation,
            y_validation_pred,
            labels=validation_model.classes_,
            zero_division=0,
        )
        validation_report = classification_report(
            y_validation,
            y_validation_pred,
            labels=validation_model.classes_,
            output_dict=True,
            zero_division=0,
        )
        validation_cm = confusion_matrix(
            y_validation,
            y_validation_pred,
            labels=validation_model.classes_,
        )
        if stop_requested():
            finish_stopped()
            return
        set_progress(
            91,
            phase="language",
            message="Проверка классификатора языка",
            processed=processed,
            total=total,
            status="running",
        )

        # Независимый классификатор языка. Для известных треков метаданные
        # Rekordbox имеют приоритет, для новых файлов используется эта модель.
        known_language_mask = np.isin(language_labels, ["Russian", "Foreign"])
        language_model = None
        language_calibration = {"enabled": False, "reason": "one_class"}
        language_validation_report = {}
        language_class_thresholds = {}
        language_threshold_diagnostics = {}
        language_rejection_policy_report = {}
        language_validation_indices = np.asarray([], dtype=int)
        language_validation_probabilities = None
        language_validation_classes = np.asarray([], dtype=object)
        language_validation_disagreement = np.asarray([], dtype=bool)
        if len(set(language_labels[known_language_mask].tolist())) >= 2:
            known_language_indices = np.where(known_language_mask)[0]
            language_all_x, language_all_y, _language_all_groups = _expand_track_segments(
                segment_features_by_track,
                language_labels,
                training_groups,
                known_language_indices,
            )
            language_model, language_calibration = _build_probability_model(
                rf_params,
                librosa_params,
                language_all_y,
            )
            language_model.fit(language_all_x, language_all_y)

            try:
                language_train_local, language_threshold_local, language_validation_local = _three_way_grouped_indices(
                    X[known_language_indices],
                    language_labels[known_language_indices],
                    training_groups[known_language_indices],
                    holdout_fraction=test_size,
                    random_state=random_state,
                )
                language_train_idx = known_language_indices[language_train_local]
                language_threshold_idx = known_language_indices[language_threshold_local]
                language_validation_indices = known_language_indices[language_validation_local]
                language_train_x, language_train_y, _language_train_groups = _expand_track_segments(
                    segment_features_by_track,
                    language_labels,
                    training_groups,
                    language_train_idx,
                )
                language_validation_model, _language_validation_calibration = _build_probability_model(
                    rf_params,
                    librosa_params,
                    language_train_y,
                )
                language_validation_model.fit(language_train_x, language_train_y)
                language_threshold_probabilities, _language_threshold_disagreement = _predict_track_probabilities(
                    language_validation_model,
                    segment_features_by_track,
                    language_threshold_idx,
                )
                language_validation_classes = np.asarray(language_validation_model.classes_)
                language_class_thresholds, language_threshold_diagnostics = _calculate_class_thresholds(
                    language_labels[language_threshold_idx],
                    language_threshold_probabilities,
                    language_validation_classes,
                    target_precision=float(librosa_params.get("target_class_precision", 0.9)),
                    fallback_threshold=float(librosa_params.get("language_threshold", 0.6)),
                    min_margin=0.0,
                )
                language_validation_probabilities, language_validation_disagreement = _predict_track_probabilities(
                    language_validation_model,
                    segment_features_by_track,
                    language_validation_indices,
                )
                language_test_y = language_labels[language_validation_indices]
                language_pred = language_validation_classes[np.argmax(language_validation_probabilities, axis=1)]
                language_validation_report = classification_report(
                    language_test_y,
                    language_pred,
                    labels=language_validation_classes,
                    output_dict=True,
                    zero_division=0,
                )
                language_rejection_policy_report = _evaluate_rejection_policy(
                    language_test_y,
                    language_validation_probabilities,
                    language_validation_classes,
                    language_class_thresholds,
                    fallback_threshold=float(librosa_params.get("language_threshold", 0.6)),
                    min_margin=0.0,
                )
            except ValueError as language_split_error:
                logger.warning("[LANGUAGE] Отдельная validation языка пропущена: %s", language_split_error)
                language_validation_report = {"error": str(language_split_error)}

        # CSV всех ошибок/сомнительных решений на независимом test-наборе.
        TRAINING_ERRORS_FILE.parent.mkdir(parents=True, exist_ok=True)
        conflict_report = _write_genre_conflict_reports(
            training_paths,
            training_groups,
            y_validation,
            validation_probabilities,
            validation_classes,
            validation_idx,
            max_pairs=int(librosa_params.get("active_review_max_pairs", 10) or 10),
            tracks_per_pair=int(
                librosa_params.get("active_review_tracks_per_pair", 40) or 40
            ),
        )
        validation_ranking = np.argsort(validation_probabilities, axis=1)[:, ::-1]
        with open(TRAINING_ERRORS_FILE, "w", newline="", encoding="utf-8-sig") as error_file:
            writer = csv.writer(error_file)
            writer.writerow([
                "path", "group", "true_base_genre", "predicted_base_genre",
                "top1_probability", "second_genre", "second_probability", "margin",
                "true_dj_category", "language", "segment_disagreement",
                "decision_threshold", "accepted", "is_error",
            ])
            for local_index, source_index in enumerate(validation_idx):
                top_index = int(validation_ranking[local_index, 0])
                second_index = int(validation_ranking[local_index, 1]) if validation_probabilities.shape[1] > 1 else top_index
                top_label = str(validation_classes[top_index])
                second_label = str(validation_classes[second_index])
                top_probability = float(validation_probabilities[local_index, top_index])
                second_probability = float(validation_probabilities[local_index, second_index])
                disagreement = bool(validation_segment_disagreement[local_index])
                decision_threshold = max(
                    float(class_thresholds.get(top_label, librosa_params.get("genre_threshold", 0.55))),
                    float(librosa_params.get("genre_threshold", 0.55)),
                )
                if disagreement:
                    decision_threshold += segment_disagreement_penalty
                decision_threshold = min(max(decision_threshold, 0.05), 0.99)
                accepted = (
                    top_probability >= decision_threshold
                    and (top_probability - second_probability) >= float(librosa_params.get("min_genre_margin", 0.1))
                )
                writer.writerow([
                    training_paths[source_index], training_groups[source_index], labels[source_index], top_label,
                    round(top_probability, 6), second_label, round(second_probability, 6),
                    round(top_probability - second_probability, 6), dj_labels[source_index],
                    language_labels[source_index], int(disagreement),
                    round(decision_threshold, 6), int(accepted),
                    int(top_label != str(labels[source_index])),
                ])

        with open(TRAINING_LANGUAGE_ERRORS_FILE, "w", newline="", encoding="utf-8-sig") as language_error_file:
            writer = csv.writer(language_error_file)
            writer.writerow([
                "path", "group", "true_language", "predicted_language",
                "top1_probability", "second_language", "second_probability", "margin",
                "segment_disagreement", "accepted", "is_error",
            ])
            if language_validation_probabilities is not None:
                language_ranking = np.argsort(language_validation_probabilities, axis=1)[:, ::-1]
                for local_index, source_index in enumerate(language_validation_indices):
                    top_index = int(language_ranking[local_index, 0])
                    second_index = int(language_ranking[local_index, 1])
                    top_label = str(language_validation_classes[top_index])
                    second_label = str(language_validation_classes[second_index])
                    top_probability = float(language_validation_probabilities[local_index, top_index])
                    second_probability = float(language_validation_probabilities[local_index, second_index])
                    threshold = float(language_class_thresholds.get(
                        top_label,
                        librosa_params.get("language_threshold", 0.6),
                    ))
                    writer.writerow([
                        training_paths[source_index], training_groups[source_index],
                        language_labels[source_index], top_label,
                        round(top_probability, 6), second_label, round(second_probability, 6),
                        round(top_probability - second_probability, 6),
                        int(language_validation_disagreement[local_index]),
                        int(top_probability >= threshold),
                        int(top_label != str(language_labels[source_index])),
                    ])

        if stop_requested():
            finish_stopped()
            return
        set_progress(
            95,
            phase="quality_gate",
            message="Проверка качества кандидата",
            processed=processed,
            total=total,
            status="running",
        )
        quality_gate_report = _evaluate_training_quality_gate(
            validation_report,
            rejection_policy_report,
            librosa_params,
            protected_styles=(
                set(active_classes_before) - set(excluded_styles)
                if active_classes_before_known else None
            ),
            active_style_metrics=active_style_metrics_before,
        )
        quality_gate_report["evaluated_at"] = datetime.datetime.now().isoformat()
        quality_gate_report["candidate_classes"] = sorted(
            str(class_name) for class_name in validation_classes
        )
        quality_gate_report["active_model_comparison"] = active_model_comparison
        LEARNING_CURVES_DIR.mkdir(parents=True, exist_ok=True)
        quality_report_temp = TRAINING_QUALITY_REPORT_FILE.with_suffix(".json.tmp")
        with open(quality_report_temp, "w", encoding="utf-8") as quality_file:
            json.dump(quality_gate_report, quality_file, ensure_ascii=False, indent=2)
            quality_file.flush()
            os.fsync(quality_file.fileno())
        os.replace(quality_report_temp, TRAINING_QUALITY_REPORT_FILE)

        training_run_report = _build_training_run_report(
            quality_gate_report["passed"],
            active_classes_before,
            active_classes_before_known,
            quality_gate_report["candidate_classes"],
            validation_report,
            rejection_policy_report,
            class_thresholds,
            skipped_base_genres,
            raw_base_counts,
            training_selection_report,
            quality_gate_report,
            threshold_diagnostics,
            hierarchy_validation_report,
        )

        if not quality_gate_report["passed"]:
            _save_training_run_report(training_run_report)
            error_message = (
                "Кандидат модели отклонён проверкой качества; рабочая модель не изменена. "
                + "; ".join(quality_gate_report["reasons"])
            )
            logger.error("[TRAIN][QUALITY GATE] %s", error_message)
            if global_state is not None:
                global_state["training_error"] = error_message
                global_state["training_quality_gate"] = quality_gate_report
            set_progress(
                100,
                phase="rejected",
                message="Кандидат отклонён; рабочая модель не изменена",
                processed=processed,
                total=total,
                status="error",
            )
            gc.collect()
            return

        if stop_requested():
            finish_stopped()
            return
        set_progress(
            97,
            phase="final_model",
            message="Обучение финальной модели на всей выборке",
            processed=processed,
            total=total,
            status="running",
        )
        all_track_indices = np.arange(len(labels), dtype=int)
        X_all_segments, y_all_segments, _all_segment_groups = _expand_track_segments(
            segment_features_by_track,
            labels,
            training_groups,
            all_track_indices,
        )
        clf, final_calibration = _build_probability_model(rf_params, librosa_params, y_all_segments)
        clf.fit(X_all_segments, y_all_segments)
        clf, hierarchy_final_report = _fit_hierarchy_safe(
            clf,
            X_all_segments,
            y_all_segments,
            rf_params,
            librosa_params,
        )
        if hasattr(clf, "hierarchy_weight"):
            clf.hierarchy_weight = float(
                hierarchy_validation_report.get("selection", {}).get(
                    "selected_weight", 0.0,
                ) or 0.0
            )
            hierarchy_final_report["selected_weight"] = clf.hierarchy_weight
            hierarchy_final_report["validated"] = True
        if effnet_training_report.get("enabled"):
            effnet_genre_head, effnet_final_report = _fit_effnet_head_for_indices(
                training_paths,
                labels,
                effnet_embedding_map,
                all_track_indices,
                pipeline_settings,
            )
            effnet_training_report["final_head"] = effnet_final_report
            if effnet_genre_head is None:
                effnet_training_report["enabled"] = False
                effnet_training_report["reason"] = "final_head_failed"
        if stop_requested():
            finish_stopped()
            return
        set_progress(
            99,
            phase="saving",
            message="Сохранение модели и отчётов",
            processed=processed,
            total=total,
            status="running",
        )
        model_logger.info(f"train_features_dict keys count: {len(train_features_dict)}")
        expected_feature_len = X.shape[1]
        model_meta = {
            "model": clf,
            "base_genre_model": clf,
            "language_model": language_model,
            "language_class_thresholds": language_class_thresholds,
            "expected_feature_len": expected_feature_len,
            "librosa_params": librosa_params,
            "librosa_hash": hash(json.dumps(librosa_params, sort_keys=True)),
            "train_features_dict": train_features_dict,
            "labels": labels,
            "dj_labels": dj_labels,
            "language_labels": language_labels,
            "training_taxonomies": training_taxonomies,
            "training_groups": training_groups,
            "norm_keys_list": norm_keys_list,

            # Для ускорения и качества поиска
            "scaler": locals().get("scaler", None),
            "faiss_index": locals().get("faiss_index", None),
            "feature_importances": _calibrated_feature_importances(clf),
            "pca_model": locals().get("pca", None),
            "pca_coords": locals().get("pca_coords", None),

            # Для анализа и отладки
            "training_time": datetime.datetime.now().isoformat(),
            "genre_distribution": dict(Counter(labels)),
            "skipped_tracks": skipped_tracks,
            "confusion_matrix": validation_cm.tolist(),
            "classification_report": validation_report,
            "validation_size": len(y_validation),
            "validation_accuracy": float(validation_report.get("accuracy", 0.0)),
            "class_thresholds": class_thresholds,
            "threshold_diagnostics": threshold_diagnostics,
            "rejection_policy_report": rejection_policy_report,
            "probability_calibration": final_calibration,
            "validation_probability_calibration": validation_calibration,
            "language_probability_calibration": language_calibration,
            "language_validation_report": language_validation_report,
            "language_threshold_diagnostics": language_threshold_diagnostics,
            "language_rejection_policy_report": language_rejection_policy_report,
            "hyperparameter_tuning": tuning_report,
            "hierarchical_genre": hierarchy_final_report,
            "hierarchical_validation": hierarchy_validation_report,
            "effnet_genre_head": effnet_genre_head,
            "effnet_genre_fusion_alpha": effnet_fusion_alpha,
            "effnet_genre_training": effnet_training_report,
            "model_pipeline_training": pipeline_settings,
            "training_quality_gate": quality_gate_report,
            "training_quality_gate_file": str(TRAINING_QUALITY_REPORT_FILE),
            "training_run_report": training_run_report,
            "training_run_report_file": str(TRAINING_RUN_REPORT_FILE),
            "training_selection": training_selection_report,
            "training_errors_file": str(TRAINING_ERRORS_FILE),
            "training_language_errors_file": str(TRAINING_LANGUAGE_ERRORS_FILE),
            "training_duplicates_file": str(TRAINING_DUPLICATES_FILE),
            "training_label_conflicts_file": str(TRAINING_LABEL_CONFLICTS_FILE),
            "training_conflicts": conflict_report,
            "training_conflicts_file": str(TRAINING_CONFLICTS_FILE),
            "training_review_queue_file": str(TRAINING_REVIEW_QUEUE_FILE),
            "train_params": {
                **rf_params,
                "trainset_size": len(labels),
                "segment_trainset_size": len(y_all_segments),
                "max_base_class_ratio": None,
                "max_tracks_per_style": style_maximum,
                "min_tracks_per_style": style_minimum,
                "balance_strategy": "independent_cap_balanced_subsample",
                "segment_disagreement_penalty": segment_disagreement_penalty,
                "validation_fraction": test_size,
                "threshold_tuning_size": len(y_threshold),
            },

            # Для расширения и explainability
            "tsne_coords": locals().get("tsne_coords", None),
            "user_feedback": locals().get("user_feedback", None),
            "version": "4.1-hierarchical-effnet-genres",
            "code_version": "commit_hash_or_tag_here",
        }


        LEARNING_CURVES_DIR.mkdir(parents=True, exist_ok=True)
        confusion_matrix_path = LEARNING_CURVES_DIR / "confusion_matrix.png"
        plt.figure(figsize=(8, 6))
        sns.heatmap(
            validation_cm,
            annot=True,
            fmt="d",
            xticklabels=validation_model.classes_,
            yticklabels=validation_model.classes_,
            cmap="Blues",
        )
        plt.xlabel("Predicted")
        plt.ylabel("True")
        plt.title("Confusion matrix (held-out validation set)")
        plt.tight_layout()
        plt.savefig(confusion_matrix_path)
        plt.close()

        temp_model_path = f"{MODEL_PATH}.tmp"
        with open(temp_model_path, "wb") as f:
            pickle.dump(model_meta, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_model_path, MODEL_PATH)
        _write_active_model_manifest(model_meta)
        _save_training_run_report(training_run_report)
        set_progress(
            100,
            phase="completed",
            message="Обучение завершено; рабочая модель обновлена",
            processed=processed,
            total=total,
            status="completed",
        )
        logger.info("Training completed (100%%). Validation accuracy: %.4f", validation_report.get("accuracy", 0.0))
        if is_log_type_enabled("model"):
            model_logger.info("Validation report:\n%s", validation_report_text)
            model_logger.info("Автоматические пороги жанров: %s", class_thresholds)
            model_logger.info("Диагностика порогов: %s", threshold_diagnostics)
            model_logger.info("Проверка политики Unknown на отдельном test-наборе: %s", rejection_policy_report)
            model_logger.info("Проверка классификатора языка: %s", language_validation_report)
            model_logger.info("Автоматические пороги языка: %s", language_class_thresholds)
            model_logger.info("Диагностика порогов языка: %s", language_threshold_diagnostics)
            model_logger.info("Проверка политики Unknown языка: %s", language_rejection_policy_report)
            model_logger.info("Отчёт ошибок обучения: %s", TRAINING_ERRORS_FILE)
            model_logger.info("Отчёт ошибок языка: %s", TRAINING_LANGUAGE_ERRORS_FILE)
            model_logger.info("Отчёт групп/дубликатов: %s", TRAINING_DUPLICATES_FILE)
            model_logger.info("Матрица validation сохранена: %s", confusion_matrix_path)

        del (
            X_train, X_threshold, X_validation,
            y_train, y_threshold, y_validation,
            y_validation_pred, validation_cm,
            threshold_probabilities, validation_probabilities,
        )
        gc.collect()

        # --- Условный вызов построения кривой обучения ---
        enable_lc = bool(librosa_params.get("enable_learning_curve", False))
        if enable_lc:
            if is_log_type_enabled("model"):
                model_logger.info("[LEARNING CURVE] enable_learning_curve=True → строим кривую обучения")
            try:
                plot_learning_curve_for_genre_model()
                logger.info("Learning curve построен и сохранён.")
            except Exception as e:
                logger.error(f"Ошибка при построении learning curve: {e}")
        elif is_log_type_enabled("model"):
            model_logger.info("[LEARNING CURVE] enable_learning_curve=False → пропускаем построение")

    except Exception as e:
        if isinstance(e, MemoryError) or "Unable to allocate" in str(e) or "OutOfMemory" in str(e):
            log_memory_error(e, context="train_genre_model")
        logger.error(f"Ошибка на этапе балансировки/обучения: {e}", exc_info=True)
        error_message = f"{type(e).__name__}: {e}"
        if global_state is not None:
            global_state["training_error"] = error_message
        current_progress = int(global_state.get("training_progress", 0) or 0) if global_state else 0
        set_progress(
            current_progress,
            phase="error",
            message=error_message,
            status="error",
        )

def get_track_val(track, key):
    return track.get(key) or track.get(key.lower()) or track.get(key.upper()) or ""

def load_rekordbox_json_tracks(json_path="parsed_rekordbox.json", genre_settings=None):
    if genre_settings is None:
        raise ValueError("genre_settings must be provided")
    if not os.path.exists(json_path):
        return []
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Пример фильтрации: только с жанром и существующим файлом
    tracks = []
    music_dir = load_config().get("music_dir")
    for track in data:
        if not isinstance(track, dict):
            continue
        genre = (get_track_val(track, "Genre") or "").strip()
        # Most Rekordbox exports contain many sampler/service rows without a
        # genre.  They can never enter training, so do not perform expensive
        # drive/UNC probes for them.
        if not genre:
            continue
        source_path = get_track_val(track, "path")
        if not source_path:
            continue
        path = resolve_mapped_music_path(source_path, music_dir)
        if path and os.path.exists(path):
            # Можно извлекать и другие поля: Color, Rating, BPM, Situation, Artist, Title ...
            tracks.append({
                "path": path,
                "source_path": source_path,
                "raw_genre": genre,
                "genre": normalize_genre_rekordbox(genre, genre_settings),
                "color": get_track_val(track, "Color"),
                "rating": get_track_val(track, "Rating"),
                "bpm": get_track_val(track, "BPM"),
                "artist": get_track_val(track, "Artist"),
                "title": get_track_val(track, "Title") or os.path.splitext(os.path.basename(path))[0],
                "situation": get_track_val(track, "Situation")
            })
    return tracks

# === YAMNet Fusion Support (runtime) ===
# Использует динамический словарь жанров из load_genre_settings (JSON).
_yamnet_session = None
_yamnet_input_name = None
_yamnet_labels_521 = None
_yamnet_model_path_cached = None  # чтобы при смене пути пересоздавать сессию
_yamnet_allow_cuda_cached = None
_yamnet_prior_cache = {}

# --- Adaptive YAMNet flags ---
_yamnet_cuda_failed = False          # Была ли хотя бы одна CUDA/OMP/OOM ошибка → дальше только CPU
_yamnet_disabled = False             # Полностью отключён (фатальный лимит ошибок)
_yamnet_fail_count = 0               # Счётчик подряд неуспешных инференсов
_YAMNET_FAIL_LIMIT = 3               # После 3 подряд ошибок отключаем
_yamnet_logged_cuda_switch = False   # Одноразовый лог о переходе на CPU
_YAMNET_CUDA_LOCK_PATH = str(YAMNET_CUDA_LOCK_FILE)
_yamnet_owns_cuda_lock = False


def _yamnet_release_cuda_lock():
    global _yamnet_owns_cuda_lock
    if not _yamnet_owns_cuda_lock:
        return
    try:
        with open(_YAMNET_CUDA_LOCK_PATH, "r", encoding="ascii") as lock_file:
            owner_pid = int(lock_file.read().strip())
        if owner_pid == os.getpid():
            os.remove(_YAMNET_CUDA_LOCK_PATH)
    except (OSError, ValueError):
        pass
    _yamnet_owns_cuda_lock = False

def _yamnet_acquire_cuda_lock():
    """
    Пытаемся создать эксклюзивный файл-лок для CUDA.
    Если успех -> True (этот процесс может создать CUDA сессию).
    Если уже существует -> False (другой процесс владеет GPU).
    Устаревший lock от аварийно завершённого процесса удаляется.
    """
    global _yamnet_owns_cuda_lock
    try:
        if os.path.exists(_YAMNET_CUDA_LOCK_PATH):
            try:
                with open(_YAMNET_CUDA_LOCK_PATH, "r", encoding="ascii") as lock_file:
                    owner_pid = int(lock_file.read().strip())
                if psutil.pid_exists(owner_pid):
                    return False
            except (OSError, ValueError):
                pass
            try:
                os.remove(_YAMNET_CUDA_LOCK_PATH)
            except OSError:
                return False
        fd = os.open(_YAMNET_CUDA_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, str(os.getpid()).encode("ascii"))
        finally:
            os.close(fd)
        _yamnet_owns_cuda_lock = True
        return True
    except FileExistsError:
        return False
    except Exception:
        return False

def _yamnet_load_class_map():
    import csv
    with open(YAMNET_CLASS_MAP_FILE, "r", encoding="utf-8", newline="") as class_map_file:
        reader = csv.reader(class_map_file)
        next(reader)
        labels = [row[2] for row in reader if len(row) >= 3]
    if len(labels) != 521:
        raise ValueError(f"YAMNet class map содержит {len(labels)} классов вместо 521")
    return labels

def _norm_for_yamnet(s: str):
    try:
        return normalize_for_genre_compare(s)
    except Exception:
        import re
        s = str(s).lower()
        s = re.sub(r"[^a-zа-я0-9]+", " ", s)
        s = re.sub(r"\s+", " ", s).strip()
        return s

def _yamnet_get_session(model_path: str, allow_cuda: bool = False):
    """
    Ленивая инициализация YAMNet.
    Алгоритм:
      1. Если _yamnet_disabled → сразу None.
      2. Если сессия уже есть и путь тот же → вернуть.
      3. Если ранее была CUDA ошибка (_yamnet_cuda_failed) → только CPU.
      4. Иначе:
         - пытаемся взять file-lock (_yamnet_acquire_cuda_lock);
         - если лок не наш — помечаем _yamnet_cuda_failed (значит все процессы будут на CPU);
         - если лок наш — пробуем CUDA → при любой CUDA / cublas / cudnn / OOM ошибке помечаем _yamnet_cuda_failed и переключаемся на CPU.
    """
    global _yamnet_session, _yamnet_input_name, _yamnet_labels_521
    global _yamnet_model_path_cached, _yamnet_allow_cuda_cached
    global _yamnet_cuda_failed, _yamnet_disabled, _yamnet_logged_cuda_switch

    if _yamnet_disabled:
        return None, None, None

    if (
        _yamnet_session is not None
        and _yamnet_model_path_cached == model_path
        and _yamnet_allow_cuda_cached == allow_cuda
    ):
        return _yamnet_session, _yamnet_input_name, _yamnet_labels_521

    try:
        import onnxruntime as ort
    except Exception as e:
        if is_log_type_enabled("model"):
            model_logger.error(f"[YAMNET] onnxruntime недоступен: {e}")
        _yamnet_disabled = True
        return None, None, None

    # Определяем список конфигураций провайдеров
    provider_attempts = []
    cuda_available = "CUDAExecutionProvider" in ort.get_available_providers()
    if not allow_cuda or not cuda_available or _yamnet_cuda_failed:
        provider_attempts = [["CPUExecutionProvider"]]
    else:
        can_try_cuda = _yamnet_acquire_cuda_lock()
        if not can_try_cuda:
            # Другой процесс уже владеет CUDA — сразу только CPU
            _yamnet_cuda_failed = True
            provider_attempts = [["CPUExecutionProvider"]]
        else:
            # Дадим шанс CUDA, затем fallback
            provider_attempts = [
                ["CUDAExecutionProvider", "CPUExecutionProvider"],
                ["CPUExecutionProvider"]
            ]

    last_err = None
    for prov in provider_attempts:
        try:
            sess = ort.InferenceSession(model_path, providers=prov)
            _yamnet_session = sess
            _yamnet_input_name = sess.get_inputs()[0].name
            _yamnet_labels_521 = _yamnet_load_class_map()
            _yamnet_model_path_cached = model_path
            _yamnet_allow_cuda_cached = allow_cuda
            if is_log_type_enabled("model"):
                model_logger.info(f"[YAMNET] Session создан: providers={sess.get_providers()}")
            # Если вдруг удалось снова создать CUDA сессию после CPU флага — снимем флаг
            if "CUDAExecutionProvider" in sess.get_providers() and _yamnet_cuda_failed:
                _yamnet_cuda_failed = False
            if "CUDAExecutionProvider" not in sess.get_providers():
                _yamnet_cuda_failed = True
                _yamnet_release_cuda_lock()
            return _yamnet_session, _yamnet_input_name, _yamnet_labels_521
        except Exception as e:
            last_err = e
            low = str(e).lower()
            if any(k in low for k in ("cuda", "cublas", "cudnn", "out of memory")):
                if not _yamnet_cuda_failed:
                    _yamnet_cuda_failed = True
                    if is_log_type_enabled("model"):
                        model_logger.error(f"[YAMNET] CUDA ошибка при создании: {e} → fallback на CPU")
                continue
            else:
                if is_log_type_enabled("model"):
                    model_logger.error(f"[YAMNET] Ошибка создания (providers={prov}): {e}")
                continue

    if is_log_type_enabled("model"):
        model_logger.error(f"[YAMNET] Не удалось создать ни одну сессию (last_err={last_err}). Отключаем.")
    _yamnet_release_cuda_lock()
    _yamnet_disabled = True
    return None, None, None

def _yamnet_handle_runtime_error(exc: Exception):
    """
    Обработка ошибок инференса:
      - CUDA / OOM / cuDNN — переключаемся на CPU (pomечаем _yamnet_cuda_failed, сбрасываем сессию)
      - Считаем подряд ошибки; при превышении _YAMNET_FAIL_LIMIT отключаем YAMNet.
    """
    global _yamnet_cuda_failed, _yamnet_session, _yamnet_fail_count, _yamnet_disabled, _yamnet_logged_cuda_switch
    msg = str(exc).lower()
    _yamnet_fail_count += 1

    if any(k in msg for k in ("cuda", "cublas", "cudnn", "out of memory")):
        if not _yamnet_cuda_failed:
            _yamnet_cuda_failed = True
            if is_log_type_enabled("model"):
                model_logger.error("[YAMNET] Обнаружена CUDA/OOM ошибка во время инференса → дальнейшие попытки только CPU")
        _yamnet_session = None  # пересоздадим CPU сессию при следующем запросе

    if _yamnet_fail_count >= _YAMNET_FAIL_LIMIT and not _yamnet_disabled:
        _yamnet_disabled = True
        if is_log_type_enabled("model"):
            model_logger.error(f"[YAMNET] Превышен лимит ошибок ({_yamnet_fail_count}). YAMNet отключён.")

def _yamnet_scores_to_prior(audioset_scores, audioset_labels, my_labels):
    """
    Явный маппинг AudioSet → жанры RF из yamnet_genre_map.json.
    Жанры без надёжного AudioSet-эквивалента получают 0, но не штрафуются.
    """
    import numpy as _np
    prior = _np.zeros(len(my_labels), dtype=_np.float32)
    if audioset_scores is None:
        return prior
    try:
        with open(YAMNET_GENRE_MAP_FILE, "r", encoding="utf-8") as mapping_file:
            mapping = json.load(mapping_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        if is_log_type_enabled("model"):
            model_logger.error("[YAMNET] Не удалось загрузить genre map: %s", exc)
        return prior

    score_by_label = {
        _norm_for_yamnet(label): float(audioset_scores[index])
        for index, label in enumerate(audioset_labels)
    }
    for genre_index, genre in enumerate(my_labels):
        class_weights = mapping.get(str(genre), {})
        evidence = 0.0
        total_weight = 0.0
        for audioset_label, weight in class_weights.items():
            weight = max(0.0, float(weight))
            evidence += score_by_label.get(_norm_for_yamnet(audioset_label), 0.0) * weight
            total_weight += weight
        if total_weight > 0:
            prior[genre_index] = min(1.0, evidence / total_weight)
    return prior

def _yamnet_infer_prior_from_audio(y, sr, sess, inp_name, labels_521, my_labels):
    """
    Инференс YAMNet:
      - нормализуем / ресемплим до 16k
      - получаем scores (N, 521)
      - усредняем → prior по нашим жанрам
      - при любой ошибке: лог + обработчик (_yamnet_handle_runtime_error)
    """
    if sess is None or inp_name is None or labels_521 is None:
        return None
    global _yamnet_fail_count, _yamnet_logged_cuda_switch
    try:
        import numpy as _np
        import librosa as _lib
        if sr != 16000:
            y16 = _lib.resample(y, orig_sr=sr, target_sr=16000)
        else:
            y16 = y
        y16 = _lib.util.normalize(y16).astype(_np.float32)
        outputs = sess.run(None, {inp_name: y16})
        scores = next((o for o in outputs
                       if isinstance(o, _np.ndarray) and o.ndim == 2 and o.shape[1] == 521), None)
        if scores is None:
            return None
        mean_scores = scores.mean(axis=0)
        prior = _yamnet_scores_to_prior(mean_scores, labels_521, my_labels)
        _yamnet_fail_count = 0  # успех — сброс счётчика
        return prior
    except Exception as e:
        if is_log_type_enabled("model"):
            model_logger.error(f"[YAMNET] Ошибка инференса: {e}")
        _yamnet_handle_runtime_error(e)
        return None
# === END YAMNET BLOCK ===


import atexit as _atexit
_atexit.register(_yamnet_release_cuda_lock)
