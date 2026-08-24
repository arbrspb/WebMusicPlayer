"""Production server and single-instance guard for Windows and source runs."""

from __future__ import annotations

import logging
import http.client
import json
import os
import socket
from pathlib import Path

from .paths import SERVER_LOCK_FILE


logger = logging.getLogger(__name__)
APPLICATION_ID = "web-music-player"


class ServerAlreadyRunningError(RuntimeError):
    pass


class SingleInstanceLock:
    def __init__(self, path=SERVER_LOCK_FILE):
        self.path = Path(path)
        self._handle = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self):
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (OSError, IOError):
            pass
        handle.close()

    def __enter__(self):
        if not self.acquire():
            raise ServerAlreadyRunningError(
                "Другой экземпляр Web Music Player уже запущен"
            )
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.release()


def port_is_listening(port, host="127.0.0.1", timeout=0.3):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def probe_web_music_player(port, host="127.0.0.1", timeout=0.35):
    """Identify a running player without mistaking any listener for our app."""
    connect_host = "127.0.0.1" if str(host).strip() in {"", "0.0.0.0", "::"} else str(host).strip()
    result = {
        "occupied": False,
        "is_web_music_player": False,
        "pid": None,
        "status": "stopped",
        "error": "",
    }
    connection = None
    try:
        connection = http.client.HTTPConnection(connect_host, int(port), timeout=timeout)
        connection.request("GET", "/health", headers={"Accept": "application/json"})
        response = connection.getresponse()
        body = response.read(64 * 1024)
        result["occupied"] = True
        if response.status != 200:
            result["status"] = "occupied_by_other"
            return result
        payload = json.loads(body.decode("utf-8"))
        # ``application`` is authoritative for new versions.  The key pair is
        # retained for compatibility with an older already-running instance.
        is_player = (
            payload.get("application") == APPLICATION_ID
            or (
                payload.get("status") == "ok"
                and "training_running" in payload
                and "pid" in payload
            )
        )
        result.update({
            "is_web_music_player": bool(is_player),
            "pid": payload.get("pid") if is_player else None,
            "status": "running" if is_player else "occupied_by_other",
        })
        return result
    except (OSError, ValueError, json.JSONDecodeError, http.client.HTTPException) as exc:
        # A successful TCP probe means another/non-HTTP application owns the
        # port.  A refused connection means the server is genuinely stopped.
        occupied = port_is_listening(port, connect_host, timeout=timeout)
        result.update({
            "occupied": occupied,
            "status": "occupied_by_other" if occupied else "stopped",
            "error": str(exc) if occupied else "",
        })
        return result
    finally:
        if connection is not None:
            connection.close()


def serve_application(app, host="0.0.0.0", port=8080, debug_enabled=False):
    """Serve via Waitress; keep Flask's development server as a fallback."""
    with SingleInstanceLock():
        try:
            from waitress import serve
        except ImportError:
            logger.warning(
                "Waitress не установлен; используется резервный сервер Flask. "
                "Установите зависимости проекта для устойчивого режима."
            )
            return app.run(
                host=host,
                port=int(port),
                debug=bool(debug_enabled),
                use_reloader=False,
                threaded=True,
            )

        logger.info(
            "HTTP-сервер Waitress запущен: host=%s port=%s threads=6",
            host, port,
        )
        return serve(
            app,
            host=host,
            port=int(port),
            threads=6,
            channel_timeout=60,
            cleanup_interval=15,
        )
