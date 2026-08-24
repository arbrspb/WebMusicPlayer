"""Конструктор обучающей выборки и предварительная разметка папок.

Модуль никогда не изменяет аудиофайлы. В JSON сохраняются только источники,
результаты предварительной разметки папок и решения пользователя.
"""
from __future__ import annotations

import hashlib
import csv
import json
import os
import re
import threading
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .paths import (
    GENRE_SETTINGS_FILE,
    TRAINING_CONFLICTS_FILE,
    TRAINING_DATASET_FILE,
    TRAINING_DUPLICATES_FILE,
    TRAINING_ERRORS_FILE,
    TRAINING_LABEL_CONFLICTS_FILE,
    TRAINING_REVIEW_QUEUE_FILE,
    resolve_mapped_music_path,
)
from .track_taxonomy import (
    FAMILY_FALLBACK_ONLY_STYLES,
    STYLE_ALIASES,
    SUPPORTED_BASE_GENRES,
    SUPPORTED_LANGUAGES,
    SUPPORTED_VERSION_TYPES,
    derive_dj_category,
    genre_family,
)
from .genre_review import (
    find_training_override,
    get_training_override,
    list_review_entries,
    save_training_override,
    training_override_index,
)


_LOCK = threading.RLock()
_AUDIO_SUFFIX = ".mp3"
_VALID_STATUSES = {"suggested", "confirmed", "excluded", "ambiguous", "unmapped"}
_DEFAULT_MAX_TRACKS_PER_STYLE = 800
_DEFAULT_MIN_TRACKS_PER_STYLE = 200
_MIN_MAX_TRACKS_PER_STYLE = 100
_MAX_MAX_TRACKS_PER_STYLE = 5000
_DEFAULT_REVIEW_PREVIEW_PERCENT = 30
_MANDATORY_EXCLUDED_STYLES = {
    "Other",
    "Новогодние",
    *FAMILY_FALLBACK_ONLY_STYLES,
}

# Специфичные маркеры идут раньше общих. Поп рядом со стилем считается
# характеристикой контента, а не вторым акустическим стилем.
_STYLE_ALIASES = {
    # The shared taxonomy is only a fallback vocabulary.  Personal aliases
    # below deliberately override it where the user's folder structure has a
    # long-established meaning (for example Downtempo -> Chillout).
    **STYLE_ALIASES,
    "drum and bass": "Drum & Bass", "drum n bass": "Drum & Bass",
    "drumnbass": "Drum & Bass", "drum bass": "Drum & Bass", "dnb": "Drum & Bass",
    "club house": "Club House", "clubhouse": "Club House",
    "deep house": "Deep House", "deephouse": "Deep House",
    "tech house": "Tech House", "techhouse": "Tech House",
    "afro house": "Afro House", "afrohouse": "Afro House",
    "future house": "Future House", "futurehouse": "Future House",
    "bass house": "Bass House",
    "nu disco": "Nu Disco", "nudisco": "Nu Disco",
    "funky disco": "Funky & Disco", "funky and disco": "Funky & Disco",
    "hip hop": "Hip-Hop", "hiphop": "Hip-Hop", "rap": "Hip-Hop",
    "moombahton": "Moombahton", "moombah": "Moombahton", "moomb": "Moombahton",
    "moombahcore": "Moombahcore", "moombahsoul": "Moombahsoul",
    "downtempo": "Chillout", "chillout": "Chillout", "chill": "Chillout",
    "lounge": "Lounge", "trap": "Trap", "rnb": "RnB", "r and b": "RnB",
    "disco": "Disco", "rock": "Rock", "pop": "Pop", "house": "House",
}

_LANGUAGE_ALIASES = {
    "Russian": {"russian", "rus", "ru", "русский", "русская", "русские", "русское", "рус"},
    "English": {"english", "eng", "английский", "английская"},
    "Foreign": {"foreign", "international", "euro", "евро", "зарубежный", "зарубежная", "зарубежные"},
}
_VERSION_ALIASES = {
    "Mashup": {"mashup", "мэшап"}, "Remix": {"remix", "rmx", "ремикс"},
    "Bootleg": {"bootleg"}, "Blend": {"blend"}, "Edit": {"edit", "intro", "outro"},
    "Original": {"original"},
}
_THEME_ALIASES = {"New Year": {"new year", "christmas", "новогод", "новый год"}}
_GENERIC_STYLES = {"House", "Pop"}


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalise(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("&", " and ")
    value = re.sub(r"^[_\s-]+", "", value)
    value = re.sub(r"[^\w\s]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def _has_alias(text, alias):
    return bool(re.search(rf"(?:^|\s){re.escape(alias)}(?:$|\s)", text))


def _empty_store():
    return {
        "version": 4,
        "updated_at": _utc_now(),
        "settings": {
            "max_tracks_per_style": _DEFAULT_MAX_TRACKS_PER_STYLE,
            "min_tracks_per_style": _DEFAULT_MIN_TRACKS_PER_STYLE,
            "excluded_styles": sorted(_MANDATORY_EXCLUDED_STYLES),
            "use_dataset_builder": True,
            "use_reference_samples": True,
            "reference_samples_path": "",
            "use_rekordbox_training": None,
            "review_preview_mode": "percent",
            "review_preview_percent": _DEFAULT_REVIEW_PREVIEW_PERCENT,
            "review_preview_seconds": 60,
        },
        "sources": [],
        "folders": [],
    }


def _sanitise_settings(value):
    value = value if isinstance(value, dict) else {}
    try:
        maximum = int(value.get("max_tracks_per_style", _DEFAULT_MAX_TRACKS_PER_STYLE))
    except (TypeError, ValueError):
        maximum = _DEFAULT_MAX_TRACKS_PER_STYLE
    maximum = max(_MIN_MAX_TRACKS_PER_STYLE, min(_MAX_MAX_TRACKS_PER_STYLE, maximum))
    try:
        minimum = int(value.get("min_tracks_per_style", _DEFAULT_MIN_TRACKS_PER_STYLE))
    except (TypeError, ValueError):
        minimum = _DEFAULT_MIN_TRACKS_PER_STYLE
    minimum = max(20, min(maximum, minimum))
    raw_excluded = value.get("excluded_styles", [])
    if not isinstance(raw_excluded, (list, tuple, set)):
        raw_excluded = []
    excluded = {
        str(style).strip() for style in raw_excluded
        if str(style).strip()
    }
    excluded.update(_MANDATORY_EXCLUDED_STYLES)
    preview_mode = str(value.get("review_preview_mode", "percent") or "percent")
    if preview_mode not in {"percent", "time"}:
        preview_mode = "percent"
    try:
        preview_percent = float(value.get("review_preview_percent", _DEFAULT_REVIEW_PREVIEW_PERCENT))
    except (TypeError, ValueError):
        preview_percent = _DEFAULT_REVIEW_PREVIEW_PERCENT
    try:
        preview_seconds = float(value.get("review_preview_seconds", 60))
    except (TypeError, ValueError):
        preview_seconds = 60
    reference_samples_path = str(value.get("reference_samples_path", "") or "").strip()
    use_rekordbox_training = value.get("use_rekordbox_training")
    if use_rekordbox_training is not None:
        use_rekordbox_training = bool(use_rekordbox_training)
    return {
        "max_tracks_per_style": maximum,
        "min_tracks_per_style": minimum,
        "excluded_styles": sorted(excluded),
        "use_dataset_builder": bool(value.get("use_dataset_builder", True)),
        "use_reference_samples": bool(value.get("use_reference_samples", True)),
        "reference_samples_path": reference_samples_path,
        "use_rekordbox_training": use_rekordbox_training,
        "review_preview_mode": preview_mode,
        "review_preview_percent": max(0.0, min(95.0, preview_percent)),
        "review_preview_seconds": max(0.0, min(3600.0, preview_seconds)),
    }


def _load_unlocked():
    if not TRAINING_DATASET_FILE.exists():
        return _empty_store()
    try:
        with TRAINING_DATASET_FILE.open("r", encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, ValueError, TypeError):
        return _empty_store()
    if not isinstance(data, dict):
        return _empty_store()
    try:
        data["version"] = max(4, int(data.get("version", 4)))
    except (TypeError, ValueError):
        data["version"] = 4
    data["settings"] = _sanitise_settings(data.get("settings"))
    data.setdefault("sources", [])
    data.setdefault("folders", [])
    return data


def _save_unlocked(data):
    TRAINING_DATASET_FILE.parent.mkdir(parents=True, exist_ok=True)
    if TRAINING_DATASET_FILE.exists():
        try:
            with TRAINING_DATASET_FILE.open("r", encoding="utf-8") as source:
                current = json.load(source)
            # Keep the last useful folder review.  An empty snapshot can be a
            # legitimate fresh project, but it must not replace a recoverable
            # review after a temporary NAS outage.
            if isinstance(current, dict) and current.get("folders"):
                backup = TRAINING_DATASET_FILE.with_name(
                    f"{TRAINING_DATASET_FILE.stem}.backup.json"
                )
                backup_temporary = backup.with_suffix(".json.tmp")
                with backup_temporary.open("w", encoding="utf-8") as target:
                    json.dump(current, target, ensure_ascii=False, indent=2)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(backup_temporary, backup)
        except (OSError, ValueError, TypeError):
            # The primary atomic save is still preferable to failing a user
            # action merely because an optional backup could not be written.
            pass
    data["updated_at"] = _utc_now()
    temporary = TRAINING_DATASET_FILE.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as target:
        json.dump(data, target, ensure_ascii=False, indent=2)
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, TRAINING_DATASET_FILE)


def load_training_dataset():
    with _LOCK:
        return json.loads(json.dumps(_load_unlocked(), ensure_ascii=False))


def get_training_dataset_settings():
    with _LOCK:
        return dict(_sanitise_settings(_load_unlocked().get("settings")))


def update_training_dataset_settings(values):
    if not isinstance(values, dict):
        raise ValueError("Настройки обучающей выборки должны быть объектом")
    with _LOCK:
        data = _load_unlocked()
        settings = dict(data.get("settings") or {})
        settings.update(values)
        data["settings"] = _sanitise_settings(settings)
        data["version"] = max(4, int(data.get("version", 4)))
        _save_unlocked(data)
        return dict(data["settings"])


def _path_key(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _stable_id(*values):
    raw = "\0".join(str(value) for value in values)
    return hashlib.sha1(raw.encode("utf-8", "surrogatepass")).hexdigest()[:20]


def _resolve_source_path(path, music_root=None):
    raw = str(path or "").strip()
    if not raw:
        raise ValueError("Не выбрана папка")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute() and music_root:
        candidate = Path(music_root).expanduser() / candidate
    candidate = candidate.resolve()
    if not candidate.is_dir():
        raise ValueError(f"Папка не найдена: {candidate}")
    return candidate


def add_training_source(path, music_root=None, recursive=True):
    resolved = _resolve_source_path(path, music_root)
    source_id = _stable_id(_path_key(resolved))
    relative_path = ""
    if music_root:
        try:
            relative_path = os.path.relpath(resolved, Path(music_root).resolve()).replace("\\", "/")
            if relative_path == ".":
                relative_path = ""
            if relative_path.startswith("../") or relative_path == "..":
                relative_path = ""
        except (OSError, ValueError):
            relative_path = ""
    with _LOCK:
        data = _load_unlocked()
        for source in data["sources"]:
            if source.get("id") == source_id:
                source.update({"enabled": True, "recursive": bool(recursive)})
                _save_unlocked(data)
                return dict(source)
        source = {
            "id": source_id,
            "path": str(resolved),
            "relative_path": relative_path,
            "name": resolved.name or str(resolved),
            "recursive": bool(recursive),
            "enabled": True,
            "created_at": _utc_now(),
        }
        data["sources"].append(source)
        _save_unlocked(data)
        return dict(source)


def remove_training_source(source_id):
    with _LOCK:
        data = _load_unlocked()
        before = len(data["sources"])
        data["sources"] = [row for row in data["sources"] if row.get("id") != source_id]
        data["folders"] = [row for row in data["folders"] if row.get("source_id") != source_id]
        removed = len(data["sources"]) != before
        if removed:
            _save_unlocked(data)
        return removed


def _load_legacy_rules():
    try:
        with GENRE_SETTINGS_FILE.open("r", encoding="utf-8") as source:
            values = json.load(source)
    except (OSError, ValueError, TypeError):
        return {}
    return values if isinstance(values, dict) else {}


def _legacy_taxonomy(parts):
    rules = _load_legacy_rules()
    normalised_rules = {_normalise(key): value for key, value in rules.items()}
    for part in reversed(parts):
        value = normalised_rules.get(_normalise(part))
        genre = value.get("genre") if isinstance(value, dict) else value
        if genre in SUPPORTED_BASE_GENRES:
            return genre, "legacy folder_keywords"
        if genre == "Русские Ремиксы":
            return "Club House", "legacy Russian Remixes"
    return "", ""


def infer_folder_taxonomy(folder_path, source_path=None):
    """Предлагает многомерную метку, не анализируя и не изменяя аудио."""
    folder = Path(folder_path)
    try:
        relative = folder.relative_to(Path(source_path)) if source_path else folder
        parts = [part for part in relative.parts if part not in {".", ""}]
        if source_path:
            source_name = Path(source_path).name
            if source_name and (not parts or parts[0] != source_name):
                parts.insert(0, source_name)
    except ValueError:
        parts = list(folder.parts)
    if not parts:
        parts = [folder.name]

    chosen = []
    matched_part = ""
    for part in reversed(parts):
        text = _normalise(part)
        matches = []
        for alias in sorted(_STYLE_ALIASES, key=len, reverse=True):
            if _has_alias(text, alias):
                canonical = _STYLE_ALIASES[alias]
                if canonical not in matches:
                    matches.append(canonical)
        # Более конкретный House-подстиль подавляет общий House.
        if "House" in matches and any(item.endswith("House") and item != "House" for item in matches):
            matches.remove("House")
        # Pop рядом с акустическим стилем хранится отдельно как content_genre.
        acoustic = [item for item in matches if item != "Pop"]
        if acoustic or matches:
            chosen = acoustic or matches
            matched_part = part
            break

    legacy_style, legacy_reason = _legacy_taxonomy(parts)
    reasons = []
    if legacy_style:
        # Personal folder_keywords are the primary taxonomy.  The universal
        # alias dictionary above is only a fallback for folders that have no
        # explicit user rule.
        chosen = [legacy_style]
        rules = _load_legacy_rules()
        normalised_rules = {_normalise(key) for key in rules}
        matched_part = next(
            (part for part in reversed(parts) if _normalise(part) in normalised_rules),
            parts[-1],
        )
        reasons.append(legacy_reason)

    all_text = " ".join(_normalise(part) for part in parts)
    language = "Unknown"
    for canonical, aliases in _LANGUAGE_ALIASES.items():
        if any(_has_alias(all_text, alias) for alias in aliases):
            language = canonical
            break
    version_type = "Unknown"
    for canonical, aliases in _VERSION_ALIASES.items():
        if any(_has_alias(all_text, alias) for alias in aliases):
            version_type = canonical
            break
    theme = ""
    for canonical, aliases in _THEME_ALIASES.items():
        if any(alias in all_text for alias in aliases):
            theme = canonical
            break
    content_genre = "Pop" if _has_alias(all_text, "pop") and chosen != ["Pop"] else ""

    if "russian remix" in all_text or (language == "Russian" and version_type == "Remix"):
        if not chosen:
            chosen = ["Club House"]
            reasons.append("Russian Remix → Club House по правилу коллекции")
        language = "Russian"
        version_type = "Remix"

    conflicts = []
    if len(chosen) > 1:
        conflicts.append("несколько стилей: " + ", ".join(chosen))
    base_style = chosen[0] if len(chosen) == 1 else ""
    if base_style:
        reasons.append(f"{matched_part or parts[-1]} → {base_style}")
    if conflicts:
        status, confidence = "ambiguous", 0.35
    elif not base_style:
        status, confidence = "unmapped", 0.0
    else:
        exact = _normalise(matched_part) in _STYLE_ALIASES
        confidence = 0.95 if exact else (0.86 if base_style not in _GENERIC_STYLES else 0.72)
        if legacy_reason and not matched_part:
            confidence = min(confidence, 0.82)
        status = "suggested"

    return {
        "base_style": base_style,
        "genre_family": genre_family(base_style) if base_style else "Other",
        "content_genre": content_genre,
        "language": language,
        "version_type": version_type,
        "theme": theme,
        "dj_category": derive_dj_category(base_style, language) if base_style else "Other",
        "confidence": round(confidence, 3),
        "status": status,
        "conflicts": conflicts,
        "reasons": reasons,
    }


def _direct_mp3_count(folder):
    try:
        return sum(
            1 for entry in os.scandir(folder)
            if entry.is_file() and entry.name.casefold().endswith(_AUDIO_SUFFIX)
        )
    except OSError:
        return 0


def preview_training_sources(progress=None):
    progress = progress if isinstance(progress, dict) else {}
    progress.update({"status": "running", "processed": 0, "total": 0, "folders": 0, "tracks": 0, "error": ""})
    try:
        with _LOCK:
            data = _load_unlocked()
            sources = [dict(row) for row in data["sources"] if row.get("enabled", True)]
            previous = {row.get("id"): row for row in data["folders"]}
            previous_by_source = defaultdict(list)
            for row in data["folders"]:
                previous_by_source[row.get("source_id")].append(dict(row))
        rows = []
        available_sources = 0
        unavailable_sources = []
        for source in sources:
            root_path = source.get("path", "")
            if not os.path.isdir(root_path):
                unavailable_sources.append(root_path or source.get("name", ""))
                rows.extend(previous_by_source.get(source.get("id"), []))
                continue
            walk_errors = []
            if source.get("recursive", True):
                walker = os.walk(root_path, onerror=walk_errors.append)
            else:
                try:
                    walker = [(root_path, [], os.listdir(root_path))]
                except OSError as exc:
                    walk_errors.append(exc)
                    walker = []
            source_rows = []
            source_tracks = 0
            for root, directories, _files in walker:
                directories[:] = [name for name in directories if not name.startswith(".")]
                count = _direct_mp3_count(root)
                progress["processed"] = int(progress.get("processed", 0)) + 1
                if not count:
                    continue
                relative = os.path.relpath(root, root_path).replace("\\", "/")
                if relative == ".":
                    relative = ""
                row_id = _stable_id(source["id"], _path_key(root))
                inferred = infer_folder_taxonomy(root, root_path)
                row = {
                    "id": row_id,
                    "source_id": source["id"],
                    "source_name": source.get("name", ""),
                    "path": str(Path(root).resolve()),
                    "relative_path": relative,
                    "track_count": count,
                    **inferred,
                }
                old = previous.get(row_id, {})
                if old.get("status") in {"confirmed", "excluded"}:
                    for key in (
                        "status", "base_style", "genre_family", "content_genre", "language",
                        "version_type", "theme", "dj_category", "confidence", "note",
                    ):
                        if key in old:
                            row[key] = old[key]
                source_rows.append(row)
                source_tracks += count
            if walk_errors:
                unavailable_sources.append(root_path or source.get("name", ""))
                rows.extend(previous_by_source.get(source.get("id"), []))
                continue
            available_sources += 1
            rows.extend(source_rows)
            progress["folders"] = len(rows)
            progress["tracks"] = sum(int(row.get("track_count", 0) or 0) for row in rows)
        if sources and available_sources == 0:
            raise OSError(
                "Ни один источник обучающей выборки недоступен. "
                "Предыдущая разметка сохранена; проверьте подключение и права к сетевой папке."
            )
        rows.sort(key=lambda row: (row.get("source_name", ""), row.get("relative_path", "")))
        with _LOCK:
            data = _load_unlocked()
            data["folders"] = rows
            _save_unlocked(data)
        progress.update({
            "status": "completed",
            "total": progress.get("processed", 0),
            "unavailable_sources": unavailable_sources,
        })
        return dataset_summary()
    except Exception as exc:
        progress.update({"status": "error", "error": str(exc)})
        raise


def dataset_summary():
    with _LOCK:
        data = _load_unlocked()
    status_counts = Counter(row.get("status", "unmapped") for row in data["folders"])
    status_track_counts = Counter()
    for row in data["folders"]:
        status_track_counts[row.get("status", "unmapped")] += int(row.get("track_count", 0) or 0)
    confirmed_rows = [row for row in data["folders"] if row.get("status") == "confirmed"]
    style_folder_counts = Counter(
        row.get("base_style") for row in confirmed_rows if row.get("base_style")
    )
    style_track_counts = Counter()
    for row in confirmed_rows:
        if row.get("base_style"):
            style_track_counts[row["base_style"]] += int(row.get("track_count", 0))
    return {
        "version": data.get("version", 4),
        "updated_at": data.get("updated_at"),
        "settings": _sanitise_settings(data.get("settings")),
        "sources": data["sources"],
        "folder_count": len(data["folders"]),
        "track_count": sum(int(row.get("track_count", 0)) for row in data["folders"]),
        "confirmed_tracks": sum(
            int(row.get("track_count", 0)) for row in data["folders"]
            if row.get("status") == "confirmed"
        ),
        "status_counts": dict(status_counts),
        "status_track_counts": dict(status_track_counts),
        "style_counts": dict(style_track_counts),
        "style_track_counts": dict(style_track_counts),
        "style_folder_counts": dict(style_folder_counts),
    }


def list_training_folders(
        offset=0, limit=100, status=None, query=None, style=None,
        track_range=None, sort_by="path", sort_dir="asc",
):
    with _LOCK:
        rows = list(_load_unlocked()["folders"])
    available_styles = sorted({
        str(row.get("base_style") or "").strip()
        for row in rows if str(row.get("base_style") or "").strip()
    })
    if status and status != "all":
        rows = [row for row in rows if row.get("status") == status]
    if style and style != "all":
        rows = [row for row in rows if row.get("base_style") == style]
    track_filters = {
        "lt20": lambda count: count < 20,
        "20-49": lambda count: 20 <= count <= 49,
        "50-99": lambda count: 50 <= count <= 99,
        "100plus": lambda count: count >= 100,
    }
    if track_range and track_range != "all":
        predicate = track_filters.get(str(track_range))
        if predicate is None:
            raise ValueError("Неизвестный диапазон количества треков")
        rows = [row for row in rows if predicate(int(row.get("track_count", 0) or 0))]
    if query:
        needle = _normalise(query)
        rows = [row for row in rows if needle in _normalise(row.get("path", "")) or needle in _normalise(row.get("base_style", ""))]
    sort_keys = {
        "path": lambda row: _normalise(row.get("relative_path") or row.get("path", "")),
        "tracks": lambda row: int(row.get("track_count", 0) or 0),
        "style": lambda row: _normalise(row.get("base_style", "")),
        "status": lambda row: _normalise(row.get("status", "")),
    }
    sort_key = sort_keys.get(str(sort_by or "path"))
    if sort_key is None:
        raise ValueError("Неизвестное поле сортировки папок")
    direction = str(sort_dir or "asc").lower()
    if direction not in {"asc", "desc"}:
        raise ValueError("Неизвестное направление сортировки папок")
    rows.sort(key=sort_key, reverse=direction == "desc")
    total = len(rows)
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or 100), 500))
    return {
        "items": rows[offset:offset + limit],
        "offset": offset,
        "limit": limit,
        "total": total,
        "available_styles": available_styles,
        "sort_by": str(sort_by or "path"),
        "sort_dir": direction,
    }


def _read_training_csv(path):
    try:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as source:
            return list(csv.DictReader(source))
    except (OSError, UnicodeError, csv.Error):
        return []


def _portable_path(value):
    """Normalise a report path without requiring its drive mapping to exist."""
    raw = str(value or "").strip().replace("\\", "/")
    raw = re.sub(r"/+", "/", raw).rstrip("/")
    return unicodedata.normalize("NFKC", raw).casefold()


def _folder_report_aliases(row):
    aliases = {_portable_path(row.get("path"))}
    source_name = str(row.get("source_name") or "").strip("/\\")
    relative_path = str(row.get("relative_path") or "").strip("/\\")
    if source_name:
        aliases.add(_portable_path("/".join(filter(None, (source_name, relative_path)))))
    return {alias for alias in aliases if alias}


def _portable_track_key(value):
    path = _portable_path(value)
    return re.sub(r"^(?:[a-z]:|//[^/]+/[^/]+)", "", path).lstrip("/")


def _mixed_folder_name_signals(row):
    """Return name-only warnings; these never alter or exclude a label."""
    name = Path(str(row.get("path") or "")).name
    normalised = _normalise(name)
    matched = []
    for alias in sorted(_STYLE_ALIASES, key=len, reverse=True):
        if _has_alias(normalised, alias):
            style = _STYLE_ALIASES[alias]
            if style not in matched:
                matched.append(style)
    if "House" in matched and any(
            style.endswith("House") and style != "House" for style in matched
    ):
        matched.remove("House")
    signals = []
    if len(matched) > 1:
        signals.append("несколько стилей в названии: " + ", ".join(matched))
    format_markers = [
        marker for marker in ("open format", "twerk", "vocal house", "mainstage")
        if _has_alias(normalised, marker)
    ]
    if format_markers:
        signals.append("возможный смешанный источник: " + ", ".join(format_markers))
    return signals


def _confirmed_folder_matcher():
    """Return a snapshot and matcher shared by folder- and track-level review."""
    with _LOCK:
        folders = [dict(row) for row in _load_unlocked()["folders"]]
    confirmed = [row for row in folders if row.get("status") == "confirmed"]
    exact_alias = {}
    suffix_alias = []
    for row in confirmed:
        for alias in _folder_report_aliases(row):
            if alias.startswith("//") or re.match(r"^[a-z]:/", alias):
                exact_alias.setdefault(alias, row)
            else:
                suffix_alias.append((alias, row))
    suffix_alias.sort(key=lambda item: len(item[0]), reverse=True)

    def match_track(track_path):
        directory = _portable_path(os.path.dirname(str(track_path or "")))
        direct = exact_alias.get(directory)
        if direct is not None:
            return direct
        logical = re.sub(r"^(?:[a-z]:|//[^/]+/[^/]+)", "", directory).lstrip("/")
        for suffix, row in suffix_alias:
            if logical == suffix or logical.endswith("/" + suffix):
                return row
        return None

    return folders, confirmed, match_track


def build_training_problem_folders():
    """Aggregate the latest training diagnostics by confirmed source folder.

    The result is read-only: model predictions are diagnostic signals and never
    change folder labels or statuses automatically.
    """
    folders, confirmed, match_track = _confirmed_folder_matcher()
    by_id = {str(row.get("id")): row for row in confirmed if row.get("id")}

    stats = defaultdict(lambda: {
        "validation_paths": set(),
        "error_paths": set(),
        "review_paths": set(),
        "label_conflict_paths": set(),
        "fingerprint_duplicate_paths": set(),
        "group_duplicate_paths": set(),
        "reported_paths": {},
        "pairs": Counter(),
    })
    unassigned = Counter()

    def folder_stats(track_path, source_name):
        row = match_track(track_path)
        if row is None:
            unassigned[source_name] += 1
            return None, None
        return row, stats[str(row["id"])]

    for item in _read_training_csv(TRAINING_ERRORS_FILE):
        path = item.get("path", "")
        row, current = folder_stats(path, "training_errors")
        if row is None:
            continue
        key = _portable_track_key(path)
        current["reported_paths"].setdefault(key, path)
        current["validation_paths"].add(key)
        if str(item.get("is_error", "0")) == "1":
            current["error_paths"].add(key)
            pair = (str(item.get("true_base_genre") or "?"), str(item.get("predicted_base_genre") or "?"))
            current["pairs"][pair] += 1

    for item in _read_training_csv(TRAINING_REVIEW_QUEUE_FILE):
        path = item.get("path", "")
        row, current = folder_stats(path, "training_review_queue")
        if row is None:
            continue
        key = _portable_track_key(path)
        current["reported_paths"].setdefault(key, path)
        current["review_paths"].add(key)

    for item in _read_training_csv(TRAINING_LABEL_CONFLICTS_FILE):
        path = item.get("path", "")
        row, current = folder_stats(path, "training_label_conflicts")
        if row is None:
            continue
        decision = str(item.get("decision") or "")
        key = _portable_track_key(path)
        current["reported_paths"].setdefault(key, path)
        if decision == "conflicting_labels_excluded":
            current["label_conflict_paths"].add(key)
        elif decision == "duplicate_excluded":
            current["fingerprint_duplicate_paths"].add(key)

    duplicate_rows = _read_training_csv(TRAINING_DUPLICATES_FILE)
    duplicate_group_sizes = Counter(str(item.get("group") or "") for item in duplicate_rows)
    for item in duplicate_rows:
        group = str(item.get("group") or "")
        if not group or duplicate_group_sizes[group] < 2:
            continue
        path = item.get("path", "")
        row, current = folder_stats(path, "training_duplicates")
        if row is not None:
            current["group_duplicate_paths"].add(_portable_track_key(path))

    override_index = training_override_index()
    rows = []
    for folder_id, current in stats.items():
        row = by_id.get(folder_id)
        if row is None:
            continue
        track_count = max(0, int(row.get("track_count", 0) or 0))
        validation_count = len(current["validation_paths"])
        error_count = len(current["error_paths"])
        review_count = len(current["review_paths"])
        label_conflicts = len(current["label_conflict_paths"])
        fingerprint_duplicates = len(current["fingerprint_duplicate_paths"])
        group_duplicates = len(current["group_duplicate_paths"])
        disputed_paths = (
            current["error_paths"]
            | current["review_paths"]
            | current["label_conflict_paths"]
        )
        review_states = Counter()
        style_override_tracks = 0
        for track_key in disputed_paths:
            if track_key in current["label_conflict_paths"]:
                review_states["automatic"] += 1
                continue
            reported_path = current["reported_paths"].get(track_key, track_key)
            override = find_training_override(override_index, reported_path)
            if (override or {}).get("exclude_from_training"):
                review_states["excluded"] += 1
            elif (override or {}).get("reviewed"):
                review_states["reviewed"] += 1
                if (override or {}).get("style_override"):
                    style_override_tracks += 1
            else:
                review_states["pending"] += 1
        disputed_percent = min(100.0, 100.0 * len(disputed_paths) / max(1, track_count))
        validation_error_rate = (
            error_count / validation_count if validation_count else 0.0
        )
        name_warnings = _mixed_folder_name_signals(row)
        score = (
            review_count * 3.0
            + error_count
            + label_conflicts * 4.0
            + fingerprint_duplicates * 0.5
            + min(5.0, group_duplicates * 0.1)
            + validation_error_rate * 5.0
            + (5.0 if name_warnings else 0.0)
        )
        if (
            review_count >= 8
            or error_count >= 10
            or label_conflicts >= 2
            or (validation_count >= 5 and validation_error_rate >= 0.6)
        ):
            risk = "high"
        elif (
            review_count >= 2
            or error_count >= 3
            or label_conflicts
            or name_warnings
            or (validation_count >= 5 and validation_error_rate >= 0.5)
        ):
            risk = "medium"
        elif (
            review_count
            or error_count
            or fingerprint_duplicates
            or group_duplicates
        ):
            # Keep the existing high/medium thresholds intact.  Remaining
            # folders with a real diagnostic signal form the low-risk UI tier.
            risk = "low"
        else:
            continue
        reasons = []
        if review_count:
            reasons.append(f"review queue: {review_count}")
        if error_count:
            reasons.append(f"validation errors: {error_count} из {validation_count}")
        if label_conflicts:
            reasons.append(f"конфликты fingerprint-меток: {label_conflicts}")
        reasons.extend(name_warnings)
        pairs = [
            {"true_style": pair[0], "predicted_style": pair[1], "count": count}
            for pair, count in current["pairs"].most_common(5)
        ]
        rows.append({
            **row,
            "training_tracks": track_count,
            "review_queue_tracks": review_count,
            "validation_tracks": validation_count,
            "validation_errors": error_count,
            "validation_error_rate": round(validation_error_rate, 6),
            "disputed_tracks": len(disputed_paths),
            "pending_disputed_tracks": review_states["pending"],
            "reviewed_disputed_tracks": review_states["reviewed"],
            "excluded_disputed_tracks": review_states["excluded"],
            "automatic_disputed_tracks": review_states["automatic"],
            "style_override_tracks": style_override_tracks,
            "review_resolved_tracks": review_states["reviewed"] + review_states["excluded"] + review_states["automatic"],
            "review_complete": bool(disputed_paths) and review_states["pending"] == 0,
            "disputed_percent": round(disputed_percent, 2),
            "label_conflicts": label_conflicts,
            "fingerprint_duplicates": fingerprint_duplicates,
            "group_duplicate_tracks": group_duplicates,
            "confusion_pairs": pairs,
            "mixed_name_warning": bool(name_warnings),
            "name_warnings": name_warnings,
            "problem_reasons": reasons,
            "risk": risk,
            "risk_score": round(score, 3),
        })

    rows.sort(key=lambda item: (-item["risk_score"], -item["disputed_percent"], item.get("path", "")))
    current_tracks_by_style = Counter()
    for row in confirmed:
        if row.get("base_style"):
            current_tracks_by_style[row["base_style"]] += int(row.get("track_count", 0) or 0)
    top_rows = rows[:20]
    removed_by_style = Counter()
    for row in top_rows:
        if row.get("base_style"):
            removed_by_style[row["base_style"]] += int(row.get("track_count", 0) or 0)
    remaining = {
        style: max(0, count - removed_by_style.get(style, 0))
        for style, count in sorted(current_tracks_by_style.items())
    }
    report_files = [
        TRAINING_ERRORS_FILE,
        TRAINING_REVIEW_QUEUE_FILE,
        TRAINING_LABEL_CONFLICTS_FILE,
        TRAINING_DUPLICATES_FILE,
        TRAINING_CONFLICTS_FILE,
    ]
    mtimes = [Path(path).stat().st_mtime for path in report_files if Path(path).is_file()]
    return {
        "items": rows,
        "summary": {
            "report_available": bool(mtimes),
            "report_updated_at": (
                datetime.fromtimestamp(max(mtimes), timezone.utc).isoformat(timespec="seconds")
                if mtimes else None
            ),
            "confirmed_folders": len(confirmed),
            "excluded_folders": sum(row.get("status") == "excluded" for row in folders),
            "confirmed_tracks": sum(current_tracks_by_style.values()),
            "problem_folders": len(rows),
            "attention_folders": sum(not row["review_complete"] for row in rows),
            "reviewed_problem_folders": sum(row["review_complete"] for row in rows),
            "high_risk_folders": sum(row["risk"] == "high" for row in rows),
            "medium_risk_folders": sum(row["risk"] == "medium" for row in rows),
            "low_risk_folders": sum(row["risk"] == "low" for row in rows),
            "pending_disputed_tracks": sum(row["pending_disputed_tracks"] for row in rows),
            "manually_resolved_disputed_tracks": sum(
                row["reviewed_disputed_tracks"] + row["excluded_disputed_tracks"]
                for row in rows
            ),
            "automatic_disputed_tracks": sum(row["automatic_disputed_tracks"] for row in rows),
            "current_tracks_by_style": dict(sorted(current_tracks_by_style.items())),
            "top_20_projection": {
                "folder_ids": [row["id"] for row in top_rows],
                "removed_tracks": sum(removed_by_style.values()),
                "removed_by_style": dict(sorted(removed_by_style.items())),
                "remaining_by_style": remaining,
            },
            "unassigned_report_rows": dict(unassigned),
        },
    }


def list_training_problem_folders(offset=0, limit=100, query=None):
    report = build_training_problem_folders()
    rows = report["items"]
    if query:
        needle = _normalise(query)
        rows = [
            row for row in rows
            if needle in _normalise(row.get("path", ""))
            or needle in _normalise(row.get("base_style", ""))
            or needle in _normalise(" ".join(row.get("problem_reasons") or []))
        ]
    total = len(rows)
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or 100), 2000))
    return {
        "items": rows[offset:offset + limit],
        "offset": offset,
        "limit": limit,
        "total": total,
        "summary": report["summary"],
    }


def build_training_disputed_tracks(music_dir=None):
    """Merge existing diagnostics into a read-only per-track review list.

    Validation mistakes are recommendations only.  The objective exclusions
    reported by the existing pipeline are exposed as such but are not copied
    into manual overrides.
    """
    _folders, confirmed, match_track = _confirmed_folder_matcher()
    rows = {}

    def ensure(report_path):
        raw_path = str(report_path or "").strip()
        if not raw_path:
            return None
        key = _portable_track_key(raw_path)
        folder = match_track(raw_path)
        if folder is None:
            return None
        resolved = resolve_mapped_music_path(raw_path, music_dir)
        if not os.path.isfile(resolved):
            candidate = os.path.join(str(folder.get("path") or ""), os.path.basename(raw_path))
            if os.path.isfile(candidate):
                resolved = candidate
        current = rows.setdefault(key, {
            "id": _stable_id("training-review", key),
            "path": resolved,
            "reported_path": raw_path,
            "filename": os.path.basename(raw_path),
            "folder_id": str(folder.get("id") or ""),
            "folder_path": str(folder.get("path") or ""),
            "folder_relative_path": str(folder.get("relative_path") or folder.get("path") or ""),
            "folder_track_count": int(folder.get("track_count", 0) or 0),
            "folder_style": str(folder.get("base_style") or ""),
            "true_style": str(folder.get("base_style") or ""),
            "predicted_style": "",
            "confidence": None,
            "second_style": "",
            "second_probability": None,
            "margin": None,
            "group_id": "",
            "reasons": set(),
            "objective_excluded": False,
            "objective_decisions": set(),
        })
        return current

    for item in _read_training_csv(TRAINING_ERRORS_FILE):
        if str(item.get("is_error", "0")) != "1":
            continue
        current = ensure(item.get("path"))
        if current is None:
            continue
        current["true_style"] = str(item.get("true_base_genre") or current["true_style"])
        current["predicted_style"] = str(item.get("predicted_base_genre") or "")
        current["confidence"] = _safe_float(item.get("top1_probability"))
        current["second_style"] = str(item.get("second_genre") or "")
        current["second_probability"] = _safe_float(item.get("second_probability"))
        current["margin"] = _safe_float(item.get("margin"))
        current["group_id"] = str(item.get("group") or current["group_id"])
        current["reasons"].add("validation_error")

    for item in _read_training_csv(TRAINING_REVIEW_QUEUE_FILE):
        current = ensure(item.get("path"))
        if current is None:
            continue
        current["true_style"] = str(item.get("true_style") or current["true_style"])
        current["predicted_style"] = str(item.get("predicted_style") or current["predicted_style"])
        current["confidence"] = _safe_float(item.get("confidence"), current["confidence"])
        current["second_style"] = str(item.get("second_style") or current["second_style"])
        current["second_probability"] = _safe_float(
            item.get("second_probability"), current["second_probability"]
        )
        current["margin"] = _safe_float(item.get("margin"), current["margin"])
        current["group_id"] = str(item.get("group") or current["group_id"])
        current["reasons"].add("review_queue")

    for item in _read_training_csv(TRAINING_LABEL_CONFLICTS_FILE):
        decision = str(item.get("decision") or "")
        if decision not in {"conflicting_labels_excluded", "duplicate_excluded"}:
            continue
        current = ensure(item.get("path"))
        if current is None:
            continue
        current["group_id"] = str(item.get("fingerprint_group") or current["group_id"])
        current["objective_excluded"] = True
        current["objective_decisions"].add(decision)
        current["reasons"].add(
            "fingerprint_label_conflict"
            if decision == "conflicting_labels_excluded" else "strict_duplicate"
        )

    override_index = training_override_index()
    result = []
    reason_order = {
        "fingerprint_label_conflict": 0, "strict_duplicate": 1,
        "review_queue": 2, "validation_error": 3,
    }
    for current in rows.values():
        override = find_training_override(override_index, current["path"])
        current["reasons"] = sorted(current["reasons"], key=lambda value: reason_order.get(value, 99))
        current["objective_decisions"] = sorted(current["objective_decisions"])
        current["override"] = override or {}
        current["review_status"] = (
            "automatic" if current["objective_excluded"]
            else "excluded" if (override or {}).get("exclude_from_training")
            else "reviewed" if (override or {}).get("reviewed")
            else "pending"
        )
        current["effective_style"] = str(
            (override or {}).get("style_override") or current["true_style"]
        )
        current["confusion_pair"] = (
            f'{current["true_style"]} → {current["predicted_style"]}'
            if current["true_style"] and current["predicted_style"] else ""
        )
        result.append(current)
    result.sort(key=lambda row: (
        row["review_status"] != "pending",
        row["folder_style"], row["folder_relative_path"], row["filename"],
    ))
    return {
        "items": result,
        "summary": {
            "total": len(result),
            "pending": sum(row["review_status"] == "pending" for row in result),
            "reviewed": sum(row["review_status"] == "reviewed" for row in result),
            "excluded": sum(row["review_status"] == "excluded" for row in result),
            "automatic": sum(row["review_status"] == "automatic" for row in result),
            "styles": sorted({row["true_style"] for row in result if row["true_style"]}),
            "confirmed_folders": len(confirmed),
        },
    }


def _safe_float(value, fallback=None):
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return fallback


def list_training_disputed_tracks(
        offset=0, limit=50, query=None, folder_id=None, style=None,
        confused_with=None, status=None, music_dir=None,
):
    report = build_training_disputed_tracks(music_dir=music_dir)
    rows = _filter_training_disputed_tracks(
        report["items"], query=query, folder_id=folder_id, style=style,
        confused_with=confused_with, status=status,
    )
    total = len(rows)
    confused_styles = sorted({row["predicted_style"] for row in rows if row["predicted_style"]})
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or 50), 500))
    summary = dict(report["summary"])
    summary["filtered_total"] = total
    summary["confused_styles"] = confused_styles
    return {"items": rows[offset:offset + limit], "offset": offset, "limit": limit, "total": total, "summary": summary}


def _filter_training_disputed_tracks(
        rows, query=None, folder_id=None, style=None, confused_with=None, status=None,
):
    rows = list(rows or [])
    if query:
        needle = _normalise(query)
        rows = [row for row in rows if needle in _normalise(
            " ".join((row["filename"], row["folder_relative_path"], row["true_style"], row["predicted_style"]))
        )]
    if folder_id:
        rows = [row for row in rows if row["folder_id"] == str(folder_id)]
    if style and style != "all":
        rows = [row for row in rows if row["true_style"] == style]
    if confused_with and confused_with != "all":
        rows = [row for row in rows if row["predicted_style"] == confused_with]
    if status and status != "all":
        rows = [row for row in rows if row["review_status"] == status]
    return rows


def training_disputed_track_ids(
        query=None, folder_id=None, style=None, confused_with=None, status=None,
        music_dir=None,
):
    report = build_training_disputed_tracks(music_dir=music_dir)
    rows = _filter_training_disputed_tracks(
        report["items"], query=query, folder_id=folder_id, style=style,
        confused_with=confused_with, status=status,
    )
    eligible = [
        row for row in rows
        if not row["objective_excluded"] and row["review_status"] != "excluded"
    ]
    return {"track_ids": [row["id"] for row in eligible], "total": len(eligible)}


def preview_training_track_exclusions(track_ids, music_dir=None):
    selected_ids = {str(value) for value in (track_ids or []) if value}
    report = build_training_disputed_tracks(music_dir=music_dir)
    selected = [row for row in report["items"] if row["id"] in selected_ids]
    if len(selected) != len(selected_ids):
        raise ValueError("Часть спорных треков больше не найдена в актуальной диагностике")
    summary = dataset_summary()
    class_totals = Counter(summary.get("style_track_counts") or {})
    _folders, confirmed, _matcher = _confirmed_folder_matcher()
    folder_totals = {
        str(row.get("id")): int(row.get("track_count", 0) or 0)
        for row in confirmed
    }
    existing_entries = list_review_entries()
    existing_by_class = Counter()
    existing_by_folder = Counter()
    for entry in existing_entries:
        if not entry.get("exclude_from_training"):
            continue
        if entry.get("review_true_style"):
            existing_by_class[str(entry["review_true_style"])] += 1
        if entry.get("review_folder_id"):
            existing_by_folder[str(entry["review_folder_id"])] += 1
    additions_by_class = Counter()
    additions_by_folder = Counter()
    for row in selected:
        if row["review_status"] == "excluded":
            continue
        additions_by_class[row["true_style"]] += 1
        additions_by_folder[row["folder_id"]] += 1
    class_impact = []
    for style, added in sorted(additions_by_class.items()):
        total = max(0, int(class_totals.get(style, 0)))
        excluded = existing_by_class[style] + added
        ratio = excluded / max(1, total)
        class_impact.append({"style": style, "excluded": excluded, "total": total, "percent": round(ratio * 100, 1)})
    folder_impact = []
    for folder_id, added in sorted(additions_by_folder.items()):
        total = max(0, int(folder_totals.get(folder_id, 0)))
        excluded = existing_by_folder[folder_id] + added
        ratio = excluded / max(1, total)
        folder_impact.append({"folder_id": folder_id, "excluded": excluded, "total": total, "percent": round(ratio * 100, 1)})
    requires_confirmation = any(row["percent"] >= 20 for row in class_impact) or any(
        row["percent"] >= 25 for row in folder_impact
    )
    return {
        "track_ids": sorted(selected_ids),
        "selected": len(selected),
        "requires_confirmation": requires_confirmation,
        "class_impact": class_impact,
        "folder_impact": folder_impact,
        "message": (
            "Исключение затронет значительную долю класса или папки — требуется подтверждение."
            if requires_confirmation else "Изменение затрагивает небольшую долю выборки — безопасно."
        ),
    }


def exclude_training_tracks(track_ids, confirm_large_change=False, music_dir=None):
    """Persist a reviewed exclusion for several diagnostic tracks at once."""
    selected_ids = {str(value) for value in (track_ids or []) if value}
    if not selected_ids:
        raise ValueError("Не выбраны спорные треки")
    report = build_training_disputed_tracks(music_dir=music_dir)
    selected = [row for row in report["items"] if row["id"] in selected_ids]
    if len(selected) != len(selected_ids):
        raise ValueError("Часть спорных треков больше не найдена в актуальной диагностике")
    impact = preview_training_track_exclusions(selected_ids, music_dir=music_dir)
    if impact["requires_confirmation"] and not confirm_large_change:
        return {"confirmation_required": True, "impact": impact, "changed": 0}
    changed = 0
    for row in selected:
        if row["objective_excluded"] or row["review_status"] == "excluded":
            continue
        save_training_override(
            row["path"], exclude_from_training=True, reviewed=True,
            reason="manual_bulk_training_exclusion",
            analysis={
                "true_style": row["true_style"],
                "predicted_style": row["predicted_style"],
                "folder_id": row["folder_id"],
                "confidence": row["confidence"],
                "margin": row["margin"],
                "reasons": row["reasons"],
            },
        )
        changed += 1
    return {
        "confirmation_required": False,
        "impact": impact,
        "changed": changed,
        "track_ids": sorted(selected_ids),
    }


def update_training_track_override(
        track_id, action, style_override=None, reason="", confirm_large_change=False,
        music_dir=None,
):
    report = build_training_disputed_tracks(music_dir=music_dir)
    row = next((item for item in report["items"] if item["id"] == str(track_id)), None)
    if row is None:
        raise ValueError("Спорный трек не найден в актуальной диагностике")
    if not os.path.isfile(row["path"]):
        raise ValueError("Аудиофайл не найден по сохранённому или сопоставленному пути")
    action = str(action or "").strip()
    if action not in {"keep", "exclude", "style"}:
        raise ValueError("Неизвестное действие проверки трека")
    impact = None
    if action == "exclude":
        impact = preview_training_track_exclusions([row["id"]], music_dir=music_dir)
        if impact["requires_confirmation"] and not confirm_large_change:
            return {"confirmation_required": True, "impact": impact, "item": row}
    analysis = {
        "true_style": row["true_style"], "predicted_style": row["predicted_style"],
        "folder_id": row["folder_id"], "confidence": row["confidence"],
        "margin": row["margin"], "reasons": row["reasons"],
    }
    if action == "style":
        style_override = str(style_override or "").strip()
        if style_override not in SUPPORTED_BASE_GENRES:
            raise ValueError("Выберите существующий базовый стиль")
        entry = save_training_override(
            row["path"], exclude_from_training=False, style_override=style_override,
            reviewed=True, reason=reason or "manual_style_override", analysis=analysis,
        )
    elif action == "exclude":
        entry = save_training_override(
            row["path"], exclude_from_training=True, reviewed=True,
            reason=reason or "manual_training_exclusion", analysis=analysis,
        )
    else:
        entry = save_training_override(
            row["path"], exclude_from_training=False, clear_style_override=True,
            reviewed=True,
            reason=reason or "current_style_confirmed", analysis=analysis,
        )
    return {"confirmation_required": False, "entry": entry, "impact": impact}


def get_training_disputed_track(track_id, music_dir=None):
    report = build_training_disputed_tracks(music_dir=music_dir)
    return next((row for row in report["items"] if row["id"] == str(track_id)), None)


def update_training_folders(folder_ids, status=None, taxonomy=None):
    ids = {str(value) for value in (folder_ids or []) if value}
    if not ids:
        raise ValueError("Не выбраны папки")
    if status is not None and status not in _VALID_STATUSES:
        raise ValueError("Неизвестный статус разметки")
    taxonomy = taxonomy if isinstance(taxonomy, dict) else {}
    base_style = str(taxonomy.get("base_style", "")).strip()
    if base_style and base_style not in SUPPORTED_BASE_GENRES:
        raise ValueError(f"Неизвестный базовый стиль: {base_style}")
    language = str(taxonomy.get("language", "")).strip()
    if language and language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"Неизвестная языковая метка: {language}")
    version = str(taxonomy.get("version_type", "")).strip()
    if version and version not in SUPPORTED_VERSION_TYPES:
        raise ValueError(f"Неизвестный тип версии: {version}")
    changed = 0
    with _LOCK:
        data = _load_unlocked()
        for row in data["folders"]:
            if row.get("id") not in ids:
                continue
            if status:
                row["status"] = status
            for key in ("base_style", "language", "version_type", "content_genre", "theme", "note"):
                if key in taxonomy:
                    row[key] = str(taxonomy.get(key) or "").strip()
            if row.get("base_style"):
                row["genre_family"] = genre_family(row["base_style"])
                row["dj_category"] = derive_dj_category(
                    row["base_style"], row.get("language", "Unknown")
                )
            if row.get("status") == "confirmed" and not row.get("base_style"):
                raise ValueError("Нельзя подтвердить папку без базового стиля")
            changed += 1
        if changed:
            _save_unlocked(data)
    return {"changed": changed, "summary": dataset_summary()}


def confirm_high_confidence(min_confidence=0.85):
    threshold = min(0.99, max(0.5, float(min_confidence)))
    with _LOCK:
        data = _load_unlocked()
        changed = 0
        for row in data["folders"]:
            if (
                row.get("status") == "suggested"
                and row.get("base_style")
                and float(row.get("confidence", 0.0)) >= threshold
                and not row.get("conflicts")
            ):
                row["status"] = "confirmed"
                changed += 1
        if changed:
            _save_unlocked(data)
    return {"changed": changed, "summary": dataset_summary()}


def iter_confirmed_training_tracks():
    with _LOCK:
        rows = [dict(row) for row in _load_unlocked()["folders"] if row.get("status") == "confirmed"]
    seen = set()
    override_index = training_override_index()
    for row in rows:
        folder = row.get("path", "")
        if not os.path.isdir(folder) or row.get("base_style") not in SUPPORTED_BASE_GENRES:
            continue
        try:
            entries = os.scandir(folder)
        except OSError:
            continue
        with entries:
            for entry in entries:
                if not entry.is_file() or not entry.name.casefold().endswith(_AUDIO_SUFFIX):
                    continue
                key = _path_key(entry.path)
                if key in seen:
                    continue
                seen.add(key)
                override = find_training_override(override_index, entry.path)
                if override and override.get("exclude_from_training"):
                    continue
                training_style = str(
                    (override or {}).get("style_override") or row["base_style"]
                )
                if training_style not in SUPPORTED_BASE_GENRES:
                    continue
                taxonomy = {
                    "base_genre": training_style,
                    "genre_family": genre_family(training_style),
                    "language": row.get("language") or "Unknown",
                    "version_type": row.get("version_type") or "Unknown",
                    "mood": row.get("theme") or "",
                    "content_genre": row.get("content_genre") or "",
                    "theme": row.get("theme") or "",
                    "dj_category": derive_dj_category(
                        training_style, row.get("language") or "Unknown"
                    ),
                    "training_source": "manual_review" if override else "dataset_builder",
                    "training_folder_id": row.get("id"),
                }
                if override:
                    taxonomy["training_override_id"] = override.get("id")
                    taxonomy["original_base_genre"] = row["base_style"]
                yield {"path": entry.path, "base_genre": training_style, "taxonomy": taxonomy}


def has_confirmed_training_tracks():
    with _LOCK:
        return any(
            row.get("status") == "confirmed" and int(row.get("track_count", 0)) > 0
            for row in _load_unlocked()["folders"]
        )


def supported_training_labels():
    return {
        "base_styles": sorted(SUPPORTED_BASE_GENRES),
        "languages": sorted(SUPPORTED_LANGUAGES),
        "version_types": sorted(SUPPORTED_VERSION_TYPES),
        "statuses": sorted(_VALID_STATUSES),
    }
