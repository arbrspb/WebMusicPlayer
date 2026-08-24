"""Build a user-facing readiness summary for the optional analysis pipeline."""
from __future__ import annotations


def _clip(value):
    return max(0.0, min(float(value or 0.0), 1.0))


def _stage(stage_id, title, status, coverage, detail, *, enabled=True):
    return {
        "id": stage_id,
        "title": title,
        "status": status,
        "coverage": round(_clip(coverage), 6),
        "detail": detail,
        "enabled": bool(enabled),
    }


_ACTIVE_RUNTIME_STATUSES = {"queued", "preparing", "in_progress", "indexing", "downloading", "queued_download"}


def effective_stage_status(
    *, runtime_status="idle", running=False, enabled=True,
    pending=0, processing=0, failed=0, completed=0, total=0,
    persistent_completed=False,
):
    """Resolve one display state from runtime and persistent counters.

    The result uses the status vocabulary consumed by the processing UI:
    ``in_progress``, ``error``, ``stopped``, ``completed``, ``idle`` and
    ``disabled``.  This helper is deliberately read-only; it does not prepare
    queues or modify stage metadata.
    """
    runtime_status = str(runtime_status or "idle").strip().lower()
    pending = max(0, int(pending or 0))
    processing = max(0, int(processing or 0))
    failed = max(0, int(failed or 0))
    completed = max(0, int(completed or 0))
    total = max(0, int(total or 0))

    if not enabled or runtime_status == "disabled":
        return "disabled"
    if running or processing > 0 or runtime_status in _ACTIVE_RUNTIME_STATUSES:
        return "in_progress"
    if failed > 0 or runtime_status == "error":
        return "error"
    if pending > 0:
        return "stopped"
    if (
        persistent_completed
        or runtime_status == "completed"
        or (total > 0 and completed >= total)
    ):
        return "completed"
    if runtime_status == "stopped":
        return "stopped"
    return "idle"


def build_collection_health(
    *, scan_tracks, live_scan, saved_scan, catalog, language, deep,
    personalization, pipeline, analysis,
):
    """Return stages, approximate tool readiness and the next useful action."""
    scan_tracks = max(0, int(scan_tracks or 0))
    live_scan = live_scan or {}
    saved_scan = saved_scan or {}
    live_status = str(live_scan.get("status") or "idle")
    live_total = max(0, int(live_scan.get("total") or 0))
    live_scanned = max(0, int(live_scan.get("scanned") or 0))
    saved_complete = (
        saved_scan.get("status") == "completed"
        and int(saved_scan.get("scan_tracks") or -1) == scan_tracks
        and scan_tracks > 0
    )
    main_effective = effective_stage_status(
        runtime_status=live_status,
        running=live_status == "in_progress",
        failed=1 if live_status == "error" else 0,
        persistent_completed=saved_complete,
    )
    scan_running = main_effective == "in_progress"
    if scan_running:
        base_coverage = live_scanned / live_total if live_total else 0.05
        base_status = "running"
        base_detail = (
            f"{live_scanned:,} из {live_total:,} треков" if live_total
            else "Подсчитываем файлы в библиотеке"
        )
    elif main_effective == "error":
        base_coverage = live_scanned / live_total if live_total else 0.0
        base_status = "error"
        base_detail = str(live_scan.get("error_message") or "Ошибка основного индекса")
    elif main_effective == "completed":
        base_coverage = 1.0
        base_status = "ready"
        base_detail = f"В основном индексе {scan_tracks:,} треков"
    elif scan_tracks:
        # Existing databases from versions before persistent stage state need
        # one Continue pass to confirm that the library has no missing files.
        base_coverage = 0.65
        base_status = "check"
        base_detail = f"В базе {scan_tracks:,} треков; требуется проверка режимом «Продолжить»"
    else:
        base_coverage = 0.0
        base_status = "pending"
        base_detail = "Основной индекс ещё не создан"

    stages = [_stage("main", "Основной индекс", base_status, base_coverage, base_detail)]

    catalog_coverage = _clip(catalog.get("coverage", 0.0))
    catalog_pending = max(0, int(catalog.get("pending_tracks") or 0))
    catalog_status = "ready" if scan_tracks and catalog_coverage >= 0.995 and not catalog_pending else "pending"
    stages.append(_stage(
        "catalog", "Интеллектуальный каталог", catalog_status, catalog_coverage,
        f"Профили: {int(catalog.get('profiled_tracks') or 0):,} из {scan_tracks:,}",
    ))

    whisper_enabled = bool(analysis.get("vocal_language_enabled", False))
    language_pending = max(0, int(language.get("pending") or 0))
    language_processing = max(0, int(language.get("processing") or 0))
    language_failed = max(0, int(language.get("failed") or 0))
    language_completed = max(0, int(language.get("completed") or 0))
    language_not_needed = max(0, int(language.get("not_needed") or 0))
    language_done = language_completed + language_not_needed
    language_queue_total = (
        language_pending + language_processing + language_failed + language_done
    )
    language_coverage = language_done / language_queue_total if language_queue_total else 0.0
    language_effective = effective_stage_status(
        runtime_status=language.get("status", "idle"),
        running=bool(language.get("running")),
        enabled=whisper_enabled,
        pending=language_pending,
        processing=language_processing,
        failed=language_failed,
        completed=language_done,
        total=language_queue_total,
    )
    language_status = {
        "disabled": "disabled", "in_progress": "running",
        "error": "error", "completed": "ready",
    }.get(language_effective, "pending")
    stages.append(_stage(
        "language", "Язык вокала", language_status,
        1.0 if not whisper_enabled else language_coverage,
        "Отключён — не влияет на готовность" if not whisper_enabled else
        f"Уточнено или не требуется: {language_done:,} из {language_queue_total:,}",
        enabled=whisper_enabled,
    ))

    deep_enabled = bool(pipeline.get("effnet_enabled", False))
    deep_coverage = _clip(deep.get("coverage", 0.0))
    deep_total = max(0, int(deep.get("total") or scan_tracks or 0))
    deep_completed = max(0, int(deep.get("completed") or 0))
    deep_pending = max(0, int(deep.get("pending") or 0))
    deep_failed = max(0, int(deep.get("errors") or 0))
    deep_effective = effective_stage_status(
        runtime_status=(deep.get("progress") or {}).get("status", "idle"),
        running=bool(deep.get("running")),
        enabled=deep_enabled,
        pending=deep_pending,
        processing=1 if deep.get("running") else 0,
        failed=deep_failed,
        completed=deep_completed,
        total=deep_total,
    )
    deep_status = {
        "disabled": "disabled", "in_progress": "running",
        "error": "error", "completed": "ready",
    }.get(deep_effective, "pending")
    stages.append(_stage(
        "deep", "Глубокая похожесть", deep_status,
        1.0 if not deep_enabled else deep_coverage,
        "Отключена — используется базовая акустика" if not deep_enabled else
        f"EffNet: {deep_completed:,} из {deep_total:,}",
        enabled=deep_enabled,
    ))

    taste_enabled = bool(
        pipeline.get("player_ratings_enabled", True) or pipeline.get("rekordbox_enabled", False)
    )
    rated_tracks = int(personalization.get("rated_tracks") or 0)
    predicted_tracks = int(personalization.get("predicted_tracks") or 0)
    catalog_tracks = int(personalization.get("catalog_tracks") or 0)
    personal_running = bool(personalization.get("running"))
    personal_ready = bool(
        personalization.get("model_exists") and catalog_tracks
        and predicted_tracks >= catalog_tracks * 0.98
        and int(personalization.get("pending_predictions") or 0) == 0
    )
    taste_coverage = predicted_tracks / catalog_tracks if catalog_tracks else 0.0
    taste_status = (
        "disabled" if not taste_enabled else "running" if personal_running else
        "ready" if personal_ready else "pending"
    )
    stages.append(_stage(
        "taste", "Личный вкус", taste_status,
        1.0 if not taste_enabled else taste_coverage,
        "Отключён" if not taste_enabled else
        f"Явных оценок: {rated_tracks:,}; прогнозов: {predicted_tracks:,}",
        enabled=taste_enabled,
    ))

    active_stages = [stage for stage in stages if stage["enabled"]]
    readiness = (
        sum(stage["coverage"] for stage in active_stages) / len(active_stages)
        if active_stages else 1.0
    )
    if scan_running:
        action = {"id": "monitor", "label": "Показать ход обработки", "detail": "Основной индекс сейчас выполняется"}
    elif main_effective != "completed":
        action = {"id": "continue_main", "label": "Продолжить основной индекс", "detail": "Проверим библиотеку и добавим только отсутствующие треки"}
    elif catalog_status != "ready":
        action = {"id": "sync_catalog", "label": "Обновить интеллектуальный каталог", "detail": "Повторно читать MP3 для этого не нужно"}
    elif whisper_enabled and language_status == "error":
        action = {"id": "retry_language", "label": "Повторить ошибки языка", "detail": "Повторно обработаем только неудачные записи Whisper"}
    elif whisper_enabled and language_status != "ready":
        action = {"id": "continue_language", "label": "Продолжить определение языка", "detail": "Whisper обработает оставшуюся очередь"}
    elif deep_enabled and deep_status == "error":
        action = {"id": "retry_deep", "label": "Повторить ошибки EffNet", "detail": "Повторно обработаем только неудачные векторы"}
    elif deep_enabled and deep_status != "ready":
        action = {"id": "continue_deep", "label": "Продолжить глубокий индекс", "detail": "EffNet обработает только недостающие векторы"}
    elif taste_enabled and rated_tracks < 100:
        action = {"id": "open_ratings", "label": "Добавить оценки трекам", "detail": "Для уверенной модели желательно не менее 100 оценок"}
    elif taste_enabled and not personal_ready:
        action = {"id": "train_personal", "label": "Обновить модель вкуса", "detail": "Применим ваши оценки ко всей коллекции"}
    else:
        action = {"id": "open_catalog", "label": "Перейти к умному подбору", "detail": "Все включённые инструменты готовы"}

    return {
        "readiness": round(_clip(readiness), 6),
        "status": "ready" if all(stage["status"] in {"ready", "disabled"} for stage in stages) else (
            "running" if any(stage["status"] == "running" for stage in stages) else
            "error" if any(stage["status"] == "error" for stage in stages) else "attention"
        ),
        "stages": stages,
        "next_action": action,
        "scan_tracks": scan_tracks,
    }
