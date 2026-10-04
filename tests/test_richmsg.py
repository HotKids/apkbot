import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import requests
from telebot import apihelper
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from cards import (
    Card,
    Entry,
    app_title,
    bold,
    help_card,
    release_card,
    subscription_card,
)
from galaxy_store import AppRequest
from richmsg import RichMessenger, UnconfirmedDelivery, button_blocks
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
    blocks = json.loads(kwargs["params"]["rich_message"])["blocks"]
    assert blocks[0]["type"] == "heading"
    # The keyboard is drawn inside the card; sending reply_markup too would
    # show every button twice.
    assert blocks[-1] == {
        "type": "buttons",
        "buttons": [
            {"text": "Download", "callback_data": "gdl:durable", "style": "primary"}
        ],
    }
    assert "reply_markup" not in kwargs["params"]
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


@pytest.mark.parametrize("message_id", [None, 55])
def test_rich_transport_uses_explicit_blocks_without_automatic_links(
    monkeypatch, message_id
):
    request = Mock(
        return_value=dict(message_id=55, date=0, chat=dict(id=100, type="private"))
    )
    monkeypatch.setattr(apihelper, "_make_request", request)
    messenger = RichMessenger(Mock(token="synthetic-only"))
    card = Card("Title", ("https://example.com/store-notes",))
    if message_id is None:
        messenger.send(100, card)
    else:
        messenger.edit(100, message_id, card)
    params = request.call_args.kwargs["params"]
    rich = json.loads(params["rich_message"])
    assert rich["skip_entity_detection"] is True
    assert rich["blocks"] == card.blocks()
    if message_id is None:
        # sendRichMessage accepts explicit blocks, not LinkPreviewOptions.
        assert "link_preview_options" not in params
    else:
        assert json.loads(params["link_preview_options"]) == {"is_disabled": True}


@pytest.mark.parametrize("message_id", [None, 55])
@pytest.mark.parametrize("result", [None, {}, True])
def test_unconfirmed_rich_result_never_falls_back_or_resends(
    monkeypatch, message_id, result
):
    request = Mock(return_value=result)
    monkeypatch.setattr(apihelper, "_make_request", request)
    bot = Mock(token="synthetic-only")
    messenger = RichMessenger(bot)
    with pytest.raises(UnconfirmedDelivery):
        if message_id is None:
            messenger.send(100, Card("Title"))
        else:
            messenger.edit(100, message_id, Card("Title"))
    request.assert_called_once()
    bot.send_message.assert_not_called()
    bot.edit_message_text.assert_not_called()


@pytest.mark.parametrize("message_id", [None, 55])
@pytest.mark.parametrize("result", [None, SimpleNamespace(message_id=0), True])
def test_unconfirmed_html_result_never_resends(message_id, result):
    bot = Mock(
        token="synthetic-only",
        send_message=Mock(return_value=result),
        edit_message_text=Mock(return_value=result),
    )
    messenger = RichMessenger(bot)
    messenger.supported = False
    with pytest.raises(UnconfirmedDelivery):
        if message_id is None:
            messenger.send(100, Card("Title"))
        else:
            messenger.edit(100, message_id, Card("Title"))
    method = bot.send_message if message_id is None else bot.edit_message_text
    method.assert_called_once()
    assert method.call_args.kwargs["link_preview_options"].is_disabled is True


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
    assert "🇺🇸" in card.html() and "🇺🇸 US" not in card.html()
    assert "<b>版本：01.02.3 · 🇺🇸</b>" in card.html()
    assert "版本代码：<code>123</code>" in card.html()
    assert "包名：<code>com.example.app</code>" in card.html()
    facts = card.blocks()[2]
    assert facts["type"] == "table" and facts["is_bordered"] is True
    assert facts["is_striped"] is True
    assert "is_compact" not in facts
    assert [row[0]["text"] for row in facts["cells"]] == [
        "文件大小",
        "更新时间",
        "版本代码",
        "包名",
    ]
    assert facts["cells"][3][1]["text"] == {"type": "code", "text": "com.example.app"}
    assert "product ID" not in card.html()
    assert card.blocks()[0]["text"] == app_title("com.example.app", "<name>&")
    assert card.blocks()[1]["type"] == "heading"
    assert card.blocks()[1]["text"] == "版本：01.02.3 · 🇺🇸"
    notes = [block for block in card.blocks() if block["type"] == "blockquote"]
    assert notes == [
        {"type": "blockquote", "blocks": [{"type": "paragraph", "text": "<script>&"}]}
    ]
    assert not any(block["type"] == "details" for block in card.blocks())
    assert "<blockquote>&lt;script&gt;&amp;</blockquote>" in card.html()


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
    blocks = json.loads(fields["rich_message"])["blocks"]
    assert blocks[1]["text"] == "版本：2.0 · 🇨🇳"
    assert blocks[-1]["buttons"] == [
        {
            "text": "下载",
            "url": "https://download.samsungapps.com/new.apk",
            "style": "success",
        }
    ]
    assert "reply_markup" not in fields
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


INLINE = {"bold", "code", "url"}
BLOCKS = {"heading", "paragraph", "table", "footer", "buttons", "blockquote"}
ACTIONS = {"url", "callback_data"}


def valid_text(text):
    if isinstance(text, str):
        return True
    if isinstance(text, list):
        # Whitespace-only pieces (a lone "\n" between nodes) are avoided.
        return bool(text) and all(
            valid_text(part) and not (isinstance(part, str) and not part.strip())
            for part in text
        )
    return (
        isinstance(text, dict)
        and text.get("type") in INLINE
        and set(text)
        == ({"type", "text", "url"} if text["type"] == "url" else {"type", "text"})
        and (text["type"] != "url" or text["url"].startswith("https://"))
        and valid_text(text["text"])
    )


def assert_valid_blocks(blocks):
    # Mirrors the Bot API shapes used here, so a typo cannot silently push
    # every card into the HTML fallback.
    assert blocks
    for block in blocks:
        kind = block["type"]
        assert kind in BLOCKS, block
        if kind in {"heading", "paragraph", "footer"}:
            assert valid_text(block["text"]), block
        if kind == "heading":
            assert block["size"] in {4, 5}
        if kind == "table":
            assert block["cells"] and all(len(row) == 2 for row in block["cells"])
            for row in block["cells"]:
                for cell in row:
                    assert valid_text(cell["text"]) and cell["valign"] == "top"
        if kind == "blockquote":
            assert_valid_blocks(block["blocks"])
            assert isinstance(block.get("credit", ""), str)
        if kind == "buttons":
            assert 1 <= len(block["buttons"]) <= 8
            for button in block["buttons"]:
                assert button["text"] and len(ACTIONS & set(button)) == 1, button
                assert button.get("style") in {None, "success", "primary"}


def download_keys():
    keys = InlineKeyboardMarkup()
    keys.row(
        InlineKeyboardButton("刷新", callback_data="gdl:key"),
        InlineKeyboardButton("下载", url="https://download.samsungapps.com/a.apk"),
    )
    return keys


@pytest.mark.parametrize(
    "card",
    [
        release_card(release(), "notes"),
        release_card(release(size=None, updated_date="2026-08-25"), update=True),
        subscription_card(AppRequest("com.example.app"), True, name="Example"),
        help_card(),
        Card(
            "我的订阅（1 项）",
            entries=(
                Entry(
                    app_title("com.example.app", "名称"),
                    ("版本：", bold("1.0")),
                    "com.example.app",
                ),
            ),
        ),
    ],
)
def test_every_card_and_button_row_matches_rich_block_shapes(card):
    assert_valid_blocks(card.blocks() + button_blocks(download_keys()))
    assert card.html()


def test_buttons_are_colored_by_action():
    (row,) = button_blocks(download_keys())
    assert [b.get("style") for b in row["buttons"]] == ["primary", "success"]
    assert button_blocks(None) == [] and button_blocks(InlineKeyboardMarkup()) == []


def test_help_commands_are_tables_and_explanations_are_footers():
    blocks = help_card().blocks()
    sections = [b for b in blocks if b["type"] == "heading" and b["size"] == 5]
    assert [b["text"] for b in sections] == ["下载与订阅", "地区", "管理员"]
    first, admin = [b for b in blocks if b["type"] == "table"]
    commands = [row[0]["text"]["text"] for row in first["cells"]]
    assert "/unsub all" in commands
    assert admin["cells"][0][0]["text"] == {"type": "code", "text": "/check"}
    assert len([b for b in blocks if b["type"] == "footer"]) == 1
    html = help_card().html()
    assert "<code>/check</code> — 立即检查所有订阅" in html
    assert "<b>管理员</b>" in html and "expandable" not in html


def test_rejected_button_blocks_fall_back_to_card_with_inline_keyboard(monkeypatch):
    sent = []

    def request(token, api_method, method=None, params=None):
        sent.append(params)
        if '"type": "buttons"' in params["rich_message"]:
            raise rejection(400, 'Bad Request: type "buttons" is unsupported')
        return dict(message_id=7, date=0, chat=dict(id=1, type="private"))

    monkeypatch.setattr(apihelper, "_make_request", request)
    bot = Mock(token="synthetic")
    messenger = RichMessenger(bot)
    keys = download_keys()
    assert messenger.send(1, Card("A"), keys).message_id == 7
    assert messenger.edit(1, 7, Card("B"), keys).message_id == 7
    # One refused attempt, then the card is kept with a normal inline keyboard,
    # and the choice is remembered for the rest of the run.
    assert len(sent) == 3 and messenger.supported and not messenger.buttons
    assert all(json.loads(p["reply_markup"]) == keys.to_dict() for p in sent[1:])
    bot.send_message.assert_not_called()
    bot.edit_message_text.assert_not_called()


def test_rejected_card_logs_the_reason_without_urls(monkeypatch, caplog):
    error = rejection(
        400,
        "Bad Request: can't parse InputRichBlock: https://cdn.example/a?sig=private",
    )
    monkeypatch.setattr(apihelper, "_make_request", Mock(side_effect=error))
    bot = Mock(
        token="synthetic", send_message=Mock(return_value=SimpleNamespace(message_id=1))
    )
    RichMessenger(bot).send(1, Card("A"))
    bot.send_message.assert_called_once()
    assert "Rich card rejected" in caplog.text and "can't parse" in caplog.text
    assert "private" not in caplog.text and "<url>" in caplog.text
