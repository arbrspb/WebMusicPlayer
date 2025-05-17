# app/__init__.py 15-05-25 18-25
import os
from flask import Flask
from .utils import resource_path
from .config import Config
from .routes import register_routes
from .db import init_favorite_db
from .librosa_settings import librosa_settings_bp, librosa_test_bp  # для настроек librosa
import logging

logger = logging.getLogger(__name__) # Логирование

# Если файл plyr_mode.py удалён, удалите или закомментируйте следующие строки:
# from .plyr_mode import plyr_bp

def create_app():
    app = Flask(__name__,
                template_folder=resource_path("templates"),
                static_folder=resource_path("static"))
    app.secret_key = os.urandom(24)
    app.config.from_object(Config)

    # Инициализируем БД избранного (при необходимости, также можно инициализировать другие БД)
    init_favorite_db()

    # Регистрируем маршруты, передавая объект приложения
    register_routes(app)

    # Если ранее был зарегистрирован blueprint для plyr_mode, его нужно удалить, если файла нет.
    # app.register_blueprint(plyr_bp)

    # Регистрируем блюпринт для настроек и тестовой страницы трека librosa
    app.register_blueprint(librosa_settings_bp)
    app.register_blueprint(librosa_test_bp)

    return app


