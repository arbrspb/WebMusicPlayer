import sqlite3
import concurrent.futures
import multiprocessing

from app import config, db, logging_config, models


def _read_worker_stop_event():
    return models._SCAN_STOP_EVENT.is_set()


def test_log_flags_are_cached_and_can_be_invalidated(monkeypatch):
    calls = []

    def fake_load_config():
        calls.append(True)
        return {"debug_enabled": True, "log_flags": {"model": True}}

    monkeypatch.setattr(config, "load_config", fake_load_config)
    logging_config.invalidate_log_settings_cache()

    assert logging_config.is_log_type_enabled("model") is True
    assert logging_config.is_log_type_enabled("model") is True
    assert len(calls) == 1

    logging_config.invalidate_log_settings_cache()
    assert logging_config.is_log_type_enabled("model") is True
    assert len(calls) == 2


def test_large_windows_model_is_limited_by_commit_headroom(tmp_path, monkeypatch):
    model_file = tmp_path / "genre_model.pkl"
    model_file.write_bytes(b"model")
    monkeypatch.setattr(models.os, "name", "nt")
    monkeypatch.setattr(models.os.path, "getsize", lambda _path: 214 * 1024 * 1024)

    workers, reason = models._safe_scan_worker_count(
        6, {}, str(model_file), commit_headroom_bytes=7 * 1024 ** 3
    )

    assert workers == 2
    assert reason == "large_windows_model_commit_safe"
    assert models._safe_scan_worker_count(
        4, {}, str(model_file), commit_headroom_bytes=7 * 1024 ** 3
    )[0] == 2
    assert models._safe_scan_worker_count(
        6, {"_scan_worker_limit": 2}, str(model_file), commit_headroom_bytes=7 * 1024 ** 3
    )[0] == 2
    assert models._safe_scan_worker_count(
        6, {}, str(model_file), commit_headroom_bytes=3 * 1024 ** 3
    ) == (1, "large_windows_model_low_commit")


def test_feature_oom_is_not_replaced_with_zero_vector(monkeypatch):
    def raise_oom(**_kwargs):
        raise MemoryError("Unable to allocate test array")

    monkeypatch.setattr(models.librosa.feature, "mfcc", raise_oom)
    result = models.extract_features(
        models.np.zeros(22050, dtype=float),
        22050,
        {"features": {"mfcc": True}, "n_mfcc": 13},
        path="oom.mp3",
    )

    assert isinstance(result, tuple)
    features, error = result
    assert features.size == 0
    assert error.startswith("MemoryError:")


def test_scan_writer_reuses_schema_and_commits_each_track(tmp_path, monkeypatch):
    scan_db = tmp_path / "scan_results.db"
    monkeypatch.setattr(db, "SCAN_DB", str(scan_db))
    db.init_scan_db()
    schema_reads = []
    original_columns = db._scan_result_columns

    def counted_columns(connection):
        schema_reads.append(True)
        return original_columns(connection)

    monkeypatch.setattr(db, "_scan_result_columns", counted_columns)
    with db.ScanResultWriter(str(scan_db)) as writer:
        writer.save("one.mp3", "Club House", 1.0, 0.8, features=[1.0])
        with sqlite3.connect(scan_db) as observer:
            assert observer.execute("SELECT COUNT(*) FROM scan_results").fetchone()[0] == 1
        writer.save("two.mp3", "Hip-Hop", 2.0, 0.7, features=[2.0])

    assert len(schema_reads) == 1
    with sqlite3.connect(scan_db) as observer:
        assert observer.execute("SELECT COUNT(*) FROM scan_results").fetchone()[0] == 2


def test_spawn_event_reaches_worker_without_manager_process():
    context = multiprocessing.get_context("spawn")
    stop_event = context.Event()
    model_lock = context.Lock()
    with concurrent.futures.ProcessPoolExecutor(
        max_workers=1,
        mp_context=context,
        initializer=models._init_scan_worker,
        initargs=(model_lock, stop_event),
    ) as executor:
        assert executor.submit(_read_worker_stop_event).result(timeout=30) is False
        stop_event.set()
        assert executor.submit(_read_worker_stop_event).result(timeout=30) is True
