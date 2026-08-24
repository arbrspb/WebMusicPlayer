import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from app import vocal_language  # noqa: E402


class FakeWhisperModel:
    def __init__(self, language="en", probability=0.9, speech_seconds=4.0, text="hello"):
        self.language = language
        self.probability = probability
        self.speech_seconds = speech_seconds
        self.text = text
        self.calls = 0

    def transcribe(self, _audio, **_kwargs):
        self.calls += 1
        segments = []
        if self.speech_seconds > 0 and self.text:
            segments.append(SimpleNamespace(
                text=self.text,
                start=0.0,
                end=self.speech_seconds,
            ))
        info = SimpleNamespace(
            language=self.language,
            language_probability=self.probability,
            duration_after_vad=self.speech_seconds,
        )
        return iter(segments), info


class MusicFallbackWhisperModel:
    def transcribe(self, _audio, **kwargs):
        if kwargs.get("vad_filter"):
            return iter([]), SimpleNamespace(
                language="en",
                language_probability=0.5,
                duration_after_vad=0.0,
            )
        return iter([SimpleNamespace(text="processed singing", start=0.0, end=4.0)]), SimpleNamespace(
            language="en",
            language_probability=0.88,
            duration_after_vad=45.0,
        )


class SegmentConsensusWhisperModel:
    def __init__(self):
        self.calls = 0

    def transcribe(self, _audio, **kwargs):
        self.calls += 1
        if kwargs.get("vad_filter"):
            return iter([]), SimpleNamespace(
                language="en",
                language_probability=0.58,
                duration_after_vad=0.0,
            )
        results = [
            ("es", 0.48, "short words"),
            ("es", 0.32, "recognised spanish vocals"),
            ("es", 0.29, "more recognised spanish vocals"),
            ("km", 0.35, "uncertain music"),
        ]
        code, probability, text = results[min(self.calls - 2, len(results) - 1)]
        return iter([SimpleNamespace(text=text, start=0.0, end=4.0)]), SimpleNamespace(
            language=code,
            language_probability=probability,
            duration_after_vad=15.0,
        )


def _settings(**overrides):
    settings = {
        "vocal_language_enabled": True,
        "vocal_language_model": "base",
        "vocal_language_device": "cpu",
        "vocal_language_compute_type": "int8",
        "vocal_language_min_probability": 0.70,
        "vocal_language_min_speech_seconds": 2.0,
        "vocal_language_detection_segments": 3,
    }
    settings.update(overrides)
    return settings


@pytest.mark.parametrize(
    ("code", "expected"),
    [("en", "English"), ("ru", "Russian"), ("es", "Other")],
)
def test_vocal_detector_maps_whisper_languages(tmp_path, monkeypatch, code, expected):
    audio_file = tmp_path / f"{code}.mp3"
    audio_file.write_bytes(b"audio")
    fake_model = FakeWhisperModel(language=code)
    monkeypatch.setattr(vocal_language, "_get_whisper_model", lambda _settings: fake_model)
    vocal_language.clear_vocal_language_cache()

    result = vocal_language.detect_vocal_language(
        str(audio_file),
        _settings(),
        [(np.ones(16000, dtype=np.float32), 16000, 0.0)],
    )

    assert result["language"] == expected
    assert result["confidence"] == 0.9
    assert result["status"] == "accepted"


def test_vocal_detector_marks_no_speech_as_instrumental_only_when_enabled(tmp_path, monkeypatch):
    audio_file = tmp_path / "instrumental.mp3"
    audio_file.write_bytes(b"audio")
    fake_model = FakeWhisperModel(speech_seconds=0.0, text="")
    monkeypatch.setattr(vocal_language, "_get_whisper_model", lambda _settings: fake_model)
    vocal_language.clear_vocal_language_cache()

    result = vocal_language.detect_vocal_language(
        str(audio_file),
        _settings(vocal_language_mark_instrumental=True),
        [(np.ones(16000, dtype=np.float32), 16000, 0.0)],
    )

    assert result["language"] == "Instrumental"
    assert result["status"] == "no_speech"


def test_vocal_detector_keeps_no_speech_unknown_by_default(tmp_path, monkeypatch):
    audio_file = tmp_path / "possibly_vocal.mp3"
    audio_file.write_bytes(b"audio")
    fake_model = FakeWhisperModel(speech_seconds=0.0, text="")
    monkeypatch.setattr(vocal_language, "_get_whisper_model", lambda _settings: fake_model)
    vocal_language.clear_vocal_language_cache()

    result = vocal_language.detect_vocal_language(
        str(audio_file),
        _settings(),
        [(np.ones(16000, dtype=np.float32), 16000, 0.0)],
    )

    assert result["language"] == "Unknown"
    assert result["status"] == "no_speech"


def test_vocal_detector_uses_strict_music_fallback_when_vad_misses_vocals(tmp_path, monkeypatch):
    audio_file = tmp_path / "processed_vocal.mp3"
    audio_file.write_bytes(b"audio")
    monkeypatch.setattr(
        vocal_language,
        "_get_whisper_model",
        lambda _settings: MusicFallbackWhisperModel(),
    )
    vocal_language.clear_vocal_language_cache()

    result = vocal_language.detect_vocal_language(
        str(audio_file),
        _settings(vocal_language_music_min_probability=0.80),
        [(np.ones(16000, dtype=np.float32), 16000, 0.0)],
    )

    assert result["language"] == "English"
    assert result["confidence"] == 0.88
    assert result["status"] == "accepted_music_fallback"


def test_vocal_detector_accepts_repeated_segment_language_without_lowering_global_threshold(tmp_path, monkeypatch):
    audio_file = tmp_path / "dense_spanish_remix.mp3"
    audio_file.write_bytes(b"audio")
    fake_model = SegmentConsensusWhisperModel()
    monkeypatch.setattr(vocal_language, "_get_whisper_model", lambda _settings: fake_model)
    vocal_language.clear_vocal_language_cache()

    audio_segments = [
        (np.ones(16000, dtype=np.float32), 16000, float(offset))
        for offset in (30, 60, 90)
    ]
    result = vocal_language.detect_vocal_language(
        str(audio_file),
        _settings(
            vocal_language_music_min_probability=0.80,
            vocal_language_segment_consensus_enabled=True,
        ),
        audio_segments,
    )

    assert result["language"] == "Other"
    assert result["language_code"] == "es"
    assert result["status"] == "accepted_segment_consensus"
    assert result["consensus_segments"] == 2


def test_vocal_detector_rejects_low_language_probability_and_caches(tmp_path, monkeypatch):
    audio_file = tmp_path / "uncertain.mp3"
    audio_file.write_bytes(b"audio")
    fake_model = FakeWhisperModel(language="en", probability=0.55)
    monkeypatch.setattr(vocal_language, "_get_whisper_model", lambda _settings: fake_model)
    vocal_language.clear_vocal_language_cache()
    audio_segments = [(np.ones(16000, dtype=np.float32), 16000, 0.0)]

    first = vocal_language.detect_vocal_language(str(audio_file), _settings(), audio_segments)
    second = vocal_language.detect_vocal_language(str(audio_file), _settings(), audio_segments)

    assert first["language"] == "Unknown"
    assert first["status"] == "low_confidence"
    assert second["cached"] is True
    assert fake_model.calls == 1


def test_disabled_vocal_detector_never_loads_backend(tmp_path, monkeypatch):
    audio_file = tmp_path / "disabled.mp3"
    audio_file.write_bytes(b"audio")
    monkeypatch.setattr(
        vocal_language,
        "_get_whisper_model",
        lambda _settings: pytest.fail("backend must not be loaded"),
    )

    result = vocal_language.detect_vocal_language(str(audio_file), _settings(vocal_language_enabled=False))

    assert result["language"] == "Unknown"
    assert result["status"] == "disabled"


def test_auto_runtime_prefers_cuda_and_cpu_is_safe_fallback(monkeypatch):
    monkeypatch.setattr(vocal_language, "_cuda_runtime_status", lambda: {
        "available": True,
        "device_count": 1,
        "compute_types": ["float16", "int8_float16"],
        "error": "",
    })
    cuda_runtime = vocal_language._resolve_runtime(_settings(vocal_language_device="auto"))
    assert cuda_runtime["device"] == "cuda"
    assert cuda_runtime["compute_type"] == "float16"

    monkeypatch.setattr(vocal_language, "_cuda_runtime_status", lambda: {
        "available": False,
        "device_count": 0,
        "compute_types": [],
        "error": "CUDA driver unavailable",
    })
    cpu_runtime = vocal_language._resolve_runtime(_settings(vocal_language_device="cuda"))
    assert cpu_runtime["device"] == "cpu"
    assert cpu_runtime["compute_type"] == "int8"
    assert cpu_runtime["fallback_to_cpu"] is True
