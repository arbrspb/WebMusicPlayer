from app.collection_health import build_collection_health, effective_stage_status


def _health(**overrides):
    values = {
        "scan_tracks": 0,
        "live_scan": {"status": "idle", "scanned": 0, "total": 0},
        "saved_scan": {},
        "catalog": {"coverage": 0.0, "profiled_tracks": 0, "pending_tracks": 0},
        "language": {"status": "idle", "completed": 0, "not_needed": 0, "running": False},
        "deep": {"coverage": 0.0, "completed": 0, "running": False, "progress": {}},
        "personalization": {
            "model_exists": False, "rated_tracks": 0, "predicted_tracks": 0,
            "catalog_tracks": 0, "pending_predictions": 0, "running": False,
        },
        "pipeline": {"effnet_enabled": False, "player_ratings_enabled": False, "rekordbox_enabled": False},
        "analysis": {"vocal_language_enabled": False},
    }
    values.update(overrides)
    return build_collection_health(**values)


def test_empty_collection_recommends_main_index():
    result = _health()
    assert result["next_action"]["id"] == "continue_main"
    assert result["stages"][0]["status"] == "pending"


def test_legacy_nonempty_database_requires_one_safe_continue_pass():
    result = _health(scan_tracks=2500)
    assert result["next_action"]["id"] == "continue_main"
    assert result["stages"][0]["status"] == "check"
    assert result["stages"][0]["coverage"] == 0.65


def test_optional_disabled_stages_do_not_reduce_readiness():
    result = _health(
        scan_tracks=100,
        live_scan={"status": "completed", "scanned": 100, "total": 100},
        saved_scan={"status": "completed", "scan_tracks": 100},
        catalog={"coverage": 1.0, "profiled_tracks": 100, "pending_tracks": 0},
    )
    assert result["status"] == "ready"
    assert result["readiness"] == 1.0
    assert result["next_action"]["id"] == "open_catalog"


def test_pipeline_action_order_is_catalog_then_language_then_deep_then_taste():
    common = {
        "scan_tracks": 100,
        "live_scan": {"status": "completed", "scanned": 100, "total": 100},
        "saved_scan": {"status": "completed", "scan_tracks": 100},
        "analysis": {"vocal_language_enabled": True},
        "pipeline": {"effnet_enabled": True, "player_ratings_enabled": True, "rekordbox_enabled": False},
    }
    result = _health(**common)
    assert result["next_action"]["id"] == "sync_catalog"

    result = _health(
        **common,
        catalog={"coverage": 1.0, "profiled_tracks": 100, "pending_tracks": 0},
    )
    assert result["next_action"]["id"] == "continue_language"

    result = _health(
        **common,
        catalog={"coverage": 1.0, "profiled_tracks": 100, "pending_tracks": 0},
        language={"status": "completed", "completed": 100, "not_needed": 0, "running": False},
    )
    assert result["next_action"]["id"] == "continue_deep"


def test_idle_whisper_is_ready_when_persistent_queue_is_complete():
    result = _health(
        scan_tracks=100,
        live_scan={"status": "stopped", "scanned": 0, "total": 0},
        saved_scan={"status": "completed", "scan_tracks": 100},
        catalog={"coverage": 1.0, "profiled_tracks": 100, "pending_tracks": 0},
        analysis={"vocal_language_enabled": True},
        language={
            "status": "idle", "pending": 0, "processing": 0,
            "failed": 0, "completed": 82, "not_needed": 18,
            "running": False,
        },
    )
    stages = {stage["id"]: stage for stage in result["stages"]}
    assert stages["main"]["status"] == "ready"
    assert stages["language"]["status"] == "ready"


def test_whisper_failures_remain_visible_after_restart():
    result = _health(
        scan_tracks=100,
        saved_scan={"status": "completed", "scan_tracks": 100},
        catalog={"coverage": 1.0, "profiled_tracks": 100, "pending_tracks": 0},
        analysis={"vocal_language_enabled": True},
        language={
            "status": "idle", "pending": 0, "processing": 0,
            "failed": 2, "completed": 80, "not_needed": 18,
            "running": False,
        },
    )
    stages = {stage["id"]: stage for stage in result["stages"]}
    assert stages["language"]["status"] == "error"
    assert result["status"] == "error"


def test_effective_state_priority_is_running_error_pending_completed_idle():
    assert effective_stage_status(
        runtime_status="idle", running=True, failed=2, pending=3,
        completed=10, total=10, persistent_completed=True,
    ) == "in_progress"
    assert effective_stage_status(
        runtime_status="idle", failed=2, pending=3, completed=10, total=10,
    ) == "error"
    assert effective_stage_status(
        runtime_status="idle", pending=3, completed=10, total=10,
    ) == "stopped"
    assert effective_stage_status(
        runtime_status="idle", completed=10, total=10,
    ) == "completed"
    assert effective_stage_status(runtime_status="idle") == "idle"


def test_processing_counter_has_running_priority_after_restart():
    assert effective_stage_status(
        runtime_status="idle", processing=1, pending=4, failed=1,
    ) == "in_progress"


def test_active_scan_overrides_persistent_completion():
    result = _health(
        scan_tracks=100,
        live_scan={"status": "in_progress", "scanned": 7, "total": 120},
        saved_scan={"status": "completed", "scan_tracks": 100},
    )
    stages = {stage["id"]: stage for stage in result["stages"]}
    assert stages["main"]["status"] == "running"
    assert result["next_action"]["id"] == "monitor"


def test_paused_scan_without_persistent_completion_can_continue():
    result = _health(
        scan_tracks=40,
        live_scan={"status": "stopped", "scanned": 40, "total": 100},
        saved_scan={"status": "stopped", "scan_tracks": 40},
    )
    stages = {stage["id"]: stage for stage in result["stages"]}
    assert stages["main"]["status"] == "check"
    assert result["next_action"]["id"] == "continue_main"


def test_whisper_pending_and_processing_states_after_restart():
    common = {
        "scan_tracks": 100,
        "saved_scan": {"status": "completed", "scan_tracks": 100},
        "catalog": {"coverage": 1.0, "profiled_tracks": 100, "pending_tracks": 0},
        "analysis": {"vocal_language_enabled": True},
    }
    pending = _health(**common, language={
        "status": "idle", "pending": 5, "processing": 0,
        "failed": 0, "completed": 95, "not_needed": 0, "running": False,
    })
    processing = _health(**common, language={
        "status": "idle", "pending": 4, "processing": 1,
        "failed": 0, "completed": 95, "not_needed": 0, "running": False,
    })
    assert {stage["id"]: stage for stage in pending["stages"]}["language"]["status"] == "pending"
    assert pending["next_action"]["id"] == "continue_language"
    assert {stage["id"]: stage for stage in processing["stages"]}["language"]["status"] == "running"


def test_effnet_completed_after_restart_uses_persistent_counters():
    result = _health(
        scan_tracks=100,
        saved_scan={"status": "completed", "scan_tracks": 100},
        catalog={"coverage": 1.0, "profiled_tracks": 100, "pending_tracks": 0},
        pipeline={"effnet_enabled": True, "player_ratings_enabled": False, "rekordbox_enabled": False},
        deep={
            "total": 100, "coverage": 1.0, "completed": 100,
            "pending": 0, "errors": 0, "running": False,
            "progress": {"status": "idle"},
        },
    )
    stages = {stage["id"]: stage for stage in result["stages"]}
    assert stages["deep"]["status"] == "ready"
    assert result["next_action"]["id"] == "open_catalog"


def test_effnet_failures_offer_retry_and_pending_offers_continue():
    common = {
        "scan_tracks": 100,
        "saved_scan": {"status": "completed", "scan_tracks": 100},
        "catalog": {"coverage": 1.0, "profiled_tracks": 100, "pending_tracks": 0},
        "pipeline": {"effnet_enabled": True, "player_ratings_enabled": False, "rekordbox_enabled": False},
    }
    failed = _health(**common, deep={
        "total": 100, "coverage": 0.98, "completed": 98,
        "pending": 2, "errors": 2, "running": False, "progress": {"status": "idle"},
    })
    pending = _health(**common, deep={
        "total": 100, "coverage": 0.98, "completed": 98,
        "pending": 2, "errors": 0, "running": False, "progress": {"status": "idle"},
    })
    assert failed["next_action"]["id"] == "retry_deep"
    assert pending["next_action"]["id"] == "continue_deep"
