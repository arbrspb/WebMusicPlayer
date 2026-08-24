# app/routes.py 14-08-25 01-50
"""
Маршруты Flask-приложения WebMusicPlayer.
Реализует API для управления воспроизведением, плейлистами, сканированием библиотеки, избранным и т.д.
"""
# Стандартные библиотеки
import os
import sys
import sqlite3
import threading
import multiprocessing
import time
import logging
import json
import uuid
from collections import deque
import datetime
from html import escape, unescape

# Третьесторонние библиотеки
import vlc
import sounddevice as sd
from flask import (
    request, redirect, url_for, jsonify, send_file, render_template, session, flash, Response, send_from_directory
)
from urllib.parse import unquote, unquote_plus
# Локальные импорты
from .config import (
    DEFAULT_CONFIG,
    add_recent_folder,
    get_advanced_mode,
    get_intelligence_preferences,
    get_log_flags,
    get_model_pipeline_settings,
    load_config,
    normalize_recent_folders,
    save_config,
    save_log_flags,
)
from .db import (
    create_scan_db_backup,
    get_language_enrichment_stats,
    init_scan_db,
    init_favorite_db,
    FAVORITE_DB,
    SCAN_DB,
    load_scan_result,
    load_scan_taxonomy,
    scan_table_exists,
    scan_db_is_ready
)
from .db import get_track_ratings, set_track_rating
from .collection_health import build_collection_health, effective_stage_status
from .language_enrichment import run_language_enrichment
from .models import (
    apply_training_preparation_assistant,
    get_genre,
    get_training_preparation_assistant,
    get_training_preflight_report,
    quick_training_quality_assessment,
    scan_library_async,
    load_genre_settings,
    save_genre_settings,
    MODEL_PATH
)
from .training_jobs import TrainingJobManager
from .track_similarity import find_similar_track
from .recommendation_history import recommendation_history
from .catalog_intelligence import (
    analyze_track_intelligence,
    build_catalog_index,
    catalog_filter_options,
    catalog_stats,
    find_similar_intelligent,
    find_track_versions,
    get_track_intelligence,
    init_catalog_intelligence_db,
    list_smart_collections,
    match_reference_tracks,
    refresh_catalog_model_labels,
    load_catalog_state,
    save_catalog_state,
    smart_collection_tracks,
    sync_catalog_index,
)
from .deep_embeddings import (
    MODEL_LICENSE_NAME,
    MODEL_LICENSE_URL,
    MODEL_SOURCE_URL,
    build_deep_embedding_index,
    deep_embedding_stats,
    deep_runtime_status,
    download_official_model,
)
from .utils import get_track_title, safe_join_music_dir
from .librosa_settings import load_librosa_settings, save_librosa_settings
from .personalization import (
    apply_personal_rating_model,
    personalization_status,
    train_personal_rating_model,
)
from .training_dataset import (
    add_training_source,
    confirm_high_confidence,
    dataset_summary,
    exclude_training_tracks,
    get_training_disputed_track,
    list_training_folders,
    list_training_problem_folders,
    list_training_disputed_tracks,
    preview_training_track_exclusions,
    preview_training_sources,
    remove_training_source,
    supported_training_labels,
    training_disputed_track_ids,
    update_training_dataset_settings,
    update_training_folders,
    update_training_track_override,
)
from .paths import (
    DISCOGS_EFFNET_MODEL_FILE,
    REKORDBOX_OUTPUT_DIR,
    YAMNET_MODEL_FILE,
    resolve_project_path,
)
from .vocal_language import vocal_language_backend_status
last_actions = deque(maxlen=50)  # Храним последние 50 событий удалить

# Логирование
from .logging_config import (
    is_log_type_enabled,
    setup_status_logger,
    setup_owner_logger,
    setup_owner_status_logger,
    setup_vlc_logger,
    setup_audio_diag_logger,
    setup_player_logger,
    setup_model_logger
)

# status
status_logger = logging.getLogger("status")
setup_status_logger()
# owner status
owner_status_logger = logging.getLogger("owner_status")
setup_owner_status_logger()
# owner
owner_logger = logging.getLogger("owner")
setup_owner_logger()
# VLC
vlc_logger = logging.getLogger("vlc")
setup_vlc_logger()
# audio diag
audio_diag_logger = logging.getLogger("audio_diag")
setup_audio_diag_logger()
# player
player_logger = logging.getLogger("player")
setup_player_logger()
# model
model_logger = logging.getLogger("model")
setup_model_logger()

logger = logging.getLogger(__name__)
training_job_manager = TrainingJobManager()

vlc_lock = threading.Lock()

def get_owner_sid():
    """
    Получает уникальный идентификатор текущей сессии пользователя (owner_sid).
    Если идентификатор отсутствует в session, генерирует новый и сохраняет его.
    Используется для контроля 'владельца' трека/управления плеером.
    """
    if "owner_sid" not in session:
        session["owner_sid"] = str(uuid.uuid4())
    return session["owner_sid"]

# --- Глобальное состояние для хранения текущего трека, плеера, плейлиста и прочего ---
global_state = {
    "current_track": {"path": None, "genre": None, "taxonomy": {}},
    "current_player": None,
    "vlc_instance": None,  # <-- VLC instance будет здесь
    "current_playlist": [],
    "current_playlist_directory": "",
    "current_index": None,
    "current_volume": None,
    "scan_thread": None,
    "scan_stop_event": None,
    "scan_progress": {"status": "stopped", "scanned": 0, "total": 0, "results": {}},
    "language_thread": None,
    "language_stop_event": threading.Event(),
    "language_progress": {
        "status": "idle", "processed": 0, "total": 0, "error": ""
    },
    "training_progress": 0,
    "training_thread": None,
    "training_stop_event": threading.Event(),
    "training_detail": {
        "status": "idle",
        "phase": "idle",
        "message": "Обучение ещё не запускалось",
        "progress": 0,
        "processed": 0,
        "total": 0,
        "started_at": None,
        "updated_at": None,
        "finished_at": None,
    },
    "training_catalog_refresh": {
        "status": "idle", "processed": 0, "total": 0, "error": "",
    },
    "training_dataset_thread": None,
    "training_dataset_progress": {
        "status": "idle", "processed": 0, "total": 0,
        "folders": 0, "tracks": 0, "error": "",
    },
    "quick_quality_thread": None,
    "quick_quality_progress": {
        "status": "idle", "progress": 0,
        "message": "Быстрая оценка ещё не запускалась",
        "result": None, "error": "",
    },
    "intelligence_thread": None,
    "intelligence_stop_event": threading.Event(),
    "intelligence_progress": {
        "status": "idle",
        "processed": 0,
        "total": 0,
        "error": "",
    },
    "personalization_thread": None,
    "personalization_progress": {
        "status": "idle", "processed": 0, "total": 0, "error": ""
    },
    "deep_index_thread": None,
    "deep_index_stop_event": threading.Event(),
    "deep_index_progress": {
        "status": "idle", "processed": 0, "total": 0, "errors": 0, "error": ""
    },
    "audio_devices": sd.query_devices(),
    "selected_device": 0,
}


# Временные разрешённые папки на текущий запуск сервера (сброс при рестарте) для ф-ии musicfile
SESSION_ALLOWED_ROOTS = set()

def get_scanned_genre(rel_path):
    if not get_advanced_mode():
        return "Unknown"
    from .db import load_scan_result
    # Приводим путь к нужному виду для сравнения.
    norm_rel_path = os.path.normpath(rel_path)
    row = load_scan_result(norm_rel_path)
    if row and row[0]:
        return row[0]
    return "Unknown"


def get_scanned_taxonomy(rel_path):
    if not get_advanced_mode() or not rel_path:
        return {}
    return load_scan_taxonomy(os.path.normpath(rel_path))

def get_favorites():
    """Возвращает список путей треков, находящихся в избранном."""
    con = sqlite3.connect(FAVORITE_DB)
    cur = con.cursor()
    cur.execute("SELECT path FROM favorites")
    favs = cur.fetchall()  # это список кортежей
    con.close()
    # Преобразуем кортежи в список строк:
    return [f[0] for f in favs]

def register_routes(app):
    DB_PATH = "favorite.db"
    # --- Миграция таблицы scan_results: добавление YAMNet колонок ---
    from .db import ensure_scan_results_yamnet_columns
    try:
        ensure_scan_results_yamnet_columns()
    except Exception as _mig_e:
        if is_log_type_enabled("model"):
            model_logger.error(f"[DB][MIGRATION] Ошибка ensure_scan_results_yamnet_columns: {_mig_e}")

    init_catalog_intelligence_db()

    def _start_language_worker(retry_failed=False):
        settings = load_librosa_settings()
        if not bool(settings.get("vocal_language_enabled", False)):
            global_state["language_progress"] = {
                "status": "disabled", "processed": 0, "total": 0, "error": ""
            }
            return False
        active = global_state.get("language_thread")
        if active and active.is_alive():
            return False
        if retry_failed:
            from .db import retry_failed_language_enrichment
            retry_failed_language_enrichment()
        global_state["language_stop_event"] = threading.Event()
        global_state["language_progress"] = {
            "status": "queued", "processed": 0, "total": 0, "error": ""
        }
        music_dir = load_config().get("music_dir", DEFAULT_CONFIG["music_dir"])

        def worker():
            try:
                run_language_enrichment(
                    music_dir,
                    settings,
                    global_state["language_stop_event"],
                    global_state["language_progress"],
                )
            finally:
                global_state["language_thread"] = None

        thread = threading.Thread(target=worker, name="vocal-language-enrichment", daemon=True)
        global_state["language_thread"] = thread
        thread.start()
        return True

    def _run_library_scan(*args):
        scan_library_async(*args)
        progress = global_state["scan_progress"]
        error_text = str(progress.get("error_message") or "")
        normalized_error = error_text.lower()
        memory_markers = (
            "memoryerror",
            "outofmemory",
            "unable to allocate",
            "could not allocate",
            "brokenprocesspool",
            "недостаточно памяти",
        )
        should_retry = (
            progress.get("status") == "error"
            and not args[2].is_set()
            and any(marker in normalized_error for marker in memory_markers)
            and not progress.get("memory_auto_retry_used", False)
        )
        if should_retry:
            retry_settings = dict(args[4])
            retry_settings["_scan_worker_limit"] = 2
            progress.update({
                "status": "in_progress",
                "error_message": "",
                "memory_auto_retry_used": True,
                "memory_auto_retry_message": (
                    "Обнаружен дефицит памяти. Сканирование автоматически "
                    "продолжено с двумя воркерами."
                ),
                "max_workers": 2,
            })
            logger.warning(
                "[SCAN][AUTO RETRY] Ошибка памяти: %s. Продолжаем с двумя воркерами.",
                error_text,
            )
            scan_library_async(
                args[0],
                "continue",
                args[2],
                progress,
                retry_settings,
                args[5],
            )
        if progress.get("status") == "completed":
            try:
                indexed_tracks = int(catalog_stats().get("scan_tracks") or 0)
                save_catalog_state("library_scan", {
                    "status": "completed",
                    # Готовность сравнивается с фактическим числом строк индекса,
                    # а не с числом просмотренных файлов (ошибочные MP3 могли быть пропущены).
                    "scan_tracks": indexed_tracks,
                    "total": int(progress.get("total") or 0),
                    "completed_at": datetime.datetime.now().isoformat(),
                })
            except Exception:
                logger.warning("Не удалось сохранить состояние основного индекса", exc_info=True)
            _start_language_worker()

    def _intelligence_path(raw_path=None):
        value = raw_path or global_state["current_track"].get("path")
        value = str(value or "").strip()
        return os.path.normpath(value) if value else ""

    def _intelligence_filters(source):
        source = source if hasattr(source, "get") else {}
        filters = {}
        for key in ("style", "dj_category", "language", "role", "mood", "vocal_mode"):
            value = source.get(key)
            if value not in (None, "", "All", "all"):
                filters[key] = str(value)
        for key in ("bpm_min", "bpm_max", "energy_min", "energy_max", "personal_min"):
            value = source.get(key)
            if value not in (None, ""):
                filters[key] = float(value)
        clean_value = source.get("clean_only")
        filters["clean_only"] = clean_value is True or str(clean_value).lower() in {"1", "true", "yes"}
        return filters

    def _run_catalog_index(limit, dimensions):
        try:
            build_catalog_index(
                limit=limit,
                dimensions=dimensions,
                progress=global_state["intelligence_progress"],
                stop_event=global_state["intelligence_stop_event"],
            )
        finally:
            global_state["intelligence_thread"] = None

    def _run_catalog_sync():
        try:
            sync_result = sync_catalog_index(progress=global_state["intelligence_progress"])
            if sync_result.get("status") == "completed":
                refresh_catalog_model_labels(progress=global_state["intelligence_progress"])
                apply_personal_rating_model(progress=global_state["intelligence_progress"])
        finally:
            global_state["intelligence_thread"] = None

    def _run_personalization_training():
        try:
            pipeline = get_model_pipeline_settings()
            train_personal_rating_model(
                progress=global_state["personalization_progress"],
                use_rekordbox=pipeline["rekordbox_enabled"],
                use_player_ratings=pipeline["player_ratings_enabled"],
                use_deep_embeddings=pipeline["effnet_enabled"],
            )
        except Exception as exc:
            logger.exception("Ошибка обучения персональной модели: %s", exc)
            global_state["personalization_progress"].update({
                "status": "error", "error": str(exc)
            })
        finally:
            global_state["personalization_thread"] = None

    def _run_deep_index(retry_failed=False, limit=None):
        try:
            config_value = load_config()
            pipeline = get_model_pipeline_settings(config_value)
            pipeline["scan_priority"] = config_value.get("scan_priority", "medium")
            build_deep_embedding_index(
                config_value.get("music_dir", DEFAULT_CONFIG["music_dir"]),
                pipeline,
                progress=global_state["deep_index_progress"],
                stop_event=global_state["deep_index_stop_event"],
                retry_failed=retry_failed,
                limit=limit,
            )
        finally:
            global_state["deep_index_thread"] = None

    def _run_effnet_download():
        try:
            download_official_model(progress=global_state["deep_index_progress"])
        finally:
            global_state["deep_index_thread"] = None

    @app.route("/intelligence")
    def intelligence_page():
        if not get_advanced_mode():
            return redirect(url_for("settings"))
        requested_path = _intelligence_path(request.args.get("path"))
        return render_template(
            "intelligence.html",
            config=load_config(),
            current_track=requested_path or global_state["current_track"].get("path") or "",
            embedded=str(request.args.get("embedded", "false")).lower() in {"1", "true", "yes"},
        )

    @app.route("/api/intelligence/stats")
    def intelligence_stats_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        return jsonify(catalog_stats())

    @app.route("/api/collection-health")
    def collection_health_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        config_value = load_config()
        pipeline = get_model_pipeline_settings(config_value)
        analysis = load_librosa_settings()
        catalog = catalog_stats()
        language = get_language_enrichment_stats()
        language.update({
            "running": bool(
                global_state.get("language_thread")
                and global_state["language_thread"].is_alive()
            ),
            "status": (global_state.get("language_progress") or {}).get("status", "idle"),
        })
        deep_status = deep_embedding_stats()
        deep_status.update({
            "running": bool(
                global_state.get("deep_index_thread")
                and global_state["deep_index_thread"].is_alive()
            ),
            "progress": dict(global_state.get("deep_index_progress") or {}),
        })
        personal = personalization_status()
        personal["running"] = bool(
            global_state.get("personalization_thread")
            and global_state["personalization_thread"].is_alive()
        )
        result = build_collection_health(
            scan_tracks=catalog.get("scan_tracks", 0),
            live_scan=dict(global_state.get("scan_progress") or {}),
            saved_scan=load_catalog_state("library_scan", default={}),
            catalog=catalog,
            language=language,
            deep=deep_status,
            personalization=personal,
            pipeline=pipeline,
            analysis=analysis,
        )
        result["runtime"] = {
            "main_running": bool(
                global_state.get("scan_thread") and global_state["scan_thread"].is_alive()
            ),
            "language_running": language["running"],
            "deep_running": deep_status["running"],
            "personal_running": personal["running"],
        }
        return jsonify(result)

    @app.route("/api/intelligence/progress")
    def intelligence_progress_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        payload = dict(global_state["intelligence_progress"])
        payload["running"] = bool(
            global_state.get("intelligence_thread")
            and global_state["intelligence_thread"].is_alive()
        )
        return jsonify(payload)

    @app.route("/api/intelligence/index/start", methods=["POST"])
    def intelligence_index_start_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        active_thread = global_state.get("intelligence_thread")
        if active_thread and active_thread.is_alive():
            return jsonify({"error": "Индекс уже строится"}), 409
        data = request.get_json(silent=True) or {}
        try:
            raw_limit = data.get("limit")
            limit = int(raw_limit) if raw_limit not in (None, "", 0, "0") else None
            if limit is not None and limit < 2:
                raise ValueError
            dimensions = int(data.get("dimensions", 32))
            if dimensions < 2 or dimensions > 64:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "limit должен быть ≥ 2, dimensions — от 2 до 64"}), 400
        global_state["intelligence_stop_event"] = threading.Event()
        global_state["intelligence_progress"] = {
            "status": "queued", "processed": 0, "total": 0, "error": ""
        }
        worker = threading.Thread(
            target=_run_catalog_index,
            args=(limit, dimensions),
            name="catalog-intelligence-index",
            daemon=True,
        )
        global_state["intelligence_thread"] = worker
        worker.start()
        return jsonify({"status": "started", "limit": limit, "dimensions": dimensions})

    @app.route("/api/intelligence/index/stop", methods=["POST"])
    def intelligence_index_stop_api():
        global_state["intelligence_stop_event"].set()
        return jsonify({"status": "stop_requested"})

    @app.route("/api/deep-index/status")
    def deep_index_status_api():
        pipeline = get_model_pipeline_settings()
        stats = deep_embedding_stats()
        progress = dict(global_state["deep_index_progress"])
        running = bool(
            global_state.get("deep_index_thread")
            and global_state["deep_index_thread"].is_alive()
        )
        payload = {
            "settings": pipeline,
            "runtime": deep_runtime_status(pipeline),
            "stats": stats,
            "progress": progress,
            "running": running,
            "effective_status": effective_stage_status(
                runtime_status=progress.get("status", "idle"),
                running=running,
                enabled=bool(pipeline.get("effnet_enabled", False)),
                pending=stats.get("pending", 0),
                processing=1 if running else 0,
                failed=stats.get("errors", 0),
                completed=stats.get("completed", 0),
                total=stats.get("total", 0),
            ),
        }
        return jsonify(payload)

    @app.route("/api/deep-index/start", methods=["POST"])
    def deep_index_start_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        pipeline = get_model_pipeline_settings()
        if not pipeline["effnet_enabled"]:
            return jsonify({"error": "Сначала включите глубокий индекс в центре обработки"}), 400
        runtime = deep_runtime_status(pipeline)
        if not runtime["dependency_available"]:
            return jsonify({"error": "onnxruntime не установлен"}), 400
        if not runtime["model_exists"]:
            return jsonify({"error": "Модель Discogs Multi-EffNet ещё не загружена"}), 400
        if global_state.get("scan_thread") and global_state["scan_thread"].is_alive():
            return jsonify({"error": "Сначала приостановите основной индекс"}), 409
        if global_state.get("language_thread") and global_state["language_thread"].is_alive():
            return jsonify({"error": "Сначала приостановите уточнение языка Whisper"}), 409
        active = global_state.get("deep_index_thread")
        if active and active.is_alive():
            return jsonify({"error": "Глубокий индекс уже обрабатывается"}), 409
        data = request.get_json(silent=True) or {}
        try:
            raw_limit = data.get("limit")
            limit = int(raw_limit) if raw_limit not in (None, "", 0, "0") else None
            if limit is not None and limit < 1:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "Лимит должен быть положительным числом"}), 400
        global_state["deep_index_stop_event"] = threading.Event()
        global_state["deep_index_progress"] = {
            "status": "queued", "processed": 0, "total": 0, "errors": 0, "error": ""
        }
        worker = threading.Thread(
            target=_run_deep_index,
            args=(bool(data.get("retry_failed", False)), limit),
            name="discogs-effnet-index",
            daemon=True,
        )
        global_state["deep_index_thread"] = worker
        worker.start()
        return jsonify({"status": "started", "limit": limit})

    @app.route("/api/deep-index/stop", methods=["POST"])
    def deep_index_stop_api():
        global_state["deep_index_stop_event"].set()
        return jsonify({"status": "stop_requested"})

    @app.route("/api/deep-index/model/download", methods=["POST"])
    def deep_index_model_download_api():
        data = request.get_json(silent=True) or {}
        if data.get("accept_license") is not True:
            return jsonify({
                "error": (
                    "Перед загрузкой подтвердите условия лицензии "
                    f"{MODEL_LICENSE_NAME}."
                ),
                "license": MODEL_LICENSE_NAME,
                "license_url": MODEL_LICENSE_URL,
                "source_url": MODEL_SOURCE_URL,
            }), 409
        if DISCOGS_EFFNET_MODEL_FILE.is_file():
            return jsonify({"status": "already_exists", "path": str(DISCOGS_EFFNET_MODEL_FILE)})
        active = global_state.get("deep_index_thread")
        if active and active.is_alive():
            return jsonify({"error": "Операция глубокого индекса уже выполняется"}), 409
        global_state["deep_index_progress"] = {
            "status": "queued_download", "downloaded": 0, "total": 0, "error": ""
        }
        worker = threading.Thread(
            target=_run_effnet_download,
            name="discogs-effnet-download",
            daemon=True,
        )
        global_state["deep_index_thread"] = worker
        worker.start()
        return jsonify({"status": "started"})

    @app.route("/api/model-pipeline/settings", methods=["GET", "POST"])
    def model_pipeline_settings_api():
        config_value = load_config()
        librosa_value = load_librosa_settings()
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            pipeline = get_model_pipeline_settings(config_value)
            for key in (
                    "effnet_enabled", "effnet_genre_fusion_enabled",
                    "rekordbox_enabled", "player_ratings_enabled",
            ):
                if key in data:
                    pipeline[key] = bool(data[key])
            for key in ("effnet_device", "effnet_preprocess_workers",
                        "effnet_segment_offsets", "effnet_segment_duration",
                        "effnet_genre_fusion_alpha", "effnet_genre_pca_dimensions",
                        "effnet_genre_min_coverage"):
                if key in data:
                    pipeline[key] = data[key]
            config_value["model_pipeline"] = get_model_pipeline_settings({
                **config_value, "model_pipeline": pipeline,
            })
            save_config(config_value)

            for key in ("yamnet_enabled", "yamnet_use_cuda", "vocal_language_enabled"):
                if key in data:
                    librosa_value[key] = bool(data[key])
            if "vocal_language_device" in data:
                device = str(data["vocal_language_device"] or "auto").lower()
                librosa_value["vocal_language_device"] = (
                    device if device in {"auto", "cpu", "cuda"} else "auto"
                )
            # Keep the legacy genre-training option synchronized with the
            # central integration switch, while the player remains usable
            # with its own ratings when Rekordbox is off.
            if "rekordbox_enabled" in data:
                librosa_value["use_rekordbox"] = bool(data["rekordbox_enabled"])
            save_librosa_settings(librosa_value)
            pipeline = get_model_pipeline_settings(config_value)

        runtime = deep_runtime_status(get_model_pipeline_settings(config_value))
        return jsonify({
            "pipeline": get_model_pipeline_settings(config_value),
            "analysis": {
                "yamnet_enabled": bool(librosa_value.get("yamnet_enabled", False)),
                "yamnet_use_cuda": bool(librosa_value.get("yamnet_use_cuda", False)),
                "yamnet_model_exists": YAMNET_MODEL_FILE.is_file(),
                "vocal_language_enabled": bool(librosa_value.get("vocal_language_enabled", False)),
                "vocal_language_device": str(librosa_value.get("vocal_language_device", "auto")),
            },
            "runtime": runtime,
            "whisper": vocal_language_backend_status(librosa_value),
            "engines": [
                {"id": "rf_librosa", "title": "RF + Librosa", "device": "CPU", "optional": False},
                {"id": "yamnet", "title": "YAMNet", "device": (
                    "CUDA/CPU auto" if librosa_value.get("yamnet_use_cuda") else "CPU"
                ), "optional": True},
                {"id": "whisper", "title": "Whisper", "device": vocal_language_backend_status(librosa_value).get("device", "cpu").upper(), "optional": True},
                {"id": "effnet", "title": "Discogs Multi-EffNet", "device": (
                    runtime.get("provider_plan") or ["CPUExecutionProvider"]
                )[0], "optional": True},
                {"id": "personal", "title": "Персональная модель", "device": "CPU", "optional": True},
            ],
        })

    @app.route("/api/intelligence/sync/start", methods=["POST"])
    def intelligence_sync_start_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        active_thread = global_state.get("intelligence_thread")
        if active_thread and active_thread.is_alive():
            return jsonify({"error": "Операция с каталогом уже выполняется"}), 409
        global_state["intelligence_progress"] = {
            "status": "queued", "processed": 0, "total": 0, "error": ""
        }
        worker = threading.Thread(
            target=_run_catalog_sync,
            name="catalog-intelligence-sync",
            daemon=True,
        )
        global_state["intelligence_thread"] = worker
        worker.start()
        return jsonify({"status": "started"})

    @app.route("/api/intelligence/profile")
    def intelligence_profile_api():
        rel_path = _intelligence_path(request.args.get("path"))
        if not rel_path:
            return jsonify({"error": "Не указан трек"}), 400
        profile = get_track_intelligence(rel_path)
        if not profile:
            return jsonify({"error": "Трек ещё не добавлен в интеллектуальный индекс"}), 404
        return jsonify(profile)

    @app.route("/api/intelligence/analyze-current", methods=["POST"])
    def intelligence_analyze_current_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        data = request.get_json(silent=True) or {}
        rel_path = _intelligence_path(data.get("path"))
        if not rel_path:
            return jsonify({"error": "Нет текущего трека"}), 400
        music_dir = load_config().get("music_dir", DEFAULT_CONFIG["music_dir"])
        full_path = safe_join_music_dir(music_dir, rel_path)
        if not os.path.isfile(full_path):
            return jsonify({"error": "Файл недоступен", "full_path": full_path}), 404
        try:
            return jsonify(analyze_track_intelligence(full_path, rel_path=rel_path))
        except Exception as exc:
            logger.exception("Ошибка глубокого анализа трека: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/intelligence/similar")
    def intelligence_similar_api():
        rel_path = _intelligence_path(request.args.get("path"))
        if not rel_path:
            return jsonify({"error": "Не указан трек"}), 400
        try:
            limit = max(1, min(int(request.args.get("limit", 20)), 100))
        except ValueError:
            return jsonify({"error": "Некорректный limit"}), 400
        same_family = str(request.args.get("same_family", "false")).lower() in {"1", "true", "yes"}
        pipeline = get_model_pipeline_settings()
        candidate_limit = min(100, max(limit * 3, limit + 20))
        candidates = find_similar_intelligent(
            rel_path, candidate_limit, same_family, use_deep=pipeline["effnet_enabled"]
        )
        return jsonify({
            "items": recommendation_history.rerank(
                candidates, [rel_path], limit, recommendation_type="quick_similar"
            ),
            "recommendation_history": recommendation_history.stats(),
        })

    @app.route("/api/intelligence/versions")
    def intelligence_versions_api():
        rel_path = _intelligence_path(request.args.get("path"))
        if not rel_path:
            return jsonify({"error": "Не указан трек"}), 400
        return jsonify({"items": find_track_versions(rel_path)})

    @app.route("/api/intelligence/collections")
    def intelligence_collections_api():
        try:
            filters = _intelligence_filters(request.args)
        except (TypeError, ValueError):
            return jsonify({"error": "Некорректные фильтры"}), 400
        return jsonify({"items": list_smart_collections(
            filters=filters,
            scope_prefix=request.args.get("scope_prefix"),
        )})

    @app.route("/api/intelligence/filter-options")
    def intelligence_filter_options_api():
        return jsonify(catalog_filter_options())

    @app.route("/api/intelligence/match", methods=["POST"])
    def intelligence_match_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        data = request.get_json(silent=True) or {}
        references = data.get("references") or []
        if isinstance(references, str):
            references = [references]
        references = [_intelligence_path(path) for path in references if str(path or "").strip()]
        if not references:
            return jsonify({"error": "Добавьте хотя бы один эталонный трек"}), 400
        if len(references) > 10:
            return jsonify({"error": "Можно использовать не более 10 эталонных треков"}), 400
        preferences = get_intelligence_preferences()
        try:
            limit = max(1, min(int(data.get("limit", preferences["result_limit"])), 100))
            filters = _intelligence_filters(data.get("filters") or {})
        except (TypeError, ValueError):
            return jsonify({"error": "Некорректные параметры фильтра"}), 400
        candidate_limit = min(100, max(limit * 3, limit + 20))
        result = match_reference_tracks(
            references,
            limit=candidate_limit,
            filters=filters,
            scope_prefix=data.get("scope_prefix"),
            exclude_versions=bool(data.get("exclude_versions", preferences["exclude_versions"])),
            weights=preferences["weights"],
            use_deep=get_model_pipeline_settings()["effnet_enabled"],
        )
        result["items"] = recommendation_history.rerank(
            result.get("items") or [], references, limit,
            recommendation_type="intelligent_recommendation",
        )
        result["recommendation_history"] = recommendation_history.stats()
        return jsonify(result)

    @app.route("/api/intelligence/recommendation-history", methods=["GET", "DELETE"])
    def intelligence_recommendation_history_api():
        if request.method == "DELETE":
            recommendation_history.clear()
        return jsonify(recommendation_history.stats())

    @app.route("/api/personalization/status")
    def personalization_status_api():
        payload = personalization_status()
        payload["progress"] = dict(global_state["personalization_progress"])
        payload["running"] = bool(
            global_state.get("personalization_thread")
            and global_state["personalization_thread"].is_alive()
        )
        return jsonify(payload)

    @app.route("/api/personalization/train/start", methods=["POST"])
    def personalization_train_start_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        active = global_state.get("personalization_thread")
        if active and active.is_alive():
            return jsonify({"error": "Персональная модель уже обучается"}), 409
        global_state["personalization_progress"] = {
            "status": "queued", "processed": 0, "total": 0, "error": ""
        }
        worker = threading.Thread(
            target=_run_personalization_training,
            name="personal-rating-training",
            daemon=True,
        )
        global_state["personalization_thread"] = worker
        worker.start()
        return jsonify({"status": "started"})

    @app.route("/api/intelligence/collections/<slug>")
    def intelligence_collection_tracks_api(slug):
        try:
            limit = max(1, min(int(request.args.get("limit", 100)), 500))
            offset = max(0, int(request.args.get("offset", 0)))
            filters = _intelligence_filters(request.args)
            items = smart_collection_tracks(
                slug,
                limit=limit,
                offset=offset,
                filters=filters,
                scope_prefix=request.args.get("scope_prefix"),
            )
        except KeyError:
            return jsonify({"error": "Неизвестная умная коллекция"}), 404
        except ValueError:
            return jsonify({"error": "Некорректные limit/offset"}), 400
        return jsonify({"items": items, "slug": slug, "offset": offset})

    def get_active_vlc_devices_default():
        inst = vlc.Instance()
        player = inst.media_player_new()
        out = player.audio_output_device_enum()
        devices = []
        while out:
            dev = out.contents
            device_id = dev.device.decode() if dev.device else ""
            description = dev.description.decode() if dev.description else ""
            if device_id:
                devices.append({'id': device_id, 'name': description})
            out = dev.next
        return devices

    def load_rekordbox_json(path):
        """
        Загружает JSON Rekordbox и возвращает dict: {path: track_dict, ...}
        Если файл уже dict — возвращает как есть.
        Если файл — список, превращает в dict по полю "path".
        """
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
            if isinstance(data, list):
                return {t["path"]: t for t in data if "path" in t}
            return {}
        except Exception as e:
            print(f"Ошибка при загрузке JSON: {e}")
            return {}

    @app.route("/get_scan_config")
    def get_scan_config():
        conf = load_config()
        if is_log_type_enabled("status"):
            status_logger.debug("Получение конфигурации сканирования: %s", conf)
        return jsonify({
            # Сохраняем поле для обратной совместимости старого frontend, но
            # обычный запуск больше не может вернуть опасный постоянный new.
            "scan_mode": "continue",
            "scan_priority": conf.get("scan_priority", "medium")
        })

    @app.route("/retrain", methods=["POST"])
    def retrain():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        quick_thread = global_state.get("quick_quality_thread")
        if quick_thread is not None and quick_thread.is_alive():
            return jsonify({
                "error": "Дождитесь завершения быстрой оценки качества."
            }), 409
        force = request.args.get("force", "") == "1"
        started, status = training_job_manager.start(force=force)
        if not started:
            error = status.get("error") or (
                "Обучение уже выполняется" if status.get("running")
                else "Не удалось запустить отдельный процесс обучения"
            )
            return jsonify({"error": error, "training_error": error, **status}), 409
        return jsonify({
            "status": "Переобучение запущено",
            "training_error": "",
            **status,
        })

    @app.route("/training_status")
    def training_status():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        return jsonify(training_job_manager.status())

    @app.route("/api/training-dataset/quick-quality", methods=["GET", "POST"])
    def training_dataset_quick_quality_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        if request.method == "GET":
            progress = dict(global_state["quick_quality_progress"])
            progress["running"] = bool(
                global_state.get("quick_quality_thread")
                and global_state["quick_quality_thread"].is_alive()
            )
            return jsonify(progress)

        if training_job_manager.status().get("running"):
            return jsonify({
                "error": "Дождитесь завершения полного обучения перед быстрой оценкой."
            }), 409
        current = global_state.get("quick_quality_thread")
        if current is not None and current.is_alive():
            return jsonify({"error": "Быстрая оценка уже выполняется."}), 409

        progress = {
            "status": "running", "progress": 0,
            "message": "Подготовка быстрой оценки", "result": None,
            "error": "", "started_at": datetime.datetime.now().isoformat(),
            "finished_at": None,
        }
        global_state["quick_quality_progress"] = progress

        def update_quick_progress(value, message):
            progress.update({
                "progress": max(0, min(100, int(value))),
                "message": str(message),
                "updated_at": datetime.datetime.now().isoformat(),
            })

        def run_quick_quality():
            try:
                result = quick_training_quality_assessment(update_quick_progress)
                progress.update({
                    "status": result.get("status", "completed"),
                    "progress": 100,
                    "message": result.get("message", "Оценка завершена"),
                    "result": result,
                })
            except Exception as exc:
                status_logger.exception("Ошибка быстрой оценки качества")
                progress.update({
                    "status": "error", "progress": 100,
                    "error": f"{type(exc).__name__}: {exc}",
                    "message": "Быстрая оценка завершилась ошибкой",
                })
            finally:
                progress["finished_at"] = datetime.datetime.now().isoformat()
                global_state["quick_quality_thread"] = None

        thread = threading.Thread(
            target=run_quick_quality,
            name="quick-training-quality",
            daemon=True,
        )
        global_state["quick_quality_thread"] = thread
        thread.start()
        return jsonify({**progress, "running": True}), 202

    @app.route("/health")
    def health():
        training = training_job_manager.status()
        return jsonify({
            "application": "web-music-player",
            "status": "ok",
            "pid": os.getpid(),
            "training_running": bool(training.get("running")),
            "training_job_id": (training.get("detail") or {}).get("job_id"),
        })

    @app.route("/stop_training", methods=["POST"])
    def stop_training():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        stopped, status = training_job_manager.stop()
        if not stopped:
            return jsonify({
                **status,
                "status": status.get("status", "idle"),
                "message": "Активного обучения нет",
            })
        return jsonify({
            **status,
            "status": "stopping",
            "message": "Запрос на безопасную остановку отправлен",
        })

    @app.route("/api/training-dataset")
    def training_dataset_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        result = dataset_summary()
        result["labels"] = supported_training_labels()
        result["progress"] = dict(global_state["training_dataset_progress"])
        return jsonify(result)

    @app.route("/api/training-dataset/plan")
    def training_dataset_plan_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        try:
            return jsonify(get_training_preflight_report())
        except Exception as exc:
            logger.exception("Ошибка построения плана обучающей выборки: %s", exc)
            return jsonify({"error": str(exc)}), 500

    @app.route("/api/training-dataset/settings", methods=["PATCH"])
    def training_dataset_settings_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        payload = request.get_json(silent=True) or {}
        try:
            settings = update_training_dataset_settings(payload)
            result = {"settings": settings}
            if request.args.get("plan", "1") != "0":
                result["plan"] = get_training_preflight_report()
            return jsonify(result)
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/training-dataset/sources", methods=["POST"])
    def training_dataset_add_source_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        payload = request.get_json(silent=True) or {}
        try:
            config_value = load_config()
            source = add_training_source(
                payload.get("path"),
                music_root=config_value.get("music_dir", DEFAULT_CONFIG["music_dir"]),
                recursive=payload.get("recursive", True),
            )
            return jsonify({"source": source, "summary": dataset_summary()})
        except (OSError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/training-dataset/sources/<source_id>", methods=["DELETE"])
    def training_dataset_remove_source_api(source_id):
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        if not remove_training_source(source_id):
            return jsonify({"error": "Источник не найден"}), 404
        return jsonify({"status": "removed", "summary": dataset_summary()})

    @app.route("/api/training-dataset/preview", methods=["POST"])
    def training_dataset_preview_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        current = global_state.get("training_dataset_thread")
        if current is not None and current.is_alive():
            return jsonify({"status": "already_running"}), 202
        progress = {
            "status": "starting", "processed": 0, "total": 0,
            "folders": 0, "tracks": 0, "error": "",
        }
        global_state["training_dataset_progress"] = progress

        def run_preview():
            try:
                preview_training_sources(progress)
            except Exception as exc:
                logger.exception("Ошибка предварительной разметки обучающей выборки: %s", exc)
                progress.update({"status": "error", "error": str(exc)})
            finally:
                global_state["training_dataset_thread"] = None

        thread = threading.Thread(target=run_preview, daemon=True)
        global_state["training_dataset_thread"] = thread
        thread.start()
        return jsonify({"status": "started"}), 202

    @app.route("/api/training-dataset/preview/status")
    def training_dataset_preview_status_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        return jsonify({
            "progress": dict(global_state["training_dataset_progress"]),
            "summary": dataset_summary(),
        })

    @app.route("/api/training-dataset/folders")
    def training_dataset_folders_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        try:
            return jsonify(list_training_folders(
                offset=request.args.get("offset", 0),
                limit=request.args.get("limit", 100),
                status=request.args.get("status"),
                query=request.args.get("q"),
                style=request.args.get("style"),
                track_range=request.args.get("tracks"),
                sort_by=request.args.get("sort", "path"),
                sort_dir=request.args.get("direction", "asc"),
            ))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/training-dataset/problem-folders")
    def training_dataset_problem_folders_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        try:
            return jsonify(list_training_problem_folders(
                offset=request.args.get("offset", 0),
                limit=request.args.get("limit", 100),
                query=request.args.get("q"),
            ))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/training-dataset/disputed-tracks")
    def training_dataset_disputed_tracks_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        config_value = load_config()
        try:
            result = list_training_disputed_tracks(
                offset=request.args.get("offset", 0),
                limit=request.args.get("limit", 50),
                query=request.args.get("q"),
                folder_id=request.args.get("folder_id"),
                style=request.args.get("style"),
                confused_with=request.args.get("confused_with"),
                status=request.args.get("status"),
                music_dir=config_value.get("music_dir", DEFAULT_CONFIG["music_dir"]),
            )
            result["settings"] = dataset_summary().get("settings", {})
            return jsonify(result)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/training-dataset/disputed-tracks/exclusion-preview", methods=["POST"])
    def training_dataset_track_exclusion_preview_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        payload = request.get_json(silent=True) or {}
        config_value = load_config()
        try:
            return jsonify(preview_training_track_exclusions(
                payload.get("track_ids") or [],
                music_dir=config_value.get("music_dir", DEFAULT_CONFIG["music_dir"]),
            ))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/training-dataset/disputed-tracks/filtered-ids")
    def training_dataset_filtered_track_ids_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        config_value = load_config()
        return jsonify(training_disputed_track_ids(
            query=request.args.get("q"), folder_id=request.args.get("folder_id"),
            style=request.args.get("style"),
            confused_with=request.args.get("confused_with"),
            status=request.args.get("status"),
            music_dir=config_value.get("music_dir", DEFAULT_CONFIG["music_dir"]),
        ))

    @app.route("/api/training-dataset/disputed-tracks/bulk-exclude", methods=["POST"])
    def training_dataset_bulk_track_exclusion_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        payload = request.get_json(silent=True) or {}
        config_value = load_config()
        try:
            return jsonify(exclude_training_tracks(
                payload.get("track_ids") or [],
                confirm_large_change=bool(payload.get("confirm_large_change", False)),
                music_dir=config_value.get("music_dir", DEFAULT_CONFIG["music_dir"]),
            ))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/training-dataset/disputed-tracks/<track_id>", methods=["PATCH"])
    def training_dataset_track_override_api(track_id):
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        payload = request.get_json(silent=True) or {}
        config_value = load_config()
        try:
            return jsonify(update_training_track_override(
                track_id,
                payload.get("action"),
                style_override=payload.get("style_override"),
                reason=payload.get("reason", ""),
                confirm_large_change=bool(payload.get("confirm_large_change", False)),
                music_dir=config_value.get("music_dir", DEFAULT_CONFIG["music_dir"]),
            ))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/training-dataset/disputed-tracks/<track_id>/stream")
    def training_dataset_track_preview_stream_api(track_id):
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        config_value = load_config()
        row = get_training_disputed_track(
            track_id,
            music_dir=config_value.get("music_dir", DEFAULT_CONFIG["music_dir"]),
        )
        if row is None:
            return jsonify({"error": "Спорный трек не найден"}), 404
        if not os.path.isfile(row["path"]):
            return jsonify({"error": "Аудиофайл не найден"}), 404
        return send_file(row["path"], mimetype="audio/mpeg", conditional=True)

    @app.route("/api/training-dataset/preparation-assistant")
    def training_dataset_preparation_assistant_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        return jsonify(get_training_preparation_assistant())

    @app.route("/api/training-dataset/preparation-assistant/apply", methods=["POST"])
    def training_dataset_preparation_assistant_apply_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        payload = request.get_json(silent=True) or {}
        try:
            return jsonify(apply_training_preparation_assistant(
                payload.get("folder_ids") or [],
                payload.get("preview_token"),
            ))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/training-dataset/folders", methods=["PATCH"])
    def training_dataset_update_folders_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        payload = request.get_json(silent=True) or {}
        try:
            return jsonify(update_training_folders(
                payload.get("ids") or [],
                status=payload.get("status"),
                taxonomy=payload.get("taxonomy"),
            ))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/api/training-dataset/confirm-high", methods=["POST"])
    def training_dataset_confirm_high_api():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        payload = request.get_json(silent=True) or {}
        try:
            return jsonify(confirm_high_confidence(payload.get("min_confidence", 0.85)))
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400

    @app.route("/check_model")
    def check_model():
        if not get_advanced_mode():
            return jsonify({"exists": False})
        exists = os.path.exists(MODEL_PATH)
        return jsonify({"exists": exists})

    @app.route('/musicfile') # Маршрут для тестового воспроизведения: http://localhost:8080/librosa-test
    def musicfile():
        path = request.args.get("path")
        if not path:
            return "No path", 404
        abs_path = os.path.abspath(unquote(path))
        if os.name == "nt":
            abs_path = abs_path.replace("/", "\\")
        ALLOWED_ROOTS = [ # Разрешенные пути для воспроизведения
            # os.path.abspath(r"D:\Music"),
            #os.path.abspath(r"z:\2025"),
        ]
        all_allowed_roots = ALLOWED_ROOTS + list(SESSION_ALLOWED_ROOTS)

        # Логируем путь и список разрешённых папок (отладка)
        if is_log_type_enabled("status"):
            status_logger.info(f"[musicfile] Проверка доступа: abs_path={abs_path}")
            status_logger.info(f"[musicfile] Текущий список разрешённых папок: {all_allowed_roots}")

        # Логируем попытку доступа к неразрешённой папке
        if not any(abs_path.lower().startswith(root.lower()) for root in all_allowed_roots):
            if is_log_type_enabled("status"):
                status_logger.warning(f"[musicfile] ОТКАЗ в доступе: путь {abs_path} не входит в разрешённые папки")
            return "Forbidden", 403

        # Логируем отсутствие файла
        if not os.path.exists(abs_path):
            if is_log_type_enabled("status"):
                status_logger.warning(f"[musicfile] Файл не найден: {abs_path}")
            return "File not found", 404

        directory, filename = os.path.split(abs_path)
        # Логируем успешную выдачу файла
        if is_log_type_enabled("status"):
            status_logger.info(f"[musicfile] Отправка файла: {abs_path}")
        return send_from_directory(directory, filename, as_attachment=False)

    @app.route('/add_temp_music_root', methods=['POST'])# Маршурт для разрешения временных папок muscifile
    def add_temp_music_root():
        """
        Добавляет папку в список временно разрешённых (на время работы сервера).
        Ожидает POST с полем 'folder'.
        """
        folder = request.form.get('folder')
        if not folder:
            return jsonify({'success': False, 'error': 'No folder provided'}), 400
        folder = os.path.abspath(folder)
        SESSION_ALLOWED_ROOTS.add(folder)
        if is_log_type_enabled("status"):
            status_logger.info(f"Временная разрешённая папка добавлена: {folder}")
        return jsonify({'success': True, 'folder': folder})

    @app.route("/start_scan", methods=["POST"])
    def start_scan():
        # Проверка наличия жанровой модели до инициализации scan_progress и запуска
        # Используем MODEL_PATH из models.py (глобальный путь)
        if not os.path.exists(MODEL_PATH):
            if is_log_type_enabled("model"):
                model_logger.error("genre_model.pkl отсутствует! Необходимо обучить модель.")
            if is_log_type_enabled("status"):
                status_logger.error("Сканирование прервано: отсутствует модель жанров (genre_model.pkl).")
            return jsonify({
                "status": "error",
                "error_message": "Файл модели жанров не найден. Сначала обучите модель!"
            }), 400
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        logger.info("=== ВЫЗВАН /start_scan ===")
        config = load_config()
        request_data = request.get_json(silent=True) or {}
        force_continue = request_data.get("force_continue") is True
        force_new = request_data.get("force_new") is True
        if force_continue and force_new:
            return jsonify({"error": "Нельзя одновременно запросить continue и полный reset"}), 400
        # Отсутствие специальных флагов тоже считается безопасным continue.
        # Старое сохранённое значение scan_mode='new' не должно когда-либо
        # превращать обычный запуск в удаление индекса.
        mode = "new" if force_new else "continue"
        if mode not in {"new", "continue"}:
            logger.error("Некорректный scan_mode в config.json: %s", mode)
            return jsonify({"error": "Некорректный режим сканирования в настройках"}), 400
        reset_confirmed = request_data.get("confirm_reset") is True
        if mode == "new" and not reset_confirmed:
            logger.warning("Полное сканирование отклонено: нет явного подтверждения очистки базы.")
            return jsonify({
                "status": "confirmation_required",
                "error": "Режим «Начать заново» требует отдельного подтверждения очистки базы.",
                "scan_mode": mode,
            }), 409
        global_state["scan_mode_global"] = mode
        logger.info(
            "Запуск сканирования с режимом %s: %s",
            "разового полного reset" if force_new else
            "безопасной проверки новых треков",
            mode,
        )
        if global_state["scan_thread"] and global_state["scan_thread"].is_alive():
            logger.warning("Сканирование уже выполняется.")
            return jsonify({"status": "already scanning"})
        if global_state.get("language_thread") and global_state["language_thread"].is_alive():
            return jsonify({
                "error": "Сначала остановите фоновое уточнение языка перед запуском основного сканирования."
            }), 409
        if global_state.get("deep_index_thread") and global_state["deep_index_thread"].is_alive():
            return jsonify({
                "error": "Сначала приостановите Discogs Multi-EffNet индекс. Аудиоиндексы запускаются по очереди."
            }), 409
        from .db import get_unique_scan_count, scan_table_exists, init_scan_db
        if mode == "new":
            if os.path.exists(SCAN_DB):
                try:
                    backup_result = create_scan_db_backup()
                except Exception as backup_error:
                    logger.exception("Не удалось создать резервную копию scan_results.db")
                    return jsonify({
                        "status": "error",
                        "error": f"Сканирование не запущено: не удалось создать резервную копию базы: {backup_error}",
                    }), 500
                if backup_result.get("backed_up"):
                    logger.info(
                        "Резервная копия базы создана: %s, записей: %s",
                        backup_result.get("path"),
                        backup_result.get("rows"),
                    )
                else:
                    logger.warning(
                        "Текущая scan_results.db пуста; существующий резервный файл не перезаписывается."
                    )
                os.remove(SCAN_DB)
                logger.info("Файл scan_results.db удалён: %s", SCAN_DB)
            init_scan_db()
            global_state["scan_progress"] = {"status": "in_progress", "scanned": 0, "total": 0, "results": {}}
        else:
            if not scan_table_exists():
                # Первый безопасный continue-проход по пустому проекту не
                # является reset: создаём новую пустую БД и индексируем всё.
                logger.info("База сканирования ещё не создана; инициализируем пустой индекс в безопасном continue-режиме.")
                init_scan_db()
            # ВАЖНО: scanned = get_unique_scan_count()
            scanned_count = get_unique_scan_count()
            global_state["scan_progress"] = {
                "status": "in_progress",
                "scanned": scanned_count,
                "total": 0,
                "results": {}
            }
            if is_log_type_enabled("status"):
                status_logger.info(f"[SCAN] Инициализация scan_progress для continue: scanned={scanned_count}")

        # Обычный spawn Event передаётся воркерам через initializer. В отличие от
        # multiprocessing.Manager он не создаёт отдельный тяжёлый Python-процесс
        # и не остаётся висеть после завершения/ошибки сканирования.
        global_state["scan_stop_event"] = multiprocessing.get_context("spawn").Event()
        MUSIC_DIR = load_config().get("music_dir", DEFAULT_CONFIG["music_dir"])
        if is_log_type_enabled("model"):
            model_logger.debug(
                f"[PATH][ROUTES] /start_scan: MUSIC_DIR={MUSIC_DIR}, exists={os.path.exists(MUSIC_DIR)}, isdir={os.path.isdir(MUSIC_DIR)}")

        # === ДОБАВЛЯЕМ: загрузка настроек и Rekordbox JSON ===
        settings = load_librosa_settings()
        settings["scan_priority"] = config.get("scan_priority", "medium")
        settings["defer_vocal_language"] = bool(settings.get("vocal_language_enabled", False))
        logger.info("Приоритет сканирования: %s", settings.get("scan_priority", "medium"))
        if is_log_type_enabled("status"):
            status_logger.info(
                f"[SCAN][START] Запуск сканирования с режимом: {mode}, приоритетом: {settings.get('scan_priority')}"
            )
        # --- YAMNet precheck: мгновенная UI-ошибка до запуска сканирования ---
        yam_enabled = bool(settings.get("yamnet_enabled", False))
        global_state["scan_progress"]["main_index_engine"] = "RF + YAMNet" if yam_enabled else "RF"
        try:
            yam_path = settings.get("yamnet_model_path") or str(YAMNET_MODEL_FILE)
            if yam_enabled:
                resolved_yam = str(resolve_project_path(yam_path, YAMNET_MODEL_FILE))
                if not os.path.isfile(resolved_yam):
                    if is_log_type_enabled("model"):
                        model_logger.error(f"[YAMNET] Включён, но файл не найден: {resolved_yam}")
                    if is_log_type_enabled("status"):
                        status_logger.error(f"[SCAN][START] Отклонён запуск: включён YAMNet, но файл не найден: {resolved_yam}")
                    # Зафиксируем ошибку в глобальном прогрессе на случай, если фронт уже начал опрос /scan_progress
                    global_state["scan_progress"] = {
                        "status": "error",
                        "scanned": 0,
                        "total": 0,
                        "results": {},
                        "error_message": (
                            f"Файл YAMNet не найден: {resolved_yam}. "
                            f"Выключите YAMNet в настройках или укажите корректный путь."
                        )
                    }
                    return jsonify({
                        "status": "error",
                        "error_message": global_state["scan_progress"]["error_message"]
                    }), 400
        except Exception as _e_yam_pre:
            if is_log_type_enabled("model"):
                model_logger.error(f"[YAMNET] Ошибка предчека в /start_scan: {_e_yam_pre}")
        use_rekordbox = settings.get("use_rekordbox", False)
        rekordbox_data = {}
        if use_rekordbox and settings.get("rekordbox_source") == "json":
            rekordbox_json_path = settings.get("rekordbox_json_path") or str(
                REKORDBOX_OUTPUT_DIR / "parsed_rekordbox.json"
            )
            rekordbox_json_path = str(resolve_project_path(rekordbox_json_path))
            if os.path.exists(rekordbox_json_path):
                rekordbox_data = load_rekordbox_json(rekordbox_json_path)
        # === КОНЕЦ ДОБАВЛЕНИЯ ===

        global_state["scan_thread"] = threading.Thread(
            target=_run_library_scan,
            args=(MUSIC_DIR, mode, global_state["scan_stop_event"], global_state["scan_progress"], settings,
                  rekordbox_data)
        )
        global_state["scan_thread"].start()
        return jsonify({"status": "scan started"})

    # @app.route("/scan_progress")  # Маршурт для получения прогресса обучения модели
    # def scan_progress_status():
    #     if not get_advanced_mode():
    #         return jsonify({"error": "Расширенные функции отключены"}), 400
    #     sp = global_state["scan_progress"]
    #     return jsonify({
    #         "scanned": sp.get("scanned", 0),
    #         "total": sp.get("total", 0),
    #         "status": sp.get("status", ""),
    #         "error_message": sp.get("error_message", ""),
    #         "error_tracks": sp["error_tracks"],
    #         "error_count": sp["error_count"],
    #     })

    @app.route("/scan_progress")  # Маршрут для получения прогресса обучения модели
    def scan_progress_status():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        sp = global_state["scan_progress"]

        # Безопасно добавляем поля, если их нет
        error_tracks = sp.get("error_tracks", [])
        error_count = sp.get("error_count", len(error_tracks))

        language_progress = dict(global_state.get("language_progress") or {})
        try:
            language_progress.update(get_language_enrichment_stats())
        except Exception as language_stats_error:
            language_progress.setdefault("error", str(language_stats_error))
        language_progress["running"] = bool(
            global_state.get("language_thread")
            and global_state["language_thread"].is_alive()
        )

        # Во время активного сканирования показываем снимок настроек именно
        # запущенной задачи. В ожидании — текущую настройку следующего запуска.
        current_model_settings = {}
        main_index_engine = sp.get("main_index_engine") if sp.get("status") == "in_progress" else None
        if not main_index_engine:
            try:
                current_model_settings = load_librosa_settings()
                main_index_engine = (
                    "RF + YAMNet"
                    if current_model_settings.get("yamnet_enabled", False)
                    else "RF"
                )
            except Exception:
                main_index_engine = "RF"
        if not current_model_settings:
            try:
                current_model_settings = load_librosa_settings()
            except Exception:
                current_model_settings = {}
        language_progress["enabled"] = bool(current_model_settings.get("vocal_language_enabled", False))

        # new — только разовая команда force_new и не является состоянием UI.
        scan_mode = "continue"

        # Runtime-состояние обнуляется после перезапуска сервера, поэтому UI
        # дополнительно получает сохранённый результат последнего полного
        # прохода. Это только read-only metadata: база и алгоритм сканирования
        # здесь не изменяются.
        try:
            saved_scan_state = load_catalog_state("library_scan", default={}) or {}
        except Exception:
            saved_scan_state = {}

        main_effective_status = effective_stage_status(
            runtime_status=sp.get("status", "idle"),
            running=bool(
                global_state.get("scan_thread")
                and global_state["scan_thread"].is_alive()
            ),
            failed=1 if sp.get("status") == "error" else 0,
            persistent_completed=bool(
                saved_scan_state.get("status") == "completed"
                and int(saved_scan_state.get("scan_tracks") or 0) > 0
            ),
        )
        language_done = (
            int(language_progress.get("completed") or 0)
            + int(language_progress.get("not_needed") or 0)
        )
        language_total = (
            int(language_progress.get("pending") or 0)
            + int(language_progress.get("processing") or 0)
            + int(language_progress.get("failed") or 0)
            + language_done
        )
        language_effective_status = effective_stage_status(
            runtime_status=language_progress.get("status", "idle"),
            running=language_progress.get("running", False),
            enabled=language_progress.get("enabled", False),
            pending=language_progress.get("pending", 0),
            processing=language_progress.get("processing", 0),
            failed=language_progress.get("failed", 0),
            completed=language_done,
            total=language_total,
        )

        return jsonify({
            "scanned": sp.get("scanned", 0),
            "total": sp.get("total", 0),
            "status": sp.get("status", ""),
            "error_message": sp.get("error_message", ""),
            "error_tracks": error_tracks,
            "error_count": error_count,
            "intelligence_sync": sp.get("intelligence_sync", {}),
            "model_label_refresh": sp.get("model_label_refresh", {}),
            "main_index_engine": main_index_engine,
            "scan_mode": scan_mode,
            "saved_scan_state": saved_scan_state,
            "language_enrichment": language_progress,
            "effective_state": {
                "main": main_effective_status,
                "language": language_effective_status,
            },
        })

    @app.route("/start_language_enrichment", methods=["POST"])
    def start_language_enrichment_route():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        if global_state.get("scan_thread") and global_state["scan_thread"].is_alive():
            return jsonify({"error": "Сначала дождитесь завершения основного индекса"}), 409
        if global_state.get("deep_index_thread") and global_state["deep_index_thread"].is_alive():
            return jsonify({"error": "Сначала приостановите Discogs Multi-EffNet индекс"}), 409
        data = request.get_json(silent=True) or {}
        started = _start_language_worker(retry_failed=data.get("retry_failed") is True)
        return jsonify({
            "status": "started" if started else "already_running_or_disabled",
            "language_enrichment": dict(global_state.get("language_progress") or {}),
        })

    @app.route("/stop_language_enrichment", methods=["POST"])
    def stop_language_enrichment_route():
        global_state["language_stop_event"].set()
        return jsonify({"status": "stopping"})

    @app.route("/stop_scan")
    def stop_scan():
        try:
            global_state["language_stop_event"].set()
            if global_state["scan_stop_event"]:
                try:
                    global_state["scan_stop_event"].set()
                    logger.info("Запрос на остановку сканирования отправлен.")
                    if is_log_type_enabled("status"):
                        status_logger.info("[SCAN] Запрос на остановку сканирования отправлен.")
                except Exception as exc:
                    logger.error(f"[SCAN] Ошибка при установке scan_stop_event: {exc}")
                    if is_log_type_enabled("status"):
                        status_logger.error(f"[SCAN] Ошибка при остановке сканирования: {exc}")
            scan_thread = global_state.get("scan_thread")
            if scan_thread and scan_thread.is_alive():
                try:
                    scan_thread.join(timeout=30)
                    logger.info("[SCAN] Сканиующий поток остановлен (join).")
                except Exception as exc:
                    logger.error(f"[SCAN] Ошибка при остановке сканирующего потока: {exc}")
                    if is_log_type_enabled("status"):
                        status_logger.error(f"[SCAN] Ошибка при остановке сканирующего потока: {exc}")
        except Exception as exc:
            logger.error(f"[SCAN] Неожиданная ошибка при остановке сканирования: {exc}")
            if is_log_type_enabled("status"):
                status_logger.error(f"[SCAN] Неожиданная ошибка при остановке сканирования: {exc}")
        return jsonify({"status": "scan stopping"})

    # @app.route("/update_scan_config", methods=["POST"])
    # def update_scan_config():
    #     if not get_advanced_mode():
    #         return jsonify({"error": "Расширенные функции отключены"}), 400
    #     data = request.get_json()
    #     scan_mode = data.get("scan_mode")
    #     if scan_mode not in ["new", "continue"]:
    #         return jsonify({"error": "Неверное значение scan_mode"}), 400
    #     conf = load_config()
    #     conf["scan_mode"] = scan_mode
    #     save_config(conf)
    #     logger.info("Сканировочный режим обновлён: %s", scan_mode)
    #     return jsonify({"status": "Сканировочный режим обновлен", "scan_mode": scan_mode})

    @app.route("/update_scan_config", methods=["POST"])
    def update_scan_config():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        data = request.get_json(force=True)
        scan_mode = data.get("scan_mode", "continue")
        scan_priority = data.get("scan_priority", "medium")
        # Полный reset больше не является сохраняемой настройкой. Он доступен
        # только как отдельный force_new-запрос с явным подтверждением.
        if scan_mode != "continue":
            return jsonify({
                "error": "Режим new не сохраняется. Используйте отдельное действие полного пересканирования."
            }), 400
        config = load_config()
        config["scan_mode"] = scan_mode
        config["scan_priority"] = scan_priority
        save_config(config)
        # Логирование: старый стиль + тип "status"
        logger.info("Сканировочный режим обновлён: %s, приоритет: %s", scan_mode, scan_priority)
        if is_log_type_enabled("status"):
            status_logger.info(
                f"[SCAN][CONFIG] scan_mode={scan_mode}, scan_priority={scan_priority} сохранены в config.json"
            )
        return jsonify({"scan_mode": scan_mode, "scan_priority": scan_priority})

    @app.route("/")
    def index():
        return redirect(url_for("browse"))

    @app.route("/browse")
    def browse():
        config = load_config()
        remember_folders = bool(config.get("remember_recent_folders", False))
        recent_folders = normalize_recent_folders(config.get("recent_folders", []))
        path = request.args.get("path")
        path_source = "request" if path else "root"
        autoplay = request.args.get("autoplay", "")  # извлекаем параметр autoplay, если он есть
        if not path and 'current_folder' in session:
            path = session['current_folder']
            path_source = "session"
        if not path and remember_folders and recent_folders:
            path = recent_folders[0]
            path_source = "recent"
        decoded_path = unquote_plus(path) if path else ""
        if is_log_type_enabled("status"):
            status_logger.debug("Decoded path: %s", decoded_path)

        MUSIC_DIR = config.get("music_dir", DEFAULT_CONFIG["music_dir"])
        if decoded_path:
            norm_decoded_path = os.path.normpath(decoded_path)
            full_dir = safe_join_music_dir(MUSIC_DIR, norm_decoded_path)
        else:
            full_dir = MUSIC_DIR
        if is_log_type_enabled("status"):
            status_logger.debug("Полный путь каталога: %s", full_dir)

        try:
            files = sorted(f for f in os.listdir(full_dir) if f.lower().endswith(".mp3"))
            if is_log_type_enabled("status"):
                status_logger.debug("Найденные файлы: %s", files)
        except OSError as error:
            can_fallback = path_source in {"session", "recent"} and bool(decoded_path)
            if not can_fallback:
                logger.error("Каталог недоступен: %s: %s", full_dir, error)
                status_code = 404 if isinstance(error, FileNotFoundError) else 503
                return f"Directory unavailable: {full_dir}", status_code

            logger.warning("Сохранённая папка недоступна, открываем корень: %s: %s", full_dir, error)
            session.pop("current_folder", None)
            if remember_folders:
                normalized_failed = decoded_path.replace("\\", "/").casefold()
                recent_folders = [
                    folder for folder in recent_folders
                    if folder.replace("\\", "/").casefold() != normalized_failed
                ]
                config["recent_folders"] = recent_folders
                save_config(config)
            decoded_path = ""
            full_dir = MUSIC_DIR
            try:
                files = sorted(f for f in os.listdir(full_dir) if f.lower().endswith(".mp3"))
            except OSError as root_error:
                logger.error("Корень музыкальной библиотеки недоступен: %s: %s", full_dir, root_error)
                return "Музыкальная библиотека сейчас недоступна. Проверьте диск или сетевое подключение.", 503

        if remember_folders and decoded_path:
            recent_folders = add_recent_folder(recent_folders, decoded_path)
            if recent_folders != config.get("recent_folders", []):
                config["recent_folders"] = recent_folders
                save_config(config)
            session["current_folder"] = decoded_path

        current_path_clean = decoded_path.replace('\\', '/') if decoded_path else ''
        if is_log_type_enabled("player"):
            player_logger.debug("Формируется область воспроизведения для %s", current_path_clean)

        # Добавляем список избранного
        favorites = get_favorites()
        # ===  проверка готовности базы! ===
        scan_ready = scan_db_is_ready()

        return render_template("main.html",
                               files=files,
                               current_path=current_path_clean,
                               favorites=favorites,
                               favorite_ratings=get_track_ratings(),
                               devices=global_state["audio_devices"],
                               selected_device=global_state["selected_device"],
                               current_track=session.get("current_track"),
                               current_genre=global_state["current_track"].get("genre"),
                               config=config,
                               enumerate=enumerate,
                               autoplay=autoplay,
                               scan_ready=scan_ready
                               )

    @app.route("/play")
    def play_track():
        path = request.args.get("path")
        if not path:
            logger.error("Нет переданного параметра path")
            return jsonify({"error": "No path provided"}), 400

        decoded_path = unescape(unquote_plus(path))
        if is_log_type_enabled("status"):
            status_logger.debug("DEBUG: decoded_path = %r", decoded_path)
        norm_rel_path = os.path.normpath(decoded_path)
        if is_log_type_enabled("status"):
            status_logger.debug("DEBUG: norm_rel_path = %r", norm_rel_path)
        track_title = get_track_title(norm_rel_path)
        MUSIC_DIR = load_config().get("music_dir", DEFAULT_CONFIG["music_dir"])
        if is_log_type_enabled("status"):
            status_logger.debug("DEBUG: MUSIC_DIR = %r", MUSIC_DIR)
        full_path = safe_join_music_dir(MUSIC_DIR, norm_rel_path)
        if is_log_type_enabled("status"):
            status_logger.debug("DEBUG: full_path = %r", full_path)

        if not os.path.isfile(full_path):
            logger.error("Файл не найден: %s", full_path)
            return jsonify({"error": "File not found", "full_path": full_path}), 404

        folder = os.path.dirname(norm_rel_path)
        global_state["current_playlist_directory"] = folder
        session["current_folder"] = folder

        try:
            norm_folder = os.path.normpath(folder)
            playlist_dir = safe_join_music_dir(MUSIC_DIR, norm_folder)
            playlist = sorted(
                f for f in os.listdir(playlist_dir)
                if f.lower().endswith(".mp3")
            )
        except Exception as e:
            logger.error("Ошибка формирования плейлиста: %s", e)
            playlist = []
        global_state["current_playlist"] = playlist

        file_name = os.path.basename(norm_rel_path)
        if file_name in playlist:
            global_state["current_index"] = playlist.index(file_name)
        else:
            global_state["current_index"] = 0

        config = load_config()
        mode = config.get("playback_mode", "host")
        play_url = None

        if mode == "host":
            with vlc_lock:
                try:
                    # Останавливаем и пересоздаём плеер
                    if global_state["current_player"] is not None:
                        try:
                            global_state["current_player"].stop()
                            if is_log_type_enabled("audio_diag"):
                                audio_diag_logger.debug("[AUDIO DIAG] Остановлен старый плеер при запуске нового трека")
                        except Exception as ex:
                            logger.warning("Ошибка при остановке плеера: %s", ex)
                    # Создаём новый экземпляр media_player
                    vlc_instance = global_state.get("vlc_instance")
                    player = vlc_instance.media_player_new()
                    if is_log_type_enabled("audio_diag"):
                        audio_diag_logger.debug("[AUDIO DIAG] Создан новый VLC media_player для воспроизведения трека")
                    media = vlc_instance.media_new(full_path)
                    player.set_media(media)
                    # Сохраняем player в глобальное состояние
                    global_state["current_player"] = player

                    # === Критически важно: Получаем устройства для ЭТОГО player ===
                    import time
                    time.sleep(0.1)  # Дать VLC инициализировать media
                    vlc_devices = []
                    out = player.audio_output_device_enum()
                    while out:
                        dev = out.contents
                        device_id = dev.device.decode() if dev.device else ""
                        description = dev.description.decode() if dev.description else ""
                        if device_id:
                            vlc_devices.append({'id': device_id, 'name': description})
                        out = dev.next

                    selected_index = int(config.get("selected_device", 0))
                    if is_log_type_enabled("audio_diag"):
                        audio_diag_logger.debug(f"[AUDIO DIAG] Список найденных VLC-устройств ({len(vlc_devices)}):")
                    for idx, dev in enumerate(vlc_devices):
                        if is_log_type_enabled("audio_diag"):
                            audio_diag_logger.debug(f"[AUDIO DIAG]   idx={idx} id={dev['id']} name={dev['name']}")
                    if is_log_type_enabled("audio_diag"):
                        audio_diag_logger.debug(f"[AUDIO DIAG] selected_index из конфига: {selected_index}")

                    if vlc_devices and selected_index >= len(vlc_devices):
                        if is_log_type_enabled("audio_diag"):
                            audio_diag_logger.debug(
                            "[AUDIO DIAG] Выбранный индекс устройства превышает число устройств. Сбрасываем на 0.")
                        selected_index = 0
                    if vlc_devices:
                        device_id = vlc_devices[selected_index]["id"]
                        if is_log_type_enabled("audio_diag"):
                            audio_diag_logger.debug(
                            f"[AUDIO DIAG] Попытка установить аудиоустройство: {device_id} ({vlc_devices[selected_index]['name']})")
                        player.audio_output_device_set(None, device_id)
                        if is_log_type_enabled("audio_diag"):
                            audio_diag_logger.debug("[AUDIO DIAG] Попытка установить аудиоустройство: %s (%s)",
                                                    device_id, vlc_devices[selected_index]["name"])
                    else:
                        if is_log_type_enabled("audio_diag"):
                            audio_diag_logger.debug("[AUDIO DIAG] Устройство не установлено, используется устройство по умолчанию.")

                    if global_state.get("current_volume") is None:
                        global_state["current_volume"] = config.get("default_volume", 70)
                    volume_to_set = global_state["current_volume"]
                    player.audio_set_volume(volume_to_set)

                    player.play()
                    if is_log_type_enabled("vlc"):
                        vlc_logger.debug("[ДИАГНОСТИКА VLC] Плеер запущен: is_playing=%s, time=%s, track=%s",
                                         player.is_playing(), player.get_time(), norm_rel_path)
                    time.sleep(0.1)
                    try:
                        is_playing = player.is_playing()
                        current_time = player.get_time()
                        if is_log_type_enabled("vlc"):
                            vlc_logger.debug("[ДИАГНОСТИКА VLC] Плеер запущен: is_playing=%s, time=%s, track=%s", is_playing,
                                     current_time, norm_rel_path)
                    except Exception as diag_ex:
                        if is_log_type_enabled("vlc"):
                            vlc_logger.debug("[ДИАГНОСТИКА VLC] Ошибка при проверке нового плеера: %s", diag_ex)
                except Exception as e:
                    logger.error("Error playing file (host): %s", str(e))
                    return jsonify({"error": str(e)}), 500
        elif mode == "plyr":
            play_url = url_for("stream", path=decoded_path)
        else:
            return jsonify({"error": "Неверный режим воспроизведения"}), 400

        genre = get_scanned_genre(norm_rel_path)
        global_state["current_track"]["path"] = norm_rel_path
        global_state["current_track"]["genre"] = genre
        global_state["current_track"]["taxonomy"] = get_scanned_taxonomy(norm_rel_path)
        global_state["current_track"]["title"] = track_title
        old_owner = global_state["current_track"].get("owner_sid", None)  # Диагностика до присваивания!
        new_owner = get_owner_sid()
        global_state["current_track"]["owner_sid"] = new_owner  # Теперь присваиваем!

        if is_log_type_enabled("owner"):
            owner_logger.debug("[OWNER] Смена владельца: %s → %s (endpoint: %s, ip: %s, track: %s)",
                     old_owner, new_owner, request.endpoint, request.remote_addr,
                     global_state["current_track"].get("path"))  # Диагностика
        session["current_track"] = norm_rel_path

        response = {
            "status": "playing",
            "track": norm_rel_path,
            "title": track_title,
            "genre": genre
        }
        if play_url:
            response["play_url"] = play_url

        if is_log_type_enabled("player"):
            player_logger.debug("Формируется ответ: %s", response)
        return jsonify(response)

    @app.route("/next")
    def next_track():
        # --- Проверка владельца трека ---
        if global_state["current_track"].get("owner_sid") != get_owner_sid():
            if is_log_type_enabled("owner"):
                owner_logger.debug("[OWNER] ОТКАЗ в %s: ip=%s, сессия=%s, expected_owner=%s, трек=%s",  # Диагностика
                         request.endpoint, request.remote_addr, get_owner_sid(),  # Диагностика
                         global_state["current_track"].get("owner_sid"),  # Диагностика
                         global_state["current_track"].get("path"))  # Диагностика
            return jsonify({"error": "You are not the owner of the current track"}), 403

        if not global_state["current_playlist"] or global_state["current_index"] is None:
            return jsonify({"error": "No playlist loaded"}), 400
        global_state["current_index"] = (global_state["current_index"] + 1) % len(global_state["current_playlist"])
        next_file = global_state["current_playlist"][global_state["current_index"]]
        next_path = os.path.join(global_state["current_playlist_directory"], next_file)
        MUSIC_DIR = load_config().get("music_dir", DEFAULT_CONFIG["music_dir"])
        norm_next_path = os.path.normpath(next_path)
        full_path = safe_join_music_dir(MUSIC_DIR, norm_next_path)
        if not os.path.isfile(full_path):
            return jsonify({"error": "File not found", "full_path": full_path}), 404

        config = load_config()
        mode = config.get("playback_mode", "host")
        play_url = None
        if mode == "host":
            with vlc_lock:
                try:
                    # --- Останавливаем и освобождаем предыдущий плеер ---
                    old_player = global_state.get("current_player")
                    if old_player is not None:
                        try:
                            old_player.stop()
                            if is_log_type_enabled("audio_diag"):
                                audio_diag_logger.debug(
                                    "[AUDIO DIAG] Старый VLC media_player освобождён (release) при переключении трека")
                            old_player.release()
                            if is_log_type_enabled("audio_diag"):
                                audio_diag_logger.debug(
                                    "[AUDIO DIAG] Старый VLC media_player освобождён (release) при переключении трека")
                        except Exception as ex:
                            logger.warning("Ошибка при остановке/освобождении старого плеера: %s", ex)

                    # --- Новый блок: пересоздаём плеер для смены аудиоустройства ---
                    vlc_instance = global_state.get("vlc_instance")
                    player = vlc_instance.media_player_new()
                    if is_log_type_enabled("audio_diag"):
                        audio_diag_logger.debug("[AUDIO DIAG] Новый VLC media_player создан при переключении трека")
                    media = vlc_instance.media_new(full_path)
                    player.set_media(media)
                    global_state["current_player"] = player

                    import time
                    time.sleep(0.1)  # Дать инициализироваться media

                    vlc_devices = []
                    out = player.audio_output_device_enum()
                    while out:
                        dev = out.contents
                        device_id = dev.device.decode() if dev.device else ""
                        description = dev.description.decode() if dev.description else ""
                        if device_id:
                            vlc_devices.append({'id': device_id, 'name': description})
                        out = dev.next

                    selected_index = int(config.get("selected_device", 0))
                    if vlc_devices and selected_index >= len(vlc_devices):
                        selected_index = 0
                    if vlc_devices:
                        device_id = vlc_devices[selected_index]["id"]
                        player.audio_output_device_set(None, device_id)
                        if is_log_type_enabled("audio_diag"):
                            audio_diag_logger.debug("[AUDIO DIAG] Попытка установить аудиоустройство: %s (%s)",
                                                    device_id, vlc_devices[selected_index]["name"])

                    if global_state.get("current_volume") is None:
                        global_state["current_volume"] = config.get("default_volume", 70)
                    volume_to_set = global_state["current_volume"]
                    player.audio_set_volume(volume_to_set)

                    player.play()
                    if is_log_type_enabled("vlc"):
                        vlc_logger.debug("[ДИАГНОСТИКА VLC] Плеер запущен: is_playing=%s, time=%s, track=%s",
                                         player.is_playing(), player.get_time(), next_path)

                    play_url = None
                except Exception as e:
                    return jsonify({"error": str(e)}), 500
        elif mode == "plyr":
            play_url = url_for("stream", path=next_path)
            global_state["current_player"] = None
        else:
            return jsonify({"error": "Неверный режим воспроизведения"}), 400

        # Обновляем глобальное состояние текущего трека с использованием next_path
        genre = get_scanned_genre(next_path)
        track_title = get_track_title(next_path)
        global_state["current_track"]["path"] = next_path
        global_state["current_track"]["genre"] = genre
        global_state["current_track"]["taxonomy"] = get_scanned_taxonomy(next_path)
        global_state["current_track"]["title"] = track_title
        old_owner = global_state["current_track"].get("owner_sid", None)  # Диагностика до присваивания!
        new_owner = get_owner_sid()
        global_state["current_track"]["owner_sid"] = new_owner  # Теперь присваиваем!

        if is_log_type_enabled("owner"):
            owner_logger.debug("[OWNER] Смена владельца: %s → %s (endpoint: %s, ip: %s, track: %s)",
                     old_owner, new_owner, request.endpoint, request.remote_addr,
                     global_state["current_track"].get("path"))

        response = {
            "status": "playing",
            "track": next_path,
            "title": track_title,
            "genre": genre,
            "volume": global_state.get("current_volume")
        }
        if play_url:
            response["play_url"] = play_url
        return jsonify(response)

    @app.route("/prev")
    def prev_track():
        # --- Проверка владельца трека ---
        if global_state["current_track"].get("owner_sid") != get_owner_sid():
            if is_log_type_enabled("owner"):
                owner_logger.debug("[OWNER] ОТКАЗ в %s: ip=%s, сессия=%s, expected_owner=%s, трек=%s",  # Диагностика
                         request.endpoint, request.remote_addr, get_owner_sid(),  # Диагностика
                         global_state["current_track"].get("owner_sid"),  # Диагностика
                         global_state["current_track"].get("path"))  # Диагностика
            return jsonify({"error": "You are not the owner of the current track"}), 403

        if not global_state["current_playlist"] or global_state["current_index"] is None:
            return jsonify({"error": "No playlist loaded"}), 400
        global_state["current_index"] = (global_state["current_index"] - 1) % len(global_state["current_playlist"])
        prev_file = global_state["current_playlist"][global_state["current_index"]]
        prev_path = os.path.join(global_state["current_playlist_directory"], prev_file)
        MUSIC_DIR = load_config().get("music_dir", DEFAULT_CONFIG["music_dir"])
        norm_prev_path = os.path.normpath(prev_path)
        full_path = safe_join_music_dir(MUSIC_DIR, norm_prev_path)
        if not os.path.isfile(full_path):
            return jsonify({"error": "File not found", "full_path": full_path}), 404

        config = load_config()
        mode = config.get("playback_mode", "host")
        play_url = None
        if mode == "host":
            with vlc_lock:
                try:
                    # --- Останавливаем и освобождаем предыдущий плеер ---
                    old_player = global_state.get("current_player")
                    if old_player is not None:
                        try:
                            old_player.stop()
                            old_player.release()
                        except Exception as ex:
                            logger.warning("Ошибка при остановке/освобождении старого плеера: %s", ex)

                    # --- Новый блок: пересоздаём плеер для смены аудиоустройства ---
                    vlc_instance = global_state.get("vlc_instance")
                    player = vlc_instance.media_player_new()
                    media = vlc_instance.media_new(full_path)
                    player.set_media(media)
                    global_state["current_player"] = player

                    import time
                    time.sleep(0.1)  # Дать инициализироваться media

                    vlc_devices = []
                    out = player.audio_output_device_enum()
                    while out:
                        dev = out.contents
                        device_id = dev.device.decode() if dev.device else ""
                        description = dev.description.decode() if dev.description else ""
                        if device_id:
                            vlc_devices.append({'id': device_id, 'name': description})
                        out = dev.next

                    selected_index = int(config.get("selected_device", 0))
                    if vlc_devices and selected_index >= len(vlc_devices):
                        selected_index = 0
                    if vlc_devices:
                        device_id = vlc_devices[selected_index]["id"]
                        player.audio_output_device_set(None, device_id)

                    if global_state.get("current_volume") is None:
                        global_state["current_volume"] = config.get("default_volume", 70)
                    volume_to_set = global_state["current_volume"]
                    player.audio_set_volume(volume_to_set)

                    player.play()
                    if is_log_type_enabled("vlc"):
                        vlc_logger.debug("[ДИАГНОСТИКА VLC] Плеер запущен: is_playing=%s, time=%s, track=%s",
                                         player.is_playing(), player.get_time(), prev_path)
                    play_url = None
                except Exception as e:
                    return jsonify({"error": str(e)}), 500
        elif mode == "plyr":
            play_url = url_for("stream", path=prev_path)
            global_state["current_player"] = None
        else:
            return jsonify({"error": "Неверный режим воспроизведения"}), 400

        # Обновляем глобальное состояние текущего трека
        genre = get_scanned_genre(prev_path)
        track_title = get_track_title(prev_path)
        global_state["current_track"]["path"] = prev_path
        global_state["current_track"]["genre"] = genre
        global_state["current_track"]["taxonomy"] = get_scanned_taxonomy(prev_path)
        global_state["current_track"]["title"] = track_title
        old_owner = global_state["current_track"].get("owner_sid", None)  # Диагностика до присваивания!
        new_owner = get_owner_sid()
        global_state["current_track"]["owner_sid"] = new_owner  # Теперь присваиваем!

        if is_log_type_enabled("owner"):
            owner_logger.debug("[OWNER] Смена владельца: %s → %s (endpoint: %s, ip: %s, track: %s)",
                     old_owner, new_owner, request.endpoint, request.remote_addr,
                     global_state["current_track"].get("path"))
        response = {
            "status": "playing",
            "track": prev_path,
            "title": track_title,
            "genre": genre,
            "volume": global_state.get("current_volume")
        }
        if play_url:
            response["play_url"] = play_url
        return jsonify(response)

    @app.route("/stop")
    def stop_track():
        if global_state["current_player"] is not None:
            try:
                global_state["current_player"].stop()
            except Exception as ex:
                logger.error("Ошибка при остановке плеера: %s", ex)
                return jsonify({"error": str(ex)}), 500
        global_state["paused"] = False
        # Очищаем глобальное состояние текущего трека
        global_state["current_track"]["path"] = None
        global_state["current_track"]["title"] = None
        global_state["current_track"]["genre"] = None
        global_state["current_track"]["taxonomy"] = {}
        return jsonify({"status": "stopped"})

    @app.route("/pause")
    def pause_track():
        # --- Проверка владельца трека ---
        if global_state["current_track"].get("owner_sid") != get_owner_sid():
            if is_log_type_enabled("owner"):
                owner_logger.debug("[OWNER] ОТКАЗ в %s: ip=%s, сессия=%s, expected_owner=%s, трек=%s",  # Диагностика
                         request.endpoint, request.remote_addr, get_owner_sid(),  # Диагностика
                         global_state["current_track"].get("owner_sid"),  # Диагностика
                         global_state["current_track"].get("path"))  # Диагностика
            return jsonify({"error": "You are not the owner of the current track"}), 403

        player = global_state["current_player"]
        if player is not None:
            try:
                # Проверяем, играет ли сейчас (1 — играет, 0 — не играет)
                playing = player.is_playing()
                if not playing:
                    logger.warning("[ДИАГНОСТИКА PAUSE] Плеер уже на паузе или не играет.")
                    global_state["paused"] = True
                    return jsonify({"status": "already_paused"})

                player.pause()  # Это toggle, но мы проверили, что играет!
                global_state["paused"] = True
                current_time = player.get_time()
                duration = player.get_length()
                if is_log_type_enabled("player"):
                    player_logger.info("[ДИАГНОСТИКА PAUSE] Плеер приостановлен. Время: %s/%s", current_time, duration)
            except Exception as ex:
                logger.error("Ошибка при приостановке плеера: %s", ex)
                return jsonify({"error": str(ex)}), 500
        else:
            logger.warning("[ДИАГНОСТИКА PAUSE] Нет активного плеера для паузы.")
            return jsonify({"status": "no_player"})

        current_time = player.get_time() if player is not None else 0
        duration = player.get_length() if player is not None else 0
        return jsonify({
            "status": "paused",
            "current_time": current_time,
            "duration": duration,
            "track": global_state["current_track"].get("path", ""),
            "title": global_state["current_track"].get("title", ""),
            "genre": global_state["current_track"].get("genre", "")
        })

    @app.route("/resume")
    def resume_track():
        # --- Проверка владельца трека ---
        if global_state["current_track"].get("owner_sid") != get_owner_sid():
            if is_log_type_enabled("owner"):
                owner_logger.debug("[OWNER] ОТКАЗ в %s: ip=%s, сессия=%s, expected_owner=%s, трек=%s",  # Диагностика
                         request.endpoint, request.remote_addr, get_owner_sid(),  # Диагностика
                         global_state["current_track"].get("owner_sid"),  # Диагностика
                         global_state["current_track"].get("path"))  # Диагностика
            return jsonify({"error": "You are not the owner of the current track"}), 403
        with vlc_lock:
            if global_state["current_player"] is not None:
                try:
                    global_state["current_player"].play()
                    global_state["paused"] = False  # КРИТИЧНО: сбрасываем флаг паузы
                except Exception as ex:
                    logger.error("Ошибка при возобновлении плеера: %s", ex)
                    return jsonify({"error": str(ex)}), 500
            current_time = global_state["current_player"].get_time() if global_state[
                                                                            "current_player"] is not None else 0
            duration = global_state["current_player"].get_length() if global_state["current_player"] is not None else 0
        return jsonify({
            "status": "playing",
            "current_time": current_time,
            "duration": duration,
            "track": global_state["current_track"].get("path", ""),
            "title": global_state["current_track"].get("title", ""),
            "genre": global_state["current_track"].get("genre", "")
        })

    @app.route("/seek", methods=["POST"])
    def seek():
        # --- Проверка владельца трека ---
        if global_state["current_track"].get("owner_sid") != get_owner_sid():
            if is_log_type_enabled("owner"):
                owner_logger.debug("[OWNER] ОТКАЗ в %s: ip=%s, сессия=%s, expected_owner=%s, трек=%s",  # Диагностика
                         request.endpoint, request.remote_addr, get_owner_sid(),  # Диагностика
                         global_state["current_track"].get("owner_sid"),  # Диагностика
                         global_state["current_track"].get("path"))  # Диагностика
            return jsonify({"error": "You are not the owner of the current track"}), 403
        data = request.get_json()
        new_time = int(data.get("time", 0))
        with vlc_lock:
            if global_state["current_player"] is not None:
                global_state["current_player"].set_time(new_time)
                if is_log_type_enabled("player"):
                    player_logger.debug("Перемотка к %d мс", new_time)
                return jsonify({"status": "seeked", "new_time": new_time})
        return jsonify({"status": "no track"}), 400

    @app.route("/status") # Диагностика маршрут после 03-06-25 00-59
    def status():
        if global_state["current_player"] is not None:
            try:
                current_time = global_state["current_player"].get_time()
                duration = global_state["current_player"].get_length()
                playing = global_state["current_player"].is_playing()

                # ДИАГНОСТИКА: дополнительное логирование
                current_track_path = global_state["current_track"].get("path", "Неизвестно")
                if is_log_type_enabled("status"):
                    status_logger.debug("[ДИАГНОСТИКА STATUS] track=%s, playing=%s, time=%s/%s",
                             current_track_path, playing, current_time, duration)

                # Если плеер не воспроизводит, но флаг paused установлен, статус именно "paused"
                if not playing and global_state.get("paused", False):
                    statusState = "paused"
                elif playing:
                    statusState = "playing"
                else:
                    statusState = "stopped"

                if is_log_type_enabled("owner_status"):
                    owner_status_logger.debug("[OWNER STATUS] endpoint=%s, ip=%s, session=%s, owner_sid=%s, track=%s, status=%s", # Диагностика
                             request.endpoint, request.remote_addr, get_owner_sid(), # Диагностика
                             global_state["current_track"].get("owner_sid"), # Диагностика
                             global_state["current_track"].get("path"), statusState) # Диагностика

                return jsonify({
                    "status": statusState,
                    "current_time": current_time,
                    "duration": duration,
                    "track": global_state["current_track"].get("path"),
                    "title": global_state["current_track"].get("title", global_state["current_track"].get("path")),
                    "genre": global_state["current_track"].get("genre"),
                    "taxonomy": global_state["current_track"].get("taxonomy", {}),
                    "owner_sid": global_state["current_track"].get("owner_sid", None)
                })
            except Exception as ex:
                if is_log_type_enabled("status"):
                    status_logger.debug("[ДИАГНОСТИКА STATUS] Ошибка при получении статуса: %s", ex)
                return jsonify({"status": "error", "error": str(ex)})
        else:
            return jsonify({"status": "stopped"})

    @app.route("/volume", methods=["POST"])
    def set_volume():
        data = request.get_json()
        config_val = load_config()
        vol = int(data.get("volume", config_val.get("default_volume", 70)))
        mode = config_val.get("playback_mode", "host")
        if mode == "host":
            global_state["current_volume"] = vol
            session["current_volume"] = vol  # сохраняем значение в сессии
            if global_state["current_player"] is not None:
                global_state["current_player"].audio_set_volume(vol)
                if is_log_type_enabled("player"):
                    player_logger.debug("Установлена громкость (host): %d", vol)
                return jsonify({"status": "volume set", "volume": vol})
            else:
                return jsonify({"error": "нет активного трека"}), 400
        else:
            return jsonify({"error": "Регулировка громкости недоступна в этом режиме"}), 400

    @app.route("/set_device", methods=["POST"])
    def set_device():
        new_device = request.form.get("device")
        # Останавливаем воспроизведение, если играет
        if global_state["current_player"]:
            try:
                global_state["current_player"].stop()
                if is_log_type_enabled("audio_diag"):
                    audio_diag_logger.debug("[AUDIO DIAG] Остановлено воспроизведение перед сменой аудиоустройства")
            except Exception as ex:
                logger.warning("Ошибка при остановке плеера перед сменой устройства: %s", ex)
        if new_device is not None:
            config_val = load_config()
            config_val["selected_device"] = int(new_device)
            selected_idx = config_val["selected_device"]

            # Получаем имена устройств из списка VLC для корректного логирования
            def get_active_vlc_devices_default():
                inst = vlc.Instance()
                player = inst.media_player_new()
                out = player.audio_output_device_enum()
                devices = []
                while out:
                    dev = out.contents
                    device_id = dev.device.decode() if dev.device else ""
                    description = dev.description.decode() if dev.description else ""
                    if device_id:
                        devices.append({'id': device_id, 'name': description})
                    out = dev.next
                return devices

            vlc_devices = get_active_vlc_devices_default()
            device_name = ""
            if 0 <= selected_idx < len(vlc_devices):
                device_name = vlc_devices[selected_idx].get("name", "")
            logger.info("Аудиоустройство изменено на (VLC): %s (№%s)", device_name, selected_idx)

            save_config(config_val)
        return redirect(url_for("settings"))

    @app.route("/stream")
    def stream():
        path = request.args.get("path")
        if not path:
            return "No path provided", 400
        decoded_path = unescape(unquote_plus(path))
        MUSIC_DIR = load_config().get("music_dir", DEFAULT_CONFIG["music_dir"])
        norm_rel_path = os.path.normpath(decoded_path)
        full_path = safe_join_music_dir(MUSIC_DIR, norm_rel_path)
        if os.path.isfile(full_path):
            return send_file(full_path, mimetype="audio/mp3")
        else:
            return "File not found", 404

    @app.route("/current-track")
    def current_track_info():
        current = global_state["current_track"].get("path", "")
        return jsonify({"currentTrack": current})

    @app.route("/update_autoplay_mode", methods=["POST"])
    def update_autoplay_mode():
        autoplay_mode = request.form.get("autoplay_mode", "off")
        config_val = load_config()
        config_val["autoplay_mode"] = autoplay_mode
        save_config(config_val)
        session["autoplay_mode"] = autoplay_mode
        return {"status": "ok", "autoplay_mode": autoplay_mode}

    @app.route("/get_autoplay_mode", methods=["GET"])
    def get_autoplay_mode():
        config_val = load_config()
        autoplay_mode = config_val.get("autoplay_mode", "off")
        return {"autoplay_mode": autoplay_mode}

    @app.route("/autoplay")
    def autoplay_route():
        # Получаем параметр track из GET-запроса
        track = request.args.get("track")
        if track and track.startswith("/browse"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(track)
            query_params = parse_qs(parsed.query)
            if 'autoplay' in query_params:
                track = unquote_plus(query_params['autoplay'][0])
        logger.info("Autoplay: получен параметр track: %s", track)
        if not track:
            logger.warning("Autoplay: параметр track отсутствует, перенаправление на browse")
            return redirect(url_for("browse"))

        # Заменяем обратные слэши на прямые и нормализуем путь
        track = track.replace("\\", "/")
        norm_rel_path = os.path.normpath(track).replace("\\", "/")
        logger.info("Autoplay: нормализованный путь: %s", norm_rel_path)

        # Получаем режим автоплея из конфига
        config = load_config()
        autoplay_mode = config.get("autoplay_mode", "off")
        logger.info("Autoplay: режим автоплея: %s", autoplay_mode)

        # Если автоплей выключен — просто редиректим на browse, НЕ МЕНЯЕМ состояние!
        if autoplay_mode == "off":
            folder = os.path.dirname(norm_rel_path)
            logger.info("Autoplay: режим OFF, просто переход в папку: %s", folder)
            return redirect(url_for("browse", path=folder))

        # --- Дальше только если автоплей включён ---
        genre = get_scanned_genre(norm_rel_path)
        logger.info("Autoplay: жанр получен: %s", genre)

        # Обновляем глобальное состояние текущего трека и сессионные данные
        global_state["current_track"]["path"] = norm_rel_path
        global_state["current_track"]["title"] = os.path.basename(norm_rel_path)
        global_state["current_track"]["genre"] = genre
        global_state["current_track"]["taxonomy"] = get_scanned_taxonomy(norm_rel_path)
        global_state["current_track"]["owner_sid"] = get_owner_sid()
        old_owner = global_state["current_track"].get("owner_sid", None)  # Диагностика
        new_owner = get_owner_sid()  # Диагностика
        global_state["current_track"]["owner_sid"] = new_owner  # Диагностика
        if is_log_type_enabled("owner"):
            owner_logger.debug("[OWNER] Смена владельца: %s → %s (endpoint: %s, ip: %s, track: %s)",  # Диагностика
                     old_owner, new_owner, request.endpoint, request.remote_addr,
                     global_state["current_track"].get("path"))  # Диагностика
        session["current_track"] = norm_rel_path

        folder = os.path.dirname(norm_rel_path)
        logger.info("Autoplay: определена директория: %s", folder)
        global_state["current_playlist_directory"] = folder
        session["current_folder"] = folder

        logger.info("Автоплей: текущий трек обновлён: %s, жанр: %s", norm_rel_path, genre)

        with vlc_lock:
            if global_state["current_player"] is not None:
                try:
                    global_state["current_player"].stop()
                except Exception as ex:
                    logger.error("Ошибка при остановке текущего плеера: %s", ex)

            # Перенаправляем на маршрут /play, чтобы запустить воспроизведение нового трека
            return redirect(url_for("browse", path=folder, autoplay=norm_rel_path))

    @app.route("/custom_keywords", methods=["GET", "POST"])
    def custom_keywords():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        if request.method == "POST":
            try:
                new_keywords = request.get_json().get("keywords")
                if isinstance(new_keywords, dict):
                    save_genre_settings(new_keywords)
                    logger.info("Жанровые настройки обновлены: %s", new_keywords)
                    return jsonify({"status": "saved", "keywords": new_keywords})
                else:
                    return jsonify({"status": "error", "message": "Неверный формат"}), 400
            except Exception as e:
                logger.error("Ошибка при обновлении жанровых настроек: %s", e)
                return jsonify({"status": "error", "message": str(e)}), 500
        else:
            keywords = load_genre_settings()
            return jsonify({"keywords": keywords})

    @app.route("/update_fav_settings", methods=["POST"])
    def update_fav_settings():
        favorite_mode = request.form.get("favorite_mode", "stay")
        config_val = load_config()
        config_val["favorite_mode"] = favorite_mode
        save_config(config_val)
        session["favorite_mode"] = favorite_mode
        global_state["favorite_mode"] = favorite_mode
        flash("Настройки избранных треков обновлены и сохранены в конфигурации!", "success")
        return redirect(url_for("settings"))

    @app.route("/update_intelligence_preferences", methods=["POST"])
    def update_intelligence_preferences():
        config_val = load_config()
        try:
            weights = {
                key: float(request.form.get(f"intelligence_{key}_weight", "0"))
                for key in ("deep", "acoustic", "character", "semantic", "bpm", "personal")
            }
            if any(value < 0 or value > 100 for value in weights.values()):
                raise ValueError("Каждый вес должен быть от 0 до 100")
            if sum(weights.values()) <= 0:
                raise ValueError("Хотя бы один вес должен быть больше нуля")
            result_limit = max(1, min(int(request.form.get("intelligence_result_limit", 20)), 100))
        except (TypeError, ValueError) as exc:
            flash(f"Настройки умного подбора не сохранены: {exc}", "danger")
            return redirect(url_for("settings", _anchor="intelligence-preferences"))
        config_val["intelligence_preferences"] = {
            "weights": weights,
            "result_limit": result_limit,
            "exclude_versions": request.form.get("intelligence_exclude_versions") == "on",
        }
        save_config(config_val)
        flash("Постоянные настройки умного подбора сохранены.", "success")
        return redirect(url_for("settings", _anchor="intelligence-preferences"))

    @app.route("/get_directories")
    def get_directories():
        config_val = load_config()
        MUSIC_DIR = config_val.get("music_dir", DEFAULT_CONFIG["music_dir"])
        if is_log_type_enabled("status"):
            status_logger.debug("MUSIC_DIR = %s", MUSIC_DIR)
        node_id = request.args.get("id", "#")
        if node_id == "#":
            current_path = MUSIC_DIR
        else:
            norm_node_id = os.path.normpath(node_id)
            current_path = safe_join_music_dir(MUSIC_DIR, norm_node_id)
        if is_log_type_enabled("status"):
            status_logger.debug("get_directories => current_path: %s", current_path)
        nodes = []
        try:
            if not os.path.exists(current_path):
                if is_log_type_enabled("status"):
                    status_logger.debug("Directory does not exist: %s", current_path)
                return jsonify(nodes)
            for entry in os.listdir(current_path):
                full_entry = os.path.join(current_path, entry)
                if os.path.isdir(full_entry):
                    try:
                        children = any(os.path.isdir(os.path.join(full_entry, e)) for e in os.listdir(full_entry))
                    except Exception as ex:
                        logger.error("Ошибка проверки вложенных папок в %s: %s", full_entry, ex)
                        children = False
                    nodes.append({
                        "id": os.path.relpath(full_entry, MUSIC_DIR).replace("\\", "/"),
                        "text": entry,
                        "children": children
                    })
            if is_log_type_enabled("status"):
                status_logger.debug("Returning nodes: %s", nodes)
            return jsonify(nodes)
        except Exception as e:
            if is_log_type_enabled("status"):
                status_logger.debug("Ошибка при построении дерева: %s", e)
            return jsonify([])

    @app.route("/scan_library")
    def scan_library():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        scan_results = {}
        MUSIC_DIR = load_config().get("music_dir", DEFAULT_CONFIG["music_dir"])
        for root, dirs, files in os.walk(MUSIC_DIR):
            for file in files:
                if file.lower().endswith(".mp3"):
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, MUSIC_DIR)
                    genre, _confidence, _features = get_genre(full_path)
                    scan_results.setdefault(genre, []).append(rel_path)
        return jsonify({"status": "scanned", "results": scan_results})

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        config_val = load_config()
        if request.method == "POST":
            previous_music_dir = str(config_val.get("music_dir", ""))
            music_dir = request.form.get("music_dir", config_val.get("music_dir"))
            playback_mode = request.form.get("playback_mode", config_val.get("playback_mode"))
            # Если по ошибке передадут "local", принудительно меняем на "plyr"
            if playback_mode == "local":
                playback_mode = "plyr"
            default_volume = request.form.get("default_volume", config_val.get("default_volume"))
            sound_quality = request.form.get("sound_quality", config_val.get("sound_quality"))
            favorite_mode = request.form.get("favorite_mode", config_val.get("favorite_mode", "stay"))
            remember_recent_folders = request.form.get("remember_recent_folders") in {"1", "true", "on", "yes"}
            try:
                default_volume = int(default_volume)
            except Exception as e:
                default_volume = DEFAULT_CONFIG["default_volume"]
            config_val["music_dir"] = music_dir
            config_val["playback_mode"] = playback_mode
            config_val["default_volume"] = default_volume
            config_val["sound_quality"] = sound_quality
            config_val["favorite_mode"] = favorite_mode  # сохраняем в config
            config_val["remember_recent_folders"] = remember_recent_folders
            if not remember_recent_folders or os.path.normcase(previous_music_dir) != os.path.normcase(str(music_dir)):
                config_val["recent_folders"] = []
            else:
                config_val["recent_folders"] = normalize_recent_folders(config_val.get("recent_folders", []))
            save_config(config_val)
            # Остановить воспроизведение, если режим host (VLC)
            mode = config_val.get("playback_mode", "host")
            if mode == "host":
                try:
                    player = global_state.get("current_player")
                    if player:
                        player.stop()
                        logger.info("VLC: Остановлено воспроизведение после изменения настроек.")
                except Exception as e:
                    logger.warning("VLC: Не удалось остановить плеер: %s", e)
            session.pop("current_volume", None) # Сброс громкости пользователя при изменении настроек
            session["favorite_mode"] = favorite_mode  # сохраняем в сессии для текущего пользователя
            logger.info("Настройки сохранены: %s", config_val)
            session.pop("current_folder", None)
            session.pop("current_track", None)
            return redirect(url_for("settings", settings_saved=1))
        else:
            devices = get_active_vlc_devices_default()
            current_folder = session.get("current_folder", "")
            # Передаём camelCase для JS
            return render_template(
                "settings.html",
                config=config_val,
                devices=devices,
                current_folder=current_folder,
                log_flags=config_val.get("log_flags", {}),
                intelligence_preferences=get_intelligence_preferences(config_val),
            )

    @app.route("/set_advanced_mode", methods=["POST"])
    def set_advanced_mode():
        data = request.get_json()
        config = load_config()
        config["advanced_mode"] = bool(data.get("advanced_mode"))
        save_config(config)
        return jsonify({"status": "ok"})

    @app.route("/log_settings", methods=["GET", "POST"])
    def log_settings():
        if request.method == "POST":
            # Собрать новые значения из формы (checkboxes)
            log_flags = get_log_flags()
            for key in log_flags:
                # Если чекбокс отправлен — True, иначе False
                log_flags[key] = (request.form.get(key) == "on")
            save_log_flags(log_flags)
            # Пересоздать логгеры — если у тебя есть функция для этого, вызови здесь!
            # from .logging_config import setup_logging, setup_numba_logger
            # setup_logging(...)
            # setup_numba_logger()
            return ("", 204)  # Для AJAX, или redirect(url_for("settings")) если обычная форма
        # GET-запрос: вернуть текущие флаги
        if is_log_type_enabled("status"):
            status_logger.debug(f"DEBUG /log_settings: {get_log_flags()}")
        return jsonify(get_log_flags())

    @app.route("/favorite", methods=["POST"])
    def favorite():
        data = request.get_json()
        path = data.get("path")
        if not path:
            logger.error("Нет переданного параметра path")
            return jsonify({"error": "No path provided"}), 400
        MUSIC_DIR = load_config().get("music_dir", DEFAULT_CONFIG["music_dir"])
        norm_rel_path = os.path.normpath(path)
        full_path = safe_join_music_dir(MUSIC_DIR, norm_rel_path)
        if not os.path.isfile(full_path):
            logger.error("Файл не найден: %s", full_path)
            return jsonify({"error": "File not found"}), 404
        con = sqlite3.connect(FAVORITE_DB)
        cur = con.cursor()

        # Если запись уже существует – возвращаем существующие данные
        cur.execute("SELECT genre FROM favorites WHERE path=?", (path,))
        existing = cur.fetchone()
        if existing:
            genre = existing[0]
            con.close()
            logger.info("Запись уже существует для %s с жанром %s", path, genre)
            return jsonify({"status": "exists", "path": path, "genre": genre})

        # Получаем жанр из базы результатов сканирования (без аудиоанализа)
        genre = get_scanned_genre(path)
        cur.execute("INSERT INTO favorites (path, genre) VALUES (?, ?)", (path, genre))
        con.commit()
        con.close()
        logger.info("Добавлен трек в избранное: %s с жанром %s", path, genre)
        return jsonify({"status": "success", "path": path, "genre": genre})

    @app.route("/favorites_list")
    def favorites_list():
        init_favorite_db()
        con = sqlite3.connect(FAVORITE_DB)
        cur = con.cursor()
        cur.execute("""
            SELECT f.path, f.genre, COALESCE(r.rating, f.rating, 0) AS rating
            FROM favorites f
            LEFT JOIN track_ratings r ON r.path=f.path
        """)
        favorites = cur.fetchall()
        con.close()

        if favorites:
            html = "<ul class='list-group'>"
            for index, (f, g, r) in enumerate(favorites):
                # Если рейтинг вдруг равен None (на всякий случай) – устанавливаем 0
                r = r if r is not None else 0
                # Получаем чистое название трека без расширения
                track_name = get_track_title(f)
                # Экранируем значения и JS-аргумент независимо друг от друга.
                safe_path = escape(str(f), quote=True)
                safe_title = escape(str(track_name), quote=True)
                safe_genre = escape(str(g or "Unknown"), quote=True)
                safe_js_path = escape(json.dumps(str(f), ensure_ascii=False), quote=True)
                html += f"""
                    <li class="list-group-item fav-entry" data-track-id="{safe_path}"
                        data-title="{safe_title.lower()}" data-genre="{safe_genre}" data-added-index="{index}">
                      <div class="favorite-track-main">
                        <div class="fw-bold text-truncate" title="{safe_path}">{safe_title}</div>
                        <div class="small text-muted">Стиль: {safe_genre}</div>
                        <div class="favorite-rating-row">
                          <span class="track-rating" data-rating="{int(r)}" aria-label="Оценка трека">
                            <span class="star" data-value="1">&#9734;</span>
                            <span class="star" data-value="2">&#9734;</span>
                            <span class="star" data-value="3">&#9734;</span>
                            <span class="star" data-value="4">&#9734;</span>
                            <span class="star" data-value="5">&#9734;</span>
                          </span>
                          <button class="btn btn-link btn-sm reset-rating p-0" type="button" title="Сбросить оценку" onclick="resetFavoriteRating(this)">Сбросить</button>
                        </div>
                      </div>
                      <div class="favorite-actions">
                        <button class="btn btn-sm btn-primary" type="button" title="Воспроизвести" aria-label="Воспроизвести {safe_title}" onclick="playFavoriteTrack({safe_js_path})"><i class="bi bi-play-fill"></i></button>
                        <button class="btn btn-sm btn-danger fav-btn is-favorite" type="button" data-track-path="{safe_path}" title="Убрать из избранного" aria-label="Убрать из избранного" aria-pressed="true" onclick="toggleFavorite({safe_js_path}, this)"><i class="bi bi-heart-fill"></i></button>
                      </div>
                    </li>
                """
            html += "</ul>"
        else:
            html = "<p>Список избранного пуст.</p>"
        return jsonify({"html": html})

    @app.route("/remove_favorite", methods=["POST"])
    def remove_favorite():
        data = request.get_json()
        path = data.get("path")
        if not path:
            logger.error("Удаление: нет переданного параметра path")
            return jsonify({"error": "No path provided"}), 400

        con = sqlite3.connect(FAVORITE_DB)
        cur = con.cursor()
        cur.execute("DELETE FROM favorites WHERE path=?", (path,))
        con.commit()
        # Проверка удаления: выполняем SELECT
        cur.execute("SELECT COUNT(*) FROM favorites WHERE path=?", (path,))
        count = cur.fetchone()[0]
        con.close()

        if count > 0:
            logger.error("Запись с path '%s' не удалена, остаток: %s", path, count)
            return jsonify({"error": "Трек не удалён из избранного"}), 400

        logger.info("Из избранного удалён трек: %s", path)
        return jsonify({"status": "removed", "path": path})

    @app.route("/updateRating", methods=["POST"])
    def update_rating():
        data = request.get_json()
        track_id = data.get("trackId")  # В данном случае track_id соответствует path
        rating = data.get("rating")
        if track_id is None or rating is None:
            return jsonify({"success": False, "error": "Неверные данные"}), 400

        try:
            result = set_track_rating(track_id, rating)
            return jsonify({"success": True, **result})
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        except Exception as exc:
            logger.exception("Не удалось сохранить оценку трека")
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.route("/api/track-rating", methods=["POST"])
    def track_rating_api():
        data = request.get_json(silent=True) or {}
        try:
            result = set_track_rating(data.get("path"), data.get("rating"))
            return jsonify({"success": True, **result})
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        except Exception as exc:
            logger.exception("Не удалось сохранить быструю оценку")
            return jsonify({"success": False, "error": str(exc)}), 500

    @app.route("/file_exists")
    def file_exists():
        path = request.args.get("path")
        if not path:
            return jsonify({"exists": False}), 400
        decoded_path = unescape(unquote_plus(path))
        MUSIC_DIR = load_config().get("music_dir", DEFAULT_CONFIG["music_dir"])
        full_path = safe_join_music_dir(MUSIC_DIR, os.path.normpath(decoded_path))
        exists = os.path.isfile(full_path) or os.path.isdir(full_path)
        return jsonify({"exists": exists})

    @app.route("/analyze")
    def analyze_current():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        if not global_state["current_track"].get("path"):
            return jsonify({"error": "Нет текущего трека"}), 400
        MUSIC_DIR = load_config().get("music_dir", DEFAULT_CONFIG["music_dir"])
        norm_rel_path = os.path.normpath(global_state["current_track"]["path"])
        full_path = safe_join_music_dir(MUSIC_DIR, norm_rel_path)
        genre_result = get_genre(full_path, return_meta=True)
        genre, confidence, _features = genre_result[:3]
        taxonomy = (
            genre_result[3].get("taxonomy")
            if len(genre_result) == 4 and genre_result[3]
            else None
        )
        global_state["current_track"]["genre"] = genre
        global_state["current_track"]["genre_confidence"] = confidence
        global_state["current_track"]["taxonomy"] = taxonomy or {}
        logger.info("Анализ выполнен. Обнаружен жанр: %s", genre)
        return jsonify({"status": "analyzed", "genre": genre, "taxonomy": taxonomy})

    @app.route("/shutdown", methods=["POST"])
    def shutdown():
        func = request.environ.get('werkzeug.server.shutdown')
        if func is None:
            logger.warning("Shutdown метод не найден. Вероятно, сервер запущен с использованием пользовательского IP.")
            return "Сервер остановлен (shutdown метод не найден)", 200
        func()
        logger.info("Сервер завершается по запросу.")
        return "Сервер завершается...", 200

    @app.route("/recommend")
    def recommend():
        if not get_advanced_mode():
            return jsonify({"error": "Расширенные функции отключены"}), 400
        current_genre = str(global_state["current_track"].get("genre") or "").strip()
        current_path = str(global_state["current_track"].get("path") or "").strip()

        if not current_path:
            return jsonify({"error": "Нет текущего трека"}), 400
        # Проверка наличия модели
        if not os.path.exists(MODEL_PATH):
            return jsonify({"error": "Модель не обучена или удалена. Сначала обучите модель!"}), 400

        # Если жанр неизвестен, пытаемся получить его из базы
        if not current_genre or current_genre.lower() == "unknown":
            norm_path = os.path.normpath(current_path)
            row = load_scan_result(norm_path)
            if row and row[0]:
                current_genre = row[0]
            else:
                return jsonify({"error": "Нет установленного жанра текущего трека"}), 400
        # Информируем о статусе YAMNet
        try:
            from .librosa_settings import load_librosa_settings
            ls = load_librosa_settings()
            yam_enabled = bool(ls.get("yamnet_enabled", False))
            yam_path = str(resolve_project_path(
                ls.get("yamnet_model_path"),
                YAMNET_MODEL_FILE,
            ))
            import os as _os
            if yam_enabled:
                if not _os.path.isfile(yam_path):
                    if is_log_type_enabled("model"):
                        model_logger.info(f"[RECOMMEND] YAMNet включён, но файл отсутствует ({yam_path}). Работаем без YAMNet-векторов.")
                else:
                    if is_log_type_enabled("model"):
                        model_logger.debug(f"[RECOMMEND] YAMNet включён. Если сканирование шло с включённым YAMNet — будут использованы fused_proba.")
            else:
                if is_log_type_enabled("model"):
                    model_logger.debug("[RECOMMEND] YAMNet выключен. Рекомендации используют только RF/либо raw features.")
        except Exception as _re_log_e:
            if is_log_type_enabled("model"):
                model_logger.debug(f"[RECOMMEND] Не удалось вывести диагностический лог YAMNet: {_re_log_e}")
        try:
            config = load_config()
            force_model = config.get("force_model_for_recommend", False)
            intelligent_pool = find_similar_intelligent(current_path, limit=30)
            intelligent_items = recommendation_history.rerank(
                intelligent_pool, [current_path], 1,
                recommendation_type="quick_similar",
            )
            if intelligent_items:
                intelligent = intelligent_items[0]
                recommended_path = intelligent["path"]
                result = {
                    "folder": os.path.dirname(recommended_path),
                    "filename": os.path.basename(recommended_path),
                    "genre": intelligent.get("genre") or current_genre,
                    "confidence": None,
                    "similarity_score": intelligent.get("similarity", 0),
                    "recommendation_engine": "catalog_intelligence_v1",
                    "character_similarity": intelligent.get("character_similarity"),
                    "acoustic_similarity": intelligent.get("acoustic_similarity"),
                }
            else:
                # Совместимый fallback до первой полной индексации каталога.
                result = find_similar_track(
                    current_path,
                    current_genre,
                    use_model=force_model,
                )

            if not result:
                return jsonify({"error": "Похожий трек не найден"}), 400

            # Получаем путь к папке для формирования правильного redirect URL
            folder_path = result["folder"]
            filename = result["filename"]

            # Формируем правильный URL для браузера с автовоспроизведением
            from urllib.parse import quote_plus

            # Формируем полный путь к треку для autoplay
            full_track_path = os.path.join(folder_path, filename).replace("\\", "/")

            redirect_url = url_for('browse',
                                   path=quote_plus(folder_path),
                                   autoplay=quote_plus(full_track_path))

            logger.info("Рекомендован трек: %s -> %s (схожесть: %.2f, confidence: %.2f)",
                        full_track_path,
                        redirect_url,
                        result.get("similarity_score", 0),
                        result.get("confidence") or 0)

            return jsonify({
                "redirect": redirect_url,
                "filename": result["filename"],
                "folder": result["folder"],
                "genre": result.get("genre", current_genre),
                "confidence": result.get("confidence"),
                "similarity_score": result.get("similarity_score"),
                "recommendation_engine": result.get("recommendation_engine", "legacy"),
                "character_similarity": result.get("character_similarity"),
                "acoustic_similarity": result.get("acoustic_similarity"),
            })

        except Exception as e:
            logger.error("Ошибка в функции recommend: %s", e)
            return jsonify({"error": "Ошибка поиска похожего трека: " + str(e)}), 500

    @app.route("/set_force_model_for_recommend", methods=["POST"]) # Принудительное включение модели рекомендации
    def set_force_model_for_recommend():
        data = request.get_json()
        config = load_config()
        config["force_model_for_recommend"] = bool(data.get("force_model_for_recommend"))
        save_config(config)
        return jsonify({"status": "ok"})

    @app.route("/diag_state") # для диагностики
    def diag_state():
        import pprint
        import os

        state = {
            "pid": os.getpid(),
            "server_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "global_state": global_state.copy(),
            "session": dict(session),
            "request_ip": request.remote_addr,
            "owner_sid": get_owner_sid(),
        }

        try:
            config = load_config()
            state["config"] = dict(config)
        except Exception as e:
            state["config"] = {"error": str(e)}

        try:
            player = global_state.get("current_player")
            vlc_status = {}
            if player:
                vlc_status = {
                    "is_playing": player.is_playing(),
                    "time": player.get_time(),
                    "length": player.get_length(),
                    "volume": player.audio_get_volume(),
                    "state": str(player.get_state()),
                    "media": str(player.get_media()),
                    "audio_output_device": None,
                }
                try:
                    audio_output_device = player.audio_output_device_enum()
                    vlc_status["audio_output_device"] = str(audio_output_device)
                except Exception as e:
                    vlc_status["audio_output_device"] = f"Ошибка: {e}"
            state["vlc_status"] = vlc_status
        except Exception as e:
            state["vlc_status"] = {"error": str(e)}

        # Журнал последних действий
        state["last_actions"] = list(last_actions)

        pretty = pprint.pformat(state, indent=2, width=120)
        return Response(f"<pre>{pretty}</pre>", mimetype="text/html")

    @app.route("/audio_diag") # для диагностики аудио  http://localhost:8080/audio_diag
    def audio_diag():
        lines = []
        config = load_config()
        selected_device = config.get("selected_device", 0)
        vlc_devices = []
        player = global_state.get("current_player")
        vlc_instance = global_state.get("vlc_instance")

        # Получение списка устройств
        if player:
            out = player.audio_output_device_enum()
            while out:
                dev = out.contents
                device_id = dev.device.decode() if dev.device else ""
                description = dev.description.decode() if dev.description else ""
                if device_id:
                    vlc_devices.append({'id': device_id, 'name': description})
                out = dev.next

            lines.append(f"VLC найдено устройств: {len(vlc_devices)}")
            for idx, dev in enumerate(vlc_devices):
                lines.append(f"  idx={idx} id={dev['id']} name={dev['name']}")
        else:
            lines.append("VLC player не инициализирован!")

        lines.append(f"selected_index из config.json: {selected_device}")
        if vlc_devices and selected_device < len(vlc_devices):
            lines.append(
                f"Ожидаемый device_id: {vlc_devices[selected_device]['id']} ({vlc_devices[selected_device]['name']})")
        else:
            lines.append("Внимание: выбранный индекс вне диапазона!")

        # Фактическое текущее устройство
        if player:
            current_id = player.audio_output_device_get()
            lines.append(f"Фактическое текущее аудиоустройство VLC: {current_id}")
            lines.append(
                f"is_playing: {player.is_playing()}, volume: {player.audio_get_volume()}, media: {player.get_media()}")
        else:
            lines.append("VLC player не инициализирован или неактивен.")

        return Response("<pre>" + "\n".join(lines) + "</pre>", mimetype="text/html")

    @app.route("/audio_diag_switch/<int:device_idx>") # для диагностики аудио http://localhost:8080/audio_diag_switch/3
    def audio_diag_switch(device_idx):
        lines = []
        config = load_config()
        player = global_state.get("current_player")
        vlc_devices = []

        if player:
            out = player.audio_output_device_enum()
            while out:
                dev = out.contents
                device_id = dev.device.decode() if dev.device else ""
                description = dev.description.decode() if dev.description else ""
                if device_id:
                    vlc_devices.append({'id': device_id, 'name': description})
                out = dev.next

            lines.append(f"VLC найдено устройств: {len(vlc_devices)}")
            for idx, dev in enumerate(vlc_devices):
                lines.append(f"  idx={idx} id={dev['id']} name={dev['name']}")

            if device_idx < len(vlc_devices):
                device_id = vlc_devices[device_idx]['id']
                player.audio_output_device_set(None, device_id)
                lines.append(f"Попытка установить device_idx={device_idx}, id={device_id}")
                # Проверяем, что реально применилось
                current_id = player.audio_output_device_get()
                lines.append(f"Фактическое текущее аудиоустройство VLC: {current_id}")
            else:
                lines.append("Индекс вне диапазона!")

            lines.append(
                f"is_playing: {player.is_playing()}, volume: {player.audio_get_volume()}, media: {player.get_media()}")
        else:
            lines.append("VLC player не инициализирован!")

        return Response("<pre>" + "\n".join(lines) + "</pre>", mimetype="text/html")

    @app.route("/audio_diag_full/<int:device_idx>") # для диагностики аудио http://localhost:8080/audio_diag_full/2
    def audio_diag_full(device_idx):
        """
        Диагностика: смена аудиоустройства, запуск/остановка трека,
        отображение состояния плеера и media.
        """
        lines = []
        config = load_config()
        player = global_state.get("current_player")
        vlc_devices = []
        action = request.args.get("action", "").lower()
        # Укажи свой трек для диагностики
        diagnostic_track = "2025/Prime Time DJ/02-03-25/Club House _ 140 Tracks/120 - 8A - Tebra - Here & Now (Extended Mix).mp3"
        music_dir = config.get("music_dir", DEFAULT_CONFIG["music_dir"])
        full_path = safe_join_music_dir(music_dir, diagnostic_track)

        # Получаем список устройств (до смены)
        if player:
            out = player.audio_output_device_enum()
            while out:
                dev = out.contents
                device_id = dev.device.decode() if dev.device else ""
                description = dev.description.decode() if dev.description else ""
                if device_id:
                    vlc_devices.append({'id': device_id, 'name': description})
                out = dev.next

            lines.append(f"VLC найдено устройств: {len(vlc_devices)}")
            for idx, dev in enumerate(vlc_devices):
                lines.append(f"  idx={idx} id={dev['id']} name={dev['name']}")

            # Смена устройства, если индекс в диапазоне
            if device_idx < len(vlc_devices):
                device_id = vlc_devices[device_idx]['id']
                player.audio_output_device_set(None, device_id)
                lines.append(f"Попытка установить device_idx={device_idx}, id={device_id}")
                # Проверяем, что реально применилось
                current_id = player.audio_output_device_get()
                lines.append(f"Фактическое текущее аудиоустройство VLC: {current_id}")
            else:
                lines.append("Индекс вне диапазона!")

            # Действия с треком (play/stop) по action
            if action == "play":
                # Остановить предыдущее воспроизведение
                player.stop()
                media = global_state["vlc_instance"].media_new(full_path)
                player.set_media(media)
                # Диагностика списка устройств после set_media
                diag_devices = []
                out2 = player.audio_output_device_enum()
                while out2:
                    dev = out2.contents
                    device_id = dev.device.decode() if dev.device else ""
                    description = dev.description.decode() if dev.description else ""
                    if device_id:
                        diag_devices.append({'id': device_id, 'name': description})
                    out2 = dev.next
                lines.append(f"[DIAG] После set_media найдено устройств: {len(diag_devices)}")
                for idx, dev in enumerate(diag_devices):
                    lines.append(f"[DIAG]   idx={idx} id={dev['id']} name={dev['name']}")
                # Повторная установка аудиоустройства (на всякий случай)
                if device_idx < len(diag_devices):
                    device_id = diag_devices[device_idx]['id']
                    player.audio_output_device_set(None, device_id)
                    lines.append(f"-> После set_media повторно установлен device_id: {device_id}")

                player.play()
                lines.append("[ACTION] Воспроизведение трека запущено!")
            elif action == "stop":
                player.stop()
                lines.append("[ACTION] Воспроизведение остановлено.")

            # Общее состояние
            lines.append(
                f"is_playing: {player.is_playing()}, volume: {player.audio_get_volume()}, state: {player.get_state()}, media: {player.get_media()}")
            try:
                media = player.get_media()
                if media:
                    lines.append("media.get_mrl: " + str(media.get_mrl()))
            except Exception as e:
                lines.append(f"media.get_mrl error: {e}")
        else:
            lines.append("VLC player не инициализирован!")

        return Response("<pre>" + "\n".join(lines) + "</pre>", mimetype="text/html")

    @app.route("/audio_diag_max/<int:device_idx>")
    def audio_diag_max(device_idx):
        """
        Максимальная диагностика VLC: аудиоустройства, состояние плеера, параметры media,
        sample rate, channels, mute, громкость, bit depth, длительность, meta, ошибки.
        """
        lines = []
        config = load_config()
        player = global_state.get("current_player")
        vlc_devices = []
        action = request.args.get("action", "").lower()
        diagnostic_track = "2025/Prime Time DJ/02-03-25/Club House _ 140 Tracks/120 - 8A - Tebra - Here & Now (Extended Mix).mp3"
        music_dir = config.get("music_dir", DEFAULT_CONFIG["music_dir"])
        full_path = safe_join_music_dir(music_dir, diagnostic_track)

        # Получение списка устройств
        if player:
            # Аудиоустройства
            out = player.audio_output_device_enum()
            while out:
                dev = out.contents
                device_id = dev.device.decode() if dev.device else ""
                description = dev.description.decode() if dev.description else ""
                if device_id:
                    vlc_devices.append({'id': device_id, 'name': description})
                out = dev.next

            lines.append(f"VLC найдено аудиоустройств: {len(vlc_devices)}")
            for idx, dev in enumerate(vlc_devices):
                lines.append(f"  idx={idx} id={dev['id']} name={dev['name']}")

            # Смена устройства
            if device_idx < len(vlc_devices):
                device_id = vlc_devices[device_idx]['id']
                player.audio_output_device_set(None, device_id)
                lines.append(f"Попытка установить device_idx={device_idx}, id={device_id}")
                current_id = player.audio_output_device_get()
                lines.append(f"Фактическое аудиоустройство: {current_id}")
            else:
                lines.append("Индекс вне диапазона!")

            # Действия с треком
            if action == "play":
                player.stop()
                media = global_state["vlc_instance"].media_new(full_path)
                player.set_media(media)
                player.play()
                lines.append("[ACTION] Воспроизведение трека запущено!")
                time.sleep(1)  # Дать время на старт (иначе параметры не прочитаются)
            elif action == "stop":
                player.stop()
                lines.append("[ACTION] Воспроизведение остановлено.")

            # --- Основная диагностика текущего состояния ---
            lines.append("\n--- Состояние плеера ---")
            lines.append(f"is_playing: {player.is_playing()}")
            lines.append(f"volume: {player.audio_get_volume()}")
            lines.append(f"mute: {player.audio_get_mute()}")
            lines.append(f"state: {player.get_state()}")
            lines.append(f"audio_output_device: {player.audio_output_device_get()}")
            lines.append(
                f"audio_output: {player.audio_output_get_device_type() if hasattr(player, 'audio_output_get_device_type') else 'n/a'}")
            lines.append(f"audio_track: {player.audio_get_track()}")
            try:
                lines.append(f"audio_channel: {player.audio_get_channel()}")
            except Exception as e:
                lines.append(f"audio_get_channel error: {e}")

            try:
                lines.append(f"audio_delay: {player.audio_get_delay()}")
            except Exception as e:
                lines.append(f"audio_get_delay error: {e}")

            lines.append(f"media: {player.get_media()}")
            lines.append(f"position: {player.get_position()}")
            lines.append(f"length: {player.get_length()}")
            lines.append(f"time: {player.get_time()}")
            lines.append(f"rate: {player.get_rate()}")

            # --- Информация о media ---
            media = player.get_media()
            if media:
                try:
                    media.parse_with_options(1, 2 * 1000)  # блокирующий парсинг 2 сек
                except Exception as e:
                    lines.append(f"media.parse_with_options error: {e}")

                lines.append("\n--- Media info ---")
                lines.append(f"media.get_mrl: {media.get_mrl()}")
                lines.append(f"media.get_duration: {media.get_duration()}")
                try:
                    lines.append(f"media.get_state: {media.get_state()}")
                except Exception as e:
                    lines.append(f"media.get_state error: {e}")

                # Meta info
                lines.append("\n--- Meta ---")
                for meta in [vlc.Meta.Title, vlc.Meta.Artist, vlc.Meta.Album, vlc.Meta.Genre, vlc.Meta.Copyright,
                             vlc.Meta.TrackNumber, vlc.Meta.Description, vlc.Meta.Rating, vlc.Meta.Date,
                             vlc.Meta.Setting, vlc.Meta.URL, vlc.Meta.Language, vlc.Meta.NowPlaying, vlc.Meta.Publisher,
                             vlc.Meta.EncodedBy, vlc.Meta.ArtworkURL, vlc.Meta.TrackID]:
                    try:
                        value = media.get_meta(meta)
                        if value:
                            lines.append(f"{meta}: {value}")
                    except Exception:
                        pass

                # Tracks info
                lines.append("\n--- Tracks ---")
                try:
                    tracks = media.tracks_get()
                    if tracks:
                        for t in tracks:
                            lines.append(
                                f"Track type: {getattr(t, 'type', None)}, codec: {getattr(t, 'codec', None)}, id: {getattr(t, 'id', None)}")
                            # Выводим __dict__ (может быть пустой)
                            lines.append(f"  __dict__: {t.__dict__}")
                            # Выводим все имена атрибутов, кроме служебных
                            all_attrs = [a for a in dir(t) if not a.startswith('__') and not callable(getattr(t, a))]
                            lines.append(f"  dir(): {all_attrs}")
                            # Пробуем вывести значения каждого атрибута через getattr
                            for attr in all_attrs:
                                try:
                                    value = getattr(t, attr)
                                    lines.append(f"    {attr}: {value}")
                                except Exception as e:
                                    lines.append(f"    {attr}: [error: {e}]")
                except Exception as e:
                    lines.append(f"tracks_get error: {e}")
            # --- Ошибки и лог ---
            lines.append("\n--- Ошибки/логи ---")
            try:
                lines.append(f"last_error: {player.get_last_error() if hasattr(player, 'get_last_error') else 'n/a'}")
            except Exception:
                pass

            # --- Системная громкость (Windows) ---
            try:
                import platform
                if platform.system() == "Windows":
                    from ctypes import POINTER, cast
                    from comtypes import CLSCTX_ALL
                    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
                    devices = AudioUtilities.GetSpeakers()
                    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
                    volume = cast(interface, POINTER(IAudioEndpointVolume))
                    currentVolumeDb = volume.GetMasterVolumeLevel()
                    lines.append(f"System volume (dB): {currentVolumeDb}")
            except Exception:
                pass

        else:
            lines.append("VLC player не инициализирован!")

        return Response("<pre>" + "\n".join(str(l) for l in lines) + "</pre>", mimetype="text/html")

    @app.route("/play_test") # для диагностики http://localhost:8080/play_test?path=2025/Prime%20Time%20DJ/02-03-25/Club%20House%20_%20140%20Tracks/120%20-%208A%20-%20Tebra%20-%20Here%20&%20Now%20(Extended%20Mix).mp3
    def play_track_test():
        logger.info("Запущен маршрут /play_test")
        path = request.args.get("path")
        if not path:
            logger.error("Нет переданного параметра path")
            return jsonify({"error": "No path provided"}), 400

        decoded_path = unescape(unquote_plus(path))
        if is_log_type_enabled("status"):
            status_logger.debug("DEBUG: decoded_path = %r", decoded_path)
        norm_rel_path = os.path.normpath(decoded_path)
        if is_log_type_enabled("status"):
            status_logger.debug("DEBUG: norm_rel_path = %r", norm_rel_path)
        track_title = get_track_title(norm_rel_path)
        MUSIC_DIR = load_config().get("music_dir", DEFAULT_CONFIG["music_dir"])
        if is_log_type_enabled("status"):
            status_logger.debug("DEBUG: MUSIC_DIR = %r", MUSIC_DIR)
        full_path = safe_join_music_dir(MUSIC_DIR, norm_rel_path)
        if is_log_type_enabled("status"):
            status_logger.debug("DEBUG: full_path = %r", full_path)

        if not os.path.isfile(full_path):
            logger.error("Файл не найден: %s", full_path)
            return jsonify({"error": "File not found", "full_path": full_path}), 404

        folder = os.path.dirname(norm_rel_path)
        global_state["current_playlist_directory"] = folder
        session["current_folder"] = folder

        # Формируем плейлист
        try:
            norm_folder = os.path.normpath(folder)
            playlist_dir = safe_join_music_dir(MUSIC_DIR, norm_folder)
            playlist = sorted(
                f for f in os.listdir(playlist_dir)
                if f.lower().endswith(".mp3")
            )
        except Exception as e:
            logger.error("Ошибка формирования плейлиста: %s", e)
            playlist = []
        global_state["current_playlist"] = playlist

        file_name = os.path.basename(norm_rel_path)
        if file_name in playlist:
            global_state["current_index"] = playlist.index(file_name)
        else:
            global_state["current_index"] = 0

        config = load_config()
        mode = config.get("playback_mode", "host")
        play_url = None

        if mode == "host":
            with vlc_lock:
                try:
                    player = global_state["current_player"]
                    player.stop()
                    time.sleep(0.2)  # Дать VLC освободить устройство (важно для Windows)
                    media = global_state["vlc_instance"].media_new(full_path)
                    player.set_media(media)
                    time.sleep(0.1)  # Дать VLC инициализировать media

                    # Получаем актуальный список устройств теперь, когда media назначена
                    vlc_devices = []
                    out = player.audio_output_device_enum()
                    while out:
                        dev = out.contents
                        device_id = dev.device.decode() if dev.device else ""
                        description = dev.description.decode() if dev.description else ""
                        if device_id:
                            vlc_devices.append({'id': device_id, 'name': description})
                        out = dev.next

                    selected_index = int(config.get("selected_device", 0))
                    if is_log_type_enabled("audio_diag"):
                        audio_diag_logger.debug(f"[AUDIO DIAG] (play_test) Найдено {len(vlc_devices)} устройств:")
                    for idx, dev in enumerate(vlc_devices):
                        if is_log_type_enabled("audio_diag"):
                            audio_diag_logger.debug(f"[AUDIO DIAG]   idx={idx} id={dev['id']} name={dev['name']}")
                    if is_log_type_enabled("audio_diag"):
                        audio_diag_logger.debug(f"[AUDIO DIAG] (play_test) selected_index из конфига: {selected_index}")

                    if vlc_devices and selected_index >= len(vlc_devices):
                        if is_log_type_enabled("audio_diag"):
                            audio_diag_logger.debug(
                            "[AUDIO DIAG] (play_test) Выбранный индекс устройства превышает число устройств. Сбрасываем на 0.")
                        selected_index = 0
                    if vlc_devices:
                        device_id = vlc_devices[selected_index]["id"]
                        if is_log_type_enabled("audio_diag"):
                            audio_diag_logger.debug(
                            f"[AUDIO DIAG] (play_test) Попытка установить аудиоустройство: {device_id} ({vlc_devices[selected_index]['name']})")
                        player.audio_output_device_set(None, device_id)
                        time.sleep(0.05)
                        device_applied = player.audio_output_device_get()
                    else:
                        if is_log_type_enabled("audio_diag"):
                            audio_diag_logger.debug(
                            "[AUDIO DIAG] (play_test) Устройство не установлено, используется устройство по умолчанию.")
                        device_applied = None

                    # Громкость — из глобального состояния или по умолчанию
                    if global_state.get("current_volume") is None:
                        global_state["current_volume"] = config.get("default_volume", 70)
                    volume_to_set = global_state["current_volume"]
                    player.audio_set_volume(volume_to_set)

                    player.play()
                    time.sleep(0.2)  # Дать стартовать

                    is_playing = player.is_playing()
                    current_time = player.get_time()
                    if is_log_type_enabled("audio_diag"):
                        audio_diag_logger.debug(
                        f"[AUDIO DIAG] (play_test) Плеер запущен: is_playing={is_playing}, time={current_time}, track={norm_rel_path}")

                except Exception as e:
                    logger.error("Error playing file (play_test): %s", str(e))
                    return jsonify({"error": str(e)}), 500
        elif mode == "plyr":
            play_url = url_for("stream", path=decoded_path)
        else:
            return jsonify({"error": "Неверный режим воспроизведения"}), 400

        genre = get_scanned_genre(norm_rel_path)
        global_state["current_track"]["path"] = norm_rel_path
        global_state["current_track"]["genre"] = genre
        global_state["current_track"]["taxonomy"] = get_scanned_taxonomy(norm_rel_path)
        global_state["current_track"]["title"] = track_title
        old_owner = global_state["current_track"].get("owner_sid", None)
        new_owner = get_owner_sid()
        global_state["current_track"]["owner_sid"] = new_owner
        session["current_track"] = norm_rel_path

        response = {
            "status": "playing",
            "track": norm_rel_path,
            "title": track_title,
            "genre": genre,
            "taxonomy": global_state["current_track"].get("taxonomy", {}),
            "audio_device": device_applied if mode == "host" else None,
            "is_playing": is_playing if mode == "host" else None
        }
        if play_url:
            response["play_url"] = play_url

        logger.info("Формируется ответ (play_test): %s", response)
        return jsonify(response)

    @app.route('/test')   # Удалить потом  нужно для отображения templates/test.html
    def test_tojson():
        return render_template('test.html')
