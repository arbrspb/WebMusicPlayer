# app/librosa_settings.py 14-08-25 01-50
"""Обработчики и настройки Librosa для WebMusicPlayer, включая загрузку/сохранение параметров, работу с Rekordbox и анализ жанров."""
import os
import json
import logging
import urllib.parse

from flask import Blueprint, render_template, request, jsonify, send_file
from werkzeug.utils import secure_filename

from app.utils import get_genre_stats_by_folders
from .reckordbox_parser import parse_reckordbox_xml
from .utils import get_genre_stats_and_tracks_by_model
from .paths import LIBROSA_CONFIG_FILE as LIBROSA_CONFIG_PATH
from .paths import (
    ACTIVE_MODEL_MANIFEST_FILE,
    REKORDBOX_OUTPUT_DIR,
    SAMPLES_DIR,
    TEST_UPLOADS_DIR,
    TRAINING_DUPLICATES_FILE,
    TRAINING_CONFLICTS_FILE,
    TRAINING_ERRORS_FILE,
    TRAINING_LANGUAGE_ERRORS_FILE,
    TRAINING_LABEL_CONFLICTS_FILE,
    TRAINING_QUALITY_REPORT_FILE,
    TRAINING_REVIEW_QUEUE_FILE,
)
from .genre_review import (
    list_review_entries,
    record_review_candidate,
    remove_review_entry,
    save_manual_correction,
)
from .track_taxonomy import (
    SUPPORTED_BASE_GENRES,
    SUPPORTED_LANGUAGES,
    SUPPORTED_VERSION_TYPES,
)
from .vocal_language import clear_vocal_language_cache, vocal_language_backend_status
# Логирование
from .logging_config import (
    is_log_type_enabled,
    setup_model_logger,
    # (другие setup_ если понадобятся)
)

# model logger
model_logger = logging.getLogger("model")
setup_model_logger()


genre_keywords_cache = {}
# Глобальный кэш (на время работы flask)
genre_stats_cache = {}

logger = logging.getLogger(__name__) # Логирование

MAX_FILES_LIBROSA = 10000  # Лимит анализа треков для всех функций
LIBROSA_CONFIG_FILE = str(LIBROSA_CONFIG_PATH)
librosa_settings_bp = Blueprint('librosa_settings', __name__)
librosa_test_bp = Blueprint('librosa_test', __name__)

UPLOAD_FOLDER = str(TEST_UPLOADS_DIR)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

REKORDBOX_UPLOAD_FOLDER = str(REKORDBOX_OUTPUT_DIR)
os.makedirs(REKORDBOX_UPLOAD_FOLDER, exist_ok=True)
REKORDBOX_XML_PATH = os.path.join(REKORDBOX_UPLOAD_FOLDER, "uploaded_rekordbox.xml")
REKORDBOX_JSON_UPLOAD_PATH = os.path.join(REKORDBOX_UPLOAD_FOLDER, "uploaded_rekordbox.json")
REKORDBOX_JSON_PARSED_PATH = os.path.join(REKORDBOX_UPLOAD_FOLDER, "parsed_rekordbox.json")

REKORDBOX_JSON_PARSED_STATE = {"status": "not_ready", "count": 0}


def _load_active_model_manifest(path=ACTIVE_MODEL_MANIFEST_FILE):
    """Read the small active-model sidecar without unpickling the RF model."""
    try:
        with open(path, "r", encoding="utf-8") as manifest_file:
            payload = json.load(manifest_file)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError) as exc:
        logger.warning("Не удалось прочитать манифест активной модели: %s", exc)
        return {}


def _genre_analysis_summary(genre, confidence, meta):
    meta = meta if isinstance(meta, dict) else {}
    return {
        "predicted_genre": genre,
        "confidence": float(confidence or 0.0),
        "top_candidates": list(meta.get("top_candidates") or []),
        "rejected_reasons": list(meta.get("rejected_reasons") or []),
        "segment_disagreement": bool(meta.get("segment_disagreement", False)),
        "decision_threshold": float(meta.get("decision_threshold", 0.0) or 0.0),
        "decision_margin": float(meta.get("decision_margin", 0.0) or 0.0),
        "acoustic_prediction": meta.get("acoustic_prediction"),
    }

DEFAULT_LIBROSA_SETTINGS = {
    "sample_rate": 22050,
    "duration": 30,
    "offset": 45,
    "rekordbox_track_limit": 5000,
    "min_tracks_per_genre": 130,
    "max_tracks_per_genre": 159,
    "n_mfcc": 40,
    "hop_length": 512,
    "n_fft": 4096,
    "win_length": 2048,
    "window": "hann",
    "use_id3": True,
    "use_folder": True,
    "genre_threshold": 0.55,
    "calibrate_probabilities": True,
    "calibration_method": "sigmoid",
    "calibration_cv": 3,
    "auto_class_thresholds_enabled": True,
    "target_class_precision": 0.9,
    "min_genre_margin": 0.1,
    "segment_disagreement_penalty": 0.1,
    "training_quality_gate_enabled": True,
    "training_min_macro_f1": 0.65,
    "training_min_accepted_precision": 0.90,
    "training_min_coverage": 0.45,
    "training_min_class_accepted_tracks": 2,
    "training_min_class_accepted_precision": 0.75,
    "training_min_retained_style_f1": 0.70,
    "training_max_retained_style_recall_drop": 0.05,
    "progressive_style_admission_enabled": True,
    "training_new_style_min_f1": 0.60,
    "training_new_style_min_recall": 0.50,
    "training_new_style_min_support": 15,
    "active_review_max_pairs": 10,
    "active_review_tracks_per_pair": 40,
    "hierarchical_genre_enabled": True,
    "hierarchical_genre_weight": 0.72,
    "hierarchical_genre_estimators": 180,
    "family_fallback_enabled": True,
    "family_fallback_threshold": 0.68,
    "family_fallback_margin": 0.15,
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
    "max_base_class_ratio": 1.5,
    "auto_tune_model": True,
    "auto_tune_iterations": 12,
    "auto_tune_cv": 3,
    "multi_segment_enabled": True,
    "multi_segment_offsets": "30,60,90",
    "multi_segment_duration": 15,
    "n_estimators": 500,
    "validation_size": 0.2,
    "random_state": 42,
    "max_depth": 24,
    "min_samples_leaf": 2,
    "max_features": "sqrt",
    "enable_learning_curve": False,
    "yamnet_enabled": False,
    "yamnet_use_cuda": False,
    "yamnet_alpha": 0.35,
    "yamnet_model_path": "yamnet.onnx",
    "features": {
        "mfcc": True,
        "chroma": True,
        "spectral_contrast": True,
        "zcr": True,
        "tonnetz": True,
        "spectral_centroid": True,
        "spectral_bandwidth": True,
        "spectral_rolloff": True,
        "rms": True,
        "onset_strength": False,
        "tempo": True,
        "tempogram": False,
        "delta_mfcc": False,
        "delta2_mfcc": False,
        "spectral_flatness": False,
        "pitch": False,
        "silence_ratio": False,
        "energy_entropy": False,
        "spectral_skewness": False,
        "harmonic_ratio": False,
        "mfcc_std": False,
        "energy_ratio": False,
        "spectral_stats": False
    },
    "use_rekordbox": False,
    "rekordbox_source": "json"
}

def load_librosa_settings():
    """Загружает настройки Librosa из файла или возвращает значения по умолчанию."""
    if os.path.exists(LIBROSA_CONFIG_FILE):
        with open(LIBROSA_CONFIG_FILE, "r", encoding="utf-8") as f:
            settings = json.load(f)
        # дополним недостающие параметры из дефолта
        for key, val in DEFAULT_LIBROSA_SETTINGS.items():
            if key not in settings:
                settings[key] = val
        # вложенные словари (features)
        if "features" in DEFAULT_LIBROSA_SETTINGS:
            for fkey, fval in DEFAULT_LIBROSA_SETTINGS["features"].items():
                if fkey not in settings.get("features", {}):
                    settings["features"][fkey] = fval

        # --- MIGRATION: bpm -> tempo ---
        try:
            feats = settings.get("features", {})
            if "tempo" not in feats and "bpm" in feats:
                feats["tempo"] = feats["bpm"]
            if "bpm" in feats:
                # удаляем устаревший ключ, чтобы не сохранять его снова
                feats.pop("bpm", None)
            settings["features"] = feats
        except Exception as mig_e:
            if is_log_type_enabled("model"):
                model_logger.warning(f"[MIGRATION] bpm->tempo migration failed: {mig_e}")

        return settings
    else:
        # ДЛЯ ПОЛНОЙ ЗАЩИТЫ можно использовать deepcopy, но copy достаточно если нет сложной вложенности
        import copy
        return copy.deepcopy(DEFAULT_LIBROSA_SETTINGS)

def save_librosa_settings(settings):
    with open(LIBROSA_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=4, ensure_ascii=False)

def get_cached_genre_stats(folder, settings, max_files=None):# Кэш для треков
    folder = os.path.abspath(folder)
    key = (folder, json.dumps(settings, sort_keys=True))
    if key in genre_stats_cache:
        return genre_stats_cache[key]
    # Анализируем и сохраняем!
    stats = get_genre_stats_and_tracks_by_model(
        folder, librosa_settings=settings, max_files=max_files or MAX_FILES_LIBROSA
    )
    genre_stats_cache[key] = stats
    return stats

@librosa_test_bp.route("/librosa-clear-cache", methods=["POST"]) # Сбрс кэша треков
def librosa_clear_cache():
    genre_stats_cache.clear()
    return jsonify({"status": "cache_cleared"})

@librosa_settings_bp.route("/librosa-settings/upload-rekordbox-json", methods=["POST"])
def upload_rekordbox_json():
    if "jsonfile" not in request.files:
        return jsonify({"error": "Нет файла!"}), 400
    f = request.files["jsonfile"]
    f.save(REKORDBOX_JSON_UPLOAD_PATH)
    return jsonify({"status": "ok"})

@librosa_settings_bp.route("/librosa-settings/parse-rekordbox-json", methods=["POST"])
def parse_rekordbox_json():
    if not os.path.exists(REKORDBOX_JSON_UPLOAD_PATH):
        return jsonify({"error": "Файл не загружен!"}), 400
    try:
        with open(REKORDBOX_JSON_UPLOAD_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        with open(REKORDBOX_JSON_PARSED_PATH, "w", encoding="utf-8") as f2:
            json.dump(data, f2)
        return jsonify({"status": "ok", "count": len(data)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@librosa_settings_bp.route("/librosa-settings/rekordbox-json-status")
def rekordbox_json_status():
    parsed_path = REKORDBOX_JSON_PARSED_PATH
    upload_path = REKORDBOX_JSON_UPLOAD_PATH

    if os.path.exists(parsed_path):
        try:
            with open(parsed_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            count = len(data)
        except Exception:
            count = 0
        return jsonify({"status": "ready", "count": count})
    elif os.path.exists(upload_path):
        try:
            with open(upload_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            count = len(data)
        except Exception:
            count = 0
        return jsonify({"status": "json_uploaded", "count": count})
    else:
        return jsonify({"status": "not_ready", "count": 0})

@librosa_settings_bp.route("/librosa-settings", methods=["GET"])
def librosa_settings_page():
    settings = load_librosa_settings()
    model_confidence = {}
    training_quality_gate = {}
    active_model = _load_active_model_manifest()
    if active_model:
        model_confidence = {
            "version": active_model.get("version", "legacy"),
            "taxonomy_active": str(active_model.get("version", "")).startswith(("2.", "3.", "4.")),
            "classification_report": active_model.get("classification_report", {}) or {},
        }
    if TRAINING_QUALITY_REPORT_FILE.exists():
        try:
            with open(TRAINING_QUALITY_REPORT_FILE, "r", encoding="utf-8") as quality_file:
                training_quality_gate = json.load(quality_file)
        except (OSError, ValueError) as exc:
            logger.warning("Не удалось прочитать отчёт quality gate: %s", exc)
    return render_template(
        "librosa/librosa_settings.html",
        settings=settings,
        active_model=active_model,
        model_confidence=model_confidence,
        training_quality_gate=training_quality_gate,
        vocal_language_status=vocal_language_backend_status(settings),
    )


@librosa_settings_bp.route("/librosa-settings/vocal-language-status", methods=["GET"])
def vocal_language_status():
    return jsonify(vocal_language_backend_status(load_librosa_settings()))


@librosa_settings_bp.route("/librosa-settings/vocal-language-cache/clear", methods=["POST"])
def vocal_language_cache_clear():
    clear_vocal_language_cache()
    return jsonify({"status": "cache_cleared"})

@librosa_settings_bp.route("/librosa-settings", methods=["POST"])
def librosa_settings_save():
    data = request.get_json()
    save_librosa_settings(data)
    return jsonify({"status": "ok"})


@librosa_settings_bp.route("/librosa-settings/training-errors.csv")
def download_training_errors():
    if not TRAINING_ERRORS_FILE.exists():
        return jsonify({"error": "Отчёт ещё не создан. Сначала переобучите модель."}), 404
    return send_file(TRAINING_ERRORS_FILE, as_attachment=True, download_name="training_errors.csv")


@librosa_settings_bp.route("/librosa-settings/training-language-errors.csv")
def download_training_language_errors():
    if not TRAINING_LANGUAGE_ERRORS_FILE.exists():
        return jsonify({"error": "Отчёт языка ещё не создан. Сначала переобучите модель."}), 404
    return send_file(
        TRAINING_LANGUAGE_ERRORS_FILE,
        as_attachment=True,
        download_name="training_language_errors.csv",
    )


@librosa_settings_bp.route("/librosa-settings/training-duplicates.csv")
def download_training_duplicates():
    if not TRAINING_DUPLICATES_FILE.exists():
        return jsonify({"error": "Отчёт ещё не создан. Сначала переобучите модель."}), 404
    return send_file(TRAINING_DUPLICATES_FILE, as_attachment=True, download_name="training_duplicates.csv")


@librosa_settings_bp.route("/librosa-settings/training-label-conflicts.csv")
def download_training_label_conflicts():
    if not TRAINING_LABEL_CONFLICTS_FILE.exists():
        return jsonify({"error": "Отчёт дублей/противоречий ещё не создан"}), 404
    return send_file(
        TRAINING_LABEL_CONFLICTS_FILE,
        as_attachment=True,
        download_name="training_label_conflicts.csv",
    )


@librosa_settings_bp.route("/librosa-settings/training-conflicts.csv")
def download_training_conflicts():
    if not TRAINING_CONFLICTS_FILE.exists():
        return jsonify({"error": "Отчёт конфликтов ещё не создан. Сначала переобучите модель."}), 404
    return send_file(
        TRAINING_CONFLICTS_FILE,
        as_attachment=True,
        download_name="training_conflicts.csv",
    )


@librosa_settings_bp.route("/librosa-settings/training-review-queue.csv")
def download_training_review_queue():
    if not TRAINING_REVIEW_QUEUE_FILE.exists():
        return jsonify({"error": "Очередь проверки ещё не создана. Сначала переобучите модель."}), 404
    return send_file(
        TRAINING_REVIEW_QUEUE_FILE,
        as_attachment=True,
        download_name="training_review_queue.csv",
    )


@librosa_settings_bp.route("/librosa-settings/training-quality-gate.json")
def download_training_quality_gate():
    if not TRAINING_QUALITY_REPORT_FILE.exists():
        return jsonify({"error": "Проверка качества ещё не выполнялась."}), 404
    return send_file(
        TRAINING_QUALITY_REPORT_FILE,
        as_attachment=True,
        download_name="training_quality_gate.json",
    )

@librosa_settings_bp.route("/librosa-settings/test", methods=["POST"]) # Тестирование одного трека
def librosa_settings_test():
    from .models import get_genre
    test_path = request.json.get("test_path")
    settings = load_librosa_settings()
    if is_log_type_enabled("model"):
        model_logger.debug("Call get_genre for: %s", test_path)
    genre_result = get_genre(test_path, librosa_params=settings, return_meta=True)
    genre, conf, _features = genre_result[:3]
    analysis_meta = genre_result[3] if len(genre_result) == 4 and genre_result[3] else {}
    taxonomy = analysis_meta.get("taxonomy")
    if is_log_type_enabled("model"):
        model_logger.debug(f"Результат get_genre: genre={genre}, confidence={conf} для test_path={test_path}")
    return jsonify({
        "genre": genre,
        "confidence": conf,
        "taxonomy": taxonomy,
        "analysis": _genre_analysis_summary(genre, conf, analysis_meta),
    })


@librosa_test_bp.route("/librosa-review", methods=["GET"])
def librosa_review_list():
    status = request.args.get("status") or None
    return jsonify({"entries": list_review_entries(status=status)})


@librosa_test_bp.route("/librosa-review/corrections", methods=["POST"])
def librosa_review_save_correction():
    payload = request.get_json(silent=True) or {}
    try:
        entry = save_manual_correction(
            path=payload.get("path"),
            base_genre=payload.get("base_genre"),
            language=payload.get("language", "Auto"),
            version_type=payload.get("version_type", "Auto"),
            note=payload.get("note", ""),
            analysis=payload.get("analysis"),
        )
    except (OSError, ValueError, TypeError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"status": "saved", "entry": entry})


@librosa_test_bp.route("/librosa-review/<entry_id>", methods=["DELETE"])
def librosa_review_delete(entry_id):
    removed = remove_review_entry(entry_id)
    if removed is None:
        return jsonify({"error": "Запись не найдена"}), 404
    return jsonify({"status": "deleted", "entry": removed})

@librosa_test_bp.route("/librosa-test", methods=["GET", "POST"])
def librosa_test():
    result = None
    settings = load_librosa_settings()

    # --- Всегда анализ samples ---
    samples_folder = str(SAMPLES_DIR)
    samples_stats = get_genre_stats_by_folders(samples_folder, max_tracks_per_genre=200)
    samples_total = sum(item['count'] for item in samples_stats)
    if is_log_type_enabled("model"):
        model_logger.debug(
            f"Анализ samples: {samples_folder}, найдено жанров: {len(samples_stats)}, всего файлов: {samples_total}")

    # --- Анализ по выбранной пользователем папке ---
    folder_path = request.form.get("folder_path") if request.method == "POST" else None
    user_genre_stats = None
    user_total_files = None
    current_folder = None
    genre_tracks = None

    if folder_path:
        current_folder = folder_path
        if os.path.isdir(folder_path):
            limit = int(request.form.get("limit", 0))
            if is_log_type_enabled("model"):
                model_logger.debug(f"Анализ пользовательской папки: {folder_path}, лимит: {limit}")
            user_genre_stats, user_total_files, genre_tracks = get_cached_genre_stats(
                folder_path, settings=settings, max_files=limit
            )
        else:
            user_genre_stats = {}
            user_total_files = 0
            genre_tracks = {}

    # --- Обработка загрузки трека для анализа ---
    if request.method == "POST" and "audiofile" in request.files:
        file = request.files.get("audiofile")
        if not file or not file.filename:
            if is_log_type_enabled("model"):
                model_logger.warning("Не удалось загрузить файл для анализа: отсутствует файл или имя файла")
        elif file and file.filename:
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
            filename = secure_filename(file.filename)
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            file.save(filepath)
            if is_log_type_enabled("model"):
                model_logger.debug(f"Тестовая загрузка файла для анализа: {filepath}")
            from .models import get_genre
            if is_log_type_enabled("model"):
                model_logger.debug("Call get_genre for uploaded file: %s", filepath)
            try:
                genre_result = get_genre(filepath, librosa_params=settings, return_meta=True)
                genre, confidence, features = genre_result[:3]
                analysis_meta = genre_result[3] if len(genre_result) == 4 and genre_result[3] else {}
                taxonomy = analysis_meta.get("taxonomy")
                analysis = _genre_analysis_summary(genre, confidence, analysis_meta)
                review_entry = None
                if genre == "Unknown" or analysis["rejected_reasons"]:
                    review_entry = record_review_candidate(filepath, analysis)
                result = {
                    "filename": filename,
                    "filepath": filepath,
                    "genre": genre,
                    "confidence": confidence,  # всегда число!
                    "features": features,      # если нужно, можно вывести в шаблоне отдельно
                    "taxonomy": taxonomy,
                    "analysis": analysis,
                    "review_entry": review_entry,
                }
                if is_log_type_enabled("model"):
                    model_logger.debug(f"Результат get_genre: genre={genre}, confidence={confidence} для файла {filepath}")
            except Exception as e:
                if is_log_type_enabled("model"):
                    model_logger.error(f"Ошибка анализа файла {filepath}: {e}")
                result = {
                    "filename": filename,
                    "genre": "Error",
                    "confidence": 0,
                    "error": str(e)
                }

    return render_template(
        "librosa/librosa_test.html",
        result=result,
        settings=settings,
        samples_stats=samples_stats,
        samples_total=samples_total,
        user_genre_stats=user_genre_stats,
        user_total_files=user_total_files,
        current_folder=current_folder,
        genre_tracks=genre_tracks or {},
        pending_reviews=list_review_entries(status="pending"),
        corrected_reviews=list_review_entries(status="corrected"),
        correction_base_genres=sorted(SUPPORTED_BASE_GENRES),
        correction_languages=["Auto"] + sorted(SUPPORTED_LANGUAGES - {"Unknown", "Foreign"}),
        correction_version_types=["Auto"] + sorted(SUPPORTED_VERSION_TYPES - {"Unknown"}),
    )

@librosa_settings_bp.route("/librosa-settings/upload-rekordbox", methods=["POST"])
def upload_rekordbox_xml():
    file = request.files.get("xmlfile")
    if not file or not file.filename.endswith(".xml"):
        return jsonify({"error": "Не выбран XML-файл"}), 400
    file.save(REKORDBOX_XML_PATH)
    return jsonify({"status": "uploaded"})

@librosa_settings_bp.route("/librosa-settings/parse-rekordbox", methods=["POST"])
def parse_rekordbox():
    try:
        parse_reckordbox_xml(REKORDBOX_XML_PATH, REKORDBOX_JSON_PARSED_PATH)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@librosa_settings_bp.route("/librosa-settings/rekordbox-status", methods=["GET"])
def rekordbox_status():
    source = request.args.get("source", "xml")  # по умолчанию xml

    xml_exists = os.path.exists(REKORDBOX_XML_PATH)
    json_parsed_exists = os.path.exists(REKORDBOX_JSON_PARSED_PATH)
    json_uploaded_exists = os.path.exists(REKORDBOX_JSON_UPLOAD_PATH)

    # === Для источника JSON ===
    if source == "json":
        if json_parsed_exists:
            try:
                with open(REKORDBOX_JSON_PARSED_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                count = len(data)
            except Exception:
                count = 0
            return jsonify({"status": "json_ready", "count": count})
        elif json_uploaded_exists:
            try:
                with open(REKORDBOX_JSON_UPLOAD_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                count = len(data)
            except Exception:
                count = 0
            return jsonify({"status": "json_uploaded", "count": count})
        else:
            return jsonify({"status": "not_ready", "count": 0})

    # === Для источника XML ===
    else:
        # Если есть готовый распарсенный JSON и исходный XML (оба файла), значит XML был успешно распарсен
        if json_parsed_exists and xml_exists:
            try:
                with open(REKORDBOX_JSON_PARSED_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                count = len(data)
            except Exception:
                count = 0
            return jsonify({"status": "xml_ready", "count": count})
        elif xml_exists:
            return jsonify({"status": "xml_uploaded", "count": 0})
        else:
            return jsonify({"status": "not_ready", "count": 0})

@librosa_test_bp.route("/librosa-genre-stats-export")
def librosa_genre_stats_export():
    folder = request.args.get("folder")
    folder = urllib.parse.unquote(folder) if folder else folder
    if os.name == "nt" and folder:
        folder = folder.replace('/', '\\')
    folder = folder.rstrip("\\/")
    if not folder or not os.path.isdir(folder):
        return jsonify({"error": "folder not found"}), 400
    settings = load_librosa_settings()
    with_links = bool(int(request.args.get("with_links", "0")))
    user_genre_stats, user_total_files, genre_tracks = get_cached_genre_stats(
        folder, settings=settings
    )
    data = []
    for genre, count in user_genre_stats.items():
        item = {"genre": genre, "count": count}
        if with_links:
            files = genre_tracks.get(genre, [])
        item["files"] = []
        for i, f in enumerate(files):
            full_path = os.path.abspath(f).replace("\\", "/")
            item["files"].append({
                "idx": i + 1,
                "name": os.path.basename(f),
                "relpath": os.path.relpath(f, start=folder).replace("\\", "/"),
                "url": f"/musicfile?path={urllib.parse.quote(full_path)}"
            })
        data.append(item)
    return jsonify(data)

@librosa_test_bp.route("/librosa-genre-tracks")
def librosa_genre_tracks():
    folder = request.args.get("folder")
    genre = request.args.get("genre")
    if is_log_type_enabled("model"):
        model_logger.debug(f"os.path.exists(folder): {os.path.exists(folder)}")
    if is_log_type_enabled("model"):
        model_logger.debug(f"os.path.isdir(folder): {os.path.isdir(folder)}")
    if is_log_type_enabled("model"):
        model_logger.debug("abs path: {os.path.abspath(folder)}")
    if is_log_type_enabled("model"):
        model_logger.debug(f"folder: {folder}")
    settings = load_librosa_settings()
    if not folder or not genre or not os.path.isdir(folder):
        if is_log_type_enabled("model"):
            model_logger.debug(f"folder not found or not a directory: {folder}")
        return jsonify([])
    _, _, genre_tracks = get_cached_genre_stats(
        folder, settings=settings
    )
    files = genre_tracks.get(genre, [])
    files_short = [
        {
            "idx": i + 1,
            "name": os.path.basename(f),
            "url": f"/musicfile?path={urllib.parse.quote(os.path.abspath(f))}"
        }
        for i, f in enumerate(files)
    ]
    if is_log_type_enabled("model"):
        model_logger.debug(f"files_short: {files_short}")
    return jsonify(files_short)
