import json

from app import models


def test_public_example_is_fallback_without_creating_personal_file(tmp_path, monkeypatch):
    personal = tmp_path / "folder_keywords.json"
    example = tmp_path / "folder_keywords.example.json"
    example.write_text(
        json.dumps({
            "club house": {"genre": "Club House", "is_trainable": True},
            "hip-hop": {"genre": "Hip-Hop", "is_trainable": False},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(models, "GENRE_SETTINGS_FILE", str(personal))
    monkeypatch.setattr(models, "GENRE_SETTINGS_EXAMPLE_FILE", example)

    settings = models.load_genre_settings()

    assert settings["club house"]["genre"] == "Club House"
    assert settings["hip-hop"]["is_trainable"] is False
    assert not personal.exists()


def test_personal_file_has_priority_over_public_example(tmp_path, monkeypatch):
    personal = tmp_path / "folder_keywords.json"
    example = tmp_path / "folder_keywords.example.json"
    personal.write_text(
        json.dumps({"my private folder": {"genre": "Tech House", "is_trainable": True}}),
        encoding="utf-8",
    )
    example.write_text(
        json.dumps({"club house": {"genre": "Club House", "is_trainable": True}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(models, "GENRE_SETTINGS_FILE", str(personal))
    monkeypatch.setattr(models, "GENRE_SETTINGS_EXAMPLE_FILE", example)

    settings = models.load_genre_settings()

    assert set(settings) == {"my private folder"}
    assert settings["my private folder"]["genre"] == "Tech House"


def test_builtin_fallback_uses_current_mapping_schema(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "GENRE_SETTINGS_FILE", str(tmp_path / "missing-personal.json"))
    monkeypatch.setattr(models, "GENRE_SETTINGS_EXAMPLE_FILE", tmp_path / "missing-example.json")

    settings = models.load_genre_settings()

    assert settings
    assert all(isinstance(value, dict) for value in settings.values())
    assert "Club House" in models.get_trainable_genres(settings)
