import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Optional

from config import AUTO_INIT_OWNER, DB_PATH

db_lock = threading.Lock()


@contextmanager
def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db_lock, db_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS channels (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                username TEXT,
                bound_by INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS releases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT NOT NULL,
                variant_url TEXT NOT NULL UNIQUE,
                file_name TEXT,
                sha256 TEXT,
                created_at INTEGER NOT NULL
            );
            """
        )


def get_setting(key: str) -> Optional[str]:
    with db_lock, db_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None


def set_setting(key: str, value: str) -> None:
    with db_lock, db_conn() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def owner_id() -> Optional[int]:
    value = get_setting("owner_id")
    return int(value) if value else None


def ensure_owner(user_id: int) -> bool:
    current = owner_id()
    if current:
        return current == user_id
    if AUTO_INIT_OWNER:
        set_setting("owner_id", str(user_id))
        return True
    return False


def is_owner(user_id: int) -> bool:
    current = owner_id()
    return current is not None and current == user_id


def add_channel(chat_id: int, title: str, username: Optional[str], bound_by: int):
    with db_lock, db_conn() as conn:
        conn.execute(
            """
            INSERT INTO channels(chat_id, title, username, bound_by, created_at, active)
            VALUES(?, ?, ?, ?, ?, 1)
            ON CONFLICT(chat_id) DO UPDATE SET
                title=excluded.title,
                username=excluded.username,
                bound_by=excluded.bound_by,
                active=1
            """,
            (chat_id, title, username, bound_by, int(time.time())),
        )


def list_channels():
    with db_lock, db_conn() as conn:
        return conn.execute(
            "SELECT chat_id, title, username, bound_by, created_at, active FROM channels WHERE active = 1 ORDER BY created_at ASC"
        ).fetchall()


def deactivate_channel(chat_id: int):
    with db_lock, db_conn() as conn:
        conn.execute("UPDATE channels SET active = 0 WHERE chat_id = ?", (chat_id,))


def release_exists(variant_url: str) -> bool:
    with db_lock, db_conn() as conn:
        row = conn.execute("SELECT 1 FROM releases WHERE variant_url = ?", (variant_url,)).fetchone()
        return row is not None


def save_release(version: str, variant_url: str, file_name: str, sha256: str):
    with db_lock, db_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO releases(version, variant_url, file_name, sha256, created_at) VALUES(?, ?, ?, ?, ?)",
            (version, variant_url, file_name, sha256, int(time.time())),
        )
