import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from config import DB_PATH

db_lock = threading.Lock()


def now_iso() -> str:
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


def _normalize_apk_url(url: str) -> str:
    """规范化 APKMirror URL：去除 query string，确保尾部有斜杠。"""
    return url.split("?")[0].rstrip("/") + "/"


def init_db() -> None:
    with db_lock, db_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS whitelist (
                user_id    INTEGER PRIMARY KEY,
                added_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS subscriptions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    INTEGER NOT NULL,
                apk_url    TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(chat_id, apk_url)
            );

            CREATE TABLE IF NOT EXISTS apk_versions (
                apk_url           TEXT PRIMARY KEY,
                last_variant_url  TEXT,
                last_version_name TEXT,
                last_version_code INTEGER,
                last_sha256       TEXT,
                last_type         TEXT,
                last_checked_at   TEXT,
                last_pushed_at    TEXT
            );
            """
        )
        # 兼容旧数据库：补充 last_type 列（已存在则忽略）
        try:
            conn.execute("ALTER TABLE apk_versions ADD COLUMN last_type TEXT")
        except sqlite3.OperationalError:
            pass
        # 迁移：规范化历史订阅 URL（补充尾部斜杠），然后删除重复行
        conn.execute(
            "UPDATE subscriptions SET apk_url = rtrim(apk_url, '/') || '/' "
            "WHERE apk_url NOT LIKE '%/'"
        )
        conn.execute(
            "DELETE FROM subscriptions WHERE id NOT IN ("
            "  SELECT MIN(id) FROM subscriptions GROUP BY chat_id, apk_url"
            ")"
        )


# ---------------------------------------------------------------------------
# 白名单
# ---------------------------------------------------------------------------

def add_to_whitelist(user_id: int) -> bool:
    """添加用户到白名单，返回 True 表示新增，False 表示已存在。"""
    with db_lock, db_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO whitelist(user_id, added_at) VALUES(?, ?)",
                (user_id, now_iso()),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def remove_from_whitelist(user_id: int) -> bool:
    """从白名单移除，返回 True 表示成功，False 表示不存在。"""
    with db_lock, db_conn() as conn:
        cur = conn.execute("DELETE FROM whitelist WHERE user_id = ?", (user_id,))
        return cur.rowcount > 0


def get_whitelist() -> list[int]:
    with db_lock, db_conn() as conn:
        rows = conn.execute(
            "SELECT user_id FROM whitelist ORDER BY added_at"
        ).fetchall()
        return [r["user_id"] for r in rows]


def is_in_whitelist(user_id: int) -> bool:
    with db_lock, db_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM whitelist WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row is not None


# ---------------------------------------------------------------------------
# 订阅
# ---------------------------------------------------------------------------

def add_subscription(chat_id: int, apk_url: str) -> bool:
    """添加订阅，返回 True 表示新增，False 表示已存在。"""
    apk_url = _normalize_apk_url(apk_url)
    with db_lock, db_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO subscriptions(chat_id, apk_url, created_at) VALUES(?, ?, ?)",
                (chat_id, apk_url, now_iso()),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def remove_subscription(chat_id: int, apk_url: str) -> bool:
    """取消指定订阅，返回 True 表示成功，False 表示不存在。"""
    apk_url = _normalize_apk_url(apk_url)
    with db_lock, db_conn() as conn:
        cur = conn.execute(
            "DELETE FROM subscriptions WHERE chat_id = ? AND apk_url = ?",
            (chat_id, apk_url),
        )
        return cur.rowcount > 0


def remove_all_subscriptions(chat_id: int) -> int:
    """取消该用户所有订阅，返回删除条数。"""
    with db_lock, db_conn() as conn:
        cur = conn.execute(
            "DELETE FROM subscriptions WHERE chat_id = ?", (chat_id,)
        )
        return cur.rowcount


def get_subscriptions(chat_id: int) -> list[str]:
    """返回该用户的所有订阅 URL。"""
    with db_lock, db_conn() as conn:
        rows = conn.execute(
            "SELECT apk_url FROM subscriptions WHERE chat_id = ? ORDER BY created_at",
            (chat_id,),
        ).fetchall()
        return [r["apk_url"] for r in rows]


def get_all_subscribed_urls() -> list[str]:
    """返回所有有订阅者的 URL（去重）。"""
    with db_lock, db_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT apk_url FROM subscriptions ORDER BY apk_url"
        ).fetchall()
        return [r["apk_url"] for r in rows]


def get_subscribers(apk_url: str) -> list[int]:
    """返回订阅该 URL 的所有 chat_id。"""
    with db_lock, db_conn() as conn:
        rows = conn.execute(
            "SELECT chat_id FROM subscriptions WHERE apk_url = ? ORDER BY created_at",
            (apk_url,),
        ).fetchall()
        return [r["chat_id"] for r in rows]


# ---------------------------------------------------------------------------
# APK 版本状态
# ---------------------------------------------------------------------------

def get_apk_version(apk_url: str) -> Optional[sqlite3.Row]:
    with db_lock, db_conn() as conn:
        return conn.execute(
            "SELECT * FROM apk_versions WHERE apk_url = ?", (apk_url,)
        ).fetchone()


def update_apk_version(apk_url: str, **kwargs) -> None:
    """UPSERT apk_versions 行，仅更新传入的字段。"""
    if not kwargs:
        return
    kwargs["apk_url"] = apk_url
    cols = list(kwargs.keys())
    placeholders = ", ".join(f":{c}" for c in cols)
    updates = ", ".join(
        f"{c} = excluded.{c}" for c in cols if c != "apk_url"
    )
    sql = (
        f"INSERT INTO apk_versions({', '.join(cols)}) VALUES({placeholders}) "
        f"ON CONFLICT(apk_url) DO UPDATE SET {updates}"
    )
    with db_lock, db_conn() as conn:
        conn.execute(sql, kwargs)


def is_new_version(
    apk_url: str,
    variant_url: str,
    version_code: Optional[int],
    sha256: str,
    variant_type: str,
) -> bool:
    """三级去重：variant_url / version_code / sha256 任一匹配则视为旧版本。

    例外：同一 version_code 但旧记录是 BUNDLE 而新变体是 APK，视为更优更新。
    """
    row = get_apk_version(apk_url)
    if row is None:
        return True
    if row["last_variant_url"] and row["last_variant_url"] == variant_url:
        return False
    if version_code is not None and row["last_version_code"] == version_code:
        # BUNDLE 已推送，现在出了真正的 APK → 视为更优更新
        if variant_type == "APK" and row["last_type"] == "BUNDLE":
            return True
        return False
    if row["last_sha256"] and row["last_sha256"] == sha256:
        return False
    return True
