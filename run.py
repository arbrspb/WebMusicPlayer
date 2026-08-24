# run.py 14-08-25 01-50-25 12-00
"""Точка входа для запуска Flask-приложения Web Music Player."""
# === Начало БЛОКА логирования ===
import os
# === БЛОК: Включение/отключение дампов и логирования Numba ===
# Этот блок гарантирует, что переменные окружения для Numba
# (отвечающие за дампы байткода и отладочный вывод) будут очищены
# или установлены до любых импортов ML/Numba/librosa.
# Управляется через LOG_FLAGS["numba"] в app/logging_config.py.

def configure_numba_env(numba_enabled):
    numba_env_vars = [
        "NUMBA_DEBUG",
        "NUMBA_DUMP_BYTECODE",
        "NUMBA_DUMP_CFG",
        "NUMBA_DUMP_IR"
    ]
    if not numba_enabled:
        for var in numba_env_vars:
            os.environ.pop(var, None)
    # else: можно добавить активацию переменных для отладки

# --- Управление дампами Numba через глобальный флаг ---
from app.logging_config import LOG_FLAGS
from app.config import load_config
configure_numba_env(LOG_FLAGS.get("numba", False))
# === КОНЕЦ БЛОКА Numba ===

# Теперь конфигурируем логирование
from app.logging_config import setup_logging, setup_numba_logger
setup_logging(load_config().get("debug_enabled", False))
setup_numba_logger()         # <- потом suppression Numba logger
import logging
from app import create_app
from app.server_runtime import serve_application
logger = logging.getLogger(__name__) # Логирование
# === КОНЕЦ БЛОКА логирования ===

app = create_app()

def main():
    """Запускает Flask-приложение."""
    serve_application(
        app,
        host="0.0.0.0",
        port=8080,
        debug_enabled=load_config().get("debug_enabled", False),
    )

if __name__ == '__main__':
    main()
