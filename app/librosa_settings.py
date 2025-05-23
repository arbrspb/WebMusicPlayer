# # app/librosa_settings.py
import os
import json
from flask import Blueprint, render_template, request, jsonify
from werkzeug.utils import secure_filename
from .reckordbox_parser import parse_reckordbox_xml
from app.utils import get_genre_stats_by_folders
from .utils import get_genre_stats_and_tracks_by_model
import logging
import urllib.parse

# Глобальный кэш (на время работы flask)
genre_stats_cache = {}

logger = logging.getLogger(__name__)
MAX_FILES_LIBROSA = 0  # Лимит анализа треков для всех функций
LIBROSA_CONFIG_FILE = "librosa_config.json"
librosa_settings_bp = Blueprint('librosa_settings', __name__)
librosa_test_bp = Blueprint('librosa_test', __name__)

UPLOAD_FOLDER = "test_uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

REKORDBOX_UPLOAD_FOLDER = "reckordbox_parcer_file_output"
os.makedirs(REKORDBOX_UPLOAD_FOLDER, exist_ok=True)
REKORDBOX_XML_PATH = os.path.join(REKORDBOX_UPLOAD_FOLDER, "uploaded_rekordbox.xml")
REKORDBOX_JSON_UPLOAD_PATH = os.path.join(REKORDBOX_UPLOAD_FOLDER, "uploaded_rekordbox.json")
REKORDBOX_JSON_PARSED_PATH = os.path.join(REKORDBOX_UPLOAD_FOLDER, "parsed_rekordbox.json")

REKORDBOX_JSON_PARSED_STATE = {"status": "not_ready", "count": 0}

DEFAULT_LIBROSA_SETTINGS = {
    "sample_rate": 22050,
    "duration": 30,
    "offset": 0,
    "REKORDBOX_TRACK_LIMIT": 5000,
    "min_tracks_per_genre": 130,
    "max_tracks_per_genre": 130,
    "n_mfcc": 13,
    "hop_length": 512,
    "n_fft": 2048,
    "win_length": 2048,
    "window": "hann",
    "use_id3": True,
    "use_folder": True,
    "genre_threshold": 0.6,
    "n_estimators": 100,
    "features": {
        "mfcc": True,
        "chroma": True,
        "spectral_contrast": True,
        "zcr": True,
        "tonnetz": True
    },
    "use_rekordbox": False
}

def load_librosa_settings():
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
        return settings
    else:
        # ДЛЯ ПОЛНОЙ ЗАЩИТЫ можно использовать deepcopy, но copy достаточно если нет сложной вложенности
        import copy
        return copy.deepcopy(DEFAULT_LIBROSA_SETTINGS)


def save_librosa_settings(settings):
    with open(LIBROSA_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=4, ensure_ascii=False)

def get_cached_genre_stats(folder, settings, logger):# Кжш для треков
    folder = os.path.abspath(folder)
    key = (folder, json.dumps(settings, sort_keys=True))
    if key in genre_stats_cache:
        return genre_stats_cache[key]
    # Анализируем и сохраняем!
    stats = get_genre_stats_and_tracks_by_model(
        folder, librosa_settings=settings, max_files=MAX_FILES_LIBROSA, logger=logger
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
    return render_template("librosa/librosa_settings.html", settings=settings)

@librosa_settings_bp.route("/librosa-settings", methods=["POST"])
def librosa_settings_save():
    data = request.get_json()
    save_librosa_settings(data)
    return jsonify({"status": "ok"})

@librosa_settings_bp.route("/librosa-settings/test", methods=["POST"])
def librosa_settings_test():
    # Протестировать текущие настройки на тестовом файле
    from .models import get_genre
    test_path = request.json.get("test_path")
    settings = load_librosa_settings()
    genre, conf = get_genre(test_path, librosa_params=settings)
    return jsonify({"genre": genre, "confidence": conf})

@librosa_test_bp.route("/librosa-test", methods=["GET", "POST"])
def librosa_test():
    result = None
    settings = load_librosa_settings()

    # --- Всегда анализ samples ---
    samples_folder = "samples"
    samples_stats = get_genre_stats_by_folders(samples_folder, max_tracks_per_genre=200)
    samples_total = sum(item['count'] for item in samples_stats)

    # --- Анализ по выбранной пользователем папке ---
    folder_path = request.form.get("folder_path") if request.method == "POST" else None
    user_genre_stats = None
    user_total_files = None
    current_folder = None
    genre_tracks = None

    if folder_path:
        current_folder = folder_path
        if os.path.isdir(folder_path):
            user_genre_stats, user_total_files, genre_tracks = get_cached_genre_stats(
                folder_path, settings=settings, logger=logger
            )
        else:
            user_genre_stats = {}
            user_total_files = 0
            genre_tracks = {}

    # --- Обработка загрузки трека для анализа ---
    if request.method == "POST" and "audiofile" in request.files:
        file = request.files.get("audiofile")
        if file and file.filename:
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
            filename = secure_filename(file.filename)
            filepath = os.path.join(UPLOAD_FOLDER, filename)
            file.save(filepath)
            from .models import get_genre
            genre, conf = get_genre(filepath, librosa_params=settings)
            result = {
                "filename": filename,
                "genre": genre,
                "confidence": conf
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
        genre_tracks=genre_tracks or {}
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
        folder, settings=settings, logger=logger
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
    print(f"[DEBUG] os.path.exists(folder): {os.path.exists(folder)}")
    print(f"[DEBUG] os.path.isdir(folder): {os.path.isdir(folder)}")
    print(f"[DEBUG] abs path: {os.path.abspath(folder)}")
    print(f"[DEBUG] folder: {folder}")
    settings = load_librosa_settings()
    if not folder or not genre or not os.path.isdir(folder):
        print(f"[DEBUG] folder not found or not a directory: {folder}")
        return jsonify([])
    _, _, genre_tracks = get_cached_genre_stats(
        folder, settings=settings, logger=logger
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
    print(f"[DEBUG] files_short: {files_short}")
    return jsonify(files_short)