import os
import json
from flask import Blueprint, render_template, request, jsonify
from werkzeug.utils import secure_filename

LIBROSA_CONFIG_FILE = "librosa_config.json"
librosa_settings_bp = Blueprint('librosa_settings', __name__)
librosa_test_bp = Blueprint('librosa_test', __name__)

UPLOAD_FOLDER = "test_uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DEFAULT_LIBROSA_SETTINGS = {
    "sample_rate": 22050,
    "duration": 30,
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
    }
}
def load_librosa_settings():
    if os.path.exists(LIBROSA_CONFIG_FILE):
        with open(LIBROSA_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return DEFAULT_LIBROSA_SETTINGS.copy()

def save_librosa_settings(settings):
    with open(LIBROSA_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=4, ensure_ascii=False)

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
    if request.method == "POST":
        file = request.files.get("audiofile")
        if file and file.filename:
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
    return render_template("librosa/librosa_test.html", result=result, settings=settings)