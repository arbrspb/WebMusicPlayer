"""Durable second-stage vocal-language enrichment for the scanned library."""

import logging
import os
import time

from .db import (
    claim_next_language_enrichment,
    fail_language_enrichment,
    finish_language_enrichment,
    get_language_enrichment_stats,
    prepare_language_enrichment_queue,
)
from .vocal_language import detect_vocal_language, vocal_language_backend_status


logger = logging.getLogger(__name__)


# def _safe_music_path(music_dir, rel_path):
#     root = os.path.abspath(os.fspath(music_dir))
#     candidate = os.path.abspath(os.path.join(root, os.fspath(rel_path)))
#     if os.path.commonpath([root, candidate]) != root:
#         raise ValueError("Путь трека выходит за пределы музыкальной библиотеки")
#     return candidate

# Заменил на новую 16-08-26 проверить
def _safe_music_path(music_dir, rel_path):
    root = os.path.abspath(os.fspath(music_dir))
    relative = os.fspath(rel_path)
    if ".." in relative.replace("/", "\\").split("\\"):
        raise ValueError("Путь трека выходит за пределы музыкальной библиотеки")
    candidate = os.path.abspath(os.path.join(root, relative))

    # os.path.commonpath() на Windows некорректно обрабатывает
    # корень UNC-шары вида \\server\share и может выдавать:
    # ValueError: Can't mix absolute and relative paths.
    # Поэтому проверяем принадлежность пути библиотеке через
    # нормализованный регистронезависимый префикс.
    root_cmp = os.path.normcase(root).rstrip("\\/")
    candidate_cmp = os.path.normcase(candidate)

    if candidate_cmp != root_cmp and not candidate_cmp.startswith(root_cmp + os.sep):
        raise ValueError("Путь трека выходит за пределы музыкальной библиотеки")

    return candidate


def _load_language_segments(path, settings):
    """Decode short 16 kHz fragments without repeating genre feature extraction."""
    import librosa

    raw_offsets = settings.get("multi_segment_offsets", "30,60,90")
    offsets = []
    for value in str(raw_offsets or "30,60,90").replace(";", ",").split(","):
        try:
            offset = max(0.0, float(value.strip()))
        except (TypeError, ValueError):
            continue
        if offset not in offsets:
            offsets.append(offset)
    if not offsets:
        offsets = [30.0, 60.0, 90.0]
    duration = max(5.0, float(settings.get("multi_segment_duration", 15) or 15))
    segments = []
    for offset in offsets[:3]:
        audio, sample_rate = librosa.load(
            path,
            sr=16000,
            mono=True,
            offset=offset,
            duration=duration,
        )
        if audio is not None and getattr(audio, "size", 0):
            segments.append((audio, sample_rate, offset))
    if not segments:
        raise ValueError("Не удалось прочитать аудиофрагменты для определения языка")
    return segments


def run_language_enrichment(music_dir, settings, stop_event, progress):
    """Process queued rows with one persistent Whisper model."""
    progress.clear()
    progress.update({
        "status": "preparing",
        "processed": 0,
        "total": 0,
        "current_track": "",
        "error": "",
        "started_at": time.time(),
    })
    if not bool(settings.get("vocal_language_enabled", False)):
        progress["status"] = "disabled"
        progress.update(get_language_enrichment_stats())
        return

    try:
        stats = prepare_language_enrichment_queue(enabled=True)
        progress.update(stats)
        progress["status"] = "in_progress"
        while not stop_event.is_set():
            rel_path = claim_next_language_enrichment()
            if not rel_path:
                progress.update(get_language_enrichment_stats())
                progress["status"] = "completed"
                progress["current_track"] = ""
                return
            progress["current_track"] = rel_path
            try:
                full_path = _safe_music_path(music_dir, rel_path)
                if not os.path.isfile(full_path):
                    raise FileNotFoundError(full_path)
                segments = _load_language_segments(full_path, settings)
                result = detect_vocal_language(
                    full_path,
                    settings=settings,
                    audio_segments=segments,
                )
                finish_language_enrichment(rel_path, result)
                progress["runtime"] = vocal_language_backend_status(settings)
            except Exception as exc:
                logger.warning("Language enrichment failed for %s: %s", rel_path, exc)
                fail_language_enrichment(rel_path, exc)
            progress.update(get_language_enrichment_stats())
        progress.update(get_language_enrichment_stats())
        progress["status"] = "stopped"
        progress["current_track"] = ""
    except Exception as exc:
        logger.exception("Language enrichment worker failed: %s", exc)
        progress["status"] = "error"
        progress["error"] = str(exc)
        progress.update(get_language_enrichment_stats())
