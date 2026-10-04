from types import SimpleNamespace as NS
from unittest.mock import Mock
import json

import pytest
import requests

import handlers
import main


@pytest.fixture
def startup(monkeypatch):
    bot = Mock()
    bot.get_webhook_info.return_value = NS(url="")
    bot.set_my_commands.return_value = True
    scheduler = Mock()
    monkeypatch.setattr(handlers, "bot", bot)
    monkeypatch.setattr(main, "BackgroundScheduler", scheduler)
    return bot, scheduler


def test_startup_registers_public_and_owner_private_commands_before_polling(startup):
    bot, scheduler = startup
    main.main()
    assert bot.set_my_commands.call_count == 2
    public, owner = bot.set_my_commands.call_args_list
    assert json.loads(public.kwargs["scope"].to_json()) == {"type": "all_private_chats"}
    assert json.loads(owner.kwargs["scope"].to_json()) == {
        "type": "chat",
        "chat_id": 100,
    }
    assert [(c.command, c.description) for c in public.args[0]] == [
        ("dl", "获取下载链接"),
        ("sub", "订阅应用更新"),
        ("unsub", "取消订阅"),
        ("list", "查看我的订阅"),
    ]
    assert [c.command for c in owner.args[0]] == [
        "dl",
        "sub",
        "unsub",
        "list",
        "check",
        "status",
        "add",
        "del",
        "user",
        "help",
    ]
    names = [call[0] for call in bot.mock_calls]
    assert (
        names.index("get_webhook_info")
        < names.index("set_my_commands")
        < names.index("infinity_polling")
    )
    scheduler.return_value.start.assert_called_once()
    bot.send_message.assert_not_called()


@pytest.mark.parametrize("failed_scope", [0, 1])
@pytest.mark.parametrize(
    "failure", [False, requests.Timeout("synthetic-private-token")]
)
def test_unconfirmed_registration_prevents_ready_state_without_retry(
    startup, failed_scope, failure, caplog
):
    bot, scheduler = startup
    bot.set_my_commands.side_effect = [True] * failed_scope + [failure]
    with pytest.raises(RuntimeError, match="命令菜单注册未确认") as caught:
        main.main()
    assert bot.set_my_commands.call_count == failed_scope + 1
    scheduler.assert_not_called()
    bot.infinity_polling.assert_not_called()
    bot.send_message.assert_not_called()
    assert "synthetic-private-token" not in str(caught.value) + caplog.text


def test_repeated_startups_register_the_same_commands_and_scopes(startup):
    bot, _ = startup
    main.main()
    first = [
        (
            json.loads(call.kwargs["scope"].to_json()),
            [json.loads(c.to_json()) for c in call.args[0]],
        )
        for call in bot.set_my_commands.call_args_list
    ]
    assert len(first) == 2
    bot.reset_mock()
    main.main()
    second = [
        (
            json.loads(call.kwargs["scope"].to_json()),
            [json.loads(c.to_json()) for c in call.args[0]],
        )
        for call in bot.set_my_commands.call_args_list
    ]
    assert second == first
