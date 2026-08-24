import json
import sqlite3
from pathlib import Path

from app.config import DEFAULT_CONFIG
from tools import backup_local_state
from tools import prepare_github_release


def test_fresh_install_default_does_not_reference_private_nas():
    value = str(DEFAULT_CONFIG["music_dir"])
    assert "192" + ".168." not in value
    assert "WinUsers\\" + "ARTUR" not in value
    assert value.lower().endswith("music")


def test_release_allowlist_contains_no_local_state_or_model_weights():
    files = list(prepare_github_release.public_files())
    relative = {
        path.relative_to(prepare_github_release.PROJECT_ROOT).as_posix()
        for path in files
    }
    assert "config.example.json" in relative
    assert "folder_keywords.example.json" in relative
    assert "config.json" not in relative
    assert "folder_keywords.json" not in relative
    assert "scan_results.db" not in relative
    assert "genre_model.pkl" not in relative
    assert "yamnet.onnx" not in relative
    assert prepare_github_release.audit_tree(
        prepare_github_release.PROJECT_ROOT, files
    ) == []


def test_release_audit_rejects_private_data_and_large_binary(tmp_path):
    config = tmp_path / "config.json"
    private_ip = "192" + ".168.1.10"
    config.write_text(
        json.dumps({"music_dir": f"\\\\{private_ip}\\Music"}), encoding="utf-8"
    )
    model = tmp_path / "model.onnx"
    model.write_bytes(b"model")
    keywords = tmp_path / "folder_keywords.json"
    keywords.write_text('{"personal folder": "Club House"}', encoding="utf-8")
    errors = prepare_github_release.audit_tree(tmp_path)
    assert any("forbidden personal file" in error for error in errors)
    assert any("folder_keywords.json" in error for error in errors)
    assert any("forbidden binary/data suffix" in error for error in errors)
    assert any("private IPv4 address" in error for error in errors)


def test_local_backup_uses_sqlite_backup_and_excludes_third_party_weights(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    database = project / "scan_results.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE scan_results (id INTEGER PRIMARY KEY, value TEXT)")
    connection.execute("INSERT INTO scan_results(value) VALUES ('safe')")
    connection.commit()
    connection.close()
    (project / "config.json").write_text('{"music_dir": "D:\\\\Music"}', encoding="utf-8")
    (project / "yamnet.onnx").write_bytes(b"must not be copied")
    monkeypatch.setattr(backup_local_state, "PROJECT_ROOT", project)

    output = tmp_path / "backup"
    manifest = backup_local_state.create_backup(output)

    copied = {item["path"] for item in manifest["files"]}
    assert "scan_results.db" in copied
    assert "config.json" in copied
    assert "yamnet.onnx" not in copied
    assert not (output / "yamnet.onnx").exists()
    backup_db = sqlite3.connect(output / "scan_results.db")
    assert backup_db.execute("SELECT value FROM scan_results").fetchone() == ("safe",)
    backup_db.close()
    saved = json.loads((output / "backup_manifest.json").read_text(encoding="utf-8"))
    assert saved["contains_audio"] is False


def test_effnet_download_requires_visible_license_acknowledgement():
    root = Path(__file__).resolve().parents[1]
    routes = (root / "app/routes.py").read_text(encoding="utf-8")
    javascript = (root / "static/js/settings.js").read_text(encoding="utf-8")
    html = (root / "templates/settings.html").read_text(encoding="utf-8")
    assert 'data.get("accept_license") is not True' in routes
    assert "accept_license: true" in javascript
    assert "CC BY-NC-SA 4.0" in javascript
    assert "Лицензия модели" in html
