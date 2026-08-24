"""Stable filesystem paths for the WebMusicPlayer project."""

import os
import re
import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent


def _runtime_project_dir():
    """Return the persistent data directory for source and PyInstaller runs.

    PyInstaller extracts Python modules into a temporary ``_MEIPASS`` folder.
    Mutable application data must never be written there because the folder is
    removed when the executable exits.  A freshly built executable is normally
    located in ``dist`` while a deployed executable may be copied directly to
    the project directory, so both layouts are supported.
    """
    configured = os.environ.get("WMP_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        if (executable_dir / "config.json").is_file():
            return executable_dir
        if (executable_dir.parent / "config.json").is_file():
            return executable_dir.parent
        return executable_dir
    return APP_DIR.parent


PROJECT_DIR = _runtime_project_dir()

CONFIG_FILE = PROJECT_DIR / "config.json"
LIBROSA_CONFIG_FILE = PROJECT_DIR / "librosa_config.json"
MODEL_FILE = PROJECT_DIR / "genre_model.pkl"
ACTIVE_MODEL_MANIFEST_FILE = PROJECT_DIR / "models" / "active_genre_model.json"
GENRE_SETTINGS_FILE = PROJECT_DIR / "folder_keywords.json"
GENRE_SETTINGS_EXAMPLE_FILE = PROJECT_DIR / "folder_keywords.example.json"
SCAN_DB_FILE = PROJECT_DIR / "scan_results.db"
SCAN_DB_BACKUP_FILE = PROJECT_DIR / "scan_results.backup.db"
FAVORITE_DB_FILE = PROJECT_DIR / "favorite.db"
BAD_FILES_FILE = PROJECT_DIR / "bad_files.json"
SCAN_REPORT_FILE = PROJECT_DIR / "scan_report.json"
YAMNET_MODEL_FILE = PROJECT_DIR / "yamnet.onnx"
YAMNET_CLASS_MAP_FILE = PROJECT_DIR / "yamnet_class_map.csv"
YAMNET_GENRE_MAP_FILE = PROJECT_DIR / "yamnet_genre_map.json"
YAMNET_CUDA_LOCK_FILE = PROJECT_DIR / ".yamnet_cuda.lock"
VOCAL_LANGUAGE_MODEL_DIR = PROJECT_DIR / "models" / "faster-whisper"
DISCOGS_EFFNET_MODEL_FILE = (
    PROJECT_DIR / "models" / "discogs_multi_embeddings-effnet-bs64-1.onnx"
)
DISCOGS_EFFNET_METADATA_FILE = (
    PROJECT_DIR / "models" / "discogs_multi_embeddings-effnet-bs64-1.json"
)
CATALOG_EMBEDDING_MODEL_FILE = PROJECT_DIR / "models" / "catalog_embedding_v1.pkl"
PERSONAL_RATING_MODEL_FILE = PROJECT_DIR / "models" / "personal_rating_v1.pkl"
LEARNING_CURVES_DIR = PROJECT_DIR / "learning_curves"
PERSONAL_RATING_REPORT_FILE = LEARNING_CURVES_DIR / "personal_rating_report.json"
TRAINING_ERRORS_FILE = LEARNING_CURVES_DIR / "training_errors.csv"
TRAINING_LANGUAGE_ERRORS_FILE = LEARNING_CURVES_DIR / "training_language_errors.csv"
TRAINING_DUPLICATES_FILE = LEARNING_CURVES_DIR / "training_duplicates.csv"
TRAINING_LABEL_CONFLICTS_FILE = LEARNING_CURVES_DIR / "training_label_conflicts.csv"
TRAINING_SOURCE_LABEL_CONFLICTS_FILE = (
    LEARNING_CURVES_DIR / "training_source_label_conflicts.csv"
)
TRAINING_CONFLICTS_FILE = LEARNING_CURVES_DIR / "training_conflicts.csv"
TRAINING_REVIEW_QUEUE_FILE = LEARNING_CURVES_DIR / "training_review_queue.csv"
TRAINING_QUALITY_REPORT_FILE = LEARNING_CURVES_DIR / "training_quality_gate.json"
TRAINING_RUN_REPORT_FILE = LEARNING_CURVES_DIR / "training_run_summary.json"
TRAINING_JOB_STATE_FILE = PROJECT_DIR / "training_job.json"
TRAINING_STOP_FILE = PROJECT_DIR / ".training_stop.json"
TRAINING_WORKER_LOG_FILE = PROJECT_DIR / "training_worker.log"
TRAINING_FEATURE_CACHE_FILE = PROJECT_DIR / "training_features_checkpoint.db"
SERVER_LOCK_FILE = PROJECT_DIR / ".web_music_player_server.lock"
GENRE_REVIEW_FILE = PROJECT_DIR / "genre_review_queue.json"
TRAINING_DATASET_FILE = PROJECT_DIR / "training_dataset.json"
TEST_UPLOADS_DIR = PROJECT_DIR / "test_uploads"
REKORDBOX_OUTPUT_DIR = PROJECT_DIR / "reckordbox_parcer_file_output"
DEBUG_LOG_FILE = PROJECT_DIR / "debug.log"


def _contains_audio_files(directory):
    if not directory.is_dir():
        return False
    audio_suffixes = {".mp3", ".wav", ".flac", ".m4a", ".ogg"}
    return any(path.suffix.lower() in audio_suffixes for path in directory.rglob("*"))


_local_samples_dir = PROJECT_DIR / "samples"
_workspace_samples_dir = PROJECT_DIR.parent / "samples"
SAMPLES_DIR = (
    _local_samples_dir
    if _contains_audio_files(_local_samples_dir)
    else _workspace_samples_dir
)


def resolve_project_path(value, default=None):
    """Resolve a user-configurable path relative to the project directory."""
    raw_value = value if value not in (None, "") else default
    if raw_value in (None, ""):
        return None
    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = PROJECT_DIR / path
    return path.resolve()


def resolve_mapped_music_path(value, music_dir=None):
    """Resolve a Rekordbox drive-letter path through the configured music root.

    Rekordbox exports mapped paths such as ``Z:/2025/...``.  Windows mappings
    are session-specific, so a service/elevated PyCharm process may not see Z:
    even though the interactive desktop does.  The fallback is used only when
    the direct path is unavailable and the translated target really exists.
    """
    raw_path = os.fspath(value or "")
    if not raw_path or os.path.exists(raw_path):
        return raw_path
    if not music_dir or not re.match(r"^[A-Za-z]:[\\/]", raw_path):
        return raw_path

    relative = raw_path[2:].lstrip("\\/")
    relative_parts = [part for part in re.split(r"[\\/]+", relative) if part]
    base_path = os.path.abspath(os.fspath(music_dir))
    candidate = os.path.abspath(os.path.join(base_path, *relative_parts))
    try:
        if os.path.commonpath([base_path, candidate]) != base_path:
            return raw_path
    except ValueError:
        return raw_path
    return candidate if os.path.exists(candidate) else raw_path
