import json
from collections import Counter, defaultdict
import re
import sys

# --- НАСТРОЙ СВОЙ СЛОВАРЬ ЖАНРОВ ---
genre_settings = {
    "chill": "Chillout",
    "chillout": "Chillout",
    "club": "Club House",
    "club house": "Club House",
    "clubhouse": "Club House",
    "deep house": "Deep House",
    "deep-house": "Deep House",
    "deephouse": "Deep House",
    "funkydisco": "Funky & Disco",
    "drum and bass": "Drum & Bass",
    "drum & bass": "Drum & Bass",
    "drumandbass": "Drum & Bass",
    "drumnbass": "Drum & Bass",
    "funk": "Funky & Disco",
    "funky disco": "Funky & Disco",
    "future house": "Future House",
    "futurehouse": "Future House",
    "g bass house": "G & Bass House",
    "g&b house": "G & Bass House",
    "g-base house": "G & Bass House",
    "gandbasshouse": "G & Bass House",
    "hip hop": "Hip-Hop",
    "hip-hop": "Hip-Hop",
    "hiphop": "Hip-Hop",
    "house": "House",
    "mainstage": "Mainstage",
    "mix": "Mix",
    "moombahton": "Moombahton",
    "nu disco": "Nu Disco",
    "nudisco": "Nu Disco",
    "pop": "Pop",
    "popmusic": "Pop",
    "progressive house": "Progressive House",
    "progressivehouse": "Progressive House",
    "r n b": "RnB",
    "r&b": "RnB",
    "rnb": "RnB",
    "rock": "Rock",
    "romantic selection": "Romantic Selection",
    "romanticselection": "Romantic Selection",
    "ru remix": "Русские Ремиксы",
    "ruremixes": "Русские Ремиксы",
    "tech house": "Tech House",
    "techhouse": "Tech House",
    "trap": "Trap",
    "underground pop": "Underground Pop",
    "undergroundpop": "Underground Pop",
    "микс": "Mix",
    "новогодние": "Новогодние",
    "новый год": "Новогодние",
    "ремикс": "Remix",
    "русские ремиксы": "Русские Ремиксы",
    "русскиеремиксы": "Русские Ремиксы"
}

def normalize_for_genre_compare(s):
    s = s.lower()
    s = re.sub(r"[’'`]", "n", s)
    s = re.sub(r"[^a-zа-я0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def normalize_genre(raw_genre, genre_settings):
    if not raw_genre:
        return "Other"
    genre_norm = normalize_for_genre_compare(raw_genre)
    # Точное совпадение
    for key in sorted(genre_settings, key=len, reverse=True):
        key_norm = normalize_for_genre_compare(key)
        if key_norm == genre_norm:
            return genre_settings[key]
    # Частичное совпадение только для длинных ключей (>4)
    for key in sorted(genre_settings, key=len, reverse=True):
        key_norm = normalize_for_genre_compare(key)
        if len(key_norm) > 4:
            if key_norm in genre_norm or genre_norm in key_norm:
                return genre_settings[key]
    return "Other"

filename = "parsed_rekordbox.json"
try:
    with open(filename, encoding="utf-8") as f:
        data = json.load(f)
except Exception as e:
    print(f"[ОШИБКА] Не удалось открыть файл {filename}: {e}")
    sys.exit(1)

print(f"Всего объектов в JSON: {len(data)}")
if not data:
    print("Файл пустой!")
    sys.exit(0)

print("\nПример первого объекта:")
print(data[0])
print("\nКлючи первого объекта:")
print(list(data[0].keys()))

# Используем 'Genre' с большой буквы!
count_genre = sum(1 for track in data if track.get("Genre"))
print(f"\nТреков с непустым Genre: {count_genre}")

raw_genres = []
normalized_genres = []
mapping = defaultdict(list)

for track in data:
    genre = track.get("Genre", "")
    if genre is None:
        continue
    genre = str(genre).strip()
    if not genre:
        continue
    norm = normalize_genre(genre, genre_settings)
    raw_genres.append(genre)
    normalized_genres.append(norm)
    mapping[norm].append(genre)

print("\n======= Топ-30 уникальных жанров в raw (исходник) =======")
for genre, count in Counter(raw_genres).most_common(30):
    print(f"{genre!r}: {count}")

print("\n======= Сводка по нормализованным жанрам =======")
for genre, count in Counter(normalized_genres).most_common():
    print(f"{genre}: {count}")

print("\n======= Какие raw жанры попали в 'Mix' =======")
for g in sorted(set(mapping["Mix"])):
    print(g)

print("\n======= Какие raw жанры попали в 'Remix' =======")
for g in sorted(set(mapping["Remix"])):
    print(g)

print("\n======= Жанры, попавшие в 'Other' (неопределённые) =======")
for g in sorted(set(mapping["Other"])):
    print(g)