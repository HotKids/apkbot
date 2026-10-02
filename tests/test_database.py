import sqlite3
import database as db
from galaxy_store import AppRequest
from tests.test_galaxy_store import release


def test_additive_migration_preserves_legacy_data_and_old_whitelist(
    monkeypatch, tmp_path
):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE whitelist(user_id INTEGER PRIMARY KEY, added_at TEXT NOT NULL);
            INSERT INTO whitelist VALUES(123,'then');
            CREATE TABLE subscriptions(chat_id INTEGER,apk_url TEXT);
            INSERT INTO subscriptions VALUES(123,'https://www.apkmirror.com/apk/old/');
            CREATE TABLE apk_versions(apk_url TEXT PRIMARY KEY,last_version_code INTEGER);
            INSERT INTO apk_versions VALUES('legacy',42);
        """)
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    db.init_db()
    assert db.get_whitelist() == [(123, "")]
    assert db.legacy_count() == 1
    assert db.subscribed_apps() == []
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT * FROM apk_versions").fetchall() == [
            ("legacy", 42)
        ]
        assert (
            connection.execute("SELECT * FROM subscriptions")
            .fetchone()[1]
            .startswith("https://www.apkmirror.com")
        )


def test_subscriptions_distinguish_preference_and_never_create_version():
    cn = AppRequest("com.example.app", "CN")
    us = AppRequest("com.example.app", "US")
    assert db.add_subscription(1, cn)
    assert not db.add_subscription(1, cn)
    assert db.add_subscription(1, us)
    assert len(db.get_subscriptions(1)) == 2
    assert all(row["release_json"] is None for row in db.get_subscriptions(1))
    assert db.get_app(cn.key) == cn
    db.remove_subscription(1, cn)
    assert db.get_app(cn.key) == cn  # Old notification callbacks remain durable.
    assert db.remove_all_subscriptions(1) == 1


def test_notification_identity_is_per_subscriber_region_product_and_code():
    app = AppRequest("com.example.app")
    db.add_subscription(1, app)
    db.add_subscription(2, app)
    selected = release()
    db.cache_release(app, selected, "notes")
    db.mark_notified(1, app, selected)
    assert db.pending_subscribers(app, selected) == [2]
    assert db.pending_subscribers(app, release(region="US")) == [1, 2]
    assert db.pending_subscribers(app, release(product_id="99999")) == [1, 2]
    assert db.pending_subscribers(app, release(version_code=124)) == [1, 2]
    assert db.cached_release(db.get_subscriptions(1)[0]) == selected
    assert "downloadURI" not in db.get_subscriptions(1)[0]["release_json"]


def test_resubscription_resets_notification_only_for_that_user():
    app = AppRequest("com.example.app", "CN")
    db.add_subscription(1, app)
    db.mark_notified(1, app, release())
    db.remove_subscription(1, app)
    db.add_subscription(1, app)
    assert db.pending_subscribers(app, release()) == [1]
