import json
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app import models  # noqa: E402
from app.paths import resolve_mapped_music_path  # noqa: E402


def test_mapped_drive_path_falls_back_to_configured_music_root(tmp_path):
    audio_file = tmp_path / "2025" / "Prime Time DJ" / "track.mp3"
    audio_file.parent.mkdir(parents=True)
    audio_file.write_bytes(b"audio")

    resolved = resolve_mapped_music_path(
        r"Z:\2025\Prime Time DJ\track.mp3",
        str(tmp_path),
    )

    assert Path(resolved) == audio_file


def test_rekordbox_loader_uses_music_root_fallback(tmp_path, monkeypatch):
    audio_file = tmp_path / "2025" / "Prime Time DJ" / "track.mp3"
    audio_file.parent.mkdir(parents=True)
    audio_file.write_bytes(b"audio")
    export_file = tmp_path / "parsed_rekordbox.json"
    export_file.write_text(json.dumps([{
        "Title": "Track",
        "Artist": "Artist",
        "Genre": "Club House",
        "path": "Z:/2025/Prime Time DJ/track.mp3",
    }]), encoding="utf-8")
    monkeypatch.setattr(models, "load_config", lambda: {"music_dir": str(tmp_path)})

    tracks = models.load_rekordbox_json_tracks(
        str(export_file),
        {"club house": {"genre": "Club House", "is_trainable": True}},
    )

    assert len(tracks) == 1
    assert Path(tracks[0]["path"]) == audio_file
    assert tracks[0]["source_path"].startswith("Z:")
