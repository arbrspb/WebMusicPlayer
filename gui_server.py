# gui_server.py 15-05-25 5555
import tkinter as tk
from tkinter import messagebox
import threading
import requests
import os
import socket
from multiprocessing import Process, freeze_support
from run import app # Импортируйте Flask-приложение из серверного модуля
from app.logging_config import setup_logging
import logging
logger = logging.getLogger(__name__)

# Определяем константу для файла логирования
LOG_FILE_NAME = "debug.log"



server_process = None  # Глобальная переменная для процесса сервера


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

def run_server(ip, port, debug_enabled=False):
    setup_logging(debug_enabled, LOG_FILE_NAME)
    logger.info("Рабочая директория: %s", os.getcwd())
    app.run(host=ip, port=int(port), debug=debug_enabled, use_reloader=False)

# Рабочий вариант
# def run_server(ip, port, debug_enabled=False):
#     if debug_enabled:
#         logging.getLogger().setLevel(logging.DEBUG)
#         logger.info("Режим отладки включен")
#     else:
#         logging.getLogger().setLevel(logging.INFO)
#         logger.info("Режим отладки выключен")
#     logger.info("Рабочая директория: %s", os.getcwd())
#     # Запускаем Flask-приложение; use_reloader=False чтобы избежать двойного запуска
#     app.run(host=ip, port=int(port), debug=debug_enabled, use_reloader=False)


def start_server(debug_enabled, ip, port):
    global server_process
    if server_process is not None and server_process.is_alive():
        messagebox.showinfo("Информация", "Сервер уже запущен!")
        return
    server_process = Process(target=run_server, args=(ip, port, debug_enabled))
    server_process.start()
    messagebox.showinfo("Информация", "Сервер запущен!")
    logger.info("Сервер запущен с IP: %s, порт: %s, debug=%s", ip, port, debug_enabled)


def stop_server():
    global server_process
    if server_process is None or not server_process.is_alive():
        messagebox.showinfo("Информация", "Сервер уже остановлен!")
        return
    try:
        curr_ip = ip_entry.get().strip()
        port = port_entry.get().strip()
        if curr_ip == "0.0.0.0":
            curr_ip = "127.0.0.1"
        shutdown_url = f"http://{curr_ip}:{port}/shutdown"
        response = requests.post(shutdown_url, timeout=5)
        messagebox.showinfo("Информация", "Запрос на остановку сервера отправлен.")
        logger.info("Запрос на остановку сервера отправлен на %s", shutdown_url)
    except requests.exceptions.RequestException as e:
        messagebox.showinfo("Информация", "Сервер уже остановлен или не отвечает.\n" + str(e))
        logger.error("Ошибка при отправке запроса на остановку сервера: %s", e)
    server_process.terminate()
    server_process.join()
    server_process = None
    logger.info("Сервер остановлен.")


def update_status_label():
    if server_process is not None and server_process.is_alive():
        curr_ip = ip_entry.get().strip()
        port = port_entry.get().strip()
        if curr_ip == "0.0.0.0":
            display_ip = get_local_ip()
        else:
            display_ip = curr_ip
        status_label.config(text=f"Сервер запущен на: http://{display_ip}:{port}", fg="green")
    else:
        status_label.config(text="Сервер остановлен", fg="red")
    root.after(1000, update_status_label)


def create_gui():
    global root, status_label, ip_entry, port_entry
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

    start_btn = tk.Button(frame, text="Запустить сервер", width=20,
                          command=lambda: start_server(debug_var.get(), ip_entry.get(), port_entry.get()))
    start_btn.grid(row=3, column=0, padx=5, pady=5)

    stop_btn = tk.Button(frame, text="Остановить сервер", width=20, command=stop_server)
    stop_btn.grid(row=3, column=1, padx=5, pady=5)

    status_label = tk.Label(frame, text="Сервер остановлен", fg="red", font=("Helvetica", 12))
    status_label.grid(row=4, column=0, columnspan=2, pady=10)

    update_status_label()
    root.mainloop()


if __name__ == '__main__':
    freeze_support()  # Необходим для Windows при использовании multiprocessing
    create_gui()


