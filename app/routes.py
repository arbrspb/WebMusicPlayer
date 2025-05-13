# app/routes.py 11-05-25
import os
import sqlite3
import threading
import time
import vlc
import logging
from flask import request, redirect, url_for, jsonify, send_file, render_template, session
from urllib.parse import unquote_plus
from .config import DEFAULT_CONFIG, load_config, save_config
from .db import init_scan_db, init_favorite_db, FAVORITE_DB
from .models import get_genre, scan_library_async, train_genre_model, load_genre_settings, save_genre_settings
import sounddevice as sd
from html import unescape
from .utils import get_track_title


logger = logging.getLogger(__name__) # Логирование

def get_scanned_genre(rel_path):
    from .db import load_scan_result
    # Приводим путь к нужному виду для сравнения.
    norm_rel_path = os.path.normpath(rel_path)
    row = load_scan_result(norm_rel_path)
    if row and row[0]:
        return row[0]
    return "Unknown"



def get_favorites():
    """Возвращает список путей треков, находящихся в избранном."""
    con = sqlite3.connect(FAVORITE_DB)
    cur = con.cursor()
    cur.execute("SELECT path FROM favorites")
    favs = cur.fetchall()  # это список кортежей
    con.close()
    # Преобразуем кортежи в список строк:
    return [f[0] for f in favs]

def register_routes(app):
    # Глобальное состояние для хранения текущего трека, плеера, плейлиста и прочего
    global_state = {
        "current_track": {"path": None, "genre": None},
        "current_player": None,
        "current_playlist": [],
        "current_playlist_directory": "",
        "current_index": None,
        "current_volume": None,
        "scan_thread": None,
        "scan_stop_event": None,
        "scan_progress": {"status": "stopped", "scanned": 0, "total": 0, "results": {}},
        "training_progress": 0,
        "audio_devices": sd.query_devices(),
        "selected_device": 0,
    }
    DB_PATH = "favorite.db"

    def get_active_vlc_devices_default():
        inst = vlc.Instance()
        player = inst.media_player_new()
        out = player.audio_output_device_enum()
        devices = []
        while out:
            dev = out.contents
            device_id = dev.device.decode() if dev.device else ""
            description = dev.description.decode() if dev.description else ""
            if device_id:
                devices.append({'id': device_id, 'name': description})
            out = dev.next
        return devices

    @app.route("/get_scan_config")
    def get_scan_config():
        conf = load_config()
        logger.debug("Получение конфигурации сканирования: %s", conf)
        return jsonify({"scan_mode": conf.get("scan_mode", "new")})

    @app.route("/retrain", methods=["POST"])
    def retrain():
        global_state["training_progress"] = 0
        logger.info("Запущено переобучение модели с принудительным режимом.")
        threading.Thread(target=train_genre_model, args=(True,), daemon=True).start()
        return jsonify({"status": "Переобучение запущено"}), 200

    @app.route("/training_status")
    def training_status():
        return jsonify({"progress": global_state["training_progress"]})

    @app.route("/start_scan")
    def start_scan():
        mode = request.args.get("mode", "new")
        global_state["scan_mode_global"] = mode
        logger.info("Запуск сканирования с режимом: %s", mode)
        if global_state["scan_thread"] and global_state["scan_thread"].is_alive():
            logger.warning("Сканирование уже выполняется.")
            return jsonify({"status": "already scanning"})
        if mode == "new":
            if os.path.exists("scan_results.db"):
                os.remove("scan_results.db")
                logger.info("Файл scan_results.db удалён")
            init_scan_db()
            global_state["scan_progress"] = {"status": "in_progress", "scanned": 0, "total": 0, "results": {}}
        else:
            global_state["scan_progress"] = {"status": "in_progress", "scanned": 0, "total": 0, "results": {}}
        global_state["scan_stop_event"] = threading.Event()
        MUSIC_DIR = load_config().get("music_dir", DEFAULT_CONFIG["music_dir"])
        global_state["scan_thread"] = threading.Thread(
            target=scan_library_async,
            args=(MUSIC_DIR, mode, global_state["scan_stop_event"], global_state["scan_progress"])
        )
        global_state["scan_thread"].start()
        return jsonify({"status": "scan started"})

    @app.route("/scan_progress")
    def scan_progress_status():
        sp = global_state["scan_progress"]
        return jsonify({
            "scanned": sp.get("scanned", 0),
            "total": sp.get("total", 0),
            "status": sp.get("status", "")
        })

    @app.route("/stop_scan")
    def stop_scan():
        if global_state["scan_stop_event"]:
            global_state["scan_stop_event"].set()
            logger.info("Запрос на остановку сканирования отправлен.")
        return jsonify({"status": "scan stopping"})

    @app.route("/update_scan_config", methods=["POST"])
    def update_scan_config():
        data = request.get_json()
        scan_mode = data.get("scan_mode")
        if scan_mode not in ["new", "continue"]:
            return jsonify({"error": "Неверное значение scan_mode"}), 400
        conf = load_config()
        conf["scan_mode"] = scan_mode
        save_config(conf)
        logger.info("Сканировочный режим обновлён: %s", scan_mode)
        return jsonify({"status": "Сканировочный режим обновлен", "scan_mode": scan_mode})

    @app.route("/")
    def index():
        return redirect(url_for("browse"))

    @app.route("/browse")
    def browse():
        path = request.args.get("path")
        if not path and 'current_folder' in session:
            path = session['current_folder']
        decoded_path = unquote_plus(path) if path else ""
        logger.debug("Decoded path: %s", decoded_path)

        config = load_config()  # получаем конфигурацию
        MUSIC_DIR = config.get("music_dir", DEFAULT_CONFIG["music_dir"])
        full_dir = os.path.join(MUSIC_DIR, decoded_path) if decoded_path else MUSIC_DIR
        logger.debug("Полный путь каталога: %s", full_dir)

        try:
            files = sorted(f for f in os.listdir(full_dir) if f.lower().endswith(".mp3"))
            logger.debug("Найденные файлы: %s", files)
        except FileNotFoundError:
            logger.error("Каталог не найден: %s", full_dir)
            return f"Directory not found: {full_dir}", 404

        current_path_clean = decoded_path.replace('\\', '/') if decoded_path else ''
        logger.debug("Формируется область воспроизведения для %s", current_path_clean)

        # Добавляем список избранного
        favorites = get_favorites()

        return render_template("main.html",
                               files=files,
                               current_path=current_path_clean,
                               favorites=favorites,  # передаем избранное в шаблон
                               devices=global_state["audio_devices"],
                               selected_device=global_state["selected_device"],
                               current_track=session.get("current_track"),
                               current_genre=global_state["current_track"].get("genre"),
                               config=config,
                               enumerate=enumerate)

    @app.route("/shutdown", methods=["POST"])
    def shutdown():
        func = request.environ.get('werkzeug.server.shutdown')
        if func is None:
            logger.warning("Shutdown метод не найден. Вероятно, сервер запущен с использованием пользовательского IP.")
            return "Сервер остановлен (shutdown метод не найден)", 200
        func()
        logger.info("Сервер завершается по запросу.")
        return "Сервер завершается...", 200

    @app.route("/recommend")
    def recommend():
        # Ваш код поиска похожего трека...
        current_genre = str(global_state["current_track"].get("genre") or "").strip()
        current_path = str(global_state["current_track"].get("path") or "").strip()
        if not current_genre or current_genre.lower() == "unknown":
            return jsonify({"error": "Нет установлено жанра текущего трека"}), 400
        try:
            con = sqlite3.connect("scan_results.db")
            cur = con.cursor()
            cur.execute(
                "SELECT rel_path, confidence FROM scan_results WHERE genre=? AND rel_path<>? ORDER BY confidence DESC",
                (current_genre, current_path)
            )
            candidates = cur.fetchall()
            con.close()
        except Exception as e:
            return jsonify({"error": "Ошибка обращения к базе: " + str(e)}), 500
        threshold = 0.5
        valid_candidates = [rel_path for rel_path, conf in candidates if conf is not None and conf > threshold]
        recommended = None
        if valid_candidates:
            import random
            recommended = random.choice(valid_candidates)
        if not recommended:
            return jsonify({"error": "Похожий трек не найден"}), 400
        logger.info("Глобально рекомендован трек: %s", recommended)
        filename = os.path.basename(recommended)
        folder = os.path.dirname(recommended)
        return jsonify({
            "redirect": recommended,
            "filename": filename,
            "folder": folder
        })

    @app.route("/autoplay")
    def autoplay_route():
        # Получаем параметр track из GET-запроса
        track = request.args.get("track")
        logger.info("Autoplay: получен параметр track: %s", track)
        if not track:
            logger.warning("Autoplay: параметр track отсутствует, перенаправление на browse")
            return redirect(url_for("browse"))

        # Заменяем обратные слэши на прямые и нормализуем путь
        track = track.replace("\\", "/")
        norm_rel_path = os.path.normpath(track).replace("\\", "/")
        logger.info("Autoplay: нормализованный путь: %s", norm_rel_path)

        # Читаем жанр из базы (без аудиоанализа)
        genre = get_scanned_genre(norm_rel_path)
        logger.info("Autoplay: жанр получен: %s", genre)

        # Обновляем глобальное состояние текущего трека и сессионные данные
        global_state["current_track"]["path"] = norm_rel_path
        global_state["current_track"]["title"] = os.path.basename(norm_rel_path)
        global_state["current_track"]["genre"] = genre
        session["current_track"] = norm_rel_path

        # Определяем директорию для перехода
        folder = os.path.dirname(norm_rel_path)
        logger.info("Autoplay: определена директория: %s", folder)
        global_state["current_playlist_directory"] = folder
        session["current_folder"] = folder

        logger.info("Автоплей: текущий трек обновлён: %s, жанр: %s", norm_rel_path, genre)

        # Останавливаем текущий плеер, если он существует
        if global_state["current_player"] is not None:
            try:
                global_state["current_player"].stop()
            except Exception as ex:
                logger.error("Ошибка при остановке текущего плеера: %s", ex)

        # Перенаправляем на маршрут /play, чтобы запустить воспроизведение нового трека
        return redirect(url_for("browse", path=folder, autoplay=norm_rel_path))

    @app.route("/play")
    def play_track():
        logger.info("Запущен маршрут /play")
        path = request.args.get("path")
        if not path:
            logger.error("Нет переданного параметра path")
            return jsonify({"error": "No path provided"}), 400

        # Декодируем входной путь и нормализуем его
        decoded_path = unescape(unquote_plus(path))
        norm_rel_path = os.path.normpath(decoded_path)

        # Получаем "чистое" название трека (без расширения .mp3)
        track_title = get_track_title(norm_rel_path)

        MUSIC_DIR = load_config().get("music_dir", DEFAULT_CONFIG["music_dir"])
        full_path = os.path.normpath(os.path.join(MUSIC_DIR, norm_rel_path))

        if not os.path.isfile(full_path):
            logger.error("Файл не найден: %s", full_path)
            return jsonify({"error": "File not found", "full_path": full_path}), 404

        folder = os.path.dirname(norm_rel_path)
        global_state["current_playlist_directory"] = folder
        session["current_folder"] = folder

        try:
            playlist = sorted(
                f for f in os.listdir(os.path.join(MUSIC_DIR, folder))
                if f.lower().endswith(".mp3")
            )
        except Exception as e:
            logger.error("Ошибка формирования плейлиста: %s", e)
            playlist = []
        global_state["current_playlist"] = playlist

        file_name = os.path.basename(norm_rel_path)
        if file_name in playlist:
            global_state["current_index"] = playlist.index(file_name)
        else:
            global_state["current_index"] = 0

        if global_state["current_player"] is not None:
            try:
                global_state["current_player"].stop()  # Останавливаем предыдущий плеер
            except Exception as ex:
                logger.error("Ошибка при остановке текущего плеера: %s", ex)

        # Получаем конфигурацию и определяем режим воспроизведения
        config = load_config()
        mode = config.get("playback_mode", "host")  # "host" или "plyr"

        if mode == "host":
            try:
                instance = vlc.Instance()
                global_state["current_player"] = instance.media_player_new()
                media = instance.media_new(full_path)
                global_state["current_player"].set_media(media)

                if global_state.get("current_volume") is None:
                    global_state["current_volume"] = config.get("default_volume", 70)
                volume_to_set = global_state["current_volume"]
                global_state["current_player"].audio_set_volume(volume_to_set)

                vlc_devices = get_active_vlc_devices_default()
                selected_index = config.get("selected_device", 0)
                if vlc_devices and selected_index >= len(vlc_devices):
                    logger.warning("Выбранный индекс устройства превышает число устройств. Сбрасываем на 0.")
                    selected_index = 0
                if vlc_devices:
                    device_id = vlc_devices[selected_index]["id"]
                    logger.info("Устанавливается устройство с ID: %s", device_id)
                    global_state["current_player"].audio_output_device_set(None, device_id)
                else:
                    logger.info("Устройство не установлено, используется устройство по умолчанию.")

                global_state["current_player"].play()
                play_url = None
            except Exception as e:
                logger.error("Error playing file (host): %s", str(e))
                return jsonify({"error": str(e)}), 500
        elif mode == "plyr":
            play_url = url_for("stream", path=decoded_path)
            global_state["current_player"] = None
        else:
            return jsonify({"error": "Неверный режим воспроизведения"}), 400

        # Получаем жанр из базы (без аудиоанализа)
        genre = get_scanned_genre(norm_rel_path)

        # Обновляем глобальное состояние текущего трека (используем уже полученные norm_rel_path и track_title)
        global_state["current_track"]["path"] = norm_rel_path
        global_state["current_track"]["genre"] = genre
        global_state["current_track"]["title"] = track_title
        session["current_track"] = norm_rel_path

        response = {
            "status": "playing",
            "track": norm_rel_path,
            "title": track_title,
            "genre": genre
        }
        if play_url:
            response["play_url"] = play_url

        logger.info("Формируется ответ: %s", response)
        return jsonify(response)

    @app.route("/next")
    def next_track():
        if not global_state["current_playlist"] or global_state["current_index"] is None:
            return jsonify({"error": "No playlist loaded"}), 400
        global_state["current_index"] = (global_state["current_index"] + 1) % len(global_state["current_playlist"])
        next_file = global_state["current_playlist"][global_state["current_index"]]
        next_path = os.path.join(global_state["current_playlist_directory"], next_file)
        MUSIC_DIR = load_config().get("music_dir", DEFAULT_CONFIG["music_dir"])
        full_path = os.path.normpath(os.path.join(MUSIC_DIR, next_path))
        if not os.path.isfile(full_path):
            return jsonify({"error": "File not found", "full_path": full_path}), 404

        if global_state["current_player"] is not None:
            global_state["current_player"].stop()

        config = load_config()
        mode = config.get("playback_mode", "host")
        if mode == "host":
            try:
                instance = vlc.Instance()
                global_state["current_player"] = instance.media_player_new()
                media = instance.media_new(full_path)
                global_state["current_player"].set_media(media)
                if global_state.get("current_volume") is None:
                    global_state["current_volume"] = config.get("default_volume", 70)
                volume_to_set = global_state["current_volume"]
                global_state["current_player"].audio_set_volume(volume_to_set)
                vlc_devices = get_active_vlc_devices_default()
                selected_index = config.get("selected_device", 0)
                if vlc_devices and selected_index < len(vlc_devices):
                    device_id = vlc_devices[selected_index]["id"]
                    global_state["current_player"].audio_output_device_set(None, device_id)
                global_state["current_player"].play()
                play_url = None
            except Exception as e:
                return jsonify({"error": str(e)}), 500
        elif mode == "plyr":
            play_url = url_for("stream", path=next_path)
            global_state["current_player"] = None
        else:
            return jsonify({"error": "Неверный режим воспроизведения"}), 400

        # Обновляем глобальное состояние текущего трека с использованием next_path
        genre = get_scanned_genre(next_path)
        track_title = get_track_title(next_path)
        global_state["current_track"]["path"] = next_path
        global_state["current_track"]["genre"] = genre
        global_state["current_track"]["title"] = track_title

        response = {
            "status": "playing",
            "track": next_path,
            "title": track_title,
            "genre": genre,
            "volume": volume_to_set
        }
        if play_url:
            response["play_url"] = play_url
        return jsonify(response)

    @app.route("/prev")
    def prev_track():
        if not global_state["current_playlist"] or global_state["current_index"] is None:
            return jsonify({"error": "No playlist loaded"}), 400
        global_state["current_index"] = (global_state["current_index"] - 1) % len(global_state["current_playlist"])
        prev_file = global_state["current_playlist"][global_state["current_index"]]
        prev_path = os.path.join(global_state["current_playlist_directory"], prev_file)
        MUSIC_DIR = load_config().get("music_dir", DEFAULT_CONFIG["music_dir"])
        full_path = os.path.normpath(os.path.join(MUSIC_DIR, prev_path))
        if not os.path.isfile(full_path):
            return jsonify({"error": "File not found", "full_path": full_path}), 404

        if global_state["current_player"] is not None:
            global_state["current_player"].stop()

        config = load_config()
        mode = config.get("playback_mode", "host")
        if mode == "host":
            try:
                instance = vlc.Instance()
                global_state["current_player"] = instance.media_player_new()
                media = instance.media_new(full_path)
                global_state["current_player"].set_media(media)
                if global_state.get("current_volume") is None:
                    global_state["current_volume"] = config.get("default_volume", 70)
                volume_to_set = global_state["current_volume"]
                global_state["current_player"].audio_set_volume(volume_to_set)
                vlc_devices = get_active_vlc_devices_default()
                selected_index = config.get("selected_device", 0)
                if vlc_devices and selected_index < len(vlc_devices):
                    device_id = vlc_devices[selected_index]["id"]
                    global_state["current_player"].audio_output_device_set(None, device_id)
                global_state["current_player"].play()
                play_url = None
            except Exception as e:
                return jsonify({"error": str(e)}), 500
        elif mode == "plyr":
            play_url = url_for("stream", path=prev_path)
            global_state["current_player"] = None
        else:
            return jsonify({"error": "Неверный режим воспроизведения"}), 400

        # Обновляем глобальное состояние текущего трека
        # Здесь используем prev_path
        genre = get_scanned_genre(prev_path)
        track_title = get_track_title(prev_path)
        global_state["current_track"]["path"] = prev_path
        global_state["current_track"]["genre"] = genre
        global_state["current_track"]["title"] = track_title

        response = {
            "status": "playing",
            "track": prev_path,
            "title": track_title,
            "genre": genre,
            "volume": volume_to_set
        }
        if play_url:
            response["play_url"] = play_url
        return jsonify(response)

    @app.route("/stop")
    def stop_track():
        if global_state["current_player"] is not None:
            try:
                global_state["current_player"].stop()
            except Exception as ex:
                logger.error("Ошибка при остановке плеера: %s", ex)
                return jsonify({"error": str(ex)}), 500
            global_state["current_player"] = None
        # Очищаем глобальное состояние текущего трека
        global_state["current_track"]["path"] = None
        global_state["current_track"]["title"] = None
        global_state["current_track"]["genre"] = None
        return jsonify({"status": "stopped"})

    @app.route("/pause")
    def pause_track():
        if global_state["current_player"] is not None:
            try:
                global_state["current_player"].pause()
                global_state["paused"] = True  # Устанавливаем флаг паузы
            except Exception as ex:
                logger.error("Ошибка при приостановке плеера: %s", ex)
                return jsonify({"error": str(ex)}), 500
        # Возвращаем актуальную позицию, длительность и данные трека – они не очищаются
        current_time = global_state["current_player"].get_time() if global_state["current_player"] is not None else 0
        duration = global_state["current_player"].get_length() if global_state["current_player"] is not None else 0
        return jsonify({
            "status": "paused",
            "current_time": current_time,
            "duration": duration,
            "track": global_state["current_track"].get("path", ""),
            "title": global_state["current_track"].get("title", ""),
            "genre": global_state["current_track"].get("genre", "")
        })

    @app.route("/favorite", methods=["POST"])
    def favorite():
        data = request.get_json()
        path = data.get("path")
        if not path:
            logger.error("Нет переданного параметра path")
            return jsonify({"error": "No path provided"}), 400

        # Открываем соединение с базой избранного
        con = sqlite3.connect(FAVORITE_DB)
        cur = con.cursor()

        # Если запись уже существует – возвращаем существующие данные
        cur.execute("SELECT genre FROM favorites WHERE path=?", (path,))
        existing = cur.fetchone()
        if existing:
            genre = existing[0]
            con.close()
            logger.info("Запись уже существует для %s с жанром %s", path, genre)
            return jsonify({"status": "exists", "path": path, "genre": genre})

        # Получаем жанр из базы результатов сканирования (без аудиоанализа)
        genre = get_scanned_genre(path)

        cur.execute("INSERT INTO favorites (path, genre) VALUES (?, ?)", (path, genre))
        con.commit()
        con.close()
        logger.info("Добавлен трек в избранное: %s с жанром %s", path, genre)
        return jsonify({"status": "success", "path": path, "genre": genre})

    # Рабочий вариант функции
    # @app.route("/favorites_list")
    # def favorites_list():
    #     con = sqlite3.connect(FAVORITE_DB)
    #     cur = con.cursor()
    #     cur.execute("SELECT path, genre FROM favorites")
    #     favorites = cur.fetchall()
    #     con.close()
    #
    #     if favorites:
    #         html = "<ul class='list-group'>"
    #         for f, g in favorites:
    #             track_name = os.path.basename(f)
    #             html += f"""
    #                 <li class="list-group-item fav-entry">
    #                   <div class="d-flex flex-column">
    #                     <div class="fw-bold text-truncate" title="{f}">{track_name}</div>
    #                     <div class="small text-muted">Genre: {g}</div>
    #                     <div class="button-container d-flex justify-content-end" style="gap: 0.2rem;">
    #                       <button class="btn btn-sm btn-primary custom-btn" style="width:80px;" onclick="playTrack('{f}')">Play</button>
    #                       <button class="btn btn-sm btn-danger custom-btn" style="width:80px;" onclick="removeFavorite('{f}')">Remove</button>
    #                     </div>
    #                   </div>
    #                 </li>
    #             """
    #         html += "</ul>"
    #     else:
    #         html = "<p>Список избранного пуст.</p>"
    #     return jsonify({"html": html})

    @app.route("/favorites_list")
    def favorites_list():
        con = sqlite3.connect(FAVORITE_DB)
        cur = con.cursor()
        cur.execute("SELECT path, genre FROM favorites")
        favorites = cur.fetchall()
        con.close()

        if favorites:
            html = "<ul class='list-group'>"
            for f, g in favorites:
                # Получаем чистое название трека без расширения
                track_name = get_track_title(f)
                html += f"""
                    <li class="list-group-item fav-entry">
                      <div class="d-flex flex-column">
                        <div class="fw-bold text-truncate" title="{f}">{track_name}</div>
                        <div class="small text-muted">Genre: {g}</div>
                        <div class="button-container d-flex justify-content-end" style="gap: 0.2rem;">
                          <button class="btn btn-sm btn-primary custom-btn" style="width:80px;" onclick="playTrack('{f}')">Play</button>
                          <button class="btn btn-sm btn-danger custom-btn" style="width:80px;" onclick="removeFavorite('{f}')">Remove</button>
                        </div>
                      </div>
                    </li>
                """
            html += "</ul>"
        else:
            html = "<p>Список избранного пуст.</p>"
        return jsonify({"html": html})



    @app.route("/remove_favorite", methods=["POST"])
    def remove_favorite():
        data = request.get_json()
        path = data.get("path")
        if not path:
            logger.error("Удаление: нет переданного параметра path")
            return jsonify({"error": "No path provided"}), 400

        con = sqlite3.connect(FAVORITE_DB)
        cur = con.cursor()
        cur.execute("DELETE FROM favorites WHERE path=?", (path,))
        con.commit()
        # Проверка удаления: выполняем SELECT
        cur.execute("SELECT COUNT(*) FROM favorites WHERE path=?", (path,))
        count = cur.fetchone()[0]
        con.close()

        if count > 0:
            logger.error("Запись с path '%s' не удалена, остаток: %s", path, count)
            return jsonify({"error": "Трек не удалён из избранного"}), 400

        logger.info("Из избранного удалён трек: %s", path)
        return jsonify({"status": "removed", "path": path})


    @app.route("/analyze")
    def analyze_current():
        if not global_state["current_track"].get("path"):
            return jsonify({"error": "Нет текущего трека"}), 400
        MUSIC_DIR = load_config().get("music_dir", DEFAULT_CONFIG["music_dir"])
        full_path = os.path.normpath(os.path.join(MUSIC_DIR, global_state["current_track"]["path"]))
        genre = get_genre(full_path)
        global_state["current_track"]["genre"] = genre
        logger.info("Анализ выполнен. Обнаружен жанр: %s", genre)
        return jsonify({"status": "analyzed", "genre": genre})

    @app.route("/status")
    def status():
        if global_state["current_player"] is not None:
            current_time = global_state["current_player"].get_time()
            duration = global_state["current_player"].get_length()
            playing = global_state["current_player"].is_playing()
            # Если плеер не воспроизводит, но флаг paused установлен, статус именно "paused"
            if not playing and global_state.get("paused", False):
                statusState = "paused"
            elif playing:
                statusState = "playing"
            else:
                statusState = "stopped"
            return jsonify({
                "status": statusState,
                "current_time": current_time,
                "duration": duration,
                "track": global_state["current_track"].get("path"),
                "title": global_state["current_track"].get("title", global_state["current_track"].get("path")),
                "genre": global_state["current_track"].get("genre")
            })
        else:
            return jsonify({"status": "stopped"})

    @app.route("/seek", methods=["POST"])
    def seek():
        data = request.get_json()
        new_time = int(data.get("time", 0))
        if global_state["current_player"] is not None:
            global_state["current_player"].set_time(new_time)
            logger.debug("Перемотка к %d мс", new_time)
            return jsonify({"status": "seeked", "new_time": new_time})
        return jsonify({"status": "no track"}), 400

    @app.route("/volume", methods=["POST"])
    def set_volume():
        data = request.get_json()
        config_val = load_config()
        vol = int(data.get("volume", config_val.get("default_volume", 70)))
        mode = config_val.get("playback_mode", "host")
        if mode == "host":
            global_state["current_volume"] = vol
            session["current_volume"] = vol  # сохраняем значение в сессии
            if global_state["current_player"] is not None:
                global_state["current_player"].audio_set_volume(vol)
                logger.debug("Установлена громкость (host): %d", vol)
                return jsonify({"status": "volume set", "volume": vol})
            else:
                return jsonify({"error": "нет активного трека"}), 400
        else:
            return jsonify({"error": "Регулировка громкости недоступна в этом режиме"}), 400

    @app.route("/set_device", methods=["POST"])
    def set_device():
        new_device = request.form.get("device")
        if new_device is not None:
            config_val = load_config()
            config_val["selected_device"] = int(new_device)
            logger.info("Устройство изменено на: %s", config_val["selected_device"])
            save_config(config_val)
        return redirect(url_for("settings"))

    @app.route("/stream")
    def stream():
        path = request.args.get("path")
        if not path:
            return "No path provided", 400
        # Применяем декодирование HTML-сущностей
        decoded_path = unescape(unquote_plus(path))
        MUSIC_DIR = load_config().get("music_dir", DEFAULT_CONFIG["music_dir"])
        full_path = os.path.normpath(os.path.join(MUSIC_DIR, decoded_path))
        if os.path.isfile(full_path):
            return send_file(full_path, mimetype="audio/mp3")
        else:
            return "File not found", 404

    @app.route("/get_directories")
    def get_directories():
        config_val = load_config()
        MUSIC_DIR = config_val.get("music_dir", DEFAULT_CONFIG["music_dir"])
        logger.debug("MUSIC_DIR = %s", MUSIC_DIR)
        node_id = request.args.get("id", "#")
        if node_id == "#":
            current_path = MUSIC_DIR
        else:
            current_path = os.path.join(MUSIC_DIR, node_id)
        logger.debug("get_directories => current_path: %s", current_path)
        nodes = []
        try:
            if not os.path.exists(current_path):
                logger.debug("Directory does not exist: %s", current_path)
                return jsonify(nodes)
            for entry in os.listdir(current_path):
                full_entry = os.path.join(current_path, entry)
                if os.path.isdir(full_entry):
                    try:
                        children = any(os.path.isdir(os.path.join(full_entry, e)) for e in os.listdir(full_entry))
                    except Exception as ex:
                        logger.error("Ошибка проверки вложенных папок в %s: %s", full_entry, ex)
                        children = False
                    nodes.append({
                        "id": os.path.relpath(full_entry, MUSIC_DIR).replace("\\", "/"),
                        "text": entry,
                        "children": children
                    })
            logger.debug("Returning nodes: %s", nodes)
            return jsonify(nodes)
        except Exception as e:
            logger.error("Ошибка при построении дерева: %s", e)
            return jsonify([])

    @app.route("/scan_library")
    def scan_library():
        scan_results = {}
        MUSIC_DIR = load_config().get("music_dir", DEFAULT_CONFIG["music_dir"])
        for root, dirs, files in os.walk(MUSIC_DIR):
            for file in files:
                if file.lower().endswith(".mp3"):
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, MUSIC_DIR)
                    genre = get_genre(full_path)
                    scan_results.setdefault(genre, []).append(rel_path)
        return jsonify({"status": "scanned", "results": scan_results})

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        config_val = load_config()
        if request.method == "POST":
            music_dir = request.form.get("music_dir", config_val.get("music_dir"))
            playback_mode = request.form.get("playback_mode", config_val.get("playback_mode"))
            # Если по ошибке передадут "local", принудительно меняем на "plyr"
            if playback_mode == "local":
                playback_mode = "plyr"
            default_volume = request.form.get("default_volume", config_val.get("default_volume"))
            sound_quality = request.form.get("sound_quality", config_val.get("sound_quality"))
            try:
                default_volume = int(default_volume)
            except Exception as e:
                default_volume = DEFAULT_CONFIG["default_volume"]
            config_val["music_dir"] = music_dir
            config_val["playback_mode"] = playback_mode
            config_val["default_volume"] = default_volume
            config_val["sound_quality"] = sound_quality
            save_config(config_val)
            logger.info("Настройки сохранены: %s", config_val)
            session.pop("current_folder", None)
            session.pop("current_track", None)
            return redirect(url_for("settings"))
        else:
            devices = get_active_vlc_devices_default()
            current_folder = session.get("current_folder", "")
            return render_template("settings.html", config=config_val, devices=devices, current_folder=current_folder)

    @app.route("/custom_keywords", methods=["GET", "POST"])
    def custom_keywords():
        if request.method == "POST":
            try:
                new_keywords = request.get_json().get("keywords")
                if isinstance(new_keywords, dict):
                    save_genre_settings(new_keywords)
                    logger.info("Жанровые настройки обновлены: %s", new_keywords)
                    return jsonify({"status": "saved", "keywords": new_keywords})
                else:
                    return jsonify({"status": "error", "message": "Неверный формат"}), 400
            except Exception as e:
                logger.error("Ошибка при обновлении жанровых настроек: %s", e)
                return jsonify({"status": "error", "message": str(e)}), 500
        else:
            keywords = load_genre_settings()
            return jsonify({"keywords": keywords})










