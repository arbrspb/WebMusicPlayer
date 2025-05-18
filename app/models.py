# app/models.py 18-05-25 21-22
import os
import threading
import json
import pickle
import numpy as np
import pandas as pd
import librosa
from mutagen.easyid3 import EasyID3
from sklearn.ensemble import RandomForestClassifier
from .db import init_scan_db, load_scan_result, save_scan_result
from .config import DEFAULT_CONFIG
import getpass
import re
import logging

global_state = None

logger = logging.getLogger(__name__)  # Логирование

MODEL_PATH = "genre_model.pkl"

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

DEFAULT_FOLDER_KEYWORDS = {
    "club house": "Club House",
    "drum & bass": "Drum & Bass",
    "hip-hop & rap": "Hip-Hop",
    "hiphop": "Hip-Hop",
    "mainstage": "Mainstage",
    "romantic selection": "Romantic Selection",
    "underground pop": "Underground Pop",
    "progressive": "Progressive House",
    "tech": "Tech House",
    "future": "Future House",
    "русские ремиксы": "Русские Ремиксы"
}
GENRE_SETTINGS_FILE = "folder_keywords.json"

import re

# Флаг для однократного логирования использования функции
normalize_for_genre_compare_used = False

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
        print("Функция normalize_for_genre_compare используется")
        normalize_for_genre_compare_used = True
    s = s.lower()
    s = re.sub(r"[’'`]", "n", s)  # апострофы и кавычки = n
    s = re.sub(r"[^a-zа-я0-9]+", " ", s)  # все не буквы/цифры -> пробел
    s = re.sub(r"\s+", " ", s)  # несколько пробелов -> один
    return s.strip()

def normalize_genre(raw_genre, genre_settings):
    """
    Приводит строку жанра к одному из ключей genre_settings с учётом разных написаний.
    Сравнивает нормализованные строки (см. normalize_for_genre_compare).
    Иначе возвращает 'Other'.
    """
    if not raw_genre:
        print(f"normalize_genre: пустой raw_genre -> 'Other'")
        return "Other"
    raw_norm = normalize_for_genre_compare(raw_genre)
    logger.debug(f"normalize_genre: original genre: '{raw_genre}', normalized: '{raw_norm}'")
    for key in sorted(genre_settings, key=len, reverse=True):
        key_norm = normalize_for_genre_compare(key)
        logger.debug(f"  Сравнение с ключом: '{key}' -> '{key_norm}'")
        if key_norm in raw_norm:
            logger.debug(f"    -> Найдено совпадение по ключу '{key}' (normalized '{key_norm}') для жанра '{raw_genre}' -> '{genre_settings[key]}'")
            return genre_settings[key]
    logger.debug(f"normalize_genre: не найдено совпадение для '{raw_genre}' -> 'Other'")
    return "Other"

def load_genre_settings():
    if os.path.exists(GENRE_SETTINGS_FILE):
        try:
            with open(GENRE_SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings = json.load(f)
                logger.debug("Loaded genre settings: %s", settings)
                print("DEBUG: genre_settings loaded keys:", list(settings.keys()))
                return settings
        except Exception as e:
            logger.error("Error loading genre settings: %s", e)
    return DEFAULT_FOLDER_KEYWORDS.copy()

def save_genre_settings(settings_dict):
    try:
        with open(GENRE_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings_dict, f, indent=4, ensure_ascii=False)
        logger.info("Genre settings saved: %s", settings_dict)
    except Exception as e:
        logger.error("Error saving genre settings: %s", e)

def extract_features(y, sr, librosa_params):
    features = []
    # MFCC
    if librosa_params["features"].get("mfcc", True):
        mfcc = librosa.feature.mfcc(
            y=y,
            sr=sr,
            n_mfcc=librosa_params.get("n_mfcc", 13),
            hop_length=librosa_params.get("hop_length", 512),
            n_fft=librosa_params.get("n_fft", 2048),
            win_length=librosa_params.get("win_length", 2048),
            window=librosa_params.get("window", "hann")
        )
        features.extend(np.mean(mfcc.T, axis=0))

    # Chroma
    if librosa_params["features"].get("chroma", False):
        chroma = librosa.feature.chroma_stft(
            y=y,
            sr=sr,
            hop_length=librosa_params.get("hop_length", 512),
            n_fft=librosa_params.get("n_fft", 2048)
        )
        features.extend(np.mean(chroma, axis=1))

    # Spectral Contrast
    if librosa_params["features"].get("spectral_contrast", False):
        contrast = librosa.feature.spectral_contrast(
            y=y,
            sr=sr,
            hop_length=librosa_params.get("hop_length", 512),
            n_fft=librosa_params.get("n_fft", 2048)
        )
        features.extend(np.mean(contrast, axis=1))

    # Zero Crossing Rate
    if librosa_params["features"].get("zcr", False):
        zcr = librosa.feature.zero_crossing_rate(y)
        features.append(np.mean(zcr))

    # Tonnetz
    if librosa_params["features"].get("tonnetz", False):
        try:
            tonnetz = librosa.feature.tonnetz(y=librosa.effects.harmonic(y), sr=sr)
            features.extend(np.mean(tonnetz, axis=1))
        except Exception as e:
            logger.warning(f"Tonnetz extraction failed: {e}")

    return np.array(features)

def extract_features_from_track(track, audio_features):

    rating = track.get("rating", 0) if track else 0
    try:
        rating = float(rating) / 5.0
    except Exception:
        rating = 0.0
    bpm = track.get("bpm", 0) if track else 0
    try:
        bpm = float(bpm) / 200.0
    except Exception:
        bpm = 0.0
    color = track.get("color", "") if track else ""
    color_idx = COLOR_MAP.get(color, -1)
    situation = track.get("situation", "") if track else ""
    situation_idx = SITUATION_MAP.get(situation, 0)
    feature_vector = np.concatenate([audio_features, [rating, bpm, color_idx, situation_idx]])
    return feature_vector

def get_genre(path, librosa_params=None):
    default_params = {
        "sample_rate": 22050,
        "duration": 30,
        "n_mfcc": 13,
        "hop_length": 512,
        "n_fft": 2048,
        "win_length": 2048,
        "window": "hann",
        "genre_threshold": 0.6,
        "features": {
            "mfcc": True,
            "chroma": False,
            "spectral_contrast": False,
            "zcr": False,
            "tonnetz": False
        }
    }
    params = default_params.copy()
    # Обрабатываем вложенные 'features'
    if librosa_params:
        params.update({k: v for k, v in librosa_params.items() if k in default_params and k != "features"})
        if "features" in librosa_params:
            params["features"].update(librosa_params["features"])

    print(f"[DEBUG] get_genre called for path={path}, librosa_params={librosa_params}")

    # Попытка взять жанр из ID3
    try:
        tags = EasyID3(path)
        genre_from_tags = tags.get("genre", [None])[0]
        print(f"[DEBUG] ID3 genre: {genre_from_tags}")
        if genre_from_tags:
            return genre_from_tags, 1.0
    except Exception as e:
        print(f"[DEBUG] Error reading ID3: {e}")

    # Попытка взять жанр по папке
    folder_name = os.path.basename(os.path.dirname(path))
    genre_settings = load_genre_settings()
    print("DEBUG: genre_settings keys:", list(genre_settings.keys()))
    candidate_genre = normalize_genre(folder_name, genre_settings)
    print(f"[DEBUG] candidate_genre after normalize: {candidate_genre}")
    for key, val in genre_settings.items():
        if key in folder_name:
            candidate_genre = val
            print(f"[DEBUG] candidate_genre found: {candidate_genre}")
            break
    print(f"[DEBUG] candidate_genre after loop: {candidate_genre}")

    try:
        print(f"[DEBUG] Start librosa.load for {path}")
        y, sr = librosa.load(
            path,
            sr=params["sample_rate"],
            duration=params["duration"]
        )
        print(f"[DEBUG] librosa.load OK, sr={sr}, y.shape={y.shape}")

        features = extract_features(y, sr, params)
        print(f"[DEBUG] Features vector shape: {features.shape}")
        features = extract_features_from_track(None, features)

        features = features.reshape(1, -1)
        with open(MODEL_PATH, "rb") as f:
            model = pickle.load(f)
        predicted_genre = model.predict(features)[0]
        print(f"[DEBUG] Model prediction: {predicted_genre}")
        try:
            proba = model.predict_proba(features)[0].max()
            print(f"[DEBUG] Model proba: {proba}")
        except Exception as e:
            print(f"[DEBUG] Error in predict_proba: {e}")
            proba = None
        threshold = params.get("genre_threshold", 0.6)
        if proba is not None and proba < threshold:
            predicted_genre = "Unknown"
        print(f"[DEBUG] Final predicted_genre: {predicted_genre}, proba: {proba}")
    except Exception as e:
        print(f"[DEBUG] Error in audio analysis: {e}")
        predicted_genre = "Unknown"
        proba = None

    print(f"[DEBUG] Returning: predicted_genre={predicted_genre}, candidate_genre={candidate_genre}, proba={proba}")
    if predicted_genre.lower() == "unknown" and candidate_genre:
        return candidate_genre, proba
    return predicted_genre, proba

def scan_library_async(MUSIC_DIR, scan_mode, scan_stop_event, scan_progress):
    if scan_mode == "new":
        init_scan_db()
    total = sum(
        1 for root, dirs, files in os.walk(MUSIC_DIR)
        for file in files if file.lower().endswith(".mp3")
    )
    logger.info("Total mp3 files: %d", total)
    scan_progress["total"] = total
    scan_progress["scanned"] = 0
    results = {}
    processed = 0
    save_interval = 100
    for root, dirs, files in os.walk(MUSIC_DIR):
        if scan_stop_event.is_set():
            scan_progress["status"] = "stopped"
            logger.info("Scanning stopped")
            break
        for file in files:
            if scan_stop_event.is_set():
                scan_progress["status"] = "stopped"
                break
            if not file.lower().endswith(".mp3"):
                continue
            full_path = os.path.join(root, file)
            rel_path = os.path.relpath(full_path, MUSIC_DIR)
            try:
                current_mtime = os.path.getmtime(full_path)
            except Exception as e:
                logger.error("Error getting mtime %s: %s", full_path, e)
                current_mtime = None
            row = load_scan_result(rel_path)
            if row and current_mtime:
                cached_genre, cached_mtime, cached_confidence = row
                if abs(cached_mtime - current_mtime) < 1:
                    genre = cached_genre
                else:
                    genre, conf = get_genre(full_path)
                    save_scan_result(rel_path, genre, current_mtime, conf)
            else:
                genre, conf = get_genre(full_path)
                if current_mtime:
                    save_scan_result(rel_path, genre, current_mtime, conf)
            results.setdefault(genre, []).append(rel_path)
            scan_progress["scanned"] += 1
            processed += 1
            if processed % save_interval == 0:
                logger.info("Processed %d files", processed)
        if scan_stop_event.is_set():
            break
    else:
        scan_progress["status"] = "completed"
    scan_progress["results"] = results
    logger.info("Scanning finished. Results: %s", results)

def train_genre_model(force=False, global_state=None):
    print("=== DEBUG INFO ===")
    print("Current working directory:", os.getcwd())
    print("Running as user:", getpass.getuser())

    def set_progress(x):
        if global_state is not None:
            global_state["training_progress"] = x

    set_progress(0)
    if os.path.exists(MODEL_PATH) and not force:
        logger.info("Model exists; skipping retraining.")
        return

    # Загружаем актуальные настройки librosa из файла
    try:
        from .librosa_settings import load_librosa_settings
        librosa_params = load_librosa_settings()
    except Exception:
        librosa_params = {
            "sample_rate": 22050,
            "duration": 30,
            "n_mfcc": 13,
            "hop_length": 512,
            "n_fft": 2048,
            "win_length": 2048,
            "window": "hann",
            "features": {
                "mfcc": True,
                "chroma": False,
                "spectral_contrast": False,
                "zcr": False,
                "tonnetz": False
            }
        }

    logger.info("librosa_params for training: %s", librosa_params)
    logger.info("Rekordbox use: %s", librosa_params.get("use_rekordbox"))

    # Загружаем твой словарь жанров
    genre_settings = load_genre_settings()

    samples = []
    labels = []
    genre_dirs = {
        "Chillout": "samples/Chillout",
        "Club House": "samples/Club House",
        "Deep House": "samples/Deep House",
        "Drum&Bass": "samples/Drum&Bass",
        "GBass House": "samples/GBass House",
        "Hip-Hop": "samples/Hip-Hop",
        "House": "samples/House",
        "Moombathon": "samples/Moombathon",
        "Nu disco": "samples/Nu disco",
        "Pop": "samples/Pop",
        "Russian House": "samples/Russian House",
        "Trap": "samples/Trap"
    }

    # === Сбор признаков из папок ===
    total_files = 0
    for folder in genre_dirs.values():
        if os.path.exists(folder):
            total_files += sum(1 for file in os.listdir(folder) if file.endswith(".mp3"))
    processed = 0
    for genre, folder in genre_dirs.items():
        if os.path.exists(folder):
            for file in os.listdir(folder):
                if file.endswith(".mp3"):
                    path = os.path.join(folder, file)
                    try:
                        y, sr = librosa.load(
                            path,
                            sr=librosa_params.get("sample_rate", 22050),
                            duration=librosa_params.get("duration", 30)
                        )
                        features = extract_features(y, sr, librosa_params)
                        # Для унификации размерности (с JSON треками) добавляем "заглушки"
                        features = extract_features_from_track(None, features)
                        if features.size == 0:
                            logger.warning(f"No features extracted from {path}, skipping.")
                            continue
                        # Используем только нормализованный жанр (только один!)
                        genre_norm = normalize_genre(genre, genre_settings)
                        samples.append(features)
                        labels.append(genre_norm)
                    except Exception as e:
                        logger.error("Error processing %s: %s", path, e)
                    processed += 1
                    set_progress(int((processed / max(1, total_files)) * 100))

    # === Сбор признаков из Reckordbox (если включено) ===
    if librosa_params.get("use_rekordbox"):
        rk_json = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "reckordbox_parcer_file_output", "parsed_rekordbox.json"))
        logger.info(f"[DEBUG] Проверяем наличие файла Reckordbox: {rk_json}, exists: {os.path.exists(rk_json)}")
        rk_tracks = []
        if os.path.exists(rk_json):
            try:
                rk_tracks = load_rekordbox_json_tracks(rk_json)
                logger.info("ВКЛЮЧЕНА опция использования треков Reckordbox для обучения модели.")
                logger.info(f"Начинаю обработку Reckordbox, всего треков: {len(rk_tracks)}")
            except Exception as e:
                logger.error(f"[Rekordbox] Не удалось загрузить JSON: {e}")
        else:
            logger.warning("Файл Reckordbox JSON не найден, треки Rekordbox не будут добавлены.")

        added = 0
        for track in rk_tracks:
            genre = track["genre"]
            # Только один жанр по словарю!
            genre_norm = normalize_genre(genre, genre_settings)
            path = track["path"]
            try:
                y, sr = librosa.load(
                    path,
                    sr=librosa_params.get("sample_rate", 22050),
                    duration=librosa_params.get("duration", 30)
                )
                base_features = extract_features(y, sr, librosa_params)
                full_features = extract_features_from_track(track, base_features)
                if full_features.size == 0:
                    logger.warning(f"No features extracted from Rekordbox track: {path}")
                    continue
                samples.append(full_features)
                labels.append(genre_norm)
                added += 1
                logger.info(f"Rekordbox track added for train: {path} (genre: {genre_norm})")
            except Exception as e:
                logger.error(f"Error processing Rekordbox track {path}: {e}")
        logger.info(f"Фактически добавлено {added} треков Reckordbox в обучение.")

    # === Блок обучения (когда все samples и labels собраны) ===
    if not samples:
        print("[ОШИБКА] Нет обучающих примеров! Проверьте папки с треками и JSON.")
        return
    try:
        X = np.stack(samples)
    except Exception as e:
        print(f"[ОШИБКА] Ошибка при формировании матрицы признаков: {e}")
        print(f"[DEBUG] Форматы features: {[s.shape for s in samples]}")
        return
    if X.shape[1] == 0:
        print("[ОШИБКА] Нет достаточных признаков для обучения! Измените настройки и попробуйте снова.")
        return
    n_estimators = librosa_params.get("n_estimators", 100)
    clf = RandomForestClassifier(n_estimators=n_estimators, n_jobs=-1)
    clf.fit(X, labels)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(clf, f)
    set_progress(100)
    logger.info("Training completed (100%).")

def get_track_val(track, key):
    return track.get(key) or track.get(key.lower()) or track.get(key.upper()) or ""

def load_rekordbox_json_tracks(json_path="parsed_rekordbox.json"):
    if not os.path.exists(json_path):
        return []
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Пример фильтрации: только с жанром и существующим файлом
    tracks = []
    for track in data:
        genre = (get_track_val(track, "Genre") or "").strip()
        path = get_track_val(track, "path")
        if genre and path and os.path.exists(path):
            # Можно извлекать и другие поля: Color, Rating, BPM, Situation, Artist, Title ...
            tracks.append({
                "path": path,
                "genre": genre,
                "color": get_track_val(track, "Color"),
                "rating": get_track_val(track, "Rating"),
                "bpm": get_track_val(track, "BPM"),
                "artist": get_track_val(track, "Artist"),
                "title": get_track_val(track, "Title") or os.path.splitext(os.path.basename(path))[0],
                "situation": get_track_val(track, "Situation")
            })
    return tracks


