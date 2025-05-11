# app/utils.py 11-05-25
import os
import sys
import logging

logger = logging.getLogger(__name__) # Логирование

def resource_path(relative_path):
    if getattr(sys, 'frozen', False):
        base_path = os.path.dirname(sys.executable)
    else:
        # Здесь поднимаемся на уровень вверх (корень проекта)
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    logger.debug("Base path: %s", base_path)
    return os.path.join(base_path, relative_path)

def remove_extension(filename, ext=".mp3"):
    """
    Возвращает имя файла без указанного расширения (по умолчанию, .mp3).
    Если расширение отсутствует, возвращает исходное имя.
    """
    root, extension = os.path.splitext(filename)
    # Если расширение совпадает (без учета регистра), вернуть root
    if extension.lower() == ext.lower():
        return root
    return filename

def get_track_title(file_path):
    """
    Из полного пути получает имя файла без расширения.
    """
    base = os.path.basename(file_path)
    return remove_extension(base)

