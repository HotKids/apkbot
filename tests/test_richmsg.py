import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import requests
from telebot import apihelper
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from cards import Card, help_card, release_card
from richmsg import RichMessenger
from tests.test_galaxy_store import release


def rejection(code, description):
    return apihelper.ApiTelegramException(
        "sendRichMessage", None, dict(error_code=code, description=description)
    )


def test_telebot_transport_serialization_and_existing_instance(monkeypatch):
    bot = Mock(token="synthetic-only")
    request = Mock(
        return_value=dict(message_id=5, date=0, chat=dict(id=1, type="private"))
    )
    monkeypatch.setattr(apihelper, "_make_request", request)
    keys = InlineKeyboardMarkup()
    keys.add(InlineKeyboardButton("Download", callback_data="gdl:durable"))
    result = RichMessenger(bot).send(1, Card("Title", ("Body",)), keys)
    assert result.message_id == 5
    args, kwargs = request.call_args
    assert args == ("synthetic-only", "sendRichMessage")
    assert kwargs["method"] == "post"
    assert (
        json.loads(kwargs["params"]["rich_message"])["blocks"][0]["type"] == "heading"
    )
    assert (
        json.loads(kwargs["params"]["reply_markup"])["inline_keyboard"][0][0][
            "callback_data"
        ]
        == "gdl:durable"
    )
    bot.send_message.assert_not_called()


def test_confirmed_unsupported_falls_back_and_is_cached(monkeypatch):
    request = Mock(side_effect=rejection(404, "Not Found: method not found"))
    monkeypatch.setattr(apihelper, "_make_request", request)
    bot = Mock(
        token="synthetic", send_message=Mock(return_value=SimpleNamespace(message_id=1))
    )
    messenger = RichMessenger(bot)
    messenger.send(1, Card("A"))
    messenger.send(1, Card("B"))
    assert request.call_count == 1 and bot.send_message.call_count == 2
    assert not messenger.supported


@pytest.mark.parametrize(
    "failure",
    [
        requests.Timeout("timeout"),
        requests.ConnectionError("lost"),
        rejection(404, "Not Found"),
        rejection(401, "Unauthorized"),
        rejection(403, "Forbidden"),
        rejection(429, "Too Many Requests"),
        rejection(500, "Internal Server Error"),
    ],
)
def test_ambiguous_or_unrelated_failure_never_resends(monkeypatch, failure):
    monkeypatch.setattr(apihelper, "_make_request", Mock(side_effect=failure))
    bot = Mock(token="synthetic")
    with pytest.raises(type(failure)):
        RichMessenger(bot).send(1, Card("A"))
    bot.send_message.assert_not_called()


def test_explicit_structural_rejection_allows_html_fallback(monkeypatch):
    monkeypatch.setattr(
        apihelper,
        "_make_request",
        Mock(
            side_effect=rejection(
                400, "Unsupported InputRichMessageBlock type specified"
            )
        ),
    )
    bot = Mock(
        token="synthetic", send_message=Mock(return_value=SimpleNamespace(message_id=1))
    )
    messenger = RichMessenger(bot)
    messenger.send(1, Card("A"))
    assert messenger.supported
    bot.send_message.assert_called_once()


def test_official_inputrichblock_parse_error_is_confirmed_rejection(monkeypatch):
    failure = rejection(
        400, 'Bad Request: can\'t parse InputRichBlock: type "details" is unsupported'
    )
    monkeypatch.setattr(apihelper, "_make_request", Mock(side_effect=failure))
    bot = Mock(
        token="synthetic", send_message=Mock(return_value=SimpleNamespace(message_id=1))
    )
    RichMessenger(bot).send(1, Card("A"))
    bot.send_message.assert_called_once()


def test_url_button_survives_explicit_html_fallback_without_preview(monkeypatch):
    monkeypatch.setattr(
        apihelper,
        "_make_request",
        Mock(side_effect=rejection(404, "Not Found: method not found")),
    )
    bot = Mock(
        token="synthetic", send_message=Mock(return_value=SimpleNamespace(message_id=1))
    )
    keys = InlineKeyboardMarkup()
    url = "https://download.samsungapps.com/a.apk?token=private&next=a%2Fb"
    keys.add(InlineKeyboardButton("Download", url=url))
    RichMessenger(bot).send(1, Card("Link"), keys)
    options = bot.send_message.call_args.kwargs
    assert options["reply_markup"].to_dict()["inline_keyboard"][0][0]["url"] == url
    assert options["link_preview_options"].is_disabled is True


def test_card_escaping_hierarchy_and_exact_region():
    card = release_card(release(name="<name>&", region="US"), "<script>&")
    assert "&lt;name&gt;&amp;" in card.html()
    assert "<script>" not in card.html()
    assert "🇺🇸 US" in card.html()
    assert "<b>版本：01.02.3</b>" in card.html()
    assert "版本代码：123" in card.html()
    assert "包名：com.example.app" in card.html()
    assert "product ID" not in card.html()
    assert card.blocks()[1]["type"] == "heading"
    assert card.blocks()[1]["text"] == "版本：01.02.3"
    notes = [block for block in card.blocks() if block["type"] == "details"]
    assert len(notes) == 1 and notes[0]["is_open"] is False
    assert any(block["type"] == "details" for block in card.blocks())
    sections = [b for b in help_card().blocks() if b["type"] == "details"]
    assert [b["is_open"] for b in sections] == [True, False, False]


def test_edit_updates_rich_card_and_buttons_on_same_message(monkeypatch):
    request = Mock(
        return_value=dict(message_id=55, date=0, chat=dict(id=100, type="private"))
    )
    monkeypatch.setattr(apihelper, "_make_request", request)
    bot = Mock(token="synthetic-only")
    keys = InlineKeyboardMarkup()
    keys.add(
        InlineKeyboardButton("下载", url="https://download.samsungapps.com/new.apk")
    )
    card = release_card(release(version_name="2.0", version_code=200))
    assert RichMessenger(bot).edit(100, 55, card, keys).message_id == 55
    assert request.call_args.args == ("synthetic-only", "editMessageText")
    fields = request.call_args.kwargs["params"]
    assert fields["chat_id"] == 100 and fields["message_id"] == 55
    assert json.loads(fields["rich_message"])["blocks"][1]["text"] == "版本：2.0"
    assert json.loads(fields["reply_markup"]) == keys.to_dict()
    bot.send_message.assert_not_called()
    bot.edit_message_text.assert_not_called()


@pytest.mark.parametrize(
    "description",
    [
        "Bad Request: message text is empty",
        "Unsupported InputRichMessageBlock type specified",
    ],
)
def test_edit_html_fallback_keeps_original_message_and_keyboard(
    monkeypatch, description
):
    monkeypatch.setattr(
        apihelper, "_make_request", Mock(side_effect=rejection(400, description))
    )
    bot = Mock(
        token="synthetic",
        edit_message_text=Mock(return_value=SimpleNamespace(message_id=55)),
    )
    keys = InlineKeyboardMarkup()
    keys.add(
        InlineKeyboardButton("下载", url="https://download.samsungapps.com/new.apk")
    )
    RichMessenger(bot).edit(100, 55, release_card(release()), keys)
    options = bot.edit_message_text.call_args.kwargs
    assert options["chat_id"] == 100 and options["message_id"] == 55
    assert options["reply_markup"] is keys
    assert options["link_preview_options"].is_disabled
    bot.send_message.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        requests.Timeout("private-url"),
        rejection(401, "Unauthorized"),
        rejection(400, "URL invalid private-url"),
    ],
)
def test_uncertain_edit_never_retries_or_sends_new_message(monkeypatch, failure):
    request = Mock(side_effect=failure)
    monkeypatch.setattr(apihelper, "_make_request", request)
    bot = Mock(token="synthetic")
    with pytest.raises(type(failure)):
        RichMessenger(bot).edit(100, 55, Card("Latest"))
    request.assert_called_once()
    bot.edit_message_text.assert_not_called()
    bot.send_message.assert_not_called()


@pytest.mark.parametrize("rich", [True, False])
def test_unchanged_edit_is_success_without_duplicate_message(monkeypatch, rich):
    error = rejection(
        400,
        "Bad Request: message is not modified: content and reply markup are the same",
    )
    monkeypatch.setattr(apihelper, "_make_request", Mock(side_effect=error))
    bot = Mock(token="synthetic", edit_message_text=Mock(side_effect=error))
    messenger = RichMessenger(bot)
    messenger.supported = rich
    assert messenger.edit(100, 55, Card("Same")) is True
    bot.send_message.assert_not_called()
