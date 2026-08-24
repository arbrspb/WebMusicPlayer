import os

import psutil
from flask import Flask


def test_quick_quality_endpoint_returns_diagnostic_without_model_write(monkeypatch):
    import app.routes as routes

    class IdleTrainingManager:
        def status(self):
            return {"running": False}

    monkeypatch.setattr(routes, "get_advanced_mode", lambda: True)
    monkeypatch.setattr(routes, "training_job_manager", IdleTrainingManager())
    monkeypatch.setattr(routes, "quick_training_quality_assessment", lambda progress: (
        progress(100, "done") or {
            "status": "completed",
            "diagnostic_only": True,
            "macro_f1": 0.72,
            "pipeline": {"model_saved": False, "feature_extraction": False},
            "message": "done",
        }
    ))
    routes.global_state["quick_quality_thread"] = None
    routes.global_state["quick_quality_progress"] = {
        "status": "idle", "progress": 0, "message": "", "result": None,
        "error": "",
    }

    flask_app = Flask(__name__)
    flask_app.secret_key = "test"
    routes.register_routes(flask_app)
    client = flask_app.test_client()

    started = client.post("/api/training-dataset/quick-quality", json={})
    assert started.status_code == 202
    thread = routes.global_state.get("quick_quality_thread")
    if thread is not None:
        thread.join(timeout=2)
    status = client.get("/api/training-dataset/quick-quality").get_json()
    assert status["status"] == "completed"
    assert status["result"]["diagnostic_only"] is True
    assert status["result"]["pipeline"]["model_saved"] is False


def test_training_status_and_cooperative_stop(monkeypatch):
    import app.routes as routes

    class FakeTrainingManager:
        def __init__(self):
            self.stop_requested = False

        def status(self):
            return {
                "progress": 37,
                "running": not self.stop_requested,
                "error": "",
                "status": "stopping" if self.stop_requested else "running",
                "phase": "stopping" if self.stop_requested else "features",
                "message": "Остановка запрошена" if self.stop_requested else "Извлечение аудиопризнаков",
                "processed": 370,
                "total": 1000,
                "detail": {
                    "status": "stopping" if self.stop_requested else "running",
                    "phase": "stopping" if self.stop_requested else "features",
                    "progress": 37,
                    "processed": 370,
                    "total": 1000,
                    "job_id": "test-job",
                },
                "catalog_refresh": {},
            }

        def stop(self):
            self.stop_requested = True
            return True, self.status()

        def start(self, force=False):
            self.stop_requested = False
            return True, self.status()

    manager = FakeTrainingManager()
    monkeypatch.setattr(routes, "get_advanced_mode", lambda: True)
    monkeypatch.setattr(routes, "training_job_manager", manager)

    flask_app = Flask(__name__)
    flask_app.secret_key = "test"
    routes.register_routes(flask_app)
    client = flask_app.test_client()

    status = client.get("/training_status")
    assert status.status_code == 200
    payload = status.get_json()
    assert payload["running"] is True
    assert payload["progress"] == 37
    assert payload["phase"] == "features"
    assert payload["processed"] == 370
    assert payload["total"] == 1000

    stopped = client.post("/stop_training", json={})
    assert stopped.status_code == 200
    assert stopped.get_json()["status"] == "stopping"
    assert manager.stop_requested is True

    started = client.post("/retrain?force=1", json={})
    assert started.status_code == 200
    assert started.get_json()["running"] is True


def test_training_manager_survives_web_manager_recreation(tmp_path):
    from app.training_jobs import TrainingJobManager, _atomic_write_json

    process = psutil.Process(os.getpid())
    state_path = tmp_path / "training.json"
    stop_path = tmp_path / "stop.json"
    _atomic_write_json(state_path, {
        "job_id": "durable-job",
        "pid": process.pid,
        "pid_create_time": process.create_time(),
        "status": "running",
        "phase": "features",
        "message": "Извлечение",
        "progress": 42,
        "processed": 420,
        "total": 1000,
        "error": "",
    })

    first = TrainingJobManager(
        state_path=state_path,
        stop_path=stop_path,
        worker_log_path=tmp_path / "worker.log",
        project_dir=tmp_path,
    )
    second = TrainingJobManager(
        state_path=state_path,
        stop_path=stop_path,
        worker_log_path=tmp_path / "worker.log",
        project_dir=tmp_path,
    )

    assert first.status()["running"] is True
    assert second.status()["progress"] == 42
    stopped, payload = second.stop()
    assert stopped is True
    assert payload["status"] == "stopping"


def test_dead_training_worker_is_reported_as_error(tmp_path):
    from app.training_jobs import TrainingJobManager, _atomic_write_json

    state_path = tmp_path / "training.json"
    _atomic_write_json(state_path, {
        "job_id": "dead-job",
        "pid": 99999999,
        "pid_create_time": 1.0,
        "status": "running",
        "phase": "features",
        "progress": 18,
        "error": "",
    })
    manager = TrainingJobManager(
        state_path=state_path,
        stop_path=tmp_path / "stop.json",
        worker_log_path=tmp_path / "worker.log",
        project_dir=tmp_path,
    )

    payload = manager.status()

    assert payload["running"] is False
    assert payload["status"] == "error"
    assert "завершился" in payload["error"]


def test_training_worker_refreshes_catalog_after_accepted_model(
        tmp_path, monkeypatch,
):
    import app.catalog_intelligence as catalog
    import app.logging_config as logging_config
    import app.models as models
    import app.training_jobs as jobs

    state_path = tmp_path / "training.json"
    model_path = tmp_path / "genre_model.pkl"
    refreshed = []

    def fake_train(_force, state):
        model_path.write_bytes(b"accepted-model")
        state["training_progress"] = 100
        state["training_detail"].update({
            "status": "completed",
            "phase": "completed",
            "message": "Готово",
            "progress": 100,
        })

    def fake_refresh(progress):
        progress.update({"status": "completed", "processed": 12, "total": 12})
        refreshed.append(True)

    monkeypatch.setattr(jobs, "TRAINING_JOB_STATE_FILE", state_path)
    monkeypatch.setattr(jobs, "TRAINING_STOP_FILE", tmp_path / "stop.json")
    monkeypatch.setattr(jobs, "MODEL_FILE", model_path)
    monkeypatch.setattr(logging_config, "setup_logging", lambda _debug: None)
    monkeypatch.setattr(models, "train_genre_model", fake_train)
    monkeypatch.setattr(catalog, "refresh_catalog_model_labels", fake_refresh)

    assert jobs.run_training_worker("accepted-job", force=True) == 0
    payload = jobs._read_json(state_path)
    assert payload["status"] == "completed"
    assert payload["catalog_refresh"]["status"] == "completed"
    assert refreshed == [True]
