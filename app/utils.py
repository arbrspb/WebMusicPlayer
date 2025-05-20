# app/utils.py 15-05-25 18-25
import os
import sys
import logging
import json
from collections import defaultdict

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

#функция для сбора статистики по найденному количеству жанров по папкам на странице librosa_test.html  и функция в librosa_settings.py @librosa_test_bp.route
def get_genre_stats_by_folders(root_dir, max_tracks_per_genre=200, track_exts=('.mp3','.wav','.flac','.ogg','.m4a')):
    genre_stats = []
    for dirpath, _, filenames in os.walk(root_dir):
        genre = os.path.basename(dirpath)
        tracks = [f for f in filenames if f.lower().endswith(track_exts)]
        if not tracks:
            continue
        selected_tracks = tracks if max_tracks_per_genre == 0 else tracks[:max_tracks_per_genre]
        genre_stats.append({
            'genre': genre,
            'count': len(selected_tracks),
            'folder': dirpath,
            'files': [os.path.join(dirpath, f) for f in selected_tracks]
        })
    return genre_stats
#функция для сбора статистики по найденному количеству жанров обученной модели на странице librosa_test.html  и функция в librosa_settings.py @librosa_test_bp.route
def get_genre_stats_by_model(
    folder_path,
    librosa_settings,
    exts=('.mp3', '.wav', '.flac', '.ogg', '.m4a'),
    max_files=None
):
    from .models import get_genre
    genre_counts = {}
    files_checked = 0
    file_paths = []

    print(f"[DEBUG] get_genre_stats_by_model: Сканируем папку {folder_path}")

    # 1. Собираем все файлы
    for root, _, files in os.walk(folder_path):
        for f in files:
            if f.lower().endswith(exts):
                file_path = os.path.join(root, f)
                file_paths.append(file_path)
    print(f"[DEBUG] Найдено файлов: {len(file_paths)}")

    # 2. Ограничиваем количество файлов, если надо
    if max_files is not None:
        file_paths = file_paths[:max_files]
        print(f"[DEBUG] Лимит файлов для анализа: {max_files}. Используем: {len(file_paths)}")

    # 3. Анализируем каждый файл
    for idx, file_path in enumerate(file_paths, 1):
        print(f"[DEBUG] Анализируем файл {idx}/{len(file_paths)}: {file_path}")
        try:
            genre, conf = get_genre(file_path, librosa_params=librosa_settings)
            print(f"[DEBUG] → Результат: genre={genre}, confidence={conf}")
            genre = genre or "Unknown"
            genre_counts[genre] = genre_counts.get(genre, 0) + 1
            files_checked += 1
        except Exception as e:
            print(f"[ERROR] Ошибка при анализе {file_path}: {e}")

    print(f"[DEBUG] Анализ завершён. Всего файлов обработано: {files_checked}")
    print(f"[DEBUG] Статистика по жанрам: {genre_counts}")
    return genre_counts, files_checked