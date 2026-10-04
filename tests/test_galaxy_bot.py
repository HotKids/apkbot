from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock
import logging
import pytest
import requests
import database as db
import handlers
from galaxy_store import AppRequest, DownloadGrant, ServiceError, VersionDrift
from tests.test_galaxy_store import metadata, ods, release, stub
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
    from scraper import GalaxyStore

    store = Mock()
    store.metadata.return_value = selected or release()
    store.details.side_effect = lambda selected: (selected, "notes")
    store.authorize.return_value = DownloadGrant(
        store.metadata.return_value, SIGNED_URL, store.metadata.return_value.size or 42
    )
    store.download_link.side_effect = lambda app: GalaxyStore.download_link(store, app)
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
    assert "检测到新版本时将自动通知。" in card.html()
    assert "暂无版本信息。" in card.html()
    assert db.get_subscriptions(100)[0]["release_json"] is None
    assert transport[1].send.call_args.args[2].to_dict()["inline_keyboard"] == [
        [{"text": "获取下载链接", "callback_data": "gdl:" + app.key}]
    ]


def test_query_caches_app_name_and_date_for_subscription_and_list(
    monkeypatch, transport
):
    selected = release(name="三星生活助手", updated_date="2026-08-25")
    store = fake_store(monkeypatch, selected)
    app = AppRequest(selected.package, "CN")
    handlers._link_once(100, app, 5)
    assert "更新时间：2026-08-25" in transport[1].edit.call_args.args[2].html()
    db.init_db()
    store.reset_mock()
    handlers.handle_sub(message())
    assert "三星生活助手" in transport[1].send.call_args.args[1].html()
    assert "版本：01.02.3 · 🇨🇳" in transport[1].send.call_args.args[1].html()
    assert db.cached_release(db.get_subscriptions(100)[0]) == selected
    assert db.pending_subscribers(app, selected) == [100]
    handlers.handle_list(message("/list"))
    assert "三星生活助手" in transport[1].send.call_args.args[1].html()
    store.metadata.assert_not_called()
    store.details.assert_not_called()


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


def test_list_is_a_rich_card_with_one_quote_and_button_per_app(transport):
    from tests.test_richmsg import assert_valid_blocks

    named = AppRequest("com.example.app")
    db.add_subscription(100, named)
    db.cache_release(named, release(name="三星生活助手", version_name="9.4"), None)
    unnamed = AppRequest("com.other.app", "US")
    db.add_subscription(100, unnamed)
    handlers.handle_list(message("/list"))
    card, markup = transport[1].send.call_args.args[1:]
    blocks = card.blocks()
    assert_valid_blocks(blocks)
    assert [b["type"] for b in blocks] == [
        "heading",
        "paragraph",
        "blockquote",
        "paragraph",
        "blockquote",
        "footer",
    ]
    assert card.title == "我的订阅（2 项）"
    assert blocks[1]["text"] == [
        {
            "type": "url",
            "text": {"type": "bold", "text": "三星生活助手"},
            "url": "https://galaxystore.samsung.com/detail/com.example.app",
        },
    ]
    assert "credit" not in blocks[2]
    assert blocks[2]["blocks"][0]["text"] == [
        "版本：9.4",
        "\n包名：",
        {"type": "code", "text": "com.example.app"},
    ]
    assert blocks[3]["text"][0]["text"] == {"type": "bold", "text": "com.other.app"}
    assert "credit" not in blocks[4]
    assert blocks[4]["blocks"][0]["text"] == [
        "暂无版本信息。",
        "\n包名：",
        {"type": "code", "text": "com.other.app"},
    ]
    assert markup.to_dict()["inline_keyboard"] == [
        [
            {"text": "三星生活助手", "callback_data": "ldl:" + named.key},
            {"text": "com.other.app", "callback_data": "ldl:" + unnamed.key},
        ]
    ]
    html = card.html()
    assert "三星生活助手</b></a>\n<blockquote>版本：9.4" in html
    assert "AUTO" not in html and "🇨🇳" not in html and "🌐" not in html
    handlers.handle_status(message("/status"))
    status_card, status_markup = transport[1].send.call_args.args[1:]
    assert status_markup is None and "用户 100" in status_card.html()


def test_list_button_opens_a_new_download_card_and_keeps_the_list(
    monkeypatch, transport
):
    app = AppRequest("com.example.app", "CN")
    db.add_subscription(100, app)
    launch = Mock()
    monkeypatch.setattr(handlers, "start_link", launch)
    handlers.handle_link_callback(
        NS(id="list", data="ldl:" + app.key, message=message(), from_user=NS(id=100))
    )
    launch.assert_called_once_with(100, app, querying=False)
    transport[0].answer_callback_query.assert_called_once()


def test_durable_callback_requeries_latest_without_subscribing(monkeypatch, transport):
    app = AppRequest("com.example.app", "CN")
    db.remember_app(app)
    launch = Mock()
    monkeypatch.setattr(handlers, "start_link", launch)
    call = NS(
        id="callback", data="gdl:" + app.key, message=message(), from_user=NS(id=100)
    )
    handlers.handle_link_callback(call)
    launch.assert_called_once_with(
        100, app, message_id=5, callback_id="callback", refresh=False
    )
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
    first = transport[1].edit.call_args.args[3].to_dict()["inline_keyboard"][0]
    # The callback resolves the durable application record after restarting DB access.
    db.init_db()
    monkeypatch.setattr(
        handlers,
        "start_link",
        lambda chat, app, **kwargs: handlers._link_once(
            chat,
            app,
            kwargs["message_id"],
            kwargs["callback_id"],
            refresh=kwargs["refresh"],
        ),
    )
    handlers.handle_link_callback(
        NS(
            id="refresh",
            data=first[0]["callback_data"],
            message=message(),
            from_user=NS(id=100),
        )
    )
    card, markup = transport[1].edit.call_args.args[2:]
    assert first[1]["url"] == SIGNED_URL
    assert markup.to_dict()["inline_keyboard"][0][1]["url"] == fresh_url
    assert newer.version_name in card.html()
    assert store.metadata.call_count == store.authorize.call_count == 2
    assert transport[1].edit.call_count == 2
    assert all(call.args[:2] == (100, 5) for call in transport[1].edit.call_args_list)
    transport[1].send.assert_not_called()
    transport[0].send_message.assert_not_called()
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
            data=keyboard[0]["callback_data"],
            message=message(user=200),
            from_user=NS(id=200),
        )
    )
    launch.assert_not_called()
    transport[0].answer_callback_query.assert_called_once_with(
        "revoked", "当前账户无使用权限。"
    )


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
    assert [c[0] for c in store.mock_calls] == [
        "download_link",
        "metadata",
        "authorize",
        "details",
    ]
    store.download.assert_not_called()
    transport[0].send_document.assert_not_called()
    card, markup = transport[1].edit.call_args.args[2:]
    keys = markup.to_dict()["inline_keyboard"]
    assert keys == [
        [
            {"text": "刷新", "callback_data": "grf:" + app.key},
            {"text": "下载", "url": SIGNED_URL},
        ]
    ]
    assert "APK sent" not in card.html() and "SHA256" not in card.html()
    assert f"{size / 1_000_000:.2f} MB" in card.html()
    assert SIGNED_URL not in card.html()
    assert transport[1].edit.call_args.args[:2] == (100, 5)
    transport[1].send.assert_not_called()
    transport[0].delete_message.assert_not_called()
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
            [{"text": "获取下载链接", "callback_data": "gdl:" + app.key}]
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
    transport[1].edit.side_effect = failure
    handlers._link_once(100, AppRequest("com.example.app", "CN"), 5)
    transport[1].edit.assert_called_once()
    store.authorize.assert_called_once()
    store.download.assert_not_called()
    transport[0].send_document.assert_not_called()
    transport[0].edit_message_text.assert_not_called()
    transport[1].send.assert_not_called()
    assert "unconfirmed" in caplog.text
    assert "private-link" not in caplog.text


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
    transport[1].edit.assert_not_called()
    store.download.assert_not_called()
    expected = "应用信息查询失败" if operation == "metadata" else "下载链接获取失败"
    assert expected in transport[0].edit_message_text.call_args.args[0]
    assert "private-link" not in caplog.text + str(transport[0].mock_calls)


@pytest.mark.parametrize("architecture", ["64", "32n64"])
@pytest.mark.parametrize("region", ["CN", "AUTO"])
def test_cn_bot_card_authorizes_but_never_requests_apk(
    monkeypatch, transport, architecture, region
):
    from scraper import GalaxyStore
    from tests.test_scraper import Response, session

    grant = ods(
        dict(
            productID="00001",
            version="01.02.3",
            versionCode="123",
            binaryArch=architecture,
            contentsSize=42,
            downLoadURI=SIGNED_URL,
        )
    )
    responses = [Response(metadata()), Response(grant), Response(b"{}")]
    if region == "AUTO":
        responses.insert(
            0,
            Response(
                stub("No app matched device/country/MCC/MNC/CSC/API conditions", "0")
            ),
        )
    http = session(*responses)
    store = GalaxyStore(http)
    monkeypatch.setattr(handlers, "GalaxyStore", lambda: store)
    handlers._link_once(100, AppRequest("com.example.app", region), 5)
    calls = http.request.call_args_list
    if region == "AUTO":
        assert calls[0].args[0] == "GET" and "stubDownload" in calls[0].args[1]
        calls = calls[1:]
    assert len(calls) == 3
    assert calls[0].args == (
        "POST",
        "https://cn-ms.galaxyappstore.com/ods.as?reqId=2298&ot=01&ct=B",
    )
    assert calls[1].args == (
        "POST",
        "https://cn-ms.galaxyappstore.com/ods.as?reqId=2311&ot=01&ct=B",
    )
    assert calls[2].args == (
        "GET",
        "https://galaxystore.samsung.com/api/detail/com.example.app?cntyCd=CHN",
    )
    url = transport[1].edit.call_args.args[3].to_dict()["inline_keyboard"][0][1]["url"]
    assert url == SIGNED_URL
    assert " · 🇨🇳" in transport[1].edit.call_args.args[2].html()
    assert "🇨🇳 CN" not in transport[1].edit.call_args.args[2].html()
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


def test_initial_query_edits_progress_into_card_without_second_message(
    monkeypatch, transport
):
    fake_store(monkeypatch)
    transport[0].send_message.return_value = NS(message_id=55)

    class InlineThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(handlers.threading, "Thread", InlineThread)
    handlers.start_link(100, AppRequest("com.example.app", "CN"))
    transport[0].send_message.assert_called_once()
    assert transport[1].edit.call_args.args[:2] == (100, 55)
    transport[1].send.assert_not_called()
    transport[0].delete_message.assert_not_called()


@pytest.mark.parametrize("operation", ["metadata", "authorize", "edit"])
def test_refresh_failure_leaves_original_card_and_reports_via_callback(
    monkeypatch, transport, caplog, operation
):
    store = fake_store(monkeypatch)
    if operation == "edit":
        transport[1].edit.side_effect = requests.Timeout("private-link")
    else:
        getattr(store, operation).side_effect = ServiceError("商店暂时不可用。")
    handlers._link_once(100, AppRequest("com.example.app", "CN"), 55, "refresh")
    transport[0].answer_callback_query.assert_called_once()
    text = transport[0].answer_callback_query.call_args.args[1]
    assert "失败" in text or "暂时无法确认操作结果" in text
    assert "private-link" not in text + caplog.text
    transport[0].edit_message_text.assert_not_called()
    transport[0].send_message.assert_not_called()
    transport[1].send.assert_not_called()


def test_busy_refresh_does_not_send_another_message(monkeypatch, transport):
    monkeypatch.setattr(handlers, "_link_slots", Mock(acquire=Mock(return_value=False)))
    handlers.start_link(100, AppRequest("com.example.app", "CN"), 55, "busy")
    transport[0].answer_callback_query.assert_called_once()
    transport[0].send_message.assert_not_called()
    transport[1].edit.assert_not_called()


def test_expired_callback_acknowledgement_does_not_undo_successful_edit(
    monkeypatch, transport
):
    fake_store(monkeypatch)
    transport[0].answer_callback_query.side_effect = rejection(400, "query is too old")
    handlers._link_once(100, AppRequest("com.example.app", "CN"), 55, "expired")
    transport[1].edit.assert_called_once()
    transport[0].edit_message_text.assert_not_called()
    transport[0].send_message.assert_not_called()


def test_refresh_failure_after_expired_callback_keeps_card_without_reply(
    monkeypatch, transport, caplog
):
    store = fake_store(monkeypatch)
    store.metadata.side_effect = ServiceError("商店暂时不可用。")
    transport[0].answer_callback_query.side_effect = rejection(400, "query is too old")
    handlers._link_once(100, AppRequest("com.example.app", "CN"), 55, "expired")
    transport[0].send_message.assert_not_called()
    transport[0].edit_message_text.assert_not_called()
    transport[1].edit.assert_not_called()


def test_unsub_without_region_removes_every_region_of_that_package(transport):
    for region in ("AUTO", "CN", "US"):
        db.add_subscription(100, AppRequest("com.example.app", region))
    handlers.handle_unsub(message("/unsub com.example.app CN"))
    assert transport[0].reply_to.call_args.args[1] == "已取消 1 项订阅。"
    url = "https://galaxystore.samsung.com/detail/com.example.app?session_id=W_1"
    handlers.handle_unsub(message("/unsub " + url))
    assert transport[0].reply_to.call_args.args[1] == "已取消 2 项订阅。"
    assert db.get_subscriptions(100) == []
    handlers.handle_unsub(message("/unsub com.example.app"))
    assert transport[0].reply_to.call_args.args[1] == "已取消 0 项订阅。"


def test_whitelist_replies_name_the_user_and_the_outcome(transport):
    replies = []
    transport[0].reply_to.side_effect = lambda message, text: replies.append(text)
    for text in ("/add 200 朋友", "/add 200", "/del 200", "/del 200"):
        handlers.handle_users(message(text))
    assert replies == [
        "已将用户 200 加入白名单。",
        "该用户已在白名单中。",
        "已将用户 200 移出白名单。",
        "该用户未在白名单中。",
    ]


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
        job = schedulers[0].get_job("galaxy_check")
        # The first check follows startup instead of a full interval later.
        delay = job.next_run_time - datetime.now(timezone.utc)
        assert timedelta(seconds=30) < delay <= timedelta(minutes=1)
        assert db.get_subscriptions() == []

    monkeypatch.setattr(handlers.bot, "infinity_polling", polling)
    monkeypatch.setattr(handlers.bot, "stop_polling", Mock())
    monkeypatch.setattr(handlers.bot, "get_me", Mock(return_value=NS(id=123456)))
    monkeypatch.setattr(handlers.bot, "get_webhook_info", Mock(return_value=NS(url="")))
    monkeypatch.setattr(handlers.bot, "set_my_commands", Mock(return_value=True))
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
    import main
    import signal

    previous = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(handlers.bot, "get_me", Mock(return_value=NS(id=123456)))
    monkeypatch.setattr(handlers.bot, "get_webhook_info", Mock(return_value=NS(url="")))
    monkeypatch.setattr(handlers.bot, "set_my_commands", Mock(return_value=True))
    stop = Mock()
    monkeypatch.setattr(handlers.bot, "stop_polling", stop)

    def polling(**kwargs):
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        stop.assert_called_once()

    monkeypatch.setattr(handlers.bot, "infinity_polling", polling)
    main.main()
    assert signal.getsignal(signal.SIGTERM) is previous


@pytest.mark.parametrize("origin", ["subscription", "notification"])
def test_subscription_get_link_turns_same_card_into_download_and_refresh(
    monkeypatch, transport, origin
):
    app = AppRequest("com.example.app", "CN")
    store = fake_store(monkeypatch)
    if origin == "subscription":
        handlers.handle_sub(message())
    else:
        db.add_subscription(100, app)
        handlers.check_app(app)
    store.authorize.assert_not_called()
    original_keys = transport[1].send.call_args.args[2].to_dict()["inline_keyboard"]
    assert original_keys == [
        [{"text": "获取下载链接", "callback_data": "gdl:" + app.key}]
    ]
    transport[1].send.reset_mock()

    class InlineThread:
        def __init__(self, target, daemon):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(handlers.threading, "Thread", InlineThread)
    original = message()
    original.message_id = 55
    handlers.handle_link_callback(
        NS(
            id="get-link",
            data=original_keys[0][0]["callback_data"],
            message=original,
            from_user=NS(id=100),
        )
    )
    store.authorize.assert_called_once()
    assert transport[1].edit.call_args.args[:2] == (100, 55)
    buttons = transport[1].edit.call_args.args[3].to_dict()["inline_keyboard"][0]
    assert buttons == [
        {"text": "刷新", "callback_data": "grf:" + app.key},
        {"text": "下载", "url": SIGNED_URL},
    ]
    transport[1].send.assert_not_called()
    transport[0].send_message.assert_not_called()
    assert len(db.get_subscriptions(100)) == 1
