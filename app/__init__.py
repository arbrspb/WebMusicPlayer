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

    # Маршрут для librosa settings.py
    from flask import request, send_from_directory
    import urllib.parse

    @app.route('/musicfile')
    def musicfile():
        path = request.args.get("path")
        if not path:
            return "No path", 404
        # Декодируем из URL
        abs_path = os.path.abspath(urllib.parse.unquote(path))
        # Для Windows: заменяем / на \
        if os.name == "nt":
            abs_path = abs_path.replace("/", "\\")
        # Безопасность: только внутри Desktop!
        allowed_root = os.path.abspath(r"d:\WinUsers\ARTUR\Desktop")
        if not abs_path.lower().startswith(allowed_root.lower()):
            return "Forbidden", 403
        if not os.path.exists(abs_path):
            return "File not found", 404
        directory, filename = os.path.split(abs_path)
        return send_from_directory(directory, filename, as_attachment=False)
    return app



