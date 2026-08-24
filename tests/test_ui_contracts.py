from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(relative):
    return (ROOT / relative).read_text(encoding="utf-8")


def test_pipeline_cards_have_distinct_status_aware_controls():
    html = _text("templates/settings.html")
    javascript = _text("static/js/settings.js")
    for element_id in (
        "main-index-start", "main-index-stop", "main-index-card-status",
        "language-index-start", "language-index-stop", "language-card-status",
        "deep-index-start", "deep-index-stop", "deep-index-card-status",
    ):
        assert f'id="{element_id}"' in html
    for label in (
        "Индекс выполняется", "Приостановить", "Проверить новые треки",
        "Повторить / продолжить", "Уточнение выполняется", "Язык готов",
        "Индекс готов",
    ):
        assert label in javascript


def test_already_scanning_does_not_show_started_modal():
    javascript = _text("static/js/settings.js")
    guard = javascript.index("data.status === 'already scanning'")
    started_modal = javascript.index("showScanStatusModal('Сканирование запущено!", guard)
    assert guard < started_modal
    branch = javascript[guard:started_modal]
    assert "updateScanProgress()" in branch
    assert "return;" in branch


def test_smart_catalog_has_safe_counts_and_single_request_guard():
    javascript = _text("static/js/catalog_intelligence.js")
    assert "let matchInProgress = false" in javascript
    assert "if (matchInProgress) return" in javascript
    assert "Number(data.candidate_count || 0)" in javascript
    assert "Array.isArray(data.references_used)" in javascript
    assert "Array.isArray(data.missing_references)" in javascript
    assert "Ничего не найдено по текущим условиям" in javascript


def test_active_model_ui_uses_manifest_not_pickle():
    python = _text("app/librosa_settings.py")
    html = _text("templates/librosa/librosa_settings.html")
    assert "_load_active_model_manifest" in python
    assert "pickle.load" not in python
    assert "Активная жанровая модель" in html


def test_processing_ui_uses_persistent_effective_state():
    routes = _text("app/routes.py")
    javascript = _text("static/js/settings.js")
    html = _text("templates/settings.html")
    assert 'load_catalog_state("library_scan"' in routes
    assert '"saved_scan_state": saved_scan_state' in routes
    assert '"effective_state": {' in routes
    assert '"effective_status": effective_stage_status(' in routes
    assert "deriveEffectiveStageStatus" in javascript
    assert "deriveEffectiveProcessingState" in javascript
    assert "deriveEffectiveDeepState" in javascript
    assert "queueCompleted" in javascript
    assert "Проверить новые треки" in javascript
    assert "force_continue: forceContinue" in javascript
    assert 'request_data.get("force_continue") is True' in routes
    assert "Number(language.failed || 0) === 0" in javascript
    assert 'action === "retry_language"' in javascript
    assert 'action === "retry_deep"' in javascript
    for element_id in (
        "language-index-start-compact",
        "language-index-stop-compact",
        "language-index-retry-compact",
    ):
        assert f'id="{element_id}"' in html


def test_full_rescan_is_one_shot_dangerous_action_not_persistent_mode():
    html = _text("templates/settings.html")
    javascript = _text("static/js/settings.js")
    routes = _text("app/routes.py")
    config = _text("app/config.py")

    assert 'id="scanModeSelect"' not in html
    assert 'id="full-rescan-start"' in html
    assert "Опасные действия" in html
    assert "Удалить индекс и пересканировать с нуля" in html
    assert "function startFullRescan()" in javascript
    assert "window.prompt('Для подтверждения полного сброса введите слово СБРОС')" in javascript
    assert "force_new: forceNew" in javascript
    assert "const forceContinue = !forceNew" in javascript
    assert 'force_new = request_data.get("force_new") is True' in routes
    assert 'mode = "new" if force_new else "continue"' in routes
    assert 'if scan_mode != "continue":' in routes
    assert '"scan_mode": "continue"' in config


def test_smart_catalog_uses_stable_result_layout_and_distinct_states():
    html = _text("templates/intelligence.html")
    javascript = _text("static/js/catalog_intelligence.js")
    css = _text("static/css/styles.css")
    assert "catalog-results-table" in html
    assert "table-hover" not in html
    for token in (
        "catalog-result-row",
        "catalog-result-details",
        "catalog-result-details-panel",
        "score-explanation-toggle",
        "is-selected",
        "is-playing",
    ):
        assert token in html + javascript
    assert "toggleScoreDetails" in javascript
    assert "selectResultRow" in javascript
    assert "catalog-theme" in javascript
    assert ".smart-catalog-panel" in css
    assert "background: #fff" in css
    assert ".catalog-result-row > td { background: var(--catalog-surface); box-shadow: none !important; transition: none; }" in html
    assert ".catalog-result-row > td:first-child::before" in html
    assert "transition: background-color .14s, box-shadow .14s" not in html


def test_smart_catalog_result_headers_are_real_sort_controls():
    html = _text("templates/intelligence.html")
    javascript = _text("static/js/catalog_intelligence.js")
    for key in ("track", "category", "character", "similarity"):
        assert f'data-sort-key="{key}"' in html
    assert 'aria-sort="descending"' in html
    assert "changeResultSort" in javascript
    assert "sortedResultItems" in javascript
    assert "renderSortHeaders" in javascript
