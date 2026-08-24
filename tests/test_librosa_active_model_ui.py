import json

from app.librosa_settings import _load_active_model_manifest


def test_active_model_manifest_is_read_from_small_json(tmp_path):
    path = tmp_path / "active_genre_model.json"
    payload = {
        "version": "4.1-test",
        "classes": ["Club House", "Hip-Hop"],
        "expected_feature_len": 134,
        "classification_report": {"macro avg": {"f1-score": 0.8}},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert _load_active_model_manifest(path) == payload


def test_invalid_active_model_manifest_is_safe(tmp_path):
    path = tmp_path / "active_genre_model.json"
    path.write_text("not-json", encoding="utf-8")
    assert _load_active_model_manifest(path) == {}
