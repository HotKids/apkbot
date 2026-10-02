from types import SimpleNamespace as NS
from unittest.mock import Mock
import logging
import pytest
import requests
import database as db
import handlers
from galaxy_store import AppRequest, DownloadGrant, ServiceError, VersionDrift
from tests.test_galaxy_store import metadata, ods, release
from tests.test_richmsg import rejection


def message(text="/sub com.example.app CN", user=100, chat_type="private"):
    return NS(
        text=text, chat=NS(id=user, type=chat_type), from_user=NS(id=user), message_id=5
    )


@pytest.fixture
def transport(monkeypatch):
    bot = Mock()
    cards = Mock()
    monkeypatch.setattr(handlers, "bot", bot)
    monkeypatch.setattr(handlers, "messages", cards)
    return bot, cards


SIGNED_URL = "https://download.samsungapps.com/file.apk?token=private-link&name=a%2Fb"


def fake_store(monkeypatch, selected=None):
    store = Mock()
    store.metadata.return_value = selected or release()
    store.notes.return_value = "notes"
    store.authorize.return_value = DownloadGrant(
        store.metadata.return_value, SIGNED_URL, store.metadata.return_value.size or 42
    )
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=store)
    factory.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(handlers, "GalaxyStore", factory)
    return store


def test_sub_persists_without_querying_or_claiming_delivery(monkeypatch, transport):
    factory = Mock(side_effect=AssertionError("Subscription must not query source"))
    monkeypatch.setattr(handlers, "GalaxyStore", factory)
    handlers.handle_sub(message())
    app = AppRequest("com.example.app", "CN")
    assert db.pending_subscribers(app, release()) == [100]
    card = transport[1].send.call_args.args[1]
    assert "尚未确认" in card.html()
    assert db.get_subscriptions(100)[0]["release_json"] is None
    assert transport[1].send.call_args.args[2].to_dict()["inline_keyboard"] == [
        [{"text": "获取链接", "callback_data": "gdl:" + app.key}]
    ]


@pytest.mark.parametrize(
    "user,kind", [(999, "private"), (100, "group"), (100, "supergroup")]
)
def test_unauthorized_commands_do_not_write_or_query(transport, user, kind):
    handlers.handle_sub(message(user=user, chat_type=kind))
    assert db.get_subscriptions() == []
    transport[0].assert_not_called()
    transport[1].send.assert_not_called()


def test_whitelist_has_download_permissions_but_not_owner_commands(
    transport, monkeypatch
):
    db.add_to_whitelist(200)
    launch = Mock()
    monkeypatch.setattr(handlers, "start_link", launch)
    handlers.handle_dl(message("/dl com.example.app CN", user=200))
    launch.assert_called_once_with(200, AppRequest("com.example.app", "CN"))
    handlers.handle_help(message("/help", user=200))
    transport[1].send.assert_not_called()
    assert db.get_subscriptions(200) == []


def test_list_status_and_legacy_controls_use_only_cache(monkeypatch, transport):
    monkeypatch.setattr(
        handlers, "GalaxyStore", Mock(side_effect=AssertionError("No source queries"))
    )
    db.add_subscription(100, AppRequest("com.example.app", "CN"))
    handlers.handle_list(message("/list"))
    handlers.handle_status(message("/status"))
    handlers.handle_app(message("/app keyword"))
    handlers.handle_legacy_callback(NS(id="old", data="dl:expired"))
    assert transport[1].send.call_count == 2


def test_durable_callback_requeries_latest_without_subscribing(monkeypatch, transport):
    app = AppRequest("com.example.app", "CN")
    db.remember_app(app)
    launch = Mock()
    monkeypatch.setattr(handlers, "start_link", launch)
    call = NS(
        id="callback", data="gdl:" + app.key, message=message(), from_user=NS(id=100)
    )
    handlers.handle_link_callback(call)
    transport[0].answer_callback_query.assert_called_once()
    launch.assert_called_once_with(100, app)
    assert db.get_subscriptions(100) == []


def test_callback_requires_authorized_private_caller(monkeypatch, transport):
    app = AppRequest("com.example.app", "CN")
    db.remember_app(app)
    launch = Mock()
    monkeypatch.setattr(handlers, "start_link", launch)
    handlers.handle_link_callback(
        NS(id="c", data="gdl:" + app.key, message=message(), from_user=NS(id=999))
    )
    launch.assert_not_called()
    transport[0].answer_callback_query.assert_called_once()


def test_refresh_button_fetches_new_version_and_url_without_subscribing(
    monkeypatch, transport, caplog
):
    app = AppRequest("com.example.app", "CN")
    store = fake_store(monkeypatch)
    newer = release(version_name="01.02.4", version_code=124)
    fresh_url = SIGNED_URL.replace("private-link", "fresh-private-link")
    store.metadata.side_effect = [release(), newer]
    store.authorize.side_effect = [
        DownloadGrant(release(), SIGNED_URL, 42),
        DownloadGrant(newer, fresh_url, 42),
    ]
    handlers._link_once(100, app, 5)
    first = transport[1].send.call_args.args[2].to_dict()["inline_keyboard"][0]
    # The callback resolves the durable application record after restarting DB access.
    db.init_db()
    monkeypatch.setattr(
        handlers, "start_link", lambda chat, app: handlers._link_once(chat, app, 6)
    )
    handlers.handle_link_callback(
        NS(
            id="refresh",
            data=first[1]["callback_data"],
            message=message(),
            from_user=NS(id=100),
        )
    )
    card, markup = transport[1].send.call_args.args[1:]
    assert first[0]["url"] == SIGNED_URL
    assert markup.to_dict()["inline_keyboard"][0][0]["url"] == fresh_url
    assert newer.version_name in card.html()
    assert store.metadata.call_count == store.authorize.call_count == 2
    assert db.get_subscriptions() == []
    assert (
        "private-link"
        not in db.DB_PATH.read_bytes().decode(errors="ignore") + caplog.text
    )
    transport[0].send_document.assert_not_called()


def test_revoked_user_cannot_refresh_old_button(monkeypatch, transport):
    app = AppRequest("com.example.app", "CN")
    db.add_to_whitelist(200)
    keyboard = handlers.keyboard(app, SIGNED_URL).to_dict()["inline_keyboard"][0]
    db.remove_from_whitelist(200)
    launch = Mock()
    monkeypatch.setattr(handlers, "start_link", launch)
    handlers.handle_link_callback(
        NS(
            id="revoked",
            data=keyboard[1]["callback_data"],
            message=message(user=200),
            from_user=NS(id=200),
        )
    )
    launch.assert_not_called()
    transport[0].answer_callback_query.assert_called_once_with("revoked", "无权使用。")


def test_update_marks_only_confirmed_subscribers_and_never_downloads(
    monkeypatch, transport
):
    app = AppRequest("com.example.app", "CN")
    db.add_to_whitelist(200)
    db.add_subscription(100, app)
    db.add_subscription(200, app)
    store = fake_store(monkeypatch)
    transport[1].send.side_effect = [NS(message_id=1), requests.Timeout("uncertain")]
    handlers.check_app(app)
    assert db.pending_subscribers(app, release()) == [200]
    store.download.assert_not_called()
    store.authorize.assert_not_called()


def test_failed_source_check_preserves_cached_version(monkeypatch, transport):
    app = AppRequest("com.example.app", "CN")
    db.add_subscription(100, app)
    db.cache_release(app, release(), "old notes")
    store = fake_store(monkeypatch)
    store.metadata.side_effect = ServiceError("Service error")
    handlers.run_check_all()
    assert db.cached_release(db.get_subscriptions()[0]) == release()
    assert db.pending_subscribers(app, release()) == [100]


@pytest.mark.parametrize("size", [85_193_594, 3_000_000_000])
def test_link_delivery_never_fetches_apk_or_uploads_or_persists_url(
    monkeypatch, tmp_path, transport, caplog, size
):
    caplog.set_level(logging.DEBUG)
    app = AppRequest("com.example.app", "CN")
    sentinel = tmp_path / "existing.apk"
    sentinel.write_bytes(b"keep")
    store = fake_store(monkeypatch, selected=release(size=size))
    handlers._link_once(100, app, 5)
    store.metadata.assert_called_once_with(app)
    store.authorize.assert_called_once_with(store.metadata.return_value)
    assert [c[0] for c in store.mock_calls] == ["metadata", "notes", "authorize"]
    store.download.assert_not_called()
    transport[0].send_document.assert_not_called()
    card, markup = transport[1].send.call_args.args[1:]
    keys = markup.to_dict()["inline_keyboard"]
    assert keys == [
        [
            {"text": "下载", "url": SIGNED_URL},
            {"text": "刷新链接", "callback_data": "gdl:" + app.key},
        ]
    ]
    assert "APK sent" not in card.html() and "SHA256" not in card.html()
    assert f"{size / 1_000_000:.2f} MB" in card.html()
    assert "刷新链接" in card.html() and SIGNED_URL not in card.html()
    assert "private-link" not in db.DB_PATH.read_bytes().decode(errors="ignore")
    assert "private-link" not in caplog.text
    assert db.get_subscriptions() == [] and db.get_app(app.key) == app
    assert (
        set(tmp_path.iterdir()) == {db.DB_PATH, sentinel}
        and sentinel.read_bytes() == b"keep"
    )


def test_subscriber_buttons_fetch_links_on_demand(monkeypatch, transport):
    app = AppRequest("com.example.app", "CN")
    db.add_to_whitelist(200)
    db.add_subscription(100, app)
    db.add_subscription(200, app)
    store = fake_store(monkeypatch)
    handlers.check_app(app)
    calls = transport[1].send.call_args_list
    assert len(calls) == 2
    assert [call.args[0] for call in calls] == [100, 200]
    for call in calls:
        assert call.args[2].to_dict()["inline_keyboard"] == [
            [{"text": "获取链接", "callback_data": "gdl:" + app.key}]
        ]
    store.authorize.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        requests.Timeout("private-link"),
        rejection(400, "URL invalid private-link"),
        rejection(500, "Internal Server Error private-link"),
    ],
)
def test_link_delivery_failure_never_retries_or_downloads(
    monkeypatch, transport, caplog, failure
):
    store = fake_store(monkeypatch)
    transport[1].send.side_effect = failure
    handlers._link_once(100, AppRequest("com.example.app", "CN"), 5)
    transport[1].send.assert_called_once()
    store.authorize.assert_called_once()
    store.download.assert_not_called()
    transport[0].send_document.assert_not_called()
    text = transport[0].edit_message_text.call_args.args[0]
    assert "未确认" in text and "已发送" not in text
    assert "private-link" not in text + caplog.text


@pytest.mark.parametrize(
    "failure",
    [
        ServiceError("Source unavailable"),
        VersionDrift("Version changed"),
        requests.Timeout("private-link"),
    ],
)
@pytest.mark.parametrize("operation", ["metadata", "authorize"])
def test_source_failure_sends_no_link_and_has_safe_diagnostics(
    monkeypatch, transport, caplog, failure, operation
):
    store = fake_store(monkeypatch)
    getattr(store, operation).side_effect = failure
    handlers._link_once(100, AppRequest("com.example.app", "CN"), 5)
    transport[1].send.assert_not_called()
    store.download.assert_not_called()
    assert "获取链接失败" in transport[0].edit_message_text.call_args.args[0]
    assert "private-link" not in caplog.text + str(transport[0].mock_calls)


def test_cn_bot_card_authorizes_but_never_requests_apk(monkeypatch, transport):
    from scraper import GalaxyStore
    from tests.test_scraper import Response, session

    grant = ods(
        dict(
            productID="00001", binaryArch="64", contentsSize=42, downLoadURI=SIGNED_URL
        )
    )
    http = session(Response(metadata()), Response(b"{}"), Response(grant))
    store = GalaxyStore(http)
    monkeypatch.setattr(handlers, "GalaxyStore", lambda: store)
    handlers._link_once(100, AppRequest("com.example.app", "CN"), 5)
    calls = http.request.call_args_list
    assert len(calls) == 3
    assert calls[0].args == (
        "POST",
        "https://cn-ms.galaxyappstore.com/ods.as?reqId=2298&ot=01&ct=B",
    )
    assert calls[1].args == (
        "GET",
        "https://galaxystore.samsung.com/api/detail/com.example.app?cntyCd=CHN",
    )
    assert calls[2].args == (
        "POST",
        "https://cn-ms.galaxyappstore.com/ods.as?reqId=2316&ot=01&ct=B",
    )
    url = transport[1].send.call_args.args[2].to_dict()["inline_keyboard"][0][0]["url"]
    assert url == SIGNED_URL
    transport[0].send_document.assert_not_called()


def test_bare_input_and_dl_both_request_links_without_subscribing(
    monkeypatch, transport
):
    launch = Mock()
    monkeypatch.setattr(handlers, "start_link", launch)
    handlers.handle_bare(message("com.example.app CN"))
    handlers.handle_dl(message("/dl com.example.app CN"))
    assert launch.call_count == 2
    assert all(
        c.args == (100, AppRequest("com.example.app", "CN"))
        for c in launch.call_args_list
    )
    assert db.get_subscriptions() == []


def test_worker_releases_slot_after_failed_link_request(monkeypatch, transport):
    import threading

    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(handlers, "_link_slots", slots)
    store = fake_store(monkeypatch)
    store.metadata.side_effect = ServiceError("Source unavailable")

    class InlineThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(handlers.threading, "Thread", InlineThread)
    app = AppRequest("com.example.app", "CN")
    handlers.start_link(100, app)
    handlers.start_link(100, app)
    assert store.metadata.call_count == 2
    assert slots.acquire(blocking=False)


def test_real_application_startup_and_shutdown_with_polling_stub(monkeypatch):
    import main
    import socket

    # Startup must not require a public origin or open any incoming listener.
    for name in (
        "PUBLIC_DOWNLOAD_BASE_URL",
        "DOWNLOAD_BIND_HOST",
        "DOWNLOAD_BIND_PORT",
    ):
        monkeypatch.delenv(name, raising=False)
    bind = Mock(side_effect=AssertionError("The bot must not open a listener"))
    monkeypatch.setattr(socket.socket, "bind", bind)
    from apscheduler.schedulers.background import BackgroundScheduler

    schedulers = []

    def scheduler_factory(**kwargs):
        scheduler = BackgroundScheduler(**kwargs)
        schedulers.append(scheduler)
        return scheduler

    monkeypatch.setattr(main, "BackgroundScheduler", scheduler_factory)

    def polling(**kwargs):
        assert kwargs["allowed_updates"] == ["message", "callback_query"]
        assert schedulers[0].running
        assert schedulers[0].get_job("galaxy_check") is not None
        assert db.get_subscriptions() == []

    monkeypatch.setattr(handlers.bot, "infinity_polling", polling)
    monkeypatch.setattr(handlers.bot, "stop_polling", Mock())
    monkeypatch.setattr(handlers.bot, "get_me", Mock(return_value=NS(id=123456)))
    monkeypatch.setattr(handlers.bot, "get_webhook_info", Mock(return_value=NS(url="")))
    main.main()
    bind.assert_not_called()
    assert not schedulers[0].running
    handlers.bot.stop_polling.assert_called_once()


@pytest.mark.parametrize(
    "failure,webhook",
    [
        (requests.ConnectionError("hidden token"), "unused"),
        (None, "https://example.test/hook"),
    ],
)
def test_startup_rejects_unavailable_api_or_existing_webhook(
    monkeypatch, failure, webhook
):
    import main

    polling = Mock()
    scheduler = Mock()
    monkeypatch.setattr(handlers.bot, "get_me", Mock(side_effect=failure))
    monkeypatch.setattr(
        handlers.bot, "get_webhook_info", Mock(return_value=NS(url=webhook))
    )
    monkeypatch.setattr(handlers.bot, "infinity_polling", polling)
    monkeypatch.setattr(main, "BackgroundScheduler", scheduler)
    with pytest.raises(RuntimeError) as caught:
        main.main()
    assert "hidden token" not in str(caught.value)
    scheduler.assert_not_called()
    polling.assert_not_called()


def test_sigterm_stops_polling_and_restores_signal_handler(monkeypatch):
    import main, signal

    previous = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(handlers.bot, "get_me", Mock(return_value=NS(id=123456)))
    monkeypatch.setattr(handlers.bot, "get_webhook_info", Mock(return_value=NS(url="")))
    stop = Mock()
    monkeypatch.setattr(handlers.bot, "stop_polling", stop)

    def polling(**kwargs):
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        stop.assert_called_once()

    monkeypatch.setattr(handlers.bot, "infinity_polling", polling)
    main.main()
    assert signal.getsignal(signal.SIGTERM) is previous
