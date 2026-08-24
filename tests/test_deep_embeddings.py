import sqlite3
import os
from pathlib import Path

import numpy as np
import pytest

from app import deep_embeddings as deep


def _scan_db(tmp_path):
    db_path = tmp_path / "scan.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "CREATE TABLE scan_results (rel_path TEXT PRIMARY KEY, mtime REAL)"
    )
    connection.executemany(
        "INSERT INTO scan_results(rel_path, mtime) VALUES (?, ?)",
        [("A.mp3", 10.0), ("B.mp3", 20.0)],
    )
    connection.commit()
    connection.close()
    return db_path


def test_provider_plan_is_portable():
    assert deep._provider_plan("auto", ["CPUExecutionProvider"]) == (
        ["CPUExecutionProvider"], False
    )
    assert deep._provider_plan("cuda", ["CPUExecutionProvider"]) == (
        ["CPUExecutionProvider"], True
    )
    assert deep._provider_plan(
        "auto", ["CUDAExecutionProvider", "CPUExecutionProvider"]
    ) == (["CUDAExecutionProvider", "CPUExecutionProvider"], False)
    assert deep._provider_plan(
        "auto", ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]
    ) == (["CUDAExecutionProvider", "CPUExecutionProvider"], False)
    assert deep._provider_plan(
        "auto", ["TensorrtExecutionProvider", "CPUExecutionProvider"]
    ) == (["CPUExecutionProvider"], False)


def test_deep_index_is_incremental_and_versioned(tmp_path, monkeypatch):
    db_path = _scan_db(tmp_path)
    model_path = tmp_path / "effnet.onnx"
    model_path.write_bytes(b"test model placeholder")

    fake_session = {
        "provider": "CPUExecutionProvider",
        "input_rank": 3,
        "session": object(),
        "input_name": "input",
        "output_name": "output",
    }
    monkeypatch.setattr(deep, "_get_session", lambda *args, **kwargs: (fake_session, False))
    monkeypatch.setattr(
        deep,
        "_preprocess_row",
        lambda row, music_dir, offsets, duration: {
            "rel_path": row["rel_path"],
            "mtime": row["mtime"],
            "patches": [np.zeros((deep.PATCH_FRAMES, deep.MEL_BANDS), dtype=np.float32)],
            "error": "",
        },
    )

    def fake_run_batch(_session, patches):
        result = np.zeros((len(patches), deep.MODEL_EMBEDDING_DIM), dtype=np.float32)
        for index in range(len(patches)):
            result[index, index] = 1.0
        return result

    monkeypatch.setattr(deep, "_run_batch", fake_run_batch)
    settings = {
        "effnet_enabled": True,
        "effnet_device": "auto",
        "effnet_segment_offsets": [30],
        "effnet_segment_duration": 2.2,
        "effnet_preprocess_workers": 1,
    }

    first = deep.build_deep_embedding_index(
        tmp_path, settings, db_path=db_path, model_path=model_path
    )
    assert first["status"] == "completed"
    assert first["processed"] == 2
    assert deep.deep_embedding_stats(db_path) == {
        "total": 2,
        "completed": 2,
        "pending": 0,
        "errors": 0,
        "coverage": 1.0,
    }

    second = deep.build_deep_embedding_index(
        tmp_path, settings, db_path=db_path, model_path=model_path
    )
    assert second["status"] == "completed"
    assert second["processed"] == 0

    connection = sqlite3.connect(db_path)
    row = connection.execute(
        "SELECT model_id, embedding_dim, embedding_dtype, length(embedding) "
        "FROM track_deep_embeddings WHERE rel_path='A.mp3'"
    ).fetchone()
    connection.close()
    assert row == (
        deep.MODEL_ID,
        deep.MODEL_EMBEDDING_DIM,
        "float16",
        deep.MODEL_EMBEDDING_DIM * 2,
    )

def test_safe_track_path_supports_unc_root():
    if os.name != "nt":
        return

    root = r"\\server\Music"

    result = deep._safe_track_path(
        root,
        r"2025\Folder\track.mp3",
    )

    assert result == os.path.abspath(
        r"\\server\Music\2025\Folder\track.mp3"
    )


def test_safe_track_path_rejects_escape_from_library():
    if os.name != "nt":
        return

    root = r"\\server\Music"

    try:
        deep._safe_track_path(root, r"..\OtherShare\track.mp3")
    except ValueError:
        pass
    else:
        raise AssertionError("Путь за пределами библиотеки должен быть запрещён")


@pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
@pytest.mark.parametrize("root, relative", [
    (r"D:\Music", r"Club\track.mp3"),
    (r"D:\Music", r"Club\Nested\track.mp3"),
    (r"\\server\Music", r"Club\track.mp3"),
    (r"\\server\Music", r"Club\Nested\track.mp3"),
])
def test_safe_track_path_accepts_windows_children(root, relative):
    assert deep._safe_track_path(root, relative)


@pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
@pytest.mark.parametrize("root, candidate", [
    (r"D:\Music", r"D:\Music2\track.mp3"),
    (r"\\server\Music", r"\\server\MusicBackup\track.mp3"),
])
def test_path_is_within_root_rejects_sibling_prefix(root, candidate):
    assert deep._path_is_within_root(root, candidate) is False


@pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
def test_safe_track_path_is_case_insensitive_and_rejects_parent_escape():
    assert deep._safe_track_path(r"D:\Music", r"CLUB\track.mp3")
    with pytest.raises(ValueError):
        deep._safe_track_path(r"D:\Music", r"..\Outside\track.mp3")


@pytest.mark.skipif(os.name != "nt", reason="Windows path semantics")
def test_cached_library_embedding_rejects_outside_path_before_db_query(tmp_path, monkeypatch):
    db_path = tmp_path / "scan.db"
    db_path.write_bytes(b"placeholder")
    monkeypatch.setattr(deep, "_connect", lambda *_args, **_kwargs: pytest.fail("DB must not be queried"))
    assert deep.cached_library_embedding(
        r"D:\Music2\track.mp3", r"D:\Music", db_path=db_path
    ) is None


@pytest.mark.skipif(os.name != "nt", reason="Windows DLL semantics")
def test_prepare_local_onnx_cuda_dll_directory_keeps_handle(tmp_path, monkeypatch):
    dll_dir = Path(tmp_path) / "runtime" / "onnx_cuda" / "bin"
    dll_dir.mkdir(parents=True)
    handle = object()
    calls = []
    monkeypatch.setattr(deep, "PROJECT_DIR", Path(tmp_path))
    monkeypatch.setattr(deep, "_ONNX_DLL_HANDLES", [])
    monkeypatch.setattr(deep, "_ONNX_DLL_READY", False)
    monkeypatch.setattr(deep.os, "add_dll_directory", lambda path: calls.append(path) or handle)

    deep._prepare_onnx_cuda_dlls()

    assert calls == [os.fspath(dll_dir)]
    assert deep._ONNX_DLL_HANDLES == [handle]
    assert deep._ONNX_DLL_READY is True


@pytest.mark.skipif(os.name != "nt", reason="Windows DLL semantics")
def test_missing_local_onnx_cuda_directory_is_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(deep, "PROJECT_DIR", Path(tmp_path))
    monkeypatch.setattr(deep, "_ONNX_DLL_HANDLES", [])
    monkeypatch.setattr(deep, "_ONNX_DLL_READY", False)
    monkeypatch.setattr(
        deep.os, "add_dll_directory",
        lambda _path: pytest.fail("Missing directory must not be registered"),
    )
    deep._prepare_onnx_cuda_dlls()
    assert deep._ONNX_DLL_READY is False
