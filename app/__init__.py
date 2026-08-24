"""Flask application factory for Web Music Player.

Imports are intentionally lazy.  Background ML workers import ``app.models``
but must not also initialise Flask routes, VLC and audio devices.
"""

import os


def init_vlc():
    import vlc

    from .config import load_config
    from .logging_config import is_log_type_enabled, setup_vlc_logger
    from .routes import global_state

    vlc_logger = __import__("logging").getLogger("vlc")
    setup_vlc_logger()
    if global_state.get("vlc_instance") is None:
        global_state["vlc_instance"] = vlc.Instance()
        if is_log_type_enabled("vlc"):
            vlc_logger.debug("VLC INSTANCE CREATED in process %s", os.getpid())
    if global_state.get("current_player") is None:
        global_state["current_player"] = global_state["vlc_instance"].media_player_new()
        if is_log_type_enabled("vlc"):
            vlc_logger.debug("VLC PLAYER CREATED in process %s", os.getpid())
    if global_state.get("current_volume") is None:
        global_state["current_volume"] = load_config().get("default_volume", 70)


def create_app():
    import datetime

    from flask import Flask, request, session

    from .catalog_intelligence import init_catalog_intelligence_db
    from .config import Config
    from .db import init_favorite_db
    from .librosa_settings import librosa_settings_bp, librosa_test_bp
    from .routes import last_actions, register_routes
    from .utils import flask_resource_path

    app = Flask(
        __name__,
        template_folder=flask_resource_path("templates"),
        static_folder=flask_resource_path("static"),
    )
    app.secret_key = os.urandom(24)
    app.config.from_object(Config)

    init_favorite_db()
    init_catalog_intelligence_db()
    if os.environ.get("WMP_TRAINING_WORKER") != "1":
        init_vlc()

    register_routes(app)
    app.register_blueprint(librosa_settings_bp)
    app.register_blueprint(librosa_test_bp)

    @app.before_request
    def log_action():
        if not app.config.get("LOG_ACTIONS", False):
            return
        ignored = ("/diag_state", "/static/", "/favicon.ico")
        if any(request.path.startswith(value) for value in ignored):
            return
        last_actions.appendleft({
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "route": request.path,
            "owner_sid": session.get("owner_sid"),
            "request_ip": request.remote_addr,
            "args": dict(request.args),
            "method": request.method,
        })

    return app

