"""Additive SQLite migration: legacy subscriptions remain stored and inactive."""

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
import json
import sqlite3
import threading

from config import DB_PATH
from galaxy_store import AppRequest, Release

_lock = threading.RLock()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def db_conn():
    with _lock:
        connection = sqlite3.connect(DB_PATH, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db_conn() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS whitelist (
                user_id INTEGER PRIMARY KEY, added_at TEXT NOT NULL, remark TEXT
            );
            CREATE TABLE IF NOT EXISTS galaxy_apps (
                app_key TEXT PRIMARY KEY, package TEXT NOT NULL, preference TEXT NOT NULL,
                release_json TEXT, notes TEXT, checked_at TEXT,
                UNIQUE(package, preference)
            );
            CREATE TABLE IF NOT EXISTS galaxy_subscriptions (
                chat_id INTEGER NOT NULL, app_key TEXT NOT NULL REFERENCES galaxy_apps(app_key),
                created_at TEXT NOT NULL, last_notified TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(chat_id, app_key)
            );
        """)
        columns = {r["name"] for r in db.execute("PRAGMA table_info(whitelist)")}
        if "remark" not in columns:
            db.execute("ALTER TABLE whitelist ADD COLUMN remark TEXT")


def add_to_whitelist(user_id, remark=""):
    with db_conn() as db:
        return (
            db.execute(
                "INSERT OR IGNORE INTO whitelist(user_id,added_at,remark) VALUES(?,?,?)",
                (user_id, now_iso(), remark),
            ).rowcount
            > 0
        )


def remove_from_whitelist(user_id):
    with db_conn() as db:
        return (
            db.execute("DELETE FROM whitelist WHERE user_id=?", (user_id,)).rowcount > 0
        )


def is_in_whitelist(user_id):
    with db_conn() as db:
        return (
            db.execute("SELECT 1 FROM whitelist WHERE user_id=?", (user_id,)).fetchone()
            is not None
        )


def get_whitelist():
    with db_conn() as db:
        return [
            (r["user_id"], r["remark"] or "")
            for r in db.execute("SELECT * FROM whitelist ORDER BY user_id")
        ]


def remember_app(app):
    with db_conn() as db:
        db.execute(
            "INSERT OR IGNORE INTO galaxy_apps(app_key,package,preference) VALUES(?,?,?)",
            (app.key, app.package, app.region),
        )
    return app.key


def get_app(key):
    with db_conn() as db:
        row = db.execute("SELECT * FROM galaxy_apps WHERE app_key=?", (key,)).fetchone()
        return AppRequest(row["package"], row["preference"]) if row else None


def add_subscription(chat_id, app):
    remember_app(app)
    with db_conn() as db:
        return (
            db.execute(
                "INSERT OR IGNORE INTO galaxy_subscriptions(chat_id,app_key,created_at) VALUES(?,?,?)",
                (chat_id, app.key, now_iso()),
            ).rowcount
            > 0
        )


def remove_subscription(chat_id, app):
    with db_conn() as db:
        return (
            db.execute(
                "DELETE FROM galaxy_subscriptions WHERE chat_id=? AND app_key=?",
                (chat_id, app.key),
            ).rowcount
            > 0
        )


def remove_all_subscriptions(chat_id):
    with db_conn() as db:
        return db.execute(
            "DELETE FROM galaxy_subscriptions WHERE chat_id=?", (chat_id,)
        ).rowcount


def get_subscriptions(chat_id=None):
    sql = "SELECT a.*, s.chat_id, s.last_notified FROM galaxy_subscriptions s JOIN galaxy_apps a USING(app_key)"
    with db_conn() as db:
        return list(
            db.execute(
                sql
                + (" WHERE s.chat_id=?" if chat_id is not None else "")
                + " ORDER BY a.package,a.preference",
                (chat_id,) if chat_id is not None else (),
            )
        )


def subscribed_apps():
    with db_conn() as db:
        return [
            AppRequest(r["package"], r["preference"])
            for r in db.execute(
                "SELECT DISTINCT a.package,a.preference FROM galaxy_apps a JOIN galaxy_subscriptions s USING(app_key)"
            )
        ]


def cache_release(app, release, notes):
    remember_app(app)
    with db_conn() as db:
        db.execute(
            "UPDATE galaxy_apps SET release_json=?,notes=?,checked_at=? WHERE app_key=?",
            (
                json.dumps(asdict(release), ensure_ascii=False),
                notes,
                now_iso(),
                app.key,
            ),
        )


def pending_subscribers(app, release):
    with db_conn() as db:
        return [
            r["chat_id"]
            for r in db.execute(
                "SELECT chat_id FROM galaxy_subscriptions WHERE app_key=? AND last_notified!=?",
                (app.key, release.identity),
            )
        ]


def mark_notified(chat_id, app, release):
    with db_conn() as db:
        db.execute(
            "UPDATE galaxy_subscriptions SET last_notified=? WHERE chat_id=? AND app_key=?",
            (release.identity, chat_id, app.key),
        )


def cached_release(row):
    return Release(**json.loads(row["release_json"])) if row["release_json"] else None


def last_app_name(package):
    with db_conn() as db:
        row = db.execute(
            "SELECT release_json FROM galaxy_apps WHERE package=? AND release_json IS NOT NULL "
            "ORDER BY checked_at DESC, rowid DESC LIMIT 1",
            (package,),
        ).fetchone()
    return cached_release(row).name if row else None


def legacy_count():
    with db_conn() as db:
        if not db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='subscriptions'"
        ).fetchone():
            return 0
        return db.execute("SELECT count(*) FROM subscriptions").fetchone()[0]
