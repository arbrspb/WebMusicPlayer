# app/db.py 15-05-25 18-25
import sqlite3
import logging

logger = logging.getLogger(__name__) # Логирование

SCAN_DB = "scan_results.db"
FAVORITE_DB = "favorite.db"

def init_scan_db():
    conn = sqlite3.connect(SCAN_DB)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS scan_results (
            id INTEGER PRIMARY KEY,
            rel_path TEXT UNIQUE,
            genre TEXT,
            mtime REAL,
            confidence REAL,
            features TEXT
        )
    ''')
    conn.commit()
    conn.close()

def get_unique_scan_count():
    """
    Возвращает количество уникальных записей в таблице scan_results по полю rel_path.
    """
    conn = sqlite3.connect(SCAN_DB)
    c = conn.cursor()
    c.execute("SELECT COUNT(DISTINCT rel_path) FROM scan_results")
    count = c.fetchone()[0]
    conn.close()
    return count

def load_scan_result(rel_path):  # делаем ее
    conn = sqlite3.connect(SCAN_DB)
    c = conn.cursor()
    c.execute("SELECT genre, mtime, confidence, features FROM scan_results WHERE rel_path = ?", (rel_path,))
    row = c.fetchone()
    conn.close()
    return row

def save_scan_result(rel_path, genre, mtime, confidence, features=None): # следующиую ее
    conn = sqlite3.connect(SCAN_DB)
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO scan_results (rel_path, genre, mtime, confidence, features) VALUES (?, ?, ?, ?, ?)",
        (rel_path, genre, mtime, confidence, features)
    )
    conn.commit()
    conn.close()

def init_favorite_db():
    conn = sqlite3.connect(FAVORITE_DB)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS favorites (
            path TEXT PRIMARY KEY,
            genre TEXT,
            rating INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()
