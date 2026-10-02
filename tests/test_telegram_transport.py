import json
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from telebot import apihelper, TeleBot, types

from telegram_transport import telegram_request, install_transport

# Preserve the pinned entry point before the offline fixture replaces it.
_make_request = apihelper._make_request
SIGNED_URL = "https://download.samsungapps.com/file.apk?token=private-link&other=a%2Fb"


def test_real_telebot_sends_exact_link_in_body_without_debug_leak(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(apihelper.logger, "level", logging.DEBUG)
    monkeypatch.setattr(apihelper, "CUSTOM_REQUEST_SENDER", None)
    install_transport()
    keys = types.InlineKeyboardMarkup()
    keys.add(types.InlineKeyboardButton("Download", url=SIGNED_URL))
    captured = []

    def request(method, url, **kwargs):
        captured.append((method, url, kwargs))
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(
            {
                "ok": True,
                "result": {
                    "message_id": 3,
                    "date": 0,
                    "chat": {"id": 100, "type": "private"},
                    "reply_markup": keys.to_dict(),
                },
            }
        ).encode()
        return response

    monkeypatch.setattr(apihelper, "_make_request", _make_request)
    monkeypatch.setattr(
        apihelper, "_get_req_session", lambda: SimpleNamespace(request=request)
    )
    sent = TeleBot("123456:offline-only").send_message(
        100, "Download link", reply_markup=keys
    )
    method, url, options = captured[0]
    assert sent.message_id == 3 and method == "post"
    assert (
        options["allow_redirects"] is False
        and "params" not in options
        and "files" not in options
    )
    assert (
        json.loads(options["data"]["reply_markup"])["inline_keyboard"][0][0]["url"]
        == SIGNED_URL
    )
    prepared = requests.Request("POST", url, data=options["data"]).prepare()
    assert "private-link" not in prepared.url and "private-link" in prepared.body
    assert "private-link" not in caplog.text


def test_timeout_is_not_retried_even_if_telebot_retry_option_is_set(monkeypatch):
    request = Mock(side_effect=requests.Timeout("uncertain"))
    monkeypatch.setattr(apihelper, "_make_request", _make_request)
    monkeypatch.setattr(apihelper, "CUSTOM_REQUEST_SENDER", telegram_request)
    monkeypatch.setattr(apihelper, "RETRY_ON_ERROR", True)
    monkeypatch.setattr(
        apihelper, "_get_req_session", lambda: SimpleNamespace(request=request)
    )
    with pytest.raises(requests.Timeout):
        TeleBot("123456:offline-only").send_message(100, "link card")
    request.assert_called_once()


def test_polling_keeps_parameters_proxy_and_timeouts_in_post_body(monkeypatch):
    http = Mock()
    monkeypatch.setattr(apihelper, "_get_req_session", lambda: http)
    telegram_request(
        "get",
        "http://telegram-bot-api:8081/botfixture/getUpdates",
        params={"offset": 42},
        timeout=(15, 35),
        proxies=None,
    )
    assert http.request.call_args.args[0] == "post"
    assert http.request.call_args.kwargs == dict(
        data={"offset": 42}, timeout=(15, 35), proxies=None, allow_redirects=False
    )


def test_file_uploads_are_rejected_before_request(monkeypatch):
    http = Mock()
    monkeypatch.setattr(apihelper, "_get_req_session", lambda: http)
    with pytest.raises(ValueError, match="uploads are disabled"):
        telegram_request(
            "post",
            "https://api.telegram.org/botfixture/sendDocument",
            files={"document": Mock()},
        )
    http.request.assert_not_called()
