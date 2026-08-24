# app/utils.py 14-08-25 01-50
"""Утилиты для работы с файлами, путями и жанровой статистикой в WebMusicPlayer."""

# Основные импорты
import re
import sys
import logging
import os
from datetime import datetime
import psutil
import multiprocessing


# Импорты для функции def plot_learning_curve_for_genre_model
import numpy as np
import matplotlib.pyplot as plt
logging.getLogger('matplotlib').setLevel(logging.WARNING) # Отключаем логирование Matplotlib ( шрифты показывается в консоли не нужны)
from sklearn.model_selection import learning_curve
import json
from .paths import LEARNING_CURVES_DIR, MODEL_FILE, PROJECT_DIR

# Логирование
from .logging_config import (
    is_log_type_enabled,
    setup_model_logger,
    setup_status_logger,
    setup_resource_logger,
    # (другие setup_ если понадобятся)
)

# model logger
model_logger = logging.getLogger("model")
setup_model_logger()
# status logger
status_logger = logging.getLogger("status")
setup_status_logger()
# resource logger
resource_logger = logging.getLogger("resource")
setup_resource_logger()

def resource_path(relative_path):
    """Возвращает абсолютный путь к ресурсу относительно директории исполняемого файла или проекта."""
    if getattr(sys, 'frozen', False):
        base_path = os.path.dirname(sys.executable)
    else:
        # Здесь поднимаемся на уровень вверх (корень проекта)
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if is_log_type_enabled("model"):
        model_logger.debug("Base path: %s", base_path)
    return os.path.join(base_path, relative_path)

def flask_resource_path(relative_path):
    """
    Для поиска ресурсов (templates, static) внутри PyInstaller exe.
    Используйте ЭТУ функцию для template_folder и static_folder!
    """
    if hasattr(sys, '_MEIPASS'): # PyInstaller special attribute
        return os.path.join(sys._MEIPASS, relative_path)
    # Обычный запуск: путь относительно корня проекта
    return os.path.join(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")), relative_path)

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
    """
    Собирает статистику по количеству треков каждого жанра в папках.
    Возвращает список словарей с жанрами, количеством, путем к папке и списком файлов.
    """
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
def get_genre_stats_and_tracks_by_model(folder_path, librosa_settings=None, max_files=700):
    """
    Возвращает:
    - user_genre_stats: {genre: count}
    - user_total_files: int
    - genre_tracks: {genre: [file_path1, ...]}
    """
    from .models import get_genre
    genre_tracks = {}
    genre_stats = {}
    file_list = []

    # Собираем все файлы (чтобы знать общее количество для красивой нумерации)
    for root, _, files in os.walk(folder_path):
        for fname in files:
            if fname.lower().endswith(('.mp3', '.wav', '.flac', '.ogg')):
                file_list.append(os.path.join(root, fname))
    total_files = len(file_list)

    if max_files is not None:
        file_list = file_list[:max_files]
        if is_log_type_enabled("model"):
            model_logger.debug(
            "[DEBUG] Лимит файлов для анализа: %d. Используем: %d (из %d)",
            max_files, len(file_list), total_files
        )
    for idx, full_path in enumerate(file_list, 1):
        if is_log_type_enabled("model"):
            model_logger.debug(
            "[DEBUG] Анализируем файл %d/%d: %s",
            idx, len(file_list), full_path
        )
        genre, conf, *_ = get_genre(full_path, librosa_params=librosa_settings)
        genre_stats[genre] = genre_stats.get(genre, 0) + 1
        genre_tracks.setdefault(genre, []).append(full_path)
        if is_log_type_enabled("model"):
            model_logger.debug(
            "[DEBUG] Предсказано: %s, уверенность: %s",
            genre, conf
        )

    return genre_stats, len(file_list), genre_tracks

def safe_join_music_dir(music_dir, rel_path):
    """Безопасно соединяет корневую папку музыки и относительный путь, нормализуя абсолютные и UNC-пути."""
    # Нормализуем music_dir и rel_path
    music_dir = os.path.normpath(music_dir)
    rel_path = os.path.normpath(rel_path)
    # UNC пути (\\server\share\file.mp3) — возвращаем как есть
    if rel_path.startswith('\\\\'):
        if is_log_type_enabled("model"):
            model_logger.debug(f"[PATH][UTILS] safe_join_music_dir: music_dir={music_dir}, rel_path={rel_path}, result={rel_path}, exists={os.path.exists(rel_path)}, isfile={os.path.isfile(rel_path)}")
        return rel_path
    # Если rel_path абсолютный (начинается с буквы диска или /), но НЕ внутри music_dir — приводим к относительному
    if os.path.isabs(rel_path):
        # Если файл реально внутри music_dir — разрешаем абсолютный путь
        if os.path.commonprefix([os.path.abspath(rel_path), os.path.abspath(music_dir)]) == os.path.abspath(music_dir):
            if is_log_type_enabled("model"):
                model_logger.debug(f"[PATH][UTILS] safe_join_music_dir: music_dir={music_dir}, rel_path={rel_path}, result={rel_path}, exists={os.path.exists(rel_path)}, isfile={os.path.isfile(rel_path)}")
            return rel_path
        # ВАЖНО: иначе — делаем путь относительным!
        rel_path = rel_path.lstrip('/\\')
    # После этого rel_path гарантированно относительный
    result = os.path.normpath(os.path.join(music_dir, rel_path))
    if is_log_type_enabled("model"):
        model_logger.debug(f"[PATH][UTILS] safe_join_music_dir: music_dir={music_dir}, rel_path={rel_path}, result={result}, exists={os.path.exists(result)}, isfile={os.path.isfile(result)}")
    return result

    return os.path.normpath(os.path.join(music_dir, rel_path))

def normalize_audio_filename(fname):
    """
    Универсальная нормализация имени аудиофайла для корректного сопоставления признаков:
    - Использует только имя файла без пути и расширения.
    - Приводит к нижнему регистру.
    - Заменяет пробелы и спецсимволы (тире, точки, запятые, доллары, амперсанды) на "_".
    - Удаляет все символы кроме букв, цифр, "_" (в т.ч. кириллицу).
    - Сводит подряд идущие "_" к одному.
    - Удаляет начальные и конечные "_".
    Использовать для сравнения треков между обучением, тестом, сканированием и поиском похожих.
    """
    name = os.path.splitext(os.path.basename(str(fname)))[0]
    name = name.lower()
    name = re.sub(r'[\s\-\,\.\$\&]+', '_', name)
    name = re.sub(r'[^a-z0-9_а-яё]', '', name)
    name = re.sub(r'_+', '_', name)
    return name.strip('_')

def filter_duplicate_tracks_by_norm_key(tracks, get_path_fn=lambda x: x, logger=None):
    """
    Убирает дубликаты по norm_key из списка tracks.
    tracks: list (может быть список tuple, dict, str — см. get_path_fn)
    get_path_fn: функция, возвращающая путь к файлу для нормализации (по умолчанию: элемент сам — строка)
    logger: логгер для вывода инфо/предупреждений
    Возвращает: новый список без дубликатов
    """
    from .utils import normalize_audio_filename
    norm_key_map = {}
    unique_tracks = []
    for item in tracks:
        path = get_path_fn(item)
        norm_key = normalize_audio_filename(path)
        if norm_key in norm_key_map:
            if logger:
                logger.warning(f"[DUPLICATE] Дубликат norm_key: {norm_key} для файла {path} (первый: {norm_key_map[norm_key]}) — будет исключён из обучающей выборки!")
        else:
            norm_key_map[norm_key] = path
            unique_tracks.append(item)
    if logger:
        logger.info(f"[DUPLICATE] Фильтрация завершена. Было: {len(tracks)}, стало: {len(unique_tracks)}, дубликатов: {len(tracks) - len(unique_tracks)}")
    return unique_tracks

def plot_learning_curve_for_genre_model(
    model_class=None,
    model_kwargs=None,
    scoring='accuracy',
    output_dir=None
):
    """
    Строит learning curve для жанровой модели, сохраняет результаты в json и png.
    model_class — класс модели (по умолчанию RandomForestClassifier)
    model_kwargs — dict с параметрами для модели
    scoring — метрика ('accuracy', 'f1_macro' и т.д.)
    output_json — путь к json-файлу с результатами
    output_png — путь к png-файлу с графиком
    """
    import pickle
    from sklearn.ensemble import RandomForestClassifier
    import matplotlib.pyplot as plt
    from sklearn.model_selection import learning_curve
    # Загружаем признаки и метки обучения
    with open(MODEL_FILE, "rb") as f:
        model_meta = pickle.load(f)
    X = []
    y = []
    train_features_dict = model_meta.get("train_features_dict", {})
    labels = model_meta.get("labels")
    if labels is not None and len(labels) > 0:
        y = labels
        for feat in train_features_dict.values():
            X.append(feat)
    else:
        logging.info("⚠️ Нет labels в model_meta. Learning curve будет некорректен.")
        return

    X = np.array(X)
    y = np.array(y)
    n_tracks = len(X)

    if model_class is None:
        model_class = RandomForestClassifier
    if model_kwargs is None:
        saved_params = model_meta.get("train_params", {})
        allowed_params = {
            "n_estimators", "max_depth", "min_samples_leaf", "max_features",
            "class_weight", "random_state", "n_jobs"
        }
        model_kwargs = {k: v for k, v in saved_params.items() if k in allowed_params}

    train_sizes = np.linspace(0.1, 1.0, 6)
    train_sizes_abs, train_scores, val_scores = learning_curve(
        model_class(**model_kwargs),
        X, y,
        train_sizes=train_sizes,
        cv=3,
        scoring=scoring,
        shuffle=True,
        random_state=42
    )

    train_scores_mean = np.mean(train_scores, axis=1)
    val_scores_mean = np.mean(val_scores, axis=1)

    # ==== Сохраняем в папку с уникальным именем ====
    output_dir = str(output_dir or LEARNING_CURVES_DIR)
    os.makedirs(output_dir, exist_ok=True)
    now_str = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    file_base = f"learning_curve_{now_str}_({n_tracks})"
    output_json = os.path.join(output_dir, file_base + ".json")
    output_png = os.path.join(output_dir, file_base + ".png")

    # Сохраняем JSON
    result = {
        'train_sizes': train_sizes_abs.tolist(),
        'train_scores': train_scores_mean.tolist(),
        'val_scores': val_scores_mean.tolist(),
        'total_tracks': n_tracks
    }
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    # Сохраняем PNG
    plt.figure(figsize=(8, 5))
    plt.plot(train_sizes_abs, train_scores_mean, 'o-', label='Train')
    plt.plot(train_sizes_abs, val_scores_mean, 'o-', label='Validation')
    plt.xlabel('Число обучающих треков')
    plt.ylabel(scoring)
    plt.title('Learning curve')
    plt.legend()
    plt.grid()
    plt.savefig(output_png)
    plt.close()

    logging.info(f"Learning curve сохранён в {output_json} и {output_png}")

def estimate_avg_task_ram_by_settings(librosa_params, file_path=None):
    """
    Оценивает RAM на обработку задачи по настройкам анализа.
    Если file_path задан, пытается определить длительность и каналы.
    """
    sample_rate = librosa_params.get("sample_rate", 22050)
    duration = librosa_params.get("duration", 30)
    channels = 2 if librosa_params.get("stereo", False) else 1
    # Если известен файл — попытаться оценить длительность и каналы (можно расширить)
    if file_path:
        try:
            import librosa
            y, sr = librosa.load(file_path, sr=sample_rate, duration=duration)
            channels = 1 if y.ndim == 1 else y.shape[0]
            duration = min(librosa.get_duration(y=y, sr=sr), duration)
        except Exception:
            pass
    # RAM массива аудио + запас на признаки и overhead
    ram_gb = sample_rate * duration * channels * 4 / (1024**3) * 1.5  # 1.5 — запас на признаки
    return max(ram_gb, 0.3)  # минимум 0.3 ГБ

def get_dynamic_max_workers_by_settings(librosa_params, priority='low', min_workers=1, file_path=None):
    """
    Возвращает оптимальное число воркеров для аудиоанализа, предупреждение и флаг критичности.

    Аргументы:
    - librosa_params: dict с настройками аудиоанализа (sample_rate, duration, признаки и пр.)
    - priority: режим загрузки ('low', 'medium', 'high') — влияет на safe_ratio (доля используемой RAM/CPU)
    - min_workers: минимальное число воркеров (по умолчанию 1)
    - file_path: путь к аудиофайлу (опционально, для точной оценки RAM)

    Внутренняя логика:
    - safe_ratio выбирается по PRIORITY_MAP (low — более консервативный, high — агрессивный).
    - free_gb: доступно RAM (psutil.virtual_memory().available).
    - RESERVED_GB: резерв для ОС и других процессов (по умолчанию 4 ГБ — не используем для воркеров).
    - usable_gb: реально доступная память для задач (free_gb - RESERVED_GB, минимум 1 ГБ).
    - avg_task_ram_gb: оценка среднего потребления RAM на воркер (estimate_avg_task_ram_by_settings).
    - max_workers_ram: сколько воркеров можно запустить по RAM.
    - max_workers_cpu: сколько воркеров можно запустить по CPU.
    - MAX_WORKER_LIMIT: жёсткий лимит воркеров (например, не более 6).
    - max_workers: итоговое число воркеров, не более лимитов и min_workers.

    Возвращает:
    - max_workers: итоговое число воркеров.
    - warning: текст предупреждения для пользователя.
    - critical: флаг критичности (True — нельзя запускать сканирование).
    """
    PRIORITY_MAP = {
        'low': (0.20, 0.30),      # более консервативно
        'medium': (0.40, 0.60),
        'high': (0.60, 0.80)      # высоко, но не 0.95!
    }
    RESERVED_GB = 4              # резервируем для ОС и других приложений
    MAX_WORKER_LIMIT = 6         # жёсткий лимит воркеров (можно уменьшить для стабильности)

    safe_ratio = PRIORITY_MAP.get(priority, (0.40, 0.60))[1]
    free_gb = psutil.virtual_memory().available / (1024 ** 3)
    total_gb = psutil.virtual_memory().total / (1024 ** 3)
    usable_gb = max(free_gb - RESERVED_GB, 1)  # гарантируем минимум 1 ГБ
    cpu_total = multiprocessing.cpu_count()

    avg_task_ram_gb = estimate_avg_task_ram_by_settings(librosa_params, file_path)
    max_workers_ram = int((usable_gb * safe_ratio) // avg_task_ram_gb)
    max_workers_cpu = int(cpu_total * safe_ratio)
    max_workers = max(
        min_workers,
        min(max_workers_ram, max_workers_cpu, MAX_WORKER_LIMIT, cpu_total)
    )

    warning = ""
    critical = False

    # Диагностика и логирование
    try:
        if is_log_type_enabled("resource"):
            resource_logger.info(
                f"[DYNAMIC WORKERS] PRIORITY={priority}, SAFE_RATIO={safe_ratio}, "
                f"CPU_TOTAL={cpu_total}, FREE_GB={free_gb:.2f}, RESERVED_GB={RESERVED_GB}, USABLE_GB={usable_gb:.2f}, "
                f"AVG_TASK_RAM_GB={avg_task_ram_gb:.2f}, "
                f"MAX_WORKERS_RAM={max_workers_ram}, MAX_WORKERS_CPU={max_workers_cpu}, "
                f"MAX_WORKER_LIMIT={MAX_WORKER_LIMIT}, FINAL_MAX_WORKERS={max_workers}"
            )
    except Exception:
        pass

    if max_workers <= min_workers and usable_gb < avg_task_ram_gb:
        warning = "Критически мало памяти для выбранных настроек аудиоанализа! Рекомендуется закрыть лишние приложения или уменьшить качество анализа."
        critical = True
    elif max_workers < 2:
        warning = "Недостаточно ресурсов, сканирование будет идти медленно."
    elif avg_task_ram_gb > 1.5:
        warning = "Настройки аудиоанализа слишком тяжелые! Время обработки будет увеличено."

    # Логирование предупреждений
    try:
        if is_log_type_enabled("resource"):
            resource_logger.info(
                f"[DYNAMIC WORKERS] Выбрано число воркеров: {max_workers} "
                f"(режим: {priority}, safe_ratio: {safe_ratio}, доступно: {free_gb:.2f} ГБ RAM, "
                f"резерв: {RESERVED_GB} ГБ, реально используем: {usable_gb:.2f} ГБ, "
                f"всего RAM: {total_gb:.2f} ГБ, ядер: {cpu_total}, "
                f"средний RAM на задачу: {avg_task_ram_gb:.2f} ГБ)"
            )
            if warning:
                status_logger.warning(f"[SCAN][RESOURCES] {warning}")
    except Exception:
        pass

    return max_workers, warning, critical

def save_bad_file_info(file_path, rel_path=None, json_path="bad_files.json"):
    """
    Сохраняет информацию о битом файле (путь и имя) в отдельный JSON-файл. Используется в сканировании библиоткеи.
    Не дублирует записи, если файл уже в списке.
    """
    import json
    import os

    if not os.path.isabs(json_path):
        json_path = str(PROJECT_DIR / json_path)

    entry = {
        "file_path": file_path,
        "rel_path": rel_path if rel_path is not None else os.path.basename(file_path)
    }

    # Загружаем существующие записи
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                bad_files = json.load(f)
        except Exception:
            bad_files = []
    else:
        bad_files = []

    # Не дублируем записи
    if not any(x['file_path'] == entry['file_path'] for x in bad_files):
        bad_files.append(entry)
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(bad_files, f, ensure_ascii=False, indent=2)
        except Exception as e:
            # Можно добавить лог ошибки сохранения, если нужно
            pass
