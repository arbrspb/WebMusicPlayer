 # app/logging.config.py 14-08-25 01-50
# Вариант 1: Централизованное логирование ошибок
#
# Если функция-воркер multiprocessing возвращает ошибку как результат,
# логировать эту ошибку нужно только в главном процессе,
# а не внутри worker-функции.
# В самой worker-функции ошибки не логируются — они возвращаются, а логируются централизованно после сбора результатов.
#
# Пример формулировки для инструкции:
#
# Если используется multiprocessing, ошибки из worker-функций возвращать как результат (например, в поле error), а логировать их только в главном процессе после получения результата. Внутри worker-функций никаких логов ошибок не писать — только возврат значения.
#
# Можно вставить это правило в раздел "Требования к логированию" или отдельный блок "Логирование в multiprocessing".
"""Конфигурация логирования для приложения WebMusicPlayer."""
import logging
import threading
import time
import traceback
from .paths import DEBUG_LOG_FILE

# Включение/отключение каждого типа диагностики по отдельности

LOG_FLAGS = {
    "status": False,       # Диагностика состояния приложения, системные операции, получение/обработка директорий и файлов, конфигов, внутренних API. Для отладки маршрутов, проверки путей, построения дерева директорий, сканирование библиотеки и пр.
    "owner": False,        # Действия, связанные с пользователем-владельцем: смена владельца, проверка прав, отказ в доступе и т.д.
    "owner_status": False, # Диагностика и статусы, связанные с владельцем (например, смена статуса владельца в системе).
    "vlc": False,          # Все, что связано с управлением VLC: запуск, диагностика, ошибки, взаимодействие с плеером VLC.
    "audio_diag": False,   # Диагностика аудио-устройств, потоков, вывода, аудиосистемы. Используй для отладки взаимодействия со звуковыми устройствами.
    "player": False,       # Управление плеером: действия типа пауза, воспроизведение, перемотка, изменение громкости и т.д.
    "model": False,        # Аудиоанализ и ML: логирование при работе и обучении моделей, анализе треков, предсказаниях жанра, диагностике ML-процессов.
    "numba": False,        # Логирование Numba для внешнего JIT анализатора (для модели).
    "resource": True,      # Логирование ошибок, связанных с ресурсами (например, MemoryError, CPU overload).
}

_LOG_SETTINGS_CACHE = None
_LOG_SETTINGS_CACHE_AT = 0.0
_LOG_SETTINGS_CACHE_TTL_SECONDS = 1.0
_LOG_SETTINGS_CACHE_LOCK = threading.Lock()


def invalidate_log_settings_cache():
    """Сбрасывает кэш переключателей логирования после сохранения настроек."""
    global _LOG_SETTINGS_CACHE, _LOG_SETTINGS_CACHE_AT
    with _LOG_SETTINGS_CACHE_LOCK:
        _LOG_SETTINGS_CACHE = None
        _LOG_SETTINGS_CACHE_AT = 0.0

def is_log_type_enabled(log_type):
    """Проверяет флаг лога, не перечитывая config.json на каждой строке лога."""
    global _LOG_SETTINGS_CACHE, _LOG_SETTINGS_CACHE_AT
    now = time.monotonic()
    cached = _LOG_SETTINGS_CACHE
    if cached is None or now - _LOG_SETTINGS_CACHE_AT >= _LOG_SETTINGS_CACHE_TTL_SECONDS:
        with _LOG_SETTINGS_CACHE_LOCK:
            now = time.monotonic()
            if (
                _LOG_SETTINGS_CACHE is None
                or now - _LOG_SETTINGS_CACHE_AT >= _LOG_SETTINGS_CACHE_TTL_SECONDS
            ):
                from .config import load_config
                config = load_config()
                _LOG_SETTINGS_CACHE = {
                    "debug_enabled": bool(config.get("debug_enabled", False)),
                    "log_flags": dict(config.get("log_flags", {})),
                }
                _LOG_SETTINGS_CACHE_AT = now
            cached = _LOG_SETTINGS_CACHE
    if not cached.get("debug_enabled", False):
        return False
    flags = cached.get("log_flags", {})
    return flags.get(log_type, False)

# Логер статуса
def setup_status_logger():
    logger = logging.getLogger("status")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if not logger.hasHandlers():
        formatter = logging.Formatter("%(asctime)s STATUS: %(message)s", "%H:%M:%S")
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        file_handler = logging.FileHandler(DEBUG_LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

# Логер владельца
def setup_owner_logger():
    """
    Настраивает отдельный логгер для OWNER (имя: 'owner').
    """
    logger = logging.getLogger("owner")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if not logger.hasHandlers():
        formatter = logging.Formatter("%(asctime)s OWNER: %(message)s", "%H:%M:%S")
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        file_handler = logging.FileHandler(DEBUG_LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

# Логер статуса владельца
def setup_owner_status_logger():
    """
    Настраивает отдельный логгер для OWNER STATUS (имя: 'owner_status').
    """
    logger = logging.getLogger("owner_status")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if not logger.hasHandlers():
        formatter = logging.Formatter("%(asctime)s OWNER STATUS: %(message)s", "%H:%M:%S")
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        file_handler = logging.FileHandler(DEBUG_LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

# Логер VLC
def setup_vlc_logger():
    """
    Настраивает отдельный логгер для VLC (имя: 'vlc').
    """
    logger = logging.getLogger("vlc")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if not logger.hasHandlers():
        formatter = logging.Formatter("%(asctime)s VLC: %(message)s", "%H:%M:%S")
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        file_handler = logging.FileHandler(DEBUG_LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

# Логер диагностики аудио
def setup_audio_diag_logger():
    """
    Настраивает отдельный логгер для AUDIO DIAG (имя: 'audio_diag').
    """
    logger = logging.getLogger("audio_diag")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if not logger.hasHandlers():
        formatter = logging.Formatter("%(asctime)s AUDIO DIAG: %(message)s", "%H:%M:%S")
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        file_handler = logging.FileHandler(DEBUG_LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

# Логер пауза,перемотка,громкость,плей
def setup_player_logger():
    logger = logging.getLogger("player")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if not logger.hasHandlers():
        formatter = logging.Formatter("%(asctime)s PLAYER: %(message)s", "%H:%M:%S")
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        file_handler = logging.FileHandler(DEBUG_LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

# Логер модели(аудиоанализ)
def setup_model_logger():
    """
    Настраивает отдельный логгер для model (имя: 'model').
    """
    logger = logging.getLogger("model")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if not logger.hasHandlers():
        # formatter = logging.Formatter("%(asctime)s MODEL: %(message)s", "%H:%M:%S") было так
        formatter = logging.Formatter("%(asctime)s MODEL: %(levelname)s %(message)s", "%H:%M:%S")
    #
    #formatter = logging.Formatter("%(asctime)s MODEL: %(levelname)s %(message)s", "%H:%M:%S")
    #позволяет использовать уровень логирования debug warning info error info: примеры
    #if is_log_type_enabled("model"):
    #model_logger.debug("[DEBUG] Это отладочная информация по обучению модели")
    #model_logger.info("[INFO] Начало обучения модели")
    #model_logger.warning("[WARNING] Возможно дублирование ключа признаков!")
    #model_logger.error("[ERROR] Ошибка при обучении модели!")
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        file_handler = logging.FileHandler(DEBUG_LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

def get_model_logger():
    """
    Возвращает логгер 'model', гарантированно настроенный.
    """
    logger = logging.getLogger("model")
    if not logger.hasHandlers():
        setup_model_logger()
    return logger

# Логирование ошибок памяти
def log_memory_error(e, context="", track_path=None):
    """
    Универсальное логирование ошибок, связанных с памятью.
    Использует логгер "model".
    """
    logger = get_model_logger()
    msg = f"[MEMORY ERROR] {type(e).__name__}: {e}"
    if track_path:
        msg += f" | Track: {track_path}"
    if context:
        msg += f" | Context: {context}"
    stack = traceback.format_exc()
    if is_log_type_enabled("model"):
        logger.error(msg)
        logger.error(f"[MEMORY ERROR] Stacktrace:\n{stack}")

# Логер ресурсов
def setup_resource_logger():
    """
    Настраивает отдельный логгер для ресурсов (имя: 'resource').
    Используется для MemoryError, превышения лимита RAM, CPU overload и пр.
    """
    logger = logging.getLogger("resource")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    if not logger.hasHandlers():
        formatter = logging.Formatter("%(asctime)s RESOURCE: %(message)s", "%H:%M:%S")
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        file_handler = logging.FileHandler(DEBUG_LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

def get_resource_logger():
    """
    Возвращает логгер 'resource', гарантированно настроенный.
    """
    logger = logging.getLogger("resource")
    if not logger.hasHandlers():
        setup_resource_logger()
    return logger

# Логер Numba внешнего JIT анализатора
def setup_numba_logger():
    """
    Управляет логированием для Numba (внешний JIT анализатор, genre ML).
    """
    level = logging.DEBUG if LOG_FLAGS.get("numba") else logging.WARNING
    for logger_name in [
        "numba",
        "numba.core",
        "numba.core.byteflow",
        "numba.core.bytecode",
        "numba.core.interpreter",
        "numba.core.ssa",
        "numba.core.compiler",
        "numba.core.dispatcher",
        "numba.experimental",
    ]:
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
        if LOG_FLAGS.get("numba"):
            # Если включён — убедись, что хотя бы 1 handler есть
            logger.propagate = True  # или False, если handler есть!
            if not logger.hasHandlers():
                handler = logging.StreamHandler()  # можно добавить FileHandler по желанию
                handler.setFormatter(logging.Formatter("%(asctime)s NUMBA: %(message)s", "%H:%M:%S"))
                logger.addHandler(handler)
        else:
            logger.propagate = False
            while logger.handlers:
                logger.handlers.pop()

# Общее логирование
def setup_logging(debug_enabled=False, log_file=None):
    """Настраивает логирование приложения.
    Если debug_enabled=True, уровень DEBUG.
    Логи выводятся на консоль и в файл."""
    log_level = logging.DEBUG if debug_enabled else logging.INFO
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s: %(message)s", "%H:%M:%S")
    )
    root_logger.addHandler(console_handler)
    log_file = log_file or str(DEBUG_LOG_FILE)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(log_level)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s: %(message)s", "%H:%M:%S")
        )
        root_logger.addHandler(file_handler)
    if not debug_enabled:
        logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.info("=== Логирование инициализировано, уровень: %s ===", logging.getLevelName(log_level))
