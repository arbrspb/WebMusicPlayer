# app/models.py 15-05-25 18-25
import os
import threading
import json
import pickle
import numpy as np
import librosa
import logging
from mutagen.easyid3 import EasyID3
from sklearn.ensemble import RandomForestClassifier
from .db import init_scan_db, load_scan_result, save_scan_result
from .config import DEFAULT_CONFIG

global_state = None

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
    folder_name = os.path.basename(os.path.dirname(path)).lower()
    genre_settings = load_genre_settings()
    candidate_genre = None
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

        features = []

        # MFCC
        if params["features"].get("mfcc", True):
            mfcc = librosa.feature.mfcc(
                y=y,
                sr=sr,
                n_mfcc=params["n_mfcc"],
                hop_length=params["hop_length"],
                n_fft=params["n_fft"],
                win_length=params["win_length"],
                window=params["window"]
            )
            print(f"[DEBUG] MFCC shape: {mfcc.shape}")
            features.extend(np.mean(mfcc.T, axis=0))

        # Chroma
        if params["features"].get("chroma", False):
            chroma = librosa.feature.chroma_stft(
                y=y,
                sr=sr,
                hop_length=params["hop_length"],
                n_fft=params["n_fft"]
            )
            print(f"[DEBUG] Chroma shape: {chroma.shape}")
            features.extend(np.mean(chroma, axis=1))

        # Spectral Contrast
        if params["features"].get("spectral_contrast", False):
            contrast = librosa.feature.spectral_contrast(
                y=y,
                sr=sr,
                hop_length=params["hop_length"],
                n_fft=params["n_fft"]
            )
            print(f"[DEBUG] Spectral Contrast shape: {contrast.shape}")
            features.extend(np.mean(contrast, axis=1))

        # Zero Crossing Rate
        if params["features"].get("zcr", False):
            zcr = librosa.feature.zero_crossing_rate(y)
            print(f"[DEBUG] ZCR shape: {zcr.shape}")
            features.append(np.mean(zcr))

        # Tonnetz
        if params["features"].get("tonnetz", False):
            try:
                tonnetz = librosa.feature.tonnetz(y=librosa.effects.harmonic(y), sr=sr)
                print(f"[DEBUG] Tonnetz shape: {tonnetz.shape}")
                features.extend(np.mean(tonnetz, axis=1))
            except Exception as e:
                print(f"[DEBUG] Tonnetz extraction failed: {e}")

        features = np.array(features).reshape(1, -1)
        print(f"[DEBUG] Features vector shape: {features.shape}")

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
    def set_progress(x):
        if global_state is not None:
            global_state["training_progress"] = x

    set_progress(0)
    if os.path.exists(MODEL_PATH) and not force:
        logger.info("Model exists; skipping retraining.")
        return

    # Загружаем актуальные настройки librosa из файла (как и get_genre)
    try:
        from .librosa_settings import load_librosa_settings
        librosa_params = load_librosa_settings()
    except Exception:
        # fallback если импорт не работает (например, при тестах)
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
                        y, sr = librosa.load(
                            path,
                            sr=librosa_params.get("sample_rate", 22050),
                            duration=librosa_params.get("duration", 30)
                        )
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

                        features = np.array(features)
                        if features.size == 0:
                            logger.warning(f"No features extracted from {path}, skipping.")
                            continue
                        samples.append(features)
                        labels.append(genre)
                    except Exception as e:
                        logger.error("Error processing %s: %s", path, e)
                    processed += 1
                    set_progress(int((processed / total_files) * 100))

    # === Весь блок обучения — СЮДА, после всех циклов ===
    if not samples:
        print("[ОШИБКА] Нет обучающих примеров! Проверьте папки с треками.")
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
    from sklearn.ensemble import RandomForestClassifier
    n_estimators = librosa_params.get("n_estimators", 100)
    clf = RandomForestClassifier(n_estimators=n_estimators)
    clf.fit(X, labels)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump(clf, f)
    set_progress(100)
    logger.info("Training completed (100%).")
