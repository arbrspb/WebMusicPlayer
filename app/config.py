# app/config.py 14-08-25 01-50
"""Модуль конфигурации для WebMusicPlayer: загрузка и сохранение настроек приложения."""
import os
import json
import logging
import posixpath
from .paths import CONFIG_FILE as CONFIG_PATH

logger = logging.getLogger(__name__) # Логирование

CONFIG_FILE = str(CONFIG_PATH)

DEFAULT_INTELLIGENCE_PREFERENCES = {
    "weights": {
        "deep": 40.0,
        "acoustic": 25.0,
        "character": 15.0,
        "semantic": 5.0,
        "bpm": 5.0,
        "personal": 10.0,
    },
    "result_limit": 20,
    "exclude_versions": True,
}

DEFAULT_MODEL_PIPELINE = {
    # Every optional stage is independent.  A fresh installation remains
    # useful on CPU and without third-party DJ software.
    "effnet_enabled": False,
    "effnet_device": "auto",
    "effnet_segment_offsets": [30.0, 60.0, 90.0],
    "effnet_segment_duration": 2.2,
    "effnet_preprocess_workers": 0,
    # Optional second genre head.  It is trained only when EffNet is enabled
    # and is rejected automatically if held-out quality becomes worse.
    "effnet_genre_fusion_enabled": True,
    "effnet_genre_fusion_alpha": 0.35,
    "effnet_genre_pca_dimensions": 48,
    "effnet_genre_min_coverage": 0.50,
    "effnet_genre_min_macro_f1": 0.65,
    "rekordbox_enabled": False,
    "player_ratings_enabled": True,
}

DEFAULT_CONFIG = {
    # A fresh clone must not reference the developer's NAS. The user's real
    # local/UNC path is persisted only in the ignored config.json.
    "music_dir": os.path.join(os.path.expanduser("~"), "Music"),
    "playback_mode": "host",    # Режим воспроизведения: "host" – VLC, "plyr" – Plyr.js
    "default_volume": 100,      # Громкость по умолчанию
    # Обычный запуск всегда безопасно дополняет индекс. Полный reset выполняется
    # только разовой явно подтверждённой командой из расширенного UI.
    "scan_mode": "continue",
    "favorite_mode": "stay",    # "stay" – оставаться в текущем плейлисте, "switch" – переходить к каталогу тре[...]
    "remember_recent_folders": False,  # Запоминать открытые папки музыкального браузера
    "recent_folders": [],        # Последние открытые относительные пути (новые первыми)
    "autoplay_mode": "off",     # "playlist" - продолжать, "off" - останавливать
    "advanced_mode": False,     # ВКЛ/ВЫКЛ расширенные функции: жанры, база, сканирование
    "force_model_for_recommend": False,  # Если True: при рекомендациях генерируем live признаки (и fused_proba если YAMNet включён)
    "intelligence_preferences": DEFAULT_INTELLIGENCE_PREFERENCES,
    "model_pipeline": DEFAULT_MODEL_PIPELINE,
    "scan_priority": "medium",  # Приоритет сканирования для процессора и озу: "high" "medium"  "low"
    "log_flags": {              # Флаги для логирования действий пользователя
            "status": False,
            "owner": False,
            "owner_status": False,
            "vlc": False,
            "audio_diag": False,
            "player": False,
            "model": False,
            "numba": False,
            "scan": False,
            "resource": False
        }
}

MAX_RECENT_FOLDERS = 8


def normalize_recent_folders(values, limit=MAX_RECENT_FOLDERS):
    """Return safe, unique, relative browser folders in most-recent-first order."""
    result = []
    seen = set()
    for raw_value in values or []:
        value = str(raw_value or "").strip().replace("\\", "/")
        value = posixpath.normpath(value).strip("/")
        if value in {"", "."} or value == ".." or value.startswith("../"):
            continue
        if ":" in value.split("/", 1)[0]:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
        if len(result) >= max(1, int(limit)):
            break
    return result


def add_recent_folder(values, folder, limit=MAX_RECENT_FOLDERS):
    """Place a folder at the beginning of a normalized recent-folder list."""
    return normalize_recent_folders([folder, *(values or [])], limit=limit)


def get_intelligence_preferences(config=None):
    """Return a complete, validated set of persistent catalog preferences."""
    config = config or load_config()
    stored = config.get("intelligence_preferences", {}) or {}
    stored_weights = stored.get("weights", {}) or {}
    weights = {}
    for key, default_value in DEFAULT_INTELLIGENCE_PREFERENCES["weights"].items():
        try:
            weights[key] = max(0.0, float(stored_weights.get(key, default_value)))
        except (TypeError, ValueError):
            weights[key] = float(default_value)
    if sum(weights.values()) <= 0:
        weights = dict(DEFAULT_INTELLIGENCE_PREFERENCES["weights"])
    try:
        result_limit = max(1, min(int(stored.get("result_limit", 20)), 100))
    except (TypeError, ValueError):
        result_limit = 20
    return {
        "weights": weights,
        "result_limit": result_limit,
        "exclude_versions": bool(stored.get("exclude_versions", True)),
    }


def get_model_pipeline_settings(config=None):
    """Return validated optional model and personalization settings."""
    config = config or load_config()
    stored = config.get("model_pipeline", {}) or {}
    result = dict(DEFAULT_MODEL_PIPELINE)
    result.update(stored)

    device = str(result.get("effnet_device", "auto")).strip().lower()
    result["effnet_device"] = device if device in {"auto", "cpu", "cuda"} else "auto"
    try:
        workers = int(result.get("effnet_preprocess_workers", 0))
    except (TypeError, ValueError):
        workers = 0
    result["effnet_preprocess_workers"] = max(0, min(workers, 8))
    try:
        duration = float(result.get("effnet_segment_duration", 2.2))
    except (TypeError, ValueError):
        duration = 2.2
    result["effnet_segment_duration"] = max(2.05, min(duration, 8.0))

    offsets = result.get("effnet_segment_offsets", [30.0, 60.0, 90.0])
    if isinstance(offsets, str):
        offsets = offsets.split(",")
    normalized_offsets = []
    for value in offsets or []:
        try:
            offset = max(0.0, float(value))
        except (TypeError, ValueError):
            continue
        if offset not in normalized_offsets:
            normalized_offsets.append(offset)
        if len(normalized_offsets) >= 5:
            break
    result["effnet_segment_offsets"] = normalized_offsets or [30.0, 60.0, 90.0]
    for key in (
            "effnet_enabled", "effnet_genre_fusion_enabled",
            "rekordbox_enabled", "player_ratings_enabled",
    ):
        result[key] = bool(result.get(key, DEFAULT_MODEL_PIPELINE[key]))
    try:
        result["effnet_genre_fusion_alpha"] = min(max(
            float(result.get("effnet_genre_fusion_alpha", 0.35)), 0.10
        ), 0.55)
    except (TypeError, ValueError):
        result["effnet_genre_fusion_alpha"] = 0.35
    try:
        result["effnet_genre_pca_dimensions"] = min(max(
            int(result.get("effnet_genre_pca_dimensions", 48)), 16
        ), 96)
    except (TypeError, ValueError):
        result["effnet_genre_pca_dimensions"] = 48
    try:
        result["effnet_genre_min_coverage"] = min(max(
            float(result.get("effnet_genre_min_coverage", 0.50)), 0.20
        ), 1.0)
    except (TypeError, ValueError):
        result["effnet_genre_min_coverage"] = 0.50
    try:
        result["effnet_genre_min_macro_f1"] = min(max(
            float(result.get("effnet_genre_min_macro_f1", 0.65)), 0.30
        ), 0.95)
    except (TypeError, ValueError):
        result["effnet_genre_min_macro_f1"] = 0.65
    return result

def load_config():
    """
       Загружает конфигурацию приложения из файла CONFIG_FILE (обычно 'config.json').
       Если файл отсутствует, используется DEFAULT_CONFIG.
       Возвращает словарь с объединёнными значениями из DEFAULT_CONFIG и config.json,
       при этом log_flags также объединяются (чтобы не терять новые типы логов при обновлении версии).
       """
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config_data = json.load(f)
            merged_config = DEFAULT_CONFIG.copy()
            merged_config.update(config_data)
            # merge log_flags dict too
            merged_config["log_flags"] = {**DEFAULT_CONFIG["log_flags"], **config_data.get("log_flags", {})}
            return merged_config
    return DEFAULT_CONFIG.copy()

def save_config(conf):
    """
    Сохраняет переданный словарь конфигурации в файл CONFIG_FILE.
    Использует pretty-форматирование (indent=4, ensure_ascii=False).
    Логирует путь и содержимое сохраняемой конфигурации.
    """
    full_path = os.path.abspath(CONFIG_FILE)
    logger.info("Saving config to: %s", full_path)
    logger.info("Configuration data: %s", conf)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(conf, f, indent=4, ensure_ascii=False)
    # Переключатели логов должны применяться сразу, несмотря на быстрый кэш
    # чтения config.json в logging_config.
    try:
        from .logging_config import invalidate_log_settings_cache
        invalidate_log_settings_cache()
    except ImportError:
        pass

def get_log_flags():
    """
    Возвращает словарь с текущими флагами логирования из конфигурации (log_flags),
    всегда дополняя его всеми ключами из DEFAULT_CONFIG["log_flags"].
    """
    flags = load_config().get("log_flags", {}).copy()
    # Подмешиваем все новые ключи из DEFAULT_CONFIG, если их нет
    for key, default_value in DEFAULT_CONFIG["log_flags"].items():
        if key not in flags:
            flags[key] = default_value
    return flags

def get_advanced_mode():
    """
    Получает состояние расширенного режима (advanced_mode) из конфигурации.
    Используется для управления расширенными возможностями приложения (жанры, ML, сканирование).
    """
    return load_config().get("advanced_mode", True)

def save_log_flags(new_flags):
    """
    Обновляет только секцию log_flags в конфигурации и сохраняет изменения в файл.
    Не изменяет остальные параметры пользователя.
    """
    config = load_config()
    config["log_flags"] = new_flags
    save_config(config)

class Config:
    """
    Класс-конфигурация, параметры которого загружаются из файла.
    Если файл отсутствует, используются значения по умолчанию из DEFAULT_CONFIG.

    Атрибуты класса:
    MUSIC_DIR (str): Путь к музыкальной библиотеке.
    PLAYBACK_MODE (str): Режим воспроизведения ("host" или "plyr").
    DEFAULT_VOLUME (int): Громкость по умолчанию.
    SCAN_MODE (str): Режим сканирования библиотеки.
    FAVORITE_MODE (str): Режим работы с избранными треками.
    AUTOPLAY_MODE (str): Режим автоплея после окончания трека.
    LOG_ACTIONS (bool): Включено ли логирование действий пользователя (на /diag_state).
    ADVANCED_MODE (bool): Включен ли расширенный режим (жанры, ML, сканирование).
    FORCE_MODEL_FOR_RECOMMEND (bool): Принудительное использование ML-модели для рекомендаций.
    """
    _config = load_config()
    MUSIC_DIR = _config.get("music_dir", DEFAULT_CONFIG["music_dir"])
    PLAYBACK_MODE = _config.get("playback_mode", DEFAULT_CONFIG["playback_mode"])
    DEFAULT_VOLUME = _config.get("default_volume", DEFAULT_CONFIG["default_volume"])
    SCAN_MODE = _config.get("scan_mode", DEFAULT_CONFIG["scan_mode"])
    FAVORITE_MODE = _config.get("favorite_mode", DEFAULT_CONFIG["favorite_mode"])
    AUTOPLAY_MODE = _config.get("autoplay_mode", DEFAULT_CONFIG["autoplay_mode"]) # <--- ДОБАВЛЕНО
    LOG_ACTIONS = True # Включить или отключить(False/True) логирование действий пользователя на http://127.0.0.1:8080/diag_state
    ADVANCED_MODE = _config.get("advanced_mode", DEFAULT_CONFIG["advanced_mode"])
    FORCE_MODEL_FOR_RECOMMEND = _config.get("force_model_for_recommend", DEFAULT_CONFIG["force_model_for_recommend"])
