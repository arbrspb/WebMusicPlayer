import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.server_runtime import (
    SingleInstanceLock,
    port_is_listening,
    probe_web_music_player,
)


def _start_health_server(payload, status=200):
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_frozen_runtime_uses_persistent_directory_next_to_executable(
        tmp_path, monkeypatch,
):
    import app.paths as paths

    dist = tmp_path / "dist"
    dist.mkdir()
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    executable = dist / "gui_server.exe"
    executable.write_bytes(b"")
    monkeypatch.delenv("WMP_DATA_DIR", raising=False)
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths.sys, "executable", str(executable))

    assert paths._runtime_project_dir() == tmp_path


def test_single_instance_lock_rejects_second_server(tmp_path):
    lock_path = tmp_path / "server.lock"
    first = SingleInstanceLock(lock_path)
    second = SingleInstanceLock(lock_path)

    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        first.release()
    assert second.acquire() is True
    second.release()


def test_port_probe_detects_listener():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        assert port_is_listening(port) is True
    finally:
        listener.close()


def test_player_probe_identifies_running_web_music_player():
    server, thread = _start_health_server({
        "application": "web-music-player",
        "status": "ok",
        "pid": 4321,
        "training_running": False,
    })
    try:
        result = probe_web_music_player(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result["occupied"] is True
    assert result["is_web_music_player"] is True
    assert result["status"] == "running"
    assert result["pid"] == 4321


def test_player_probe_distinguishes_another_http_application():
    server, thread = _start_health_server({"status": "ok", "service": "other"})
    try:
        result = probe_web_music_player(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert result["occupied"] is True
    assert result["is_web_music_player"] is False
    assert result["status"] == "occupied_by_other"


def test_application_server_uses_waitress(monkeypatch, tmp_path):
    import waitress
    import app.server_runtime as runtime

    calls = []
    monkeypatch.setattr(
        runtime,
        "SingleInstanceLock",
        lambda: SingleInstanceLock(tmp_path / "server.lock"),
    )
    monkeypatch.setattr(
        waitress,
        "serve",
        lambda app, **kwargs: calls.append((app, kwargs)),
    )
    application = object()

    runtime.serve_application(application, host="127.0.0.1", port=8765)

    assert calls[0][0] is application
    assert calls[0][1]["threads"] == 6
    assert calls[0][1]["channel_timeout"] == 60
