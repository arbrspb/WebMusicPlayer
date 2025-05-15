# app/config.py 15-05-25 18-25
import os
import json
import logging

logger = logging.getLogger(__name__) # Логирование

CONFIG_FILE = "config.json"

# Допустимые варианты режима воспроизведения:
# "host"  – воспроизведение через VLC (на компьютере-хосте)
# "plyr"  – воспроизведение с помощью Plyr.js (новый режим)
DEFAULT_CONFIG = {
    "music_dir": r"\\192.168.1.120\Music",
    "playback_mode": "host",    # Можно поменять на "plyr", если хотите использовать новый режим
    "default_volume": 100,
    "scan_mode": "new",         # "new" – начать заново, "continue" – дополнить новые записи
    "favorite_mode": "stay"     # Новая настройка: "stay" – оставаться в текущем плейлисте, "switch" – переходить к каталогу трека
}

def load_config():
    """
    Загружает конфигурацию из файла CONFIG_FILE.
    Если файла нет или какое-либо поле отсутствует – возвращается объединённый словарь со значениями по умолчанию.
    """
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config_data = json.load(f)
            merged_config = DEFAULT_CONFIG.copy()
            merged_config.update(config_data)
            return merged_config
    return DEFAULT_CONFIG.copy()

def save_config(conf):
    full_path = os.path.abspath(CONFIG_FILE)
    # Временно для диагностики:
    print("Saving config to:", full_path)
    print("Configuration data:", conf)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(conf, f, indent=4, ensure_ascii=False)

class Config:
    """
    Класс-конфигурация, параметры которого загружаются из файла.
    Если файл отсутствует, используются значения по умолчанию из DEFAULT_CONFIG.
    """
    _config = load_config()
    MUSIC_DIR = _config.get("music_dir", DEFAULT_CONFIG["music_dir"])
    PLAYBACK_MODE = _config.get("playback_mode", DEFAULT_CONFIG["playback_mode"])
    DEFAULT_VOLUME = _config.get("default_volume", DEFAULT_CONFIG["default_volume"])
    SCAN_MODE = _config.get("scan_mode", DEFAULT_CONFIG["scan_mode"])
    FAVORITE_MODE = _config.get("favorite_mode", DEFAULT_CONFIG["favorite_mode"])
