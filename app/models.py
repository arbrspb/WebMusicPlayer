# app/models.py 11-05-25
import os
import threading
import json
import pickle
import numpy as np
import librosa
import logging
from mutagen.easyid3 import EasyID3
from .db import init_scan_db, load_scan_result, save_scan_result
from .config import DEFAULT_CONFIG

logger = logging.getLogger(__name__) # Логирование

MODEL_PATH = "genre_model.pkl"

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


def load_genre_settings():
    if os.path.exists(GENRE_SETTINGS_FILE):
        try:
            with open(GENRE_SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings = json.load(f)
                logger.debug("Loaded genre settings: %s", settings)
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


def get_genre(path):
    # Попытка получить жанр из ID3-тегов
    try:
        tags = EasyID3(path)
        genre_from_tags = tags.get("genre", [None])[0]
        if genre_from_tags:
            logger.debug("Genre (ID3) for %s: %s", path, genre_from_tags)
            return genre_from_tags, 1.0
    except Exception as e:
        logger.debug("Error reading ID3 for %s: %s", path, e)

    # Используем имя родительской папки по настройкам жанра
    folder_name = os.path.basename(os.path.dirname(path)).lower()
    genre_settings = load_genre_settings()
    candidate_genre = None
    for key, val in genre_settings.items():
        if key in folder_name:
            candidate_genre = val
            logger.debug("Candidate genre for %s: %s", path, candidate_genre)
            break
    # Аудио-анализ
    try:
        y, sr = librosa.load(path, duration=30)
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
        features = np.mean(mfcc.T, axis=0).reshape(1, -1)
        with open(MODEL_PATH, "rb") as f:
            model = pickle.load(f)
        predicted_genre = model.predict(features)[0]
        try:
            proba = model.predict_proba(features)[0].max()
        except Exception:
            proba = None
        if proba is not None and proba < 0.6:
            predicted_genre = "Unknown"
        logger.debug("Audio analysis for %s: genre=%s", path, predicted_genre)
    except Exception as e:
        logger.error("Error in audio analysis for %s: %s", path, e)
        predicted_genre = "Unknown"
        proba = None

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


def train_genre_model(force=False):
    global training_progress
    training_progress = 0
    if os.path.exists(MODEL_PATH) and not force:
        logger.info("Model exists; skipping retraining.")
        return
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
                        y, sr = librosa.load(path, duration=30)
                        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
                        features = np.mean(mfcc.T, axis=0)
                        samples.append(features)
                        labels.append(genre)
                    except Exception as e:
                        logger.error("Error processing %s: %s", path, e)
                    processed += 1
                    training_progress = int((processed / total_files) * 100)
    if samples:
        from sklearn.ensemble import RandomForestClassifier
        clf = RandomForestClassifier(500)
        clf.fit(samples, labels)
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(clf, f)
        training_progress = 100
        logger.info("Training completed (100%).")
