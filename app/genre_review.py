"""Durable review queue and manual taxonomy corrections.

The store never modifies an audio file.  Entries are tied to a compact audio
fingerprint and also keep the last absolute path for fast runtime lookup.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from .paths import GENRE_REVIEW_FILE
from .track_taxonomy import SUPPORTED_BASE_GENRES, SUPPORTED_LANGUAGES, SUPPORTED_VERSION_TYPES


_STORE_LOCK = threading.RLock()
_AUDIO_SUFFIXES = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"}
_FINGERPRINT_CHUNK_SIZE = 1024 * 1024


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path_key(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _validate_audio_path(path):
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ValueError("Аудиофайл не найден")
    if resolved.suffix.casefold() not in _AUDIO_SUFFIXES:
        raise ValueError("Неподдерживаемый формат аудиофайла")
    return resolved


def track_fingerprint(path):
    """Fingerprint the file edges and size without reading a whole large track."""
    resolved = _validate_audio_path(path)
    size = resolved.stat().st_size
    digest = hashlib.blake2b(digest_size=20)
    digest.update(str(size).encode("ascii"))
    with resolved.open("rb") as audio_file:
        digest.update(audio_file.read(_FINGERPRINT_CHUNK_SIZE))
        if size > _FINGERPRINT_CHUNK_SIZE:
            audio_file.seek(max(0, size - _FINGERPRINT_CHUNK_SIZE))
            digest.update(audio_file.read(_FINGERPRINT_CHUNK_SIZE))
    return digest.hexdigest()


def _empty_store():
    return {"version": 2, "entries": {}}


def _load_unlocked():
    if not GENRE_REVIEW_FILE.exists():
        return _empty_store()
    try:
        with GENRE_REVIEW_FILE.open("r", encoding="utf-8") as store_file:
            data = json.load(store_file)
    except (OSError, ValueError, TypeError):
        return _empty_store()
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return _empty_store()
    data.setdefault("version", 1)
    return data


def _save_unlocked(data):
    GENRE_REVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = GENRE_REVIEW_FILE.with_suffix(GENRE_REVIEW_FILE.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as store_file:
        json.dump(data, store_file, ensure_ascii=False, indent=2)
        store_file.flush()
        os.fsync(store_file.fileno())
    os.replace(temporary, GENRE_REVIEW_FILE)


def _public_entry(entry):
    allowed = {
        "id", "status", "path", "filename", "predicted_genre", "confidence",
        "top_candidates", "rejected_reasons", "segment_disagreement",
        "corrected_base_genre", "corrected_language", "corrected_version_type",
        "exclude_from_training", "style_override", "reviewed", "reason",
        "review_true_style", "review_predicted_style", "review_folder_id",
        "review_confidence", "review_margin", "review_reasons",
        "size", "note", "created_at", "updated_at",
    }
    return {key: entry.get(key) for key in allowed if key in entry}


def _is_training_override(entry):
    return bool(
        entry.get("exclude_from_training")
        or entry.get("reviewed")
        or entry.get("style_override")
        or entry.get("status") == "corrected"
    )


def list_review_entries(status=None):
    with _STORE_LOCK:
        entries = list(_load_unlocked()["entries"].values())
    if status:
        entries = [entry for entry in entries if entry.get("status") == status]
    entries.sort(key=lambda entry: entry.get("updated_at", ""), reverse=True)
    return [_public_entry(entry) for entry in entries]


def get_manual_correction(path):
    path_key = _path_key(path)
    with _STORE_LOCK:
        entries = _load_unlocked()["entries"].values()
        for entry in entries:
            if entry.get("status") == "corrected" and entry.get("path_key") == path_key:
                return _public_entry(entry)
    return None


def training_override_index():
    """Return lightweight path/id indexes without fingerprinting the library.

    Fingerprints remain the durable primary keys in the JSON store.  The path
    index is used while building large training pools so that reviewing a few
    tracks does not cause thousands of audio files to be read again.
    """
    with _STORE_LOCK:
        entries = list(_load_unlocked()["entries"].values())
    by_path = {}
    by_id = {}
    by_hint = {}
    by_filename = {}
    by_size = {}
    for entry in entries:
        if not _is_training_override(entry):
            continue
        public = _public_entry(entry)
        entry_id = str(entry.get("id") or "")
        path_key = entry.get("path_key")
        if entry_id:
            by_id[entry_id] = public
        if path_key:
            by_path[path_key] = public
        filename = str(entry.get("filename") or "").casefold()
        size = entry.get("size")
        if filename and isinstance(size, int):
            by_hint.setdefault((filename, size), []).append(entry_id)
            by_filename.setdefault(filename, set()).add(size)
            by_size.setdefault(size, []).append(entry_id)
    return {
        "by_path": by_path, "by_id": by_id, "by_hint": by_hint,
        "by_filename": {key: sorted(values) for key, values in by_filename.items()},
        "by_size": by_size,
    }


def find_training_override(index, path):
    """Fast path lookup with fingerprint verification only for moved matches."""
    index = index if isinstance(index, dict) else training_override_index()
    exact = (index.get("by_path") or {}).get(_path_key(path))
    if exact:
        return exact
    try:
        resolved = _validate_audio_path(path)
        candidates = (index.get("by_size") or {}).get(resolved.stat().st_size, [])
    except (OSError, ValueError):
        return None
    if not candidates:
        return None
    try:
        fingerprint = track_fingerprint(resolved)
    except (OSError, ValueError):
        return None
    if fingerprint not in candidates:
        return None
    return (index.get("by_id") or {}).get(fingerprint)


def get_training_override(path, verify_fingerprint=True):
    """Find a persisted training decision by path, then by audio fingerprint."""
    path_key = _path_key(path)
    with _STORE_LOCK:
        data = _load_unlocked()
        for entry in data["entries"].values():
            if _is_training_override(entry) and entry.get("path_key") == path_key:
                return _public_entry(entry)
    if not verify_fingerprint:
        return None
    try:
        fingerprint = track_fingerprint(path)
    except (OSError, ValueError):
        return None
    with _STORE_LOCK:
        entry = _load_unlocked()["entries"].get(fingerprint)
    return _public_entry(entry) if entry and _is_training_override(entry) else None


def save_training_override(
        path,
        *,
        exclude_from_training=False,
        style_override=None,
        clear_style_override=False,
        reviewed=True,
        reason="",
        analysis=None,
):
    """Persist a user decision without touching the underlying audio file."""
    resolved = _validate_audio_path(path)
    if style_override is not None:
        style_override = str(style_override or "").strip()
        if style_override and style_override not in SUPPORTED_BASE_GENRES:
            raise ValueError(f"Неизвестный базовый стиль: {style_override}")
    fingerprint = track_fingerprint(resolved)
    now = _utc_now()
    analysis = analysis if isinstance(analysis, dict) else {}
    with _STORE_LOCK:
        data = _load_unlocked()
        existing = dict(data["entries"].get(fingerprint, {}))
        if clear_style_override:
            effective_style = ""
        elif style_override is None:
            effective_style = str(
                existing.get("style_override")
                or existing.get("corrected_base_genre")
                or ""
            )
        else:
            effective_style = style_override
        entry = {
            **existing,
            "id": fingerprint,
            "status": "corrected" if effective_style else "reviewed",
            "path": str(resolved),
            "path_key": _path_key(resolved),
            "filename": resolved.name,
            "size": resolved.stat().st_size,
            "exclude_from_training": bool(exclude_from_training),
            "style_override": effective_style,
            "reviewed": bool(reviewed),
            "reason": str(reason or "").strip()[:1000],
            "created_at": existing.get("created_at", now),
            "updated_at": now,
        }
        if effective_style:
            entry["corrected_base_genre"] = effective_style
            entry.setdefault("corrected_language", "Auto")
            entry.setdefault("corrected_version_type", "Auto")
        else:
            entry.pop("corrected_base_genre", None)
            entry.pop("corrected_language", None)
            entry.pop("corrected_version_type", None)
        diagnostic_fields = {
            "review_true_style": "true_style",
            "review_predicted_style": "predicted_style",
            "review_folder_id": "folder_id",
            "review_confidence": "confidence",
            "review_margin": "margin",
            "review_reasons": "reasons",
        }
        for target, source in diagnostic_fields.items():
            if source in analysis:
                entry[target] = analysis.get(source)
        data["version"] = max(2, int(data.get("version", 1)))
        data["entries"][fingerprint] = entry
        _save_unlocked(data)
    return _public_entry(entry)


def record_review_candidate(path, analysis):
    resolved = _validate_audio_path(path)
    fingerprint = track_fingerprint(resolved)
    now = _utc_now()
    with _STORE_LOCK:
        data = _load_unlocked()
        existing = data["entries"].get(fingerprint, {})
        if (
            existing.get("status") in {"corrected", "reviewed"}
            or existing.get("exclude_from_training")
            or existing.get("reviewed")
        ):
            return _public_entry(existing)
        entry = {
            "id": fingerprint,
            "status": "pending",
            "path": str(resolved),
            "path_key": _path_key(resolved),
            "filename": resolved.name,
            "size": resolved.stat().st_size,
            "predicted_genre": str(analysis.get("predicted_genre", "Unknown")),
            "confidence": round(float(analysis.get("confidence", 0.0) or 0.0), 6),
            "top_candidates": list(analysis.get("top_candidates") or [])[:3],
            "rejected_reasons": list(analysis.get("rejected_reasons") or []),
            "segment_disagreement": bool(analysis.get("segment_disagreement", False)),
            "created_at": existing.get("created_at", now),
            "updated_at": now,
        }
        data["entries"][fingerprint] = entry
        _save_unlocked(data)
    return _public_entry(entry)


def save_manual_correction(
        path,
        base_genre,
        language="Auto",
        version_type="Auto",
        note="",
        analysis=None,
):
    resolved = _validate_audio_path(path)
    base_genre = str(base_genre or "").strip()
    language = str(language or "Auto").strip()
    version_type = str(version_type or "Auto").strip()
    if base_genre not in SUPPORTED_BASE_GENRES:
        raise ValueError(f"Неизвестный базовый стиль: {base_genre}")
    if language not in SUPPORTED_LANGUAGES | {"Auto"}:
        raise ValueError(f"Неизвестная языковая метка: {language}")
    if version_type not in SUPPORTED_VERSION_TYPES | {"Auto"}:
        raise ValueError(f"Неизвестный тип версии: {version_type}")

    fingerprint = track_fingerprint(resolved)
    now = _utc_now()
    analysis = analysis if isinstance(analysis, dict) else {}
    with _STORE_LOCK:
        data = _load_unlocked()
        existing = data["entries"].get(fingerprint, {})
        entry = {
            **existing,
            "id": fingerprint,
            "status": "corrected",
            "path": str(resolved),
            "path_key": _path_key(resolved),
            "filename": resolved.name,
            "size": resolved.stat().st_size,
            "predicted_genre": str(
                analysis.get("predicted_genre", existing.get("predicted_genre", "Unknown"))
            ),
            "confidence": round(float(
                analysis.get("confidence", existing.get("confidence", 0.0)) or 0.0
            ), 6),
            "top_candidates": list(
                analysis.get("top_candidates", existing.get("top_candidates", [])) or []
            )[:3],
            "rejected_reasons": list(
                analysis.get("rejected_reasons", existing.get("rejected_reasons", [])) or []
            ),
            "segment_disagreement": bool(
                analysis.get("segment_disagreement", existing.get("segment_disagreement", False))
            ),
            "corrected_base_genre": base_genre,
            "style_override": base_genre,
            "exclude_from_training": False,
            "reviewed": True,
            "corrected_language": language,
            "corrected_version_type": version_type,
            "note": str(note or "").strip()[:1000],
            "created_at": existing.get("created_at", now),
            "updated_at": now,
        }
        data["entries"][fingerprint] = entry
        _save_unlocked(data)
    return _public_entry(entry)


def remove_review_entry(entry_id):
    entry_id = str(entry_id or "").strip()
    with _STORE_LOCK:
        data = _load_unlocked()
        removed = data["entries"].pop(entry_id, None)
        if removed is not None:
            _save_unlocked(data)
    return _public_entry(removed) if removed else None


def iter_training_corrections(allowed_base_genres=None):
    allowed = set(allowed_base_genres or SUPPORTED_BASE_GENRES)
    rows = []
    with _STORE_LOCK:
        entries = list(_load_unlocked()["entries"].values())
    for entry in entries:
        path = entry.get("path")
        base_genre = entry.get("corrected_base_genre")
        if (
            entry.get("status") == "corrected"
            and not entry.get("exclude_from_training")
            and base_genre in allowed
            and path
            and Path(path).is_file()
        ):
            rows.append(_public_entry(entry))
    return rows
