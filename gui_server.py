"""Небольшое окно управления сервером Web Music Player."""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import tkinter as tk
from multiprocessing import Process, freeze_support, parent_process
from tkinter import messagebox


os.environ["WMP_SERVER_PROCESS"] = "0"

from app.logging_config import setup_logging


logger = logging.getLogger(__name__)
server_process = None
status_probe_inflight = False
last_server_probe = {"status": "stopped", "occupied": False}


def get_local_ip():
    connection = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        connection.connect(("8.8.8.8", 80))
        return connection.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        connection.close()


def _watch_process_owner(owner, exit_process):
    """Exit the server when the GUI process that created it disappears."""
    try:
        owner.join()
    except Exception:
        logger.exception("Не удалось дождаться завершения родительского GUI-процесса")
        return
    logger.warning(
        "Родительский GUI-процесс PID %s завершён; останавливаем сервер.",
        getattr(owner, "pid", "unknown"),
    )
    exit_process(0)


def start_owner_watchdog():
    """Attach a non-blocking lifecycle guard in multiprocessing children."""
    owner = parent_process()
    if owner is None:
        # Direct starts through run.py intentionally remain standalone.
        return None
    watchdog = threading.Thread(
        target=_watch_process_owner,
        args=(owner, os._exit),
        name="server-owner-watchdog",
        daemon=True,
    )
    watchdog.start()
    return watchdog


def run_server(ip, port, debug_enabled=False):
    os.environ["WMP_SERVER_PROCESS"] = "1"
    start_owner_watchdog()
    from run import app
    from app.server_runtime import serve_application

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    setup_logging(debug_enabled)
    logger.info("Рабочая директория: %s", os.getcwd())
    serve_application(app, host=ip, port=int(port), debug_enabled=debug_enabled)


def _validated_port(raw_value):
    try:
        value = int(str(raw_value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("Порт должен быть числом от 1 до 65535.") from exc
    if not 1 <= value <= 65535:
        raise ValueError("Порт должен быть числом от 1 до 65535.")
    return value


def get_server_probe():
    from app.server_runtime import probe_web_music_player

    try:
        port = _validated_port(port_entry.get())
    except (ValueError, tk.TclError):
        return {
            "occupied": False,
            "is_web_music_player": False,
            "status": "invalid_port",
        }
    return probe_web_music_player(port, ip_entry.get().strip())


def start_server(debug_enabled, ip, port):
    global server_process

    try:
        port_number = _validated_port(port)
    except ValueError as exc:
        messagebox.showerror("Ошибка", str(exc))
        return

    if server_process is not None and server_process.is_alive():
        messagebox.showinfo("Информация", "Сервер уже запущен этим окном.")
        return
    server_process = None

    probe = get_server_probe()
    if probe.get("is_web_music_player"):
        pid_text = f" (PID {probe.get('pid')})" if probe.get("pid") else ""
        messagebox.showinfo(
            "Сервер уже запущен",
            f"Web Music Player уже работает на порту {port_number}{pid_text}.\n"
            "Окно подключилось к его статусу; второй сервер не требуется.",
        )
        request_status_probe()
        return
    if probe.get("occupied"):
        messagebox.showerror(
            "Порт занят другой программой",
            f"Порт {port_number} занят не Web Music Player.\n"
            "Выберите другой порт или закройте использующую его программу.",
        )
        request_status_probe()
        return

    try:
        from app.config import load_config, save_config

        config = load_config()
        config["debug_enabled"] = bool(debug_enabled)
        save_config(config)
    except Exception as exc:
        logger.error("Не удалось обновить debug_enabled в config.json: %s", exc)

    server_process = Process(
        target=run_server,
        args=(str(ip).strip() or "0.0.0.0", port_number, bool(debug_enabled)),
    )
    server_process.start()
    status_label.config(text="Сервер запускается…", fg="#b36b00")
    logger.info(
        "Сервер запускается с IP: %s, порт: %s, debug=%s",
        ip,
        port_number,
        debug_enabled,
    )
    root.after(150, request_status_probe)


def stop_server(show_messages=True, refresh=True):
    global server_process

    owned = server_process is not None and server_process.is_alive()
    if not owned:
        server_process = None
        probe = get_server_probe()
        if show_messages and probe.get("is_web_music_player"):
            messagebox.showinfo(
                "Сервер запущен в другом окне",
                "Этот экземпляр не запускал сервер и не будет завершать чужой процесс.\n"
                "Остановите его в том окне, из которого он был запущен.",
            )
        elif show_messages and probe.get("occupied"):
            messagebox.showinfo("Информация", "Порт занят другой программой.")
        elif show_messages:
            messagebox.showinfo("Информация", "Сервер уже остановлен.")
        return

    server_process.terminate()
    server_process.join(timeout=5)
    if server_process.is_alive() and hasattr(server_process, "kill"):
        server_process.kill()
        server_process.join(timeout=2)
    server_process = None
    if show_messages:
        messagebox.showinfo("Информация", "Сервер остановлен.")
    logger.info("Сервер остановлен.")
    if refresh:
        request_status_probe()


def apply_server_probe(probe, endpoint):
    global status_probe_inflight, last_server_probe, server_process

    status_probe_inflight = False
    try:
        current_endpoint = (
            ip_entry.get().strip(),
            _validated_port(port_entry.get()),
        )
    except (ValueError, tk.TclError):
        current_endpoint = None
    if current_endpoint != endpoint:
        return

    last_server_probe = dict(probe)
    owned = server_process is not None and server_process.is_alive()
    if server_process is not None and not owned:
        server_process = None

    display_ip = (
        get_local_ip()
        if endpoint[0] in {"", "0.0.0.0", "::"}
        else endpoint[0]
    )
    if probe.get("is_web_music_player"):
        pid_text = f" · PID {probe.get('pid')}" if probe.get("pid") else ""
        owner_text = "" if owned else " · запущен другим окном"
        status_label.config(
            text=(
                f"Сервер запущен: http://{display_ip}:{endpoint[1]}"
                f"{pid_text}{owner_text}"
            ),
            fg="green",
        )
    elif owned:
        status_label.config(text="Сервер запускается…", fg="#b36b00")
    elif probe.get("occupied"):
        status_label.config(
            text=f"Порт {endpoint[1]} занят другой программой",
            fg="#b36b00",
        )
    else:
        status_label.config(text="Сервер остановлен", fg="red")

    start_btn.config(state="disabled" if probe.get("occupied") or owned else "normal")
    # Другой экземпляр отображается, но безопасно остановить можно только
    # дочерний процесс, созданный текущим окном.
    stop_btn.config(state="normal" if owned else "disabled")


def request_status_probe():
    global status_probe_inflight

    if status_probe_inflight:
        return
    try:
        endpoint = (
            ip_entry.get().strip(),
            _validated_port(port_entry.get()),
        )
    except (ValueError, tk.TclError):
        status_label.config(text="Некорректный порт", fg="red")
        start_btn.config(state="normal")
        stop_btn.config(state="disabled")
        return

    status_probe_inflight = True

    def worker():
        from app.server_runtime import probe_web_music_player

        probe = probe_web_music_player(endpoint[1], endpoint[0])
        try:
            root.after(0, lambda: apply_server_probe(probe, endpoint))
        except (tk.TclError, RuntimeError):
            pass

    threading.Thread(
        target=worker,
        name="server-status-probe",
        daemon=True,
    ).start()


def update_status_label():
    request_status_probe()
    root.after(1000, update_status_label)


def close_gui():
    if server_process is not None and server_process.is_alive():
        if not messagebox.askyesno(
            "Закрытие",
            "Сервер запущен этим окном. Остановить его и закрыть программу?",
        ):
            return
        stop_server(show_messages=False, refresh=False)
    root.destroy()


def create_gui():
    global root, status_label, ip_entry, port_entry, start_btn, stop_btn

    root = tk.Tk()
    root.title("Управление сервером Web Music Player")

    frame = tk.Frame(root, padx=20, pady=20)
    frame.pack()

    tk.Label(frame, text="IP (хост):").grid(row=0, column=0, sticky="e", padx=5, pady=5)
    ip_entry = tk.Entry(frame, width=15)
    ip_entry.insert(0, "0.0.0.0")
    ip_entry.grid(row=0, column=1, padx=5, pady=5)

    tk.Label(frame, text="Порт:").grid(row=1, column=0, sticky="e", padx=5, pady=5)
    port_entry = tk.Entry(frame, width=15)
    port_entry.insert(0, "8080")
    port_entry.grid(row=1, column=1, padx=5, pady=5)

    debug_var = tk.BooleanVar(value=False)
    debug_chk = tk.Checkbutton(frame, text="Включить отладку", variable=debug_var)
    debug_chk.grid(row=2, column=0, columnspan=2, pady=10)

    start_btn = tk.Button(
        frame,
        text="Запустить сервер",
        width=20,
        command=lambda: start_server(
            debug_var.get(), ip_entry.get(), port_entry.get()
        ),
    )
    start_btn.grid(row=3, column=0, padx=5, pady=5)

    stop_btn = tk.Button(
        frame,
        text="Остановить сервер",
        width=20,
        command=stop_server,
        state="disabled",
    )
    stop_btn.grid(row=3, column=1, padx=5, pady=5)

    status_label = tk.Label(
        frame,
        text="Проверяем состояние сервера…",
        fg="#555555",
        font=("Helvetica", 11),
        wraplength=430,
    )
    status_label.grid(row=4, column=0, columnspan=2, pady=10)

    root.protocol("WM_DELETE_WINDOW", close_gui)
    update_status_label()
    root.mainloop()


if __name__ == "__main__":
    freeze_support()
    if "--training-worker" in sys.argv:
        from app.training_jobs import worker_main

        raise SystemExit(worker_main(sys.argv[1:]))
    try:
        create_gui()
    finally:
        # Covers an unhandled Tkinter/Python error. Forced process termination
        # is covered independently by the watchdog inside the server child.
        if server_process is not None and server_process.is_alive():
            stop_server(show_messages=False, refresh=False)
