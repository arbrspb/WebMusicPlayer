"""Durable, process-isolated genre training jobs.

The web server only starts and observes a worker.  Progress is persisted to a
small JSON file, so restarting the UI does not terminate or lose sight of an
active training run.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import psutil

from .paths import (
    MODEL_FILE,
    PROJECT_DIR,
    TRAINING_JOB_STATE_FILE,
    TRAINING_STOP_FILE,
    TRAINING_WORKER_LOG_FILE,
)


ACTIVE_STATUSES = {"starting", "running", "stopping"}
TERMINAL_STATUSES = {"completed", "stopped", "error", "rejected"}


def _now_iso():
    return _datetime.datetime.now().isoformat()


def _idle_state():
    return {
        "job_id": None,
        "pid": None,
        "pid_create_time": None,
        "status": "idle",
        "phase": "idle",
        "message": "Обучение ещё не запускалось",
        "progress": 0,
        "processed": 0,
        "total": 0,
        "error": "",
        "started_at": None,
        "updated_at": None,
        "heartbeat_at": None,
        "finished_at": None,
        "catalog_refresh": {
            "status": "idle", "processed": 0, "total": 0, "error": "",
        },
        "preflight": {},
    }


def _atomic_write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(temporary, "w", encoding="utf-8") as destination:
        json.dump(payload, destination, ensure_ascii=False, indent=2)
        destination.flush()
        os.fsync(destination.fileno())
    os.replace(temporary, path)


def _read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as source:
            value = json.load(source)
        return value if isinstance(value, dict) else (default or {})
    except (OSError, ValueError, TypeError):
        return default or {}


def _process_matches(pid, expected_create_time=None):
    try:
        process = psutil.Process(int(pid))
        if not process.is_running():
            return False
        if expected_create_time is not None:
            return abs(float(process.create_time()) - float(expected_create_time)) < 2.0
        return True
    except (psutil.Error, TypeError, ValueError, OSError):
        return False


class FileStopEvent:
    """Small ``threading.Event`` compatible stop flag shared via a file."""

    def __init__(self, job_id, path=None):
        self.job_id = str(job_id)
        self.path = Path(path or TRAINING_STOP_FILE)

    def is_set(self):
        payload = _read_json(self.path)
        return payload.get("job_id") == self.job_id and bool(payload.get("stop"))

    def set(self):
        _atomic_write_json(self.path, {
            "job_id": self.job_id,
            "stop": True,
            "requested_at": _now_iso(),
        })

    def clear(self):
        payload = _read_json(self.path)
        if not payload or payload.get("job_id") == self.job_id:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


def _snapshot_worker_state(job_id, local_state, pid, pid_create_time, started_at):
    detail = dict(local_state.get("training_detail") or {})
    status = str(detail.get("status") or "running")
    phase = str(detail.get("phase") or "preparing")
    progress = int(local_state.get("training_progress", detail.get("progress", 0)) or 0)
    error = str(local_state.get("training_error") or "")
    if error and status not in TERMINAL_STATUSES:
        status = "error"
        phase = "error"
    return {
        "job_id": job_id,
        "pid": pid,
        "pid_create_time": pid_create_time,
        "status": status,
        "phase": phase,
        "message": str(detail.get("message") or ""),
        "progress": max(0, min(100, progress)),
        "processed": int(detail.get("processed", 0) or 0),
        "total": int(detail.get("total", 0) or 0),
        "error": error,
        "started_at": detail.get("started_at") or started_at,
        "updated_at": detail.get("updated_at") or _now_iso(),
        "heartbeat_at": _now_iso(),
        "finished_at": detail.get("finished_at"),
        "quality_gate": local_state.get("training_quality_gate") or {},
        "preflight": local_state.get("training_preflight") or {},
        "catalog_refresh": dict(local_state.get("training_catalog_refresh") or {}),
    }


def run_training_worker(job_id, force=False):
    """Run one training job.  This function is safe as a PyInstaller target."""
    os.environ["WMP_SERVER_PROCESS"] = "0"
    os.environ["WMP_TRAINING_WORKER"] = "1"

    from .config import load_config
    from .logging_config import setup_logging
    from .models import train_genre_model

    setup_logging(load_config().get("debug_enabled", False))
    stop_event = FileStopEvent(job_id)
    started_at = _now_iso()
    pid = os.getpid()
    try:
        pid_create_time = psutil.Process(pid).create_time()
    except psutil.Error:
        pid_create_time = time.time()

    local_state = {
        "training_progress": 0,
        "training_error": "",
        "training_stop_event": stop_event,
        "training_detail": {
            "status": "running",
            "phase": "preparing",
            "message": "Подготовка обучающей выборки",
            "progress": 0,
            "processed": 0,
            "total": 0,
            "started_at": started_at,
            "updated_at": started_at,
            "finished_at": None,
        },
        "training_catalog_refresh": {
            "status": "idle", "processed": 0, "total": 0, "error": "",
        },
    }
    writer_stop = threading.Event()

    def persist():
        snapshot = _snapshot_worker_state(
            job_id, local_state, pid, pid_create_time, started_at,
        )
        _atomic_write_json(TRAINING_JOB_STATE_FILE, snapshot)

    def heartbeat_loop():
        while not writer_stop.wait(1.0):
            try:
                persist()
            except OSError:
                pass

    heartbeat = threading.Thread(
        target=heartbeat_loop,
        name="training-state-heartbeat",
        daemon=True,
    )
    heartbeat.start()
    persist()
    model_mtime_before = MODEL_FILE.stat().st_mtime_ns if MODEL_FILE.is_file() else 0

    try:
        train_genre_model(force, local_state)
        model_mtime_after = MODEL_FILE.stat().st_mtime_ns if MODEL_FILE.is_file() else 0
        if (
            model_mtime_after > model_mtime_before
            and not local_state.get("training_error")
            and not stop_event.is_set()
        ):
            try:
                from .catalog_intelligence import refresh_catalog_model_labels

                refresh_catalog_model_labels(
                    progress=local_state["training_catalog_refresh"]
                )
            except Exception as exc:  # model is valid even if catalog refresh fails
                local_state["training_catalog_refresh"].update({
                    "status": "error", "error": str(exc),
                })
        detail = local_state.setdefault("training_detail", {})
        if stop_event.is_set():
            detail.update({
                "status": "stopped",
                "phase": "stopped",
                "message": "Обучение остановлено; рабочая модель не изменена",
            })
        elif local_state.get("training_error"):
            detail.update({
                "status": "error",
                "phase": "error",
                "message": str(local_state["training_error"]),
            })
        elif detail.get("status") not in TERMINAL_STATUSES:
            detail.update({
                "status": "completed",
                "phase": "completed",
                "message": "Обучение завершено",
                "progress": 100,
            })
            local_state["training_progress"] = 100
    except BaseException as exc:
        local_state["training_error"] = f"{type(exc).__name__}: {exc}"
        local_state.setdefault("training_detail", {}).update({
            "status": "error",
            "phase": "error",
            "message": local_state["training_error"],
        })
    finally:
        finished_at = _now_iso()
        detail = local_state.setdefault("training_detail", {})
        detail["finished_at"] = finished_at
        detail["updated_at"] = finished_at
        writer_stop.set()
        heartbeat.join(timeout=2.0)
        persist()
    return 0 if not local_state.get("training_error") else 1


class TrainingJobManager:
    """Start, observe and stop the durable worker without owning its lifetime."""

    def __init__(
            self,
            state_path=TRAINING_JOB_STATE_FILE,
            stop_path=TRAINING_STOP_FILE,
            worker_log_path=TRAINING_WORKER_LOG_FILE,
            project_dir=PROJECT_DIR,
    ):
        self.state_path = Path(state_path)
        self.stop_path = Path(stop_path)
        self.worker_log_path = Path(worker_log_path)
        self.project_dir = Path(project_dir)

    def _read_state(self):
        state = _idle_state()
        state.update(_read_json(self.state_path))
        return state

    def _write_state(self, state):
        _atomic_write_json(self.state_path, state)

    def status(self):
        state = self._read_state()
        alive = _process_matches(state.get("pid"), state.get("pid_create_time"))
        if state.get("status") in ACTIVE_STATUSES and not alive:
            state.update({
                "status": "error",
                "phase": "error",
                "error": state.get("error") or "Процесс обучения неожиданно завершился",
                "message": state.get("error") or "Процесс обучения неожиданно завершился",
                "finished_at": state.get("finished_at") or _now_iso(),
                "updated_at": _now_iso(),
            })
            self._write_state(state)
        elif alive and FileStopEvent(state.get("job_id"), self.stop_path).is_set():
            state["status"] = "stopping"
            state["phase"] = "stopping"
            state["message"] = "Остановка запрошена; завершается текущая операция"

        detail = {
            "status": state.get("status", "idle"),
            "phase": state.get("phase", "idle"),
            "message": state.get("message", ""),
            "progress": int(state.get("progress", 0) or 0),
            "processed": int(state.get("processed", 0) or 0),
            "total": int(state.get("total", 0) or 0),
            "started_at": state.get("started_at"),
            "updated_at": state.get("updated_at"),
            "heartbeat_at": state.get("heartbeat_at"),
            "finished_at": state.get("finished_at"),
            "pid": state.get("pid"),
            "job_id": state.get("job_id"),
        }
        return {
            "progress": detail["progress"],
            "running": bool(alive and state.get("status") in ACTIVE_STATUSES),
            "error": state.get("error", ""),
            "status": detail["status"],
            "phase": detail["phase"],
            "message": detail["message"],
            "processed": detail["processed"],
            "total": detail["total"],
            "detail": detail,
            "catalog_refresh": state.get("catalog_refresh") or {},
            "quality_gate": state.get("quality_gate") or {},
            "preflight": state.get("preflight") or {},
        }

    def _worker_command(self, job_id, force):
        arguments = ["--training-worker", "--job-id", job_id]
        if force:
            arguments.append("--force")
        if getattr(sys, "frozen", False):
            return [sys.executable, *arguments]
        return [sys.executable, str(self.project_dir / "gui_server.py"), *arguments]

    def start(self, force=False):
        current = self.status()
        if current.get("running"):
            return False, current

        job_id = uuid.uuid4().hex
        stop_event = FileStopEvent(job_id, self.stop_path)
        stop_event.clear()
        started_at = _now_iso()
        state = _idle_state()
        state.update({
            "job_id": job_id,
            "status": "starting",
            "phase": "preparing",
            "message": "Запуск отдельного процесса обучения",
            "started_at": started_at,
            "updated_at": started_at,
            "heartbeat_at": started_at,
        })
        self._write_state(state)

        self.worker_log_path.parent.mkdir(parents=True, exist_ok=True)
        output = open(self.worker_log_path, "ab", buffering=0)
        creationflags = 0
        if os.name == "nt":
            creationflags = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        environment = os.environ.copy()
        environment.update({
            "WMP_SERVER_PROCESS": "0",
            "WMP_TRAINING_WORKER": "1",
            "WMP_DATA_DIR": str(self.project_dir),
        })
        try:
            process = subprocess.Popen(
                self._worker_command(job_id, force),
                cwd=str(self.project_dir),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                close_fds=True,
                creationflags=creationflags,
            )
        except Exception as exc:
            output.close()
            state.update({
                "status": "error",
                "phase": "error",
                "message": f"Не удалось запустить процесс обучения: {exc}",
                "error": f"{type(exc).__name__}: {exc}",
                "finished_at": _now_iso(),
            })
            self._write_state(state)
            return False, self.status()
        finally:
            try:
                output.close()
            except OSError:
                pass

        try:
            create_time = psutil.Process(process.pid).create_time()
        except psutil.Error:
            create_time = time.time()
        latest = self._read_state()
        if latest.get("job_id") == job_id:
            latest.update({
                "pid": process.pid,
                "pid_create_time": create_time,
                "status": "running",
                "phase": "preparing",
                "updated_at": _now_iso(),
            })
            self._write_state(latest)
        return True, self.status()

    def stop(self):
        state = self._read_state()
        status = self.status()
        if not status.get("running"):
            return False, status
        FileStopEvent(state.get("job_id"), self.stop_path).set()
        return True, self.status()


def worker_main(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--training-worker", action="store_true")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--force", action="store_true")
    options, _unknown = parser.parse_known_args(argv)
    return run_training_worker(options.job_id, force=options.force)
