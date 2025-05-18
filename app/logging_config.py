#logging.config.py
import logging

def setup_logging(debug_enabled=False, log_file="debug.log"):
    log_level = logging.DEBUG if debug_enabled else logging.INFO
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s", "%H:%M:%S"))
    root_logger.addHandler(console_handler)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(log_level)
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s", "%H:%M:%S"))
        root_logger.addHandler(file_handler)
    if not debug_enabled:
        logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.info("=== Логирование инициализировано, уровень: %s ===", logging.getLevelName(log_level))