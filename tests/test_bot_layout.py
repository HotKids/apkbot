from unittest.mock import Mock
from types import SimpleNamespace as NS

import pytest

import database as db
import handlers
from telebot import types
from telebot import apihelper
from galaxy_store import AppRequest, ServiceError
from tests import test_galaxy_bot
from tests.test_galaxy_bot import fake_store, message
from tests.test_galaxy_store import release
from tests.test_cards import ParsedHTML

transport = test_galaxy_bot.transport


def test_download_button_order_and_get_link_state():
    app = AppRequest("com.example.app")
    keys = handlers.keyboard(app, "https://download.samsungapps.com/test.apk")
    assert [b["text"] for b in keys.to_dict()["inline_keyboard"][0]] == ["刷新", "下载"]
    assert keys.to_dict()["inline_keyboard"][0][0]["callback_data"] == "grf:" + app.key
    assert handlers.keyboard(app).to_dict()["inline_keyboard"] == [
        [{"text": "获取下载链接", "callback_data": "gdl:" + app.key}]
    ]


def test_expired_failure_feedback_preserves_card_without_extra_message(
    monkeypatch, transport
):
    store = fake_store(monkeypatch)
    store.metadata.side_effect = ServiceError("商店暂时不可用。")
    transport[0].answer_callback_query.side_effect = RuntimeError("expired")
    handlers._link_once(100, AppRequest("com.example.app"), 55, "expired")
    transport[0].send_message.assert_not_called()
    transport[0].edit_message_text.assert_not_called()
    transport[1].edit.assert_not_called()
    transport[1].send.assert_not_called()


def test_auto_confirmation_reuses_latest_release_without_query(monkeypatch, transport):
    selected = release(name="三星生活助手", version_name="9.4")
    db.cache_release(AppRequest(selected.package, "CN"), selected, None)
    monkeypatch.setattr(
        handlers, "GalaxyStore", Mock(side_effect=AssertionError("No query"))
    )
    handlers.handle_sub(message("/sub com.example.app"))
    card = transport[1].send.call_args.args[1].html()
    assert "三星生活助手" in card and "版本：9.4" in card
    assert "🇨🇳" not in card and "AUTO" not in card and "🌐" not in card
    assert 'href="https://galaxystore.samsung.com/detail/com.example.app"' in card
    assert "10 分钟" not in card


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/dl com.example.app", "当前账户无使用权限。"),
        ("/check", "此操作仅限管理员。"),
    ],
)
def test_private_permission_feedback(transport, text, expected):
    if text.startswith("/dl"):
        handlers.handle_dl(message(text, user=999))
    else:
        handlers.handle_check(message(text, user=999))
    assert transport[0].reply_to.call_args.args[1] == expected


def test_check_summary_reports_queries_without_claiming_delivery(
    monkeypatch, transport
):
    db.add_subscription(100, AppRequest("com.example.app"))
    fake_store(monkeypatch)
    handlers.run_check_all(triggered_by=100)
    assert (
        transport[0].send_message.call_args.args[1]
        == "更新检查已完成。1 项查询成功，0 项查询失败。"
    )


def test_unconfirmed_callback_edit_only_uses_existing_feedback(monkeypatch, transport):
    fake_store(monkeypatch)
    transport[1].edit.side_effect = RuntimeError("uncertain")
    handlers._link_once(100, AppRequest("com.example.app"), 55, "callback")
    transport[0].answer_callback_query.assert_called_once_with(
        "callback", "暂时无法确认操作结果，请查看当前卡片。"
    )
    transport[0].send_message.assert_not_called()
    transport[0].edit_message_text.assert_not_called()


def test_list_link_failure_uses_get_link_copy(monkeypatch, transport):
    store = fake_store(monkeypatch)
    store.metadata.side_effect = ServiceError("商店暂时不可用。")
    handlers._link_once(100, AppRequest("com.example.app"), 55, querying=False)
    assert (
        transport[0].edit_message_text.call_args.args[0]
        == "下载链接获取失败：商店暂时不可用。"
    )


@pytest.mark.parametrize("rich", [False, True])
def test_old_download_callbacks_still_report_refresh(monkeypatch, transport, rich):
    app = AppRequest("com.example.app")
    db.remember_app(app)
    launch = Mock()
    monkeypatch.setattr(handlers, "start_link", launch)
    old_message = message()
    if rich:
        old_message.json = {
            "rich_message": {
                "blocks": [
                    {
                        "type": "buttons",
                        "buttons": [{"text": "下载", "url": "https://example.test"}],
                    }
                ]
            }
        }
    else:
        old_message.reply_markup = handlers.keyboard(app, "https://example.test")
    handlers.handle_link_callback(
        NS(id="old", data="gdl:" + app.key, message=old_message, from_user=NS(id=100))
    )
    launch.assert_called_once_with(
        100, app, message_id=5, callback_id="old", refresh=True
    )


def test_registered_callback_dispatch_reaches_link_handler(monkeypatch):
    app = AppRequest("com.example.app")
    db.remember_app(app)
    launch = Mock()
    monkeypatch.setattr(handlers, "start_link", launch)
    monkeypatch.setattr(handlers.bot, "threaded", False)
    call = types.CallbackQuery.de_json(
        {
            "id": "callback",
            "from": {"id": 100, "is_bot": False, "first_name": "Test"},
            "chat_instance": "synthetic",
            "data": "gdl:" + app.key,
            "message": {
                "message_id": 55,
                "date": 1,
                "chat": {"id": 100, "type": "private"},
            },
        }
    )
    handlers.bot.process_new_callback_query([call])
    launch.assert_called_once_with(
        100, app, message_id=55, callback_id="callback", refresh=False
    )


@pytest.mark.parametrize(
    "command,expected_pages", [("/list", 3), ("/status", 3), ("/user", 2)]
)
@pytest.mark.parametrize("known_name", [True, False])
def test_long_lists_keep_every_item_within_telegram_length(
    transport, command, expected_pages, known_name
):
    for index in range(12):
        user_id = 2**63 - 1 - index
        db.add_to_whitelist(user_id, "😀" * 200)
        package = "com." + "a" * 248 + f"{index:03d}"
        app = AppRequest(package)
        db.add_subscription(100, app)
        db.cache_release(
            app,
            release(
                package=package,
                name="😀" * 100 if known_name else package,
                version_name="😀" * 100,
            ),
            None,
        )
    handler = {
        "/list": handlers.handle_list,
        "/status": handlers.handle_status,
        "/user": handlers.handle_user_list,
    }[command]
    handler(message(command))
    assert transport[1].send.call_count == expected_pages
    for sent in transport[1].send.call_args_list:
        parsed = ParsedHTML(sent.args[1].html())
        assert not parsed.stack
        assert len("".join(parsed.content).encode("utf-16-le")) // 2 <= 4096
    if command != "/user":
        assert (
            sum(len(sent.args[1].entries) for sent in transport[1].send.call_args_list)
            == 12
        )


def test_explicit_subscription_uses_latest_name_and_only_matching_region(
    monkeypatch, transport
):
    db.cache_release(
        AppRequest("com.example.app", "CN"),
        release(name="Known name", region="CN"),
        None,
    )
    monkeypatch.setattr(
        handlers, "GalaxyStore", Mock(side_effect=AssertionError("No query"))
    )
    handlers.handle_sub(message("/sub com.example.app US"))
    html = transport[1].send.call_args.args[1].html()
    assert "Known name" in html and "暂无版本信息。" in html
    assert "🇨🇳" not in html and "🇺🇸" not in html


def test_plain_sends_replies_and_progress_edits_disable_previews(monkeypatch):
    import json

    request = Mock(
        return_value={
            "message_id": 55,
            "date": 1,
            "chat": {"id": 100, "type": "private"},
        }
    )
    monkeypatch.setattr(apihelper, "_make_request", request)
    handlers.bot.send_message(100, "https://example.test")
    handlers.bot.reply_to(message(), "https://example.test")
    handlers.edit_progress(100, 55, "https://example.test")
    assert request.call_count == 3
    for sent in request.call_args_list:
        assert (
            json.loads(sent.kwargs["params"]["link_preview_options"])["is_disabled"]
            is True
        )
