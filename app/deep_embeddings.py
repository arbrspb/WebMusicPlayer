"""Optional Discogs Multi-EffNet index for deep music similarity.

The index is additive: it reads tracks already known to ``scan_results`` and
stores versioned vectors in its own table.  It never changes the RF/YAMNet
result and can be disabled without affecting playback or the basic catalog.
"""
from __future__ import annotations

import concurrent.futures
import datetime as _dt
import hashlib
import importlib.util
import json
import logging
import math
import os
import sqlite3
import threading
import time
import urllib.request
from pathlib import Path

import librosa
import numpy as np

# до изменений 18-08-26
# from .paths import (
#     DISCOGS_EFFNET_MODEL_FILE,
#     SCAN_DB_FILE,
#     TRAINING_FEATURE_CACHE_FILE,
# )

from .paths import (
    PROJECT_DIR,
    DISCOGS_EFFNET_MODEL_FILE,
    SCAN_DB_FILE,
    TRAINING_FEATURE_CACHE_FILE,
)

logger = logging.getLogger(__name__)

MODEL_ID = "discogs-multi-effnet-bs64-v1"
MODEL_LABEL = "Discogs Multi-EffNet"
MODEL_URL = (
    "https://essentia.upf.edu/models/feature-extractors/discogs-effnet/"
    "discogs_multi_embeddings-effnet-bs64-1.onnx"
)
MODEL_SOURCE_URL = (
    "https://essentia.upf.edu/models/feature-extractors/discogs-effnet/"
)
MODEL_LICENSE_NAME = "CC BY-NC-SA 4.0 (non-commercial)"
MODEL_LICENSE_URL = (
    "https://github.com/MTG/essentia-models/blob/master/LICENSE"
)
MODEL_SHA256 = "65cfde30655a939de420e5c09a49a43648336fdbbf79eb3af6d3b40176339d8e"
MODEL_MAX_BYTES = 64 * 1024 * 1024
SAMPLE_RATE = 16000
FRAME_SIZE = 512
HOP_SIZE = 256
MEL_BANDS = 96
PATCH_FRAMES = 128
MODEL_BATCH_SIZE = 64
MODEL_EMBEDDING_DIM = 1280

_SESSION_CACHE = {}
_SESSION_LOCK = threading.RLock()
# новое 18-08-26
_ONNX_DLL_HANDLES = []
_ONNX_DLL_LOCK = threading.RLock()
_ONNX_DLL_READY = False


def _prepare_onnx_cuda_dlls():
    """Подключить локальные cuDNN 8 DLL только для ONNX Runtime на Windows."""
    global _ONNX_DLL_READY

    if os.name != "nt" or _ONNX_DLL_READY:
        return

    dll_dir = Path(PROJECT_DIR) / "runtime" / "onnx_cuda" / "bin"

    if not dll_dir.is_dir():
        return

    with _ONNX_DLL_LOCK:
        if _ONNX_DLL_READY:
            return

        try:
            handle = os.add_dll_directory(os.fspath(dll_dir))
        except (AttributeError, OSError) as exc:
            logger.warning(
                "Не удалось подключить локальные ONNX CUDA DLL из %s: %s",
                dll_dir,
                exc,
            )
            return

        # Handle нужно хранить всё время жизни процесса.
        _ONNX_DLL_HANDLES.append(handle)
        _ONNX_DLL_READY = True

def _connect(db_path=SCAN_DB_FILE):
    connection = sqlite3.connect(os.fspath(db_path), timeout=60)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=60000")
    return connection


def init_deep_embedding_db(db_path=SCAN_DB_FILE):
    """Create the independent, versioned deep-embedding table."""
    connection = _connect(db_path)
    try:
        connection.executescript(
            """
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
            CREATE TABLE IF NOT EXISTS catalog_intelligence_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        connection.commit()
    finally:
        connection.close()


def _table_exists(connection, name):
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


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


def vector_to_blob(vector):
    """Store normalized deep vectors as float16 to halve catalog size."""
    return np.asarray(vector, dtype=np.float16).reshape(-1).tobytes()


def blob_to_vector(blob, expected_dim=0, dtype="float16"):
    if blob in (None, b""):
        return None
    np_dtype = np.float16 if str(dtype or "").lower() == "float16" else np.float32
    vector = np.frombuffer(blob, dtype=np_dtype).astype(np.float32)
    if expected_dim and vector.size != int(expected_dim):
        return None
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-12 else vector


def _available_onnx_providers():
    _prepare_onnx_cuda_dlls() # добавил 18-08-26
    try:
        import onnxruntime as ort
        return list(ort.get_available_providers())
    except Exception:
        return []


def _provider_plan(requested, available):
    requested = str(requested or "auto").strip().lower()
    available = set(available or [])
    accelerators = [
        name for name in (
            "CUDAExecutionProvider", "DmlExecutionProvider", "CoreMLExecutionProvider"
        ) if name in available
    ]
    if requested == "cpu":
        return ["CPUExecutionProvider"], False
    if requested == "cuda":
        if "CUDAExecutionProvider" in available:
            return ["CUDAExecutionProvider", "CPUExecutionProvider"], False
        return ["CPUExecutionProvider"], True
    if accelerators:
        return [accelerators[0], "CPUExecutionProvider"], False
    return ["CPUExecutionProvider"], False


def deep_runtime_status(settings=None, model_path=DISCOGS_EFFNET_MODEL_FILE):
    settings = settings or {}
    available = _available_onnx_providers()
    requested = str(settings.get("effnet_device", "auto")).lower()
    plan, fallback = _provider_plan(requested, available)
    dependency = importlib.util.find_spec("onnxruntime") is not None
    return {
        "model_id": MODEL_ID,
        "model_label": MODEL_LABEL,
        "enabled": bool(settings.get("effnet_enabled", False)),
        "dependency_available": dependency,
        "model_exists": Path(model_path).is_file(),
        "model_path": os.fspath(model_path),
        "model_url": MODEL_URL,
        "requested_device": requested,
        "available_providers": available,
        "provider_plan": plan,
        "cuda_available": "CUDAExecutionProvider" in available,
        "fallback_to_cpu": fallback,
    }


def _session_options(cpu_threads):
    _prepare_onnx_cuda_dlls() # добавил 18-08-26
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.intra_op_num_threads = max(1, int(cpu_threads))
    options.inter_op_num_threads = 1
    options.enable_mem_pattern = True
    options.enable_cpu_mem_arena = True
    return options


def _get_session(model_path, requested_device="auto", cpu_threads=1, force_cpu=False):
    _prepare_onnx_cuda_dlls() # добавил 18-08-26
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise RuntimeError("onnxruntime не установлен") from exc
    path = os.path.abspath(os.fspath(model_path))
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"Модель {MODEL_LABEL} не найдена: {path}"
        )
    available = ort.get_available_providers()
    plan, fallback = _provider_plan("cpu" if force_cpu else requested_device, available)
    key = (path, tuple(plan), max(1, int(cpu_threads)))
    with _SESSION_LOCK:
        cached = _SESSION_CACHE.get(key)
        if cached is not None:
            return cached, fallback
        try:
            session = ort.InferenceSession(
                path,
                sess_options=_session_options(cpu_threads),
                providers=plan,
            )
        except Exception:
            if plan == ["CPUExecutionProvider"]:
                raise
            logger.warning("EffNet accelerator initialization failed; using CPU", exc_info=True)
            return _get_session(path, "cpu", cpu_threads, force_cpu=True)[0], True

        model_input = session.get_inputs()[0]
        input_shape = list(model_input.shape)
        if len(input_shape) not in {3, 4}:
            raise RuntimeError(f"Неожиданная форма входа EffNet: {input_shape}")
        if input_shape[-2:] != [PATCH_FRAMES, MEL_BANDS]:
            raise RuntimeError(f"EffNet ожидает другую форму mel-спектрограммы: {input_shape}")
        payload = {
            "session": session,
            "input_name": model_input.name,
            "output_name": session.get_outputs()[0].name,
            "input_rank": len(input_shape),
            "provider": session.get_providers()[0],
        }
        _SESSION_CACHE[key] = payload
        return payload, fallback


def _audio_to_patch(audio):
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    minimum_samples = (PATCH_FRAMES - 1) * HOP_SIZE + FRAME_SIZE
    if audio.size < minimum_samples:
        audio = np.pad(audio, (0, minimum_samples - audio.size))
    spectrum = np.abs(librosa.stft(
        audio,
        n_fft=FRAME_SIZE,
        hop_length=HOP_SIZE,
        win_length=FRAME_SIZE,
        window="hann",
        center=True,
        pad_mode="constant",
    ))
    mel = librosa.feature.melspectrogram(
        S=spectrum,
        sr=SAMPLE_RATE,
        n_fft=FRAME_SIZE,
        hop_length=HOP_SIZE,
        n_mels=MEL_BANDS,
        fmin=0.0,
        fmax=SAMPLE_RATE / 2.0,
        htk=False,
        norm="slaney",
        power=1.0,
    )
    bands = np.log10(1.0 + 10000.0 * np.maximum(mel, 0.0)).T
    if bands.shape[0] < PATCH_FRAMES:
        bands = np.pad(bands, ((0, PATCH_FRAMES - bands.shape[0]), (0, 0)))
    start = max(0, (bands.shape[0] - PATCH_FRAMES) // 2)
    patch = bands[start:start + PATCH_FRAMES]
    return np.nan_to_num(patch, copy=False).astype(np.float32, copy=False)


def _load_track_patches(path, offsets, duration):
    patches = []
    errors = []
    for offset in offsets:
        attempts = [float(offset)]
        if float(offset) > 0:
            attempts.append(0.0)
        audio = None
        for candidate_offset in attempts:
            try:
                loaded, _ = librosa.load(
                    os.fspath(path),
                    sr=SAMPLE_RATE,
                    mono=True,
                    offset=candidate_offset,
                    duration=duration,
                    dtype=np.float32,
                    # ``soxr`` is a direct librosa dependency.  Unlike
                    # ``kaiser_fast`` it does not require the optional
                    # resampy package to be installed separately.
                    res_type="soxr_hq",
                )
                if loaded.size:
                    audio = loaded
                    break
            except Exception as exc:
                errors.append(str(exc))
        if audio is not None:
            patches.append(_audio_to_patch(audio))
    if not patches:
        raise RuntimeError(errors[-1] if errors else "аудио не декодировано")
    return patches


# Старая функция не работала проверить ниже будет новые взамен
# def _safe_track_path(music_dir, rel_path):
#     root = os.path.abspath(os.fspath(music_dir))
#     candidate = os.path.abspath(os.path.join(root, os.fspath(rel_path)))
#     try:
#         if os.path.commonpath([root, candidate]) != root:
#             raise ValueError("путь выходит за пределы музыкальной библиотеки")
#     except ValueError as exc:
#         raise ValueError(f"Некорректный путь трека: {rel_path}") from exc
#     return candidate

def _path_is_within_root(root, candidate):
    root_cmp = os.path.normcase(
        os.path.normpath(os.path.abspath(os.fspath(root)))
    ).rstrip("\\/")

    candidate_cmp = os.path.normcase(
        os.path.normpath(os.path.abspath(os.fspath(candidate)))
    )

    return (
        candidate_cmp == root_cmp
        or candidate_cmp.startswith(root_cmp + os.sep)
    )


def _safe_track_path(music_dir, rel_path):
    root = os.path.abspath(os.fspath(music_dir))
    relative = os.fspath(rel_path)
    if ".." in relative.replace("/", "\\").split("\\"):
        raise ValueError(f"Некорректный путь трека: {rel_path}")
    candidate = os.path.abspath(os.path.join(root, relative))

    if not _path_is_within_root(root, candidate):
        raise ValueError(f"Некорректный путь трека: {rel_path}")

    return candidate


def _preprocess_row(row, music_dir, offsets, duration):
    rel_path, mtime = row["rel_path"], row["mtime"]
    try:
        full_path = _safe_track_path(music_dir, rel_path)
        if not os.path.isfile(full_path):
            raise FileNotFoundError(full_path)
        return {
            "rel_path": rel_path,
            "mtime": mtime,
            "patches": _load_track_patches(full_path, offsets, duration),
            "error": "",
        }
    except Exception as exc:
        return {
            "rel_path": rel_path,
            "mtime": mtime,
            "patches": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def _auto_preprocess_workers(configured, provider):
    if int(configured or 0) > 0:
        return max(1, min(int(configured), 8))
    cpu_total = max(1, os.cpu_count() or 1)
    # Network audio decoding benefits from a small overlap.  A conservative
    # cap prevents the memory spikes previously seen in ProcessPool scanning.
    return max(1, min(4 if provider != "CPUExecutionProvider" else 2, cpu_total // 2))


def _run_batch(session_info, patches):
    valid = len(patches)
    if valid <= 0 or valid > MODEL_BATCH_SIZE:
        raise ValueError("Некорректный размер пакета EffNet")
    batch = np.zeros((MODEL_BATCH_SIZE, PATCH_FRAMES, MEL_BANDS), dtype=np.float32)
    batch[:valid] = np.asarray(patches, dtype=np.float32)
    if session_info["input_rank"] == 4:
        batch = batch[:, None, :, :]
    output = session_info["session"].run(
        [session_info["output_name"]],
        {session_info["input_name"]: batch},
    )[0]
    output = np.asarray(output, dtype=np.float32)
    if output.ndim != 2 or output.shape[0] < valid:
        raise RuntimeError(f"Неожиданный выход EffNet: {output.shape}")
    return output[:valid]


def _absolute_preprocess(path, offsets, duration):
    try:
        return {
            "path": os.path.abspath(os.fspath(path)),
            "patches": _load_track_patches(path, offsets, duration),
            "error": "",
        }
    except Exception as exc:
        return {
            "path": os.path.abspath(os.fspath(path)),
            "patches": [],
            "error": f"{type(exc).__name__}: {exc}",
        }


def extract_deep_embedding(path, settings, model_path=DISCOGS_EFFNET_MODEL_FILE):
    """Extract one normalized vector, reusing the process-wide ONNX session."""
    runtime = deep_runtime_status(settings, model_path)
    if not runtime["enabled"]:
        return None
    session_info, _fallback = _get_session(
        model_path,
        settings.get("effnet_device", "auto"),
        max(1, min(os.cpu_count() or 1, 6)),
    )
    offsets = list(settings.get("effnet_segment_offsets") or [30.0, 60.0, 90.0])[:5]
    duration = max(2.05, float(settings.get("effnet_segment_duration", 2.2)))
    patches = _load_track_patches(path, offsets, duration)
    try:
        vectors = _run_batch(session_info, patches[:MODEL_BATCH_SIZE])
    except Exception as exc:
        if session_info["provider"] == "CPUExecutionProvider":
            raise
        logger.warning("Live EffNet accelerator failed; retrying on CPU: %s", exc)
        session_info, _ = _get_session(
            model_path,
            "cpu",
            max(1, min(os.cpu_count() or 1, 3)),
            force_cpu=True,
        )
        vectors = _run_batch(session_info, patches[:MODEL_BATCH_SIZE])
    vector = np.mean(np.asarray(vectors, dtype=np.float32), axis=0)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-12 else vector


def cached_library_embedding(path, music_dir, db_path=SCAN_DB_FILE, model_id=MODEL_ID):
    """Read a fresh library vector without loading ONNX or decoding audio."""
    if not path or not music_dir or not os.path.isfile(db_path):
        return None
    # изначальный кусок нижебудет новый
    # root = os.path.abspath(os.fspath(music_dir))
    # absolute = os.path.abspath(os.fspath(path))
    # try:
    #     if os.path.commonpath([root, absolute]) != root:
    #         return None
    # except ValueError:
    #     return None
    root = os.path.abspath(os.fspath(music_dir))
    absolute = os.path.abspath(os.fspath(path))

    if not _path_is_within_root(root, absolute):
        return None
    rel_path = os.path.relpath(absolute, root)
    connection = _connect(db_path)
    try:
        row = connection.execute(
            """
            SELECT de.embedding, de.embedding_dim, de.embedding_dtype, de.mtime
            FROM track_deep_embeddings de
            WHERE de.rel_path=? AND de.model_id=? AND de.status='completed'
            """,
            (rel_path, model_id),
        ).fetchone()
        if not row:
            return None
        try:
            if abs(float(row["mtime"]) - float(os.path.getmtime(absolute))) > 0.01:
                return None
        except (OSError, TypeError, ValueError):
            return None
        return blob_to_vector(
            row["embedding"], row["embedding_dim"], row["embedding_dtype"]
        )
    finally:
        connection.close()


def _training_embedding_signature(settings, model_path):
    stat = os.stat(model_path)
    payload = {
        "model_id": MODEL_ID,
        "model_size": int(stat.st_size),
        "model_mtime_ns": int(stat.st_mtime_ns),
        "offsets": list(settings.get("effnet_segment_offsets") or [30.0, 60.0, 90.0])[:5],
        "duration": float(settings.get("effnet_segment_duration", 2.2)),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _training_embedding_key(path, signature):
    value = signature + "\0" + os.path.normcase(os.path.abspath(os.fspath(path)))
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()


def _open_training_embedding_cache(cache_path=TRAINING_FEATURE_CACHE_FILE):
    Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(os.fspath(cache_path), timeout=60)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS training_effnet_embeddings (
            cache_key TEXT PRIMARY KEY,
            signature TEXT NOT NULL,
            path TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            file_mtime_ns INTEGER NOT NULL,
            embedding BLOB NOT NULL,
            embedding_dim INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_training_effnet_signature "
        "ON training_effnet_embeddings(signature)"
    )
    connection.commit()
    return connection


def _file_fingerprint(path):
    try:
        stat = os.stat(path)
        return int(stat.st_size), int(stat.st_mtime_ns)
    except OSError:
        return None


def _load_training_embedding_cache(paths, signature, cache_path):
    if not os.path.isfile(cache_path):
        return {}
    connection = _open_training_embedding_cache(cache_path)
    try:
        rows = connection.execute(
            "SELECT cache_key, file_size, file_mtime_ns, embedding, embedding_dim "
            "FROM training_effnet_embeddings WHERE signature=?",
            (signature,),
        ).fetchall()
    finally:
        connection.close()
    stored = {row[0]: row[1:] for row in rows}
    result = {}
    for path in paths:
        absolute = os.path.abspath(os.fspath(path))
        row = stored.get(_training_embedding_key(absolute, signature))
        fingerprint = _file_fingerprint(absolute)
        if row is None or fingerprint is None or fingerprint != (int(row[0]), int(row[1])):
            continue
        vector = blob_to_vector(row[2], row[3], "float16")
        if vector is not None:
            result[absolute] = vector
    return result


def _save_training_embedding_cache(vectors, signature, cache_path):
    now = _dt.datetime.now().isoformat()
    rows = []
    for path, vector in vectors.items():
        fingerprint = _file_fingerprint(path)
        if fingerprint is None or vector is None:
            continue
        vector = np.asarray(vector, dtype=np.float32).reshape(-1)
        rows.append((
            _training_embedding_key(path, signature), signature, path,
            fingerprint[0], fingerprint[1], vector_to_blob(vector),
            int(vector.size), now,
        ))
    if not rows:
        return 0
    connection = _open_training_embedding_cache(cache_path)
    try:
        connection.executemany(
            """
            INSERT INTO training_effnet_embeddings(
                cache_key, signature, path, file_size, file_mtime_ns,
                embedding, embedding_dim, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                file_size=excluded.file_size,
                file_mtime_ns=excluded.file_mtime_ns,
                embedding=excluded.embedding,
                embedding_dim=excluded.embedding_dim,
                updated_at=excluded.updated_at
            """,
            rows,
        )
        connection.commit()
    finally:
        connection.close()
    return len(rows)


def build_training_embedding_map(
        paths,
        settings,
        *,
        model_path=DISCOGS_EFFNET_MODEL_FILE,
        cache_path=TRAINING_FEATURE_CACHE_FILE,
        progress=None,
        progress_callback=None,
        stop_event=None,
):
    """Build/cached EffNet vectors for training without duplicating ONNX sessions."""
    progress = progress if isinstance(progress, dict) else {}

    def notify():
        if callable(progress_callback):
            try:
                progress_callback(dict(progress))
            except Exception:
                logger.debug("Training EffNet progress callback failed", exc_info=True)
    unique_paths = list(dict.fromkeys(
        os.path.abspath(os.fspath(path)) for path in paths if path
    ))
    runtime = deep_runtime_status(settings, model_path)
    if not runtime["enabled"] or not runtime["model_exists"]:
        report = {
            "enabled": False, "total": len(unique_paths), "completed": 0,
            "cached": 0, "errors": 0,
            "reason": "disabled" if not runtime["enabled"] else "model_missing",
        }
        progress.update(report)
        notify()
        return {}, report

    signature = _training_embedding_signature(settings, model_path)
    vectors = _load_training_embedding_cache(unique_paths, signature, cache_path)
    pending = [path for path in unique_paths if path not in vectors]
    session_info, fallback = _get_session(
        model_path,
        settings.get("effnet_device", "auto"),
        max(1, min(os.cpu_count() or 1, 6)),
    )
    offsets = list(settings.get("effnet_segment_offsets") or [30.0, 60.0, 90.0])[:5]
    duration = max(2.05, float(settings.get("effnet_segment_duration", 2.2)))
    workers = _auto_preprocess_workers(
        settings.get("effnet_preprocess_workers", 0), session_info["provider"]
    )
    tracks_per_batch = max(1, MODEL_BATCH_SIZE // max(1, len(offsets)))
    errors = 0
    processed = len(vectors)
    progress.update({
        "enabled": True, "status": "extracting", "total": len(unique_paths),
        "processed": processed, "cached": len(vectors), "errors": 0,
        "provider": session_info["provider"], "fallback_to_cpu": bool(fallback),
        "preprocess_workers": workers,
    })
    notify()
    forced_cpu = session_info["provider"] == "CPUExecutionProvider"

    with concurrent.futures.ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="training-effnet-preprocess",
    ) as executor:
        for start in range(0, len(pending), tracks_per_batch):
            if stop_event is not None and stop_event.is_set():
                progress["status"] = "stopped"
                break
            batch_paths = pending[start:start + tracks_per_batch]
            prepared = list(executor.map(
                lambda path: _absolute_preprocess(path, offsets, duration), batch_paths
            ))
            patches = []
            owners = []
            for item in prepared:
                for patch in item["patches"]:
                    patches.append(patch)
                    owners.append(item["path"])
            outputs = None
            batch_error = ""
            if patches:
                try:
                    outputs = _run_batch(session_info, patches)
                except Exception as exc:
                    if session_info["provider"] != "CPUExecutionProvider":
                        logger.warning(
                            "Training EffNet accelerator failed; retrying on CPU: %s", exc
                        )
                        session_info, _ = _get_session(
                            model_path,
                            "cpu",
                            max(1, min(os.cpu_count() or 1, 3)),
                            force_cpu=True,
                        )
                        forced_cpu = True
                        progress.update({
                            "provider": session_info["provider"],
                            "fallback_to_cpu": True,
                            "runtime_warning": str(exc),
                        })
                        try:
                            outputs = _run_batch(session_info, patches)
                        except Exception as cpu_exc:
                            batch_error = f"{type(cpu_exc).__name__}: {cpu_exc}"
                    else:
                        batch_error = f"{type(exc).__name__}: {exc}"
            grouped = {}
            if outputs is not None:
                for owner, vector in zip(owners, outputs):
                    grouped.setdefault(owner, []).append(vector)
            completed_batch = {}
            for item in prepared:
                rows = grouped.get(item["path"], [])
                if not rows:
                    errors += 1
                    continue
                vector = np.mean(np.asarray(rows, dtype=np.float32), axis=0)
                norm = float(np.linalg.norm(vector))
                completed_batch[item["path"]] = vector / norm if norm > 1e-12 else vector
            vectors.update(completed_batch)
            _save_training_embedding_cache(completed_batch, signature, cache_path)
            processed += len(prepared)
            progress.update({
                "processed": processed,
                "errors": errors,
                "provider": session_info["provider"],
                "fallback_to_cpu": forced_cpu or bool(fallback),
            })
            if batch_error:
                progress["runtime_warning"] = batch_error
            notify()

    if progress.get("status") != "stopped":
        progress["status"] = "completed"
    report = {
        "enabled": True,
        "status": progress["status"],
        "total": len(unique_paths),
        "completed": len(vectors),
        "cached": int(progress.get("cached", 0)),
        "errors": errors,
        "coverage": float(len(vectors) / len(unique_paths)) if unique_paths else 0.0,
        "provider": session_info["provider"],
        "fallback_to_cpu": forced_cpu or bool(fallback),
        "preprocess_workers": workers,
        "signature": signature,
    }
    progress.update(report)
    notify()
    return vectors, report


def deep_embedding_stats(db_path=SCAN_DB_FILE, model_id=MODEL_ID):
    init_deep_embedding_db(db_path)
    connection = _connect(db_path)
    try:
        if not _table_exists(connection, "scan_results"):
            return {"total": 0, "completed": 0, "pending": 0, "errors": 0, "coverage": 0.0}
        total = int(connection.execute("SELECT COUNT(1) FROM scan_results").fetchone()[0])
        row = connection.execute(
            """
            SELECT
                SUM(CASE WHEN de.status='completed' AND de.embedding IS NOT NULL
                          AND COALESCE(de.mtime, -1)=COALESCE(sr.mtime, -1) THEN 1 ELSE 0 END),
                SUM(CASE WHEN de.status='error' THEN 1 ELSE 0 END)
            FROM scan_results sr
            LEFT JOIN track_deep_embeddings de
              ON de.rel_path=sr.rel_path AND de.model_id=?
            """,
            (model_id,),
        ).fetchone()
        completed, errors = int(row[0] or 0), int(row[1] or 0)
        return {
            "total": total,
            "completed": completed,
            "pending": max(0, total - completed),
            "errors": errors,
            "coverage": float(completed / total) if total else 0.0,
        }
    finally:
        connection.close()


def build_deep_embedding_index(
        music_dir,
        settings,
        *,
        db_path=SCAN_DB_FILE,
        model_path=DISCOGS_EFFNET_MODEL_FILE,
        progress=None,
        stop_event=None,
        retry_failed=False,
        limit=None,
):
    """Index missing or changed tracks in model-sized batches."""
    progress = progress if isinstance(progress, dict) else {}
    stop_event = stop_event or threading.Event()
    init_deep_embedding_db(db_path)
    runtime = deep_runtime_status(settings, model_path)
    if not runtime["enabled"]:
        raise ValueError("Глубокий индекс отключён в настройках")
    if not runtime["model_exists"]:
        raise FileNotFoundError(f"Не найдена модель: {model_path}")

    requested_device = settings.get("effnet_device", "auto")
    cpu_threads = max(1, min(os.cpu_count() or 1, 6))
    session_info, fallback = _get_session(model_path, requested_device, cpu_threads)
    offsets = list(settings.get("effnet_segment_offsets") or [30.0, 60.0, 90.0])[:5]
    duration = max(2.05, float(settings.get("effnet_segment_duration", 2.2)))
    preprocess_workers = _auto_preprocess_workers(
        settings.get("effnet_preprocess_workers", 0), session_info["provider"]
    )
    tracks_per_batch = max(1, MODEL_BATCH_SIZE // max(1, len(offsets)))

    connection = _connect(db_path)
    started = time.monotonic()
    try:
        if not _table_exists(connection, "scan_results"):
            raise ValueError("Сначала выполните основное сканирование библиотеки")
        failed_clause = "" if retry_failed else """
            AND (
                COALESCE(de.status, '') != 'error'
                OR COALESCE(de.mtime, -1) != COALESCE(sr.mtime, -1)
            )
        """
        where = f"""
            (
                de.rel_path IS NULL
                OR COALESCE(de.mtime, -1) != COALESCE(sr.mtime, -1)
                OR de.status NOT IN ('completed', 'error')
                OR de.embedding IS NULL
            )
            {failed_clause}
        """
        total = int(connection.execute(
            f"""
            SELECT COUNT(1)
            FROM scan_results sr
            LEFT JOIN track_deep_embeddings de
              ON de.rel_path=sr.rel_path AND de.model_id=?
            WHERE {where}
            """,
            (MODEL_ID,),
        ).fetchone()[0])
        if limit is not None:
            total = min(total, max(1, int(limit)))
        progress.update({
            "status": "preparing",
            "phase": "deep_index",
            "processed": 0,
            "total": total,
            "errors": 0,
            "provider": session_info["provider"],
            "fallback_to_cpu": bool(fallback),
            "preprocess_workers": preprocess_workers,
            "tracks_per_hour": 0,
            "eta_seconds": None,
            "error": "",
        })
        if not total:
            progress["status"] = "completed"
            return dict(progress)

        cursor = connection.execute(
            f"""
            SELECT sr.rel_path, sr.mtime
            FROM scan_results sr
            LEFT JOIN track_deep_embeddings de
              ON de.rel_path=sr.rel_path AND de.model_id=?
            WHERE {where}
            ORDER BY sr.rel_path
            """,
            (MODEL_ID,),
        )
        processed = 0
        errors = 0
        forced_cpu = session_info["provider"] == "CPUExecutionProvider"
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=preprocess_workers,
                thread_name_prefix="effnet-preprocess",
        ) as executor:
            while processed < total and not stop_event.is_set():
                rows = cursor.fetchmany(min(tracks_per_batch, total - processed))
                if not rows:
                    break
                prepared = list(executor.map(
                    lambda row: _preprocess_row(row, music_dir, offsets, duration), rows
                ))
                patches, owners = [], []
                for item in prepared:
                    for patch in item["patches"]:
                        if len(patches) >= MODEL_BATCH_SIZE:
                            break
                        patches.append(patch)
                        owners.append(item["rel_path"])

                outputs = None
                batch_error = ""
                if patches:
                    try:
                        outputs = _run_batch(session_info, patches)
                    except Exception as exc:
                        if session_info["provider"] != "CPUExecutionProvider":
                            logger.warning("EffNet CUDA inference failed; retrying on CPU: %s", exc)
                            session_info, _ = _get_session(
                                model_path, "cpu", max(1, cpu_threads // 2), force_cpu=True
                            )
                            forced_cpu = True
                            progress.update({
                                "provider": session_info["provider"],
                                "fallback_to_cpu": True,
                                "runtime_warning": str(exc),
                            })
                            outputs = _run_batch(session_info, patches)
                        else:
                            batch_error = f"{type(exc).__name__}: {exc}"

                vectors_by_track = {}
                if outputs is not None:
                    for owner, vector in zip(owners, outputs):
                        vectors_by_track.setdefault(owner, []).append(vector)

                now = _dt.datetime.now().isoformat()
                payload = []
                for item in prepared:
                    track_vectors = vectors_by_track.get(item["rel_path"], [])
                    error = item["error"] or batch_error
                    if track_vectors and not error:
                        vector = np.mean(np.asarray(track_vectors, dtype=np.float32), axis=0)
                        norm = float(np.linalg.norm(vector))
                        if norm > 1e-12:
                            vector = vector / norm
                        payload.append((
                            item["rel_path"], MODEL_ID, item["mtime"], "completed",
                            vector_to_blob(vector), int(vector.size), "float16",
                            len(track_vectors), session_info["provider"], None, now,
                        ))
                    else:
                        errors += 1
                        payload.append((
                            item["rel_path"], MODEL_ID, item["mtime"], "error",
                            None, 0, "float16", 0, session_info["provider"],
                            error or "EffNet не вернул вектор", now,
                        ))
                connection.executemany(
                    """
                    INSERT INTO track_deep_embeddings(
                        rel_path, model_id, mtime, status, embedding, embedding_dim,
                        embedding_dtype, segment_count, provider, error, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(rel_path, model_id) DO UPDATE SET
                        mtime=excluded.mtime,
                        status=excluded.status,
                        embedding=excluded.embedding,
                        embedding_dim=excluded.embedding_dim,
                        embedding_dtype=excluded.embedding_dtype,
                        segment_count=excluded.segment_count,
                        provider=excluded.provider,
                        error=excluded.error,
                        updated_at=excluded.updated_at
                    """,
                    payload,
                )
                connection.commit()
                processed += len(prepared)
                elapsed = max(0.001, time.monotonic() - started)
                speed = processed / elapsed * 3600.0
                remaining = max(0, total - processed)
                progress.update({
                    "status": "indexing",
                    "processed": processed,
                    "total": total,
                    "errors": errors,
                    "provider": session_info["provider"],
                    "fallback_to_cpu": forced_cpu or bool(fallback),
                    "tracks_per_hour": round(speed, 1),
                    "eta_seconds": int(remaining / max(speed / 3600.0, 1e-9)),
                })

        status = "stopped" if stop_event.is_set() else "completed"
        state = {
            "status": status,
            "model_id": MODEL_ID,
            "processed": processed,
            "errors": errors,
            "provider": session_info["provider"],
            "fallback_to_cpu": forced_cpu or bool(fallback),
            "completed_at": _dt.datetime.now().isoformat(),
        }
        _save_state(connection, "deep_embedding_index", state)
        connection.commit()
        progress.update(state)
        return dict(progress)
    except Exception as exc:
        logger.exception("Discogs Multi-EffNet index failed: %s", exc)
        progress.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        try:
            _save_state(connection, "deep_embedding_index", dict(progress))
            connection.commit()
        except sqlite3.Error:
            pass
        return dict(progress)
    finally:
        connection.close()


def download_official_model(
        target_path=DISCOGS_EFFNET_MODEL_FILE,
        *,
        progress=None,
):
    """Download the pinned official model atomically and verify its checksum."""
    progress = progress if isinstance(progress, dict) else {}
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".download")
    progress.update({"status": "downloading", "downloaded": 0, "total": 0, "error": ""})
    try:
        request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "WebMusicPlayer/12"})
        with urllib.request.urlopen(request, timeout=60) as response, open(temporary, "wb") as output:
            total = int(response.headers.get("Content-Length", 0) or 0)
            if total > MODEL_MAX_BYTES:
                raise ValueError("Файл модели неожиданно большой")
            progress["total"] = total
            digest = hashlib.sha256()
            downloaded = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > MODEL_MAX_BYTES:
                    raise ValueError("Превышен безопасный размер загрузки")
                output.write(chunk)
                digest.update(chunk)
                progress["downloaded"] = downloaded
            output.flush()
            os.fsync(output.fileno())
        if digest.hexdigest().lower() != MODEL_SHA256:
            raise ValueError("Контрольная сумма модели не совпала")
        os.replace(temporary, target)
        result = {"status": "completed", "path": os.fspath(target), "bytes": downloaded}
        progress.update(result)
        return result
    except Exception as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        progress.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        return dict(progress)
