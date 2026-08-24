"""Многомерная музыкальная таксономия для Rekordbox и ML-классификации."""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import asdict, dataclass


HOUSE_STYLES = {
    "House", "Club House", "Deep House", "Tech House", "Afro House",
    "Future House", "Bass House", "Progressive House", "Organic House",
    "Electro House", "Funky House", "Disco House", "Soulful House",
    "Jackin House", "Minimal House",
}

# These values are useful as broad families and inference fallbacks, but are
# too broad to compete with their own children in one acoustic classifier.
# For example, a generic ``House`` target makes every House subtype ambiguous.
FAMILY_FALLBACK_ONLY_STYLES = frozenset({"House"})

STYLE_ALIASES = {
    "club house": "Club House",
    "house": "House",
    "deep house": "Deep House",
    "tech house": "Tech House",
    "afro house": "Afro House",
    "future house": "Future House",
    "bass house": "Bass House",
    "progressive house": "Progressive House",
    "organic house": "Organic House",
    "electro house": "Electro House",
    "funky house": "Funky House",
    "disco house": "Disco House",
    "soulful house": "Soulful House",
    "jackin house": "Jackin House",
    "minimal house": "Minimal House",
    "micro house": "Minimal House",
    "nu disco": "Nu Disco",
    "funky disco": "Funky & Disco",
    "funky and disco": "Funky & Disco",
    "disco": "Disco",
    "drumnbass": "Drum & Bass",
    "drum bass": "Drum & Bass",
    "drum and bass": "Drum & Bass",
    "dnb": "Drum & Bass",
    "hip hop": "Hip-Hop",
    "hiphop": "Hip-Hop",
    "rap": "Hip-Hop",
    "trap": "Trap",
    "rnb": "RnB",
    "pop": "Pop",
    "rock": "Rock",
    "moomb": "Moombahton",
    "moombahton": "Moombahton",
    "moombahcore": "Moombahcore",
    "moombahsoul": "Moombahsoul",
    "chillout": "Chillout",
    "lounge": "Lounge",
    "techno": "Techno",
    "melodic techno": "Melodic Techno",
    "peak time techno": "Peak Time Techno",
    "hard techno": "Hard Techno",
    "minimal techno": "Minimal Techno",
    "industrial techno": "Industrial Techno",
    "trance": "Trance",
    "progressive trance": "Progressive Trance",
    "uplifting trance": "Uplifting Trance",
    "vocal trance": "Vocal Trance",
    "tech trance": "Tech Trance",
    "psytrance": "Psytrance",
    "goa trance": "Goa Trance",
    "drill": "Drill",
    "phonk": "Phonk",
    "jungle": "Jungle",
    "dubstep": "Dubstep",
    "future bass": "Future Bass",
    "uk garage": "UK Garage",
    "speed garage": "Speed Garage",
    "breakbeat": "Breakbeat",
    "big beat": "Big Beat",
    "hardstyle": "Hardstyle",
    "hardcore": "Hardcore",
    "frenchcore": "Frenchcore",
    "uptempo": "Uptempo",
    "afrobeats": "Afrobeats",
    "amapiano": "Amapiano",
    "dancehall": "Dancehall",
    "reggae": "Reggae",
    "reggaeton": "Reggaeton",
    "dembow": "Dembow",
    "latin trap": "Latin Trap",
    "edm trap": "Trap EDM",
    "trap edm": "Trap EDM",
    "ambient": "Ambient",
    "downtempo": "Downtempo",
    "trip hop": "Trip-Hop",
    "lo fi": "Lo-Fi",
    "metal": "Metal",
    "punk": "Punk",
    "indie": "Indie",
}

MOOD_ALIASES = {
    "light": "Light",
    "легкая": "Легкая",
    "грустная": "Грустная",
    "веселая": "Веселая",
    "медляк": "Медляк",
    "ставим": "Ставим",
    "кач": "Кач",
    "танцевально поставить": "Танцевально/Поставить",
    "нейтрально": "Нейтрально",
    "нейтральная": "Нейтрально",
}

VERSION_ALIASES = {
    "mashup": "Mashup",
    "remix": "Remix",
    "rmx": "Remix",
    "edit": "Edit",
    "intro": "Edit",
    "bootleg": "Bootleg",
    "blend": "Blend",
    "original": "Original",
}

SUPPORTED_BASE_GENRES = frozenset(STYLE_ALIASES.values())
SUPPORTED_LANGUAGES = frozenset({
    "Russian", "English", "Foreign", "Other", "Instrumental", "Unknown",
})
SUPPORTED_VERSION_TYPES = frozenset(VERSION_ALIASES.values()) | {"Unknown"}

RUSSIAN_LANGUAGE_ALIASES = {
    "russian", "rus", "russia", "русский", "русская", "русские", "русское",
    "рус", "ru",
}
ENGLISH_LANGUAGE_ALIASES = {"eng", "english"}
FOREIGN_LANGUAGE_ALIASES = {
    "foreign", "euro", "evro", "international", "евро",
    "зарубежный", "зарубежная", "зарубежные", "иностранный", "иностранная",
    "иностранные",
}


@dataclass(frozen=True)
class TrackTaxonomy:
    base_genre: str = "Other"
    genre_family: str = "Other"
    language: str = "Unknown"
    version_type: str = "Unknown"
    mood: str = ""
    dj_category: str = "Other"

    def to_dict(self):
        return asdict(self)


def _normalise_token(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or "")).casefold()
    value = value.replace("&", " and ")
    value = re.sub(r"[^\w\s]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def _tokens(raw_genre: str):
    return [part.strip() for part in re.split(r"[,;/|]+", str(raw_genre or "")) if part.strip()]


def contains_cyrillic(value: str) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", str(value or "")))


def _contains_language_alias(value: str, aliases) -> bool:
    normalised = _normalise_token(value)
    return any(re.search(rf"\b{re.escape(alias)}\b", normalised) for alias in aliases)


def infer_language_from_path(path: str) -> str:
    """Берёт только явные языковые маркеры из папок, не угадывая по латинице."""
    directory = os.path.dirname(str(path or ""))
    path_parts = [part for part in re.split(r"[\\/]+", directory) if part]
    for part in reversed(path_parts):
        if _contains_language_alias(part, RUSSIAN_LANGUAGE_ALIASES) or "русск" in _normalise_token(part):
            return "Russian"
        if _contains_language_alias(part, ENGLISH_LANGUAGE_ALIASES):
            return "English"
        if _contains_language_alias(part, FOREIGN_LANGUAGE_ALIASES):
            return "Foreign"
    return "Unknown"


def genre_family(base_genre: str) -> str:
    if base_genre in HOUSE_STYLES:
        return "House"
    if base_genre in {
        "Techno", "Melodic Techno", "Peak Time Techno", "Hard Techno",
        "Minimal Techno", "Industrial Techno",
    }:
        return "Techno"
    if base_genre in {
        "Trance", "Progressive Trance", "Uplifting Trance", "Vocal Trance",
        "Tech Trance", "Psytrance", "Goa Trance",
    }:
        return "Trance"
    if base_genre in {"Hip-Hop", "Trap", "RnB", "Drill", "Phonk"}:
        return "Urban"
    if base_genre in {
        "Drum & Bass", "Jungle", "Dubstep", "Future Bass", "Trap EDM",
        "UK Garage", "Speed Garage", "Breakbeat", "Big Beat",
    }:
        return "Bass"
    if base_genre in {"Hardstyle", "Hardcore", "Frenchcore", "Uptempo"}:
        return "Hard Dance"
    if base_genre in {"Disco", "Nu Disco", "Funky & Disco"}:
        return "Disco / Funk"
    if base_genre == "Pop":
        return "Pop / Commercial"
    if base_genre in {"Afrobeats", "Amapiano"}:
        return "Afro"
    if base_genre in {"Dancehall", "Reggae"}:
        return "Caribbean"
    # Moombahton is an electronic club style with dembow/reggaeton influence,
    # not a Latin subgenre.  Keep it in its own electronic family so the
    # hierarchy does not force it to compete with Reggaeton/Latin Trap.
    if base_genre in {"Moombahton", "Moombahcore", "Moombahsoul"}:
        return "Electronic / Club"
    if base_genre in {"Reggaeton", "Dembow", "Latin Trap"}:
        return "Latin"
    if base_genre in {"Rock", "Metal", "Punk", "Indie"}:
        return "Rock / Alternative"
    if base_genre in {"Ambient", "Chillout", "Downtempo", "Lounge", "Trip-Hop", "Lo-Fi"}:
        return "Ambient / Downtempo"
    return base_genre or "Other"


def derive_dj_category(base_genre: str, language: str) -> str:
    # Пользовательское правило: русский House-контент остаётся в привычной
    # категории «Русские Ремиксы». Русские DnB/Hip-Hop сохраняют свой стиль.
    if language == "Russian" and base_genre in HOUSE_STYLES:
        return "Русские Ремиксы"
    return base_genre or "Other"


def _find_style(raw_tokens, fallback_genre=None):
    explicit_styles = []
    for token in raw_tokens:
        norm = _normalise_token(token)
        for alias in sorted(STYLE_ALIASES, key=len, reverse=True):
            if norm == alias or re.search(rf"\b{re.escape(alias)}\b", norm):
                explicit_styles.append(STYLE_ALIASES[alias])
                break
    if explicit_styles:
        # Явный DnB/Hip-Hop важнее общего Russian Remix/House bucket.
        for priority in ("Drum & Bass", "Hip-Hop", "Trap", "RnB"):
            if priority in explicit_styles:
                return priority
        return explicit_styles[0]
    # В коллекции пользователя голая метка Russian Remix исторически означает
    # русский клубный/house-ремикс. Явный DnB/Hip-Hop выше всё равно приоритетнее.
    joined = " ".join(_normalise_token(token) for token in raw_tokens)
    if "russian remix" in joined or "русск" in joined and "ремикс" in joined:
        return "Club House"
    if fallback_genre == "Русские Ремиксы":
        return "Club House"
    return fallback_genre or "Other"


def parse_track_taxonomy(
        raw_genre="",
        fallback_genre=None,
        title="",
        artist="",
        path="",
        fallback_language=None,
):
    raw_tokens = _tokens(raw_genre)
    normalised = [_normalise_token(token) for token in raw_tokens]
    joined = " ".join(normalised)
    text_meta = " ".join([str(title or ""), str(artist or ""), os.path.basename(str(path or ""))])
    path_language = infer_language_from_path(path)

    base_genre = _find_style(raw_tokens, fallback_genre=fallback_genre)
    if (
            "russian remix" in joined
            or any(_contains_language_alias(token, RUSSIAN_LANGUAGE_ALIASES) for token in normalised)
            or "русск" in joined
    ):
        language = "Russian"
    elif any(_contains_language_alias(token, ENGLISH_LANGUAGE_ALIASES) for token in normalised):
        language = "English"
    elif any(_contains_language_alias(token, FOREIGN_LANGUAGE_ALIASES) for token in normalised):
        language = "Foreign"
    elif path_language != "Unknown":
        language = path_language
    elif contains_cyrillic(text_meta):
        language = "Russian"
    elif fallback_language:
        language = fallback_language
    else:
        language = "Unknown"

    version_type = "Unknown"
    version_text = _normalise_token(" ".join([str(raw_genre or ""), text_meta]))
    if "russian remix" in joined:
        version_type = "Remix"
    else:
        for alias, canonical in VERSION_ALIASES.items():
            if re.search(rf"\b{re.escape(alias)}\b", version_text):
                version_type = canonical
                break

    moods = []
    for token in normalised:
        if token in MOOD_ALIASES and MOOD_ALIASES[token] not in moods:
            moods.append(MOOD_ALIASES[token])
    mood = ", ".join(moods)
    category = derive_dj_category(base_genre, language)
    return TrackTaxonomy(
        base_genre=base_genre,
        genre_family=genre_family(base_genre),
        language=language,
        version_type=version_type,
        mood=mood,
        dj_category=category,
    )


def taxonomy_from_training_label(label, path=""):
    # Название папки/класса описывает стиль, а не обязательно язык. Раньше все
    # DnB/Hip-Hop/Club House автоматически получали Foreign, поэтому русский
    # DnB учил языковую модель неправильной метке. Явно размечаем только
    # историческую категорию «Русские Ремиксы»; для остальных язык берётся из
    # кириллицы в имени либо остаётся Unknown и не участвует в language model.
    if label == "Русские Ремиксы":
        language = "Russian"
    elif label == "Club House":
        # В пользовательской структуре латинский Club House хранит зарубежные
        # треки, а русский House вынесен в «Русские Ремиксы». Кириллица и явные
        # маркеры пути всё равно имеют приоритет внутри parse_track_taxonomy().
        language = "Foreign"
    else:
        language = None
    return parse_track_taxonomy(
        raw_genre=label,
        fallback_genre="Club House" if label == "Русские Ремиксы" else label,
        path=path,
        fallback_language=language,
    )


def track_group_key(path: str) -> str:
    """Группирует ремиксы/эдиты одного оригинала для честного train/test split."""
    name = os.path.splitext(os.path.basename(str(path or "")))[0]
    # Убираем целиком скобку с названием версии/ремиксера: иначе
    # "Song (DJ A Remix)" и "Song (DJ B Remix)" считались разными оригиналами.
    name = re.sub(
        r"[\(\[\{][^\)\]\}]*\b(remix|rmx|edit|intro|outro|extended|radio|clean|dirty|mix|bootleg|blend|mashup|version|original)\b[^\)\]\}]*[\)\]\}]",
        " ",
        name,
        flags=re.IGNORECASE,
    )
    norm = _normalise_token(name)
    norm = re.sub(
        r"\b(remix|rmx|edit|intro|outro|extended|radio|clean|dirty|mix|bootleg|blend|mashup|version|original)\b",
        " ",
        norm,
    )
    norm = re.sub(r"\b\d{1,3}\s*(bpm)?\b", " ", norm)
    norm = re.sub(r"\s+", " ", norm).strip()
    return norm or _normalise_token(name)
