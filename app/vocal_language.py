"""Optional vocal-language detection with faster-whisper.

The detector is deliberately independent from the genre classifier.  It is
loaded lazily, works on the audio segments already decoded by librosa and
never makes the main analysis fail when the optional dependency/model is not
available.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import threading
from collections import Counter, OrderedDict
from pathlib import Path

import librosa
import numpy as np

from .paths import VOCAL_LANGUAGE_MODEL_DIR


logger = logging.getLogger(__name__)

_MODEL_CACHE = {}
_MODEL_RUNTIME = {}
_MODEL_LOCK = threading.Lock()
_INFERENCE_LOCK = threading.Lock()
_RESULT_CACHE = OrderedDict()
_RESULT_CACHE_LOCK = threading.Lock()
_RESULT_CACHE_LIMIT = 2048


def _cuda_runtime_status():
    status = {"available": False, "device_count": 0, "compute_types": [], "error": ""}
    try:
        import ctranslate2
        count = int(ctranslate2.get_cuda_device_count())
        status["device_count"] = count
        if count > 0:
            status["compute_types"] = sorted(
                str(value) for value in ctranslate2.get_supported_compute_types("cuda")
            )
            status["available"] = True
    except Exception as exc:
        status["error"] = str(exc)
    return status


def _resolve_runtime(settings):
    requested = str(settings.get("vocal_language_device", "cpu")).strip().lower()
    if requested not in {"cpu", "cuda", "auto"}:
        requested = "auto"
    cuda = _cuda_runtime_status()
    use_cuda = requested in {"cuda", "auto"} and cuda["available"]
    device = "cuda" if use_cuda else "cpu"
    if device == "cuda":
        supported = set(cuda.get("compute_types") or [])
        compute_type = "float16" if "float16" in supported else (
            "int8_float16" if "int8_float16" in supported else "int8"
        )
    else:
        compute_type = "int8"
    return {
        "requested_device": requested,
        "device": device,
        "compute_type": compute_type,
        "cuda": cuda,
        "fallback_to_cpu": requested == "cuda" and device == "cpu",
    }


def vocal_language_backend_status(settings=None):
    settings = settings or {}
    model_name = str(settings.get("vocal_language_model", "base"))
    requested_device = str(settings.get("vocal_language_device", "cpu"))
    requested_compute_type = str(settings.get("vocal_language_compute_type", "int8"))
    key = (model_name, requested_device, requested_compute_type)
    runtime = dict(_MODEL_RUNTIME.get(key) or _resolve_runtime(settings))
    return {
        "dependency_available": importlib.util.find_spec("faster_whisper") is not None,
        "model_loaded": key in _MODEL_CACHE,
        "model": model_name,
        "requested_device": requested_device,
        "device": runtime.get("device", "cpu"),
        "compute_type": runtime.get("compute_type", "int8"),
        "cuda_available": bool((runtime.get("cuda") or {}).get("available", False)),
        "cuda_device_count": int((runtime.get("cuda") or {}).get("device_count", 0)),
        "cuda_error": str((runtime.get("cuda") or {}).get("error", "")),
        "fallback_to_cpu": bool(runtime.get("fallback_to_cpu", False)),
        "runtime_error": str(runtime.get("runtime_error", "")),
        "download_root": str(VOCAL_LANGUAGE_MODEL_DIR),
    }


def clear_vocal_language_cache():
    with _RESULT_CACHE_LOCK:
        _RESULT_CACHE.clear()


def _cache_key(path, settings):
    try:
        stat = os.stat(path)
        file_signature = (os.path.abspath(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        file_signature = (os.path.abspath(str(path)), None, None)
    return file_signature + (
        str(settings.get("vocal_language_model", "base")),
        str(settings.get("vocal_language_device", "cpu")),
        str(settings.get("vocal_language_compute_type", "int8")),
        float(settings.get("vocal_language_min_probability", 0.70)),
        float(settings.get("vocal_language_min_speech_seconds", 2.0)),
        bool(settings.get("vocal_language_mark_instrumental", False)),
        bool(settings.get("vocal_language_music_fallback_enabled", True)),
        float(settings.get("vocal_language_music_min_probability", 0.80)),
        bool(settings.get("vocal_language_segment_consensus_enabled", True)),
    )


def _get_cached_result(key):
    with _RESULT_CACHE_LOCK:
        result = _RESULT_CACHE.get(key)
        if result is not None:
            _RESULT_CACHE.move_to_end(key)
            return dict(result, cached=True)
    return None


def _store_cached_result(key, result):
    with _RESULT_CACHE_LOCK:
        _RESULT_CACHE[key] = dict(result, cached=False)
        _RESULT_CACHE.move_to_end(key)
        while len(_RESULT_CACHE) > _RESULT_CACHE_LIMIT:
            _RESULT_CACHE.popitem(last=False)


def _get_whisper_model(settings):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper не установлен; установите зависимости проекта"
        ) from exc

    model_name = str(settings.get("vocal_language_model", "base"))
    requested_device = str(settings.get("vocal_language_device", "cpu"))
    requested_compute_type = str(settings.get("vocal_language_compute_type", "int8"))
    key = (model_name, requested_device, requested_compute_type)
    with _MODEL_LOCK:
        if key not in _MODEL_CACHE:
            Path(VOCAL_LANGUAGE_MODEL_DIR).mkdir(parents=True, exist_ok=True)
            cpu_threads = max(1, int(settings.get("vocal_language_cpu_threads", min(4, os.cpu_count() or 1))))
            runtime = _resolve_runtime(settings)
            try:
                _MODEL_CACHE[key] = WhisperModel(
                    model_name,
                    device=runtime["device"],
                    compute_type=runtime["compute_type"],
                    cpu_threads=cpu_threads,
                    num_workers=1,
                    download_root=str(VOCAL_LANGUAGE_MODEL_DIR),
                )
            except Exception as exc:
                if runtime["device"] != "cuda":
                    raise
                logger.warning("CUDA Whisper unavailable, falling back to CPU: %s", exc)
                runtime.update({
                    "device": "cpu",
                    "compute_type": "int8",
                    "fallback_to_cpu": True,
                    "runtime_error": str(exc),
                })
                _MODEL_CACHE[key] = WhisperModel(
                    model_name,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=cpu_threads,
                    num_workers=1,
                    download_root=str(VOCAL_LANGUAGE_MODEL_DIR),
                )
            _MODEL_RUNTIME[key] = runtime
        return _MODEL_CACHE[key]


def _prepare_audio(audio_segments):
    prepared = []
    for segment in audio_segments or []:
        if not isinstance(segment, (tuple, list)) or len(segment) < 2:
            continue
        audio, sample_rate = segment[0], int(segment[1])
        row = np.asarray(audio, dtype=np.float32).reshape(-1)
        if not row.size or not np.all(np.isfinite(row)):
            continue
        if sample_rate != 16000:
            row = librosa.resample(row, orig_sr=sample_rate, target_sr=16000)
        peak = float(np.max(np.abs(row))) if row.size else 0.0
        if peak > 1.0:
            row = row / peak
        prepared.append(np.asarray(row, dtype=np.float32))
    if not prepared:
        return None
    silence = np.zeros(4000, dtype=np.float32)
    joined = []
    for index, row in enumerate(prepared):
        if index:
            joined.append(silence)
        joined.append(row)
    return np.concatenate(joined)


def _map_language_code(code):
    code = str(code or "").casefold()
    if code == "ru":
        return "Russian"
    if code == "en":
        return "English"
    if code:
        return "Other"
    return "Unknown"


def detect_vocal_language(path, settings=None, audio_segments=None):
    """Return a safe language decision without raising into genre analysis."""
    settings = settings or {}
    if not bool(settings.get("vocal_language_enabled", False)):
        return {
            "language": "Unknown",
            "confidence": 0.0,
            "source": "vocal_detector",
            "status": "disabled",
        }

    key = _cache_key(path, settings)
    cached = _get_cached_result(key)
    if cached is not None:
        return cached

    try:
        model = _get_whisper_model(settings)
        prepared_audio = _prepare_audio(audio_segments)
        audio_input = prepared_audio if prepared_audio is not None else str(path)
        with _INFERENCE_LOCK:
            segments, info = model.transcribe(
                audio_input,
                language=None,
                beam_size=1,
                best_of=1,
                temperature=0.0,
                condition_on_previous_text=False,
                vad_filter=True,
                without_timestamps=True,
                language_detection_segments=max(
                    1,
                    int(settings.get("vocal_language_detection_segments", 3)),
                ),
            )
            segment_rows = list(segments)
        speech_seconds = float(getattr(info, "duration_after_vad", 0.0) or 0.0)
        if speech_seconds <= 0.0:
            speech_seconds = sum(
                max(0.0, float(getattr(segment, "end", 0.0)) - float(getattr(segment, "start", 0.0)))
                for segment in segment_rows
            )
        transcript = " ".join(
            str(getattr(segment, "text", "") or "").strip()
            for segment in segment_rows
        ).strip()
        min_speech_seconds = max(
            0.0,
            float(settings.get("vocal_language_min_speech_seconds", 2.0)),
        )
        no_speech = not segment_rows or speech_seconds < min_speech_seconds or len(transcript) < 2
        if no_speech and bool(settings.get("vocal_language_music_fallback_enabled", True)):
            # Silero VAD может не выделить вокал внутри плотного ремикса. В
            # таком случае разрешаем один дополнительный проход без VAD, но
            # принимаем язык только с существенно более строгим порогом.
            with _INFERENCE_LOCK:
                fallback_segments, fallback_info = model.transcribe(
                    audio_input,
                    language=None,
                    beam_size=1,
                    best_of=1,
                    temperature=0.0,
                    condition_on_previous_text=False,
                    vad_filter=False,
                    without_timestamps=True,
                    language_detection_segments=max(
                        1,
                        int(settings.get("vocal_language_detection_segments", 3)),
                    ),
                )
                fallback_rows = list(fallback_segments)
            fallback_text = " ".join(
                str(getattr(segment, "text", "") or "").strip()
                for segment in fallback_rows
            ).strip()
            fallback_code = str(getattr(fallback_info, "language", "") or "")
            fallback_probability = float(
                getattr(fallback_info, "language_probability", 0.0) or 0.0
            )
            fallback_threshold = min(
                max(float(settings.get("vocal_language_music_min_probability", 0.80)), 0.5),
                0.99,
            )
            if fallback_rows and len(fallback_text) >= 2 and fallback_probability >= fallback_threshold:
                result = {
                    "language": _map_language_code(fallback_code),
                    "confidence": fallback_probability,
                    "source": "faster-whisper",
                    "status": "accepted_music_fallback",
                    "language_code": fallback_code or None,
                    "speech_seconds": 0.0,
                    "transcript_characters": len(fallback_text),
                }
                _store_cached_result(key, result)
                return dict(result, cached=False)

            # A dense remix can defeat VAD and make the joined pass uncertain.
            # Do not lower the global threshold: ask each already decoded part
            # independently and accept only a repeated language code.
            if (
                bool(settings.get("vocal_language_segment_consensus_enabled", True))
                and len(audio_segments or []) >= 2
            ):
                consensus_rows = []
                for audio_segment in audio_segments:
                    segment_audio = _prepare_audio([audio_segment])
                    if segment_audio is None:
                        continue
                    with _INFERENCE_LOCK:
                        probe_segments, probe_info = model.transcribe(
                            segment_audio,
                            language=None,
                            beam_size=1,
                            best_of=1,
                            temperature=0.0,
                            condition_on_previous_text=False,
                            vad_filter=False,
                            without_timestamps=True,
                            language_detection_segments=1,
                        )
                        probe_rows = list(probe_segments)
                    probe_text = " ".join(
                        str(getattr(segment, "text", "") or "").strip()
                        for segment in probe_rows
                    ).strip()
                    probe_code = str(getattr(probe_info, "language", "") or "")
                    probe_probability = float(
                        getattr(probe_info, "language_probability", 0.0) or 0.0
                    )
                    if (
                        probe_rows
                        and len(probe_text) >= 2
                        and probe_code
                        and probe_probability >= 0.25
                    ):
                        consensus_rows.append({
                            "code": probe_code,
                            "probability": probe_probability,
                            "characters": len(probe_text),
                        })

                vote_counts = Counter(row["code"] for row in consensus_rows)
                if vote_counts:
                    winner_code = max(
                        vote_counts,
                        key=lambda code: (
                            vote_counts[code],
                            sum(
                                row["probability"]
                                for row in consensus_rows
                                if row["code"] == code
                            ),
                        ),
                    )
                    winner_rows = [
                        row for row in consensus_rows if row["code"] == winner_code
                    ]
                    winner_probability = float(np.mean([
                        row["probability"] for row in winner_rows
                    ]))
                    winner_characters = sum(row["characters"] for row in winner_rows)
                    winner_share = len(winner_rows) / max(1, len(consensus_rows))
                    if (
                        len(winner_rows) >= 2
                        and winner_probability >= 0.30
                        and winner_characters >= 20
                        and winner_share >= (2.0 / 3.0)
                    ):
                        result = {
                            "language": _map_language_code(winner_code),
                            "confidence": winner_probability,
                            "source": "faster-whisper",
                            "status": "accepted_segment_consensus",
                            "language_code": winner_code,
                            "speech_seconds": 0.0,
                            "transcript_characters": winner_characters,
                            "consensus_segments": len(winner_rows),
                            "segment_probabilities": [
                                round(row["probability"], 6) for row in winner_rows
                            ],
                        }
                        _store_cached_result(key, result)
                        return dict(result, cached=False)

        if no_speech:
            mark_instrumental = bool(settings.get("vocal_language_mark_instrumental", False))
            result = {
                "language": "Instrumental" if mark_instrumental else "Unknown",
                "confidence": 1.0 if mark_instrumental else 0.0,
                "source": "faster-whisper",
                "status": "no_speech",
                "language_code": None,
                "speech_seconds": round(speech_seconds, 3),
                "transcript_characters": 0,
            }
        else:
            language_code = str(getattr(info, "language", "") or "")
            probability = float(getattr(info, "language_probability", 0.0) or 0.0)
            min_probability = min(
                max(float(settings.get("vocal_language_min_probability", 0.70)), 0.0),
                0.99,
            )
            language = _map_language_code(language_code) if probability >= min_probability else "Unknown"
            result = {
                "language": language,
                "confidence": probability,
                "source": "faster-whisper",
                "status": "accepted" if language != "Unknown" else "low_confidence",
                "language_code": language_code or None,
                "speech_seconds": round(speech_seconds, 3),
                # Полный текст не сохраняем: для задачи нужен только язык.
                "transcript_characters": len(transcript),
            }
        _store_cached_result(key, result)
        return dict(result, cached=False)
    except Exception as exc:
        logger.warning("Vocal language detection failed for %s: %s", path, exc)
        return {
            "language": "Unknown",
            "confidence": 0.0,
            "source": "vocal_detector",
            "status": "unavailable" if isinstance(exc, RuntimeError) else "error",
            "error": str(exc),
        }
