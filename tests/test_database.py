import sqlite3
import json
from dataclasses import asdict
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


def test_package_unsubscribe_covers_every_region_for_that_user_only():
    for chat_id, region in ((1, "AUTO"), (1, "CN"), (2, "CN")):
        db.add_subscription(chat_id, AppRequest("com.example.app", region))
    db.add_subscription(1, AppRequest("com.other.app", "CN"))
    assert db.remove_package_subscriptions(1, "com.example.app") == 2
    assert [r["package"] for r in db.get_subscriptions(1)] == ["com.other.app"]
    assert len(db.get_subscriptions(2)) == 1


def test_notification_is_per_subscriber_and_only_for_a_higher_version_code():
    app = AppRequest("com.example.app")
    db.add_subscription(1, app)
    db.add_subscription(2, app)
    selected = release()
    db.cache_release(app, selected, "notes")
    db.mark_notified(1, app, selected)
    assert db.pending_subscribers(app, selected) == [2]
    # AUTO switching region or product for the same version is not an update.
    assert db.pending_subscribers(app, release(region="US")) == [2]
    assert db.pending_subscribers(app, release(product_id="99999")) == [2]
    assert db.pending_subscribers(app, release(version_code=122)) == [2]
    assert db.pending_subscribers(app, release(version_code=124)) == [1, 2]
    db.mark_notified(1, app, release(region="US", version_code=124))
    assert db.pending_subscribers(app, release(version_code=123)) == [2]
    assert db.cached_release(db.get_subscriptions(1)[0]) == selected
    assert "downloadURI" not in db.get_subscriptions(1)[0]["release_json"]


def test_resubscription_resets_notification_only_for_that_user():
    app = AppRequest("com.example.app", "CN")
    db.add_subscription(1, app)
    db.mark_notified(1, app, release())
    db.remove_subscription(1, app)
    db.add_subscription(1, app)
    assert db.pending_subscribers(app, release()) == [1]


def test_legacy_cache_without_update_date_still_loads():
    values = asdict(release())
    del values["updated_date"]
    assert db.cached_release({"release_json": json.dumps(values)}) == release()


def test_latest_name_is_shared_across_region_preferences(monkeypatch):
    times = iter(["2026-10-02T01:00:00+00:00", "2026-10-02T02:00:00+00:00"])
    monkeypatch.setattr(db, "now_iso", lambda: next(times))
    db.cache_release(AppRequest("com.example.app", "US"), release(name="Example"), None)
    db.cache_release(AppRequest("com.example.app", "CN"), release(name="应用"), None)
    assert db.last_app_name("com.example.app") == "应用"
    assert db.last_app_name("com.other.app") is None
