import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import DB_PATH

db_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db_lock, db_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS state (
                id              INTEGER PRIMARY KEY CHECK (id = 1),
                last_release_url    TEXT,
                last_variant_url    TEXT,
                last_version_name   TEXT,
                last_version_code   INTEGER,
                last_sha256         TEXT,
                last_checked_at     TEXT,
                last_pushed_at      TEXT,
                last_status         TEXT,
                last_error          TEXT
            );

            CREATE TABLE IF NOT EXISTS downloads (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                file_name   TEXT NOT NULL,
                file_path   TEXT NOT NULL,
                size_bytes  INTEGER,
                sha256      TEXT,
                source_url  TEXT,
                created_at  TEXT NOT NULL
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
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def get_apk_url() -> Optional[str]:
    """返回当前监控地址（由 /sub 命令配置，存储在 DB）。"""
    return get_setting("apk_url") or None


def get_state() -> Optional[sqlite3.Row]:
    with db_lock, db_conn() as conn:
        return conn.execute("SELECT * FROM state WHERE id = 1").fetchone()


def update_state(**kwargs) -> None:
    """UPSERT state 行（id=1）。只更新传入的字段。"""
    if not kwargs:
        return
    kwargs["id"] = 1
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join("?" for _ in kwargs)
    updates = ", ".join(f"{k}=excluded.{k}" for k in kwargs if k != "id")
    sql = (
        f"INSERT INTO state ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}"
    )
    with db_lock, db_conn() as conn:
        conn.execute(sql, list(kwargs.values()))


def save_download(
    file_name: str,
    file_path: str,
    size_bytes: int,
    sha256: str,
    source_url: str,
) -> None:
    with db_lock, db_conn() as conn:
        conn.execute(
            "INSERT INTO downloads(file_name, file_path, size_bytes, sha256, source_url, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?)",
            (file_name, file_path, size_bytes, sha256, source_url, _now_iso()),
        )


def is_already_pushed(
    variant_url: str,
    version_code: Optional[int],
    sha256: str,
) -> bool:
    """三级去重：variant_url → version_code → sha256。"""
    state = get_state()
    if not state:
        return False
    if state["last_variant_url"] and state["last_variant_url"] == variant_url:
        return True
    if version_code and state["last_version_code"] and state["last_version_code"] == version_code:
        return True
    if state["last_sha256"] and state["last_sha256"] == sha256:
        return True
    return False
