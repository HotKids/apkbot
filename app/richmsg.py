"""Synchronous TeleBot adapter; ambiguous sends never trigger a second send."""

import json
import threading
from telebot import apihelper, types


class UnconfirmedDelivery(RuntimeError):
    pass


class RichMessenger:
    def __init__(self, bot):
        self.bot = bot
        self.supported = True
        self._lock = threading.Lock()

    def send(self, chat_id, card, reply_markup=None):
        # Serialize capability detection so concurrent first sends cannot race.
        with self._lock:
            if self.supported:
                params = {
                    "chat_id": chat_id,
                    "rich_message": json.dumps(
                        {"blocks": card.blocks()}, ensure_ascii=False
                    ),
                }
                if reply_markup is not None:
                    params["reply_markup"] = json.dumps(
                        reply_markup.to_dict(), ensure_ascii=False
                    )
                try:
                    result = apihelper._make_request(
                        self.bot.token, "sendRichMessage", method="post", params=params
                    )
                    message = types.Message.de_json(result)
                    if message is None or message.message_id <= 0:
                        raise UnconfirmedDelivery("富消息送达状态未确认。")
                    return message
                except apihelper.ApiTelegramException as exc:
                    code = exc.error_code
                    description = exc.description.casefold()
                    unsupported = (
                        code == 404 and description == "not found: method not found"
                    )
                    malformed = code == 400 and any(
                        text in description
                        for text in (
                            "can't parse rich message json object",
                            "object expected as inputrichmessageblock",
                            "unsupported inputrichmessageblock type",
                            "can't parse inputrichmessageblock",
                            "can't parse inputrichblock:",
                        )
                    )
                    if unsupported:
                        self.supported = False
                    elif not malformed:
                        raise
                    # A specific Bot API rejection proves this card was not sent.
                    # Generic 404, auth errors, 429, timeouts and network loss escape.
            message = self.bot.send_message(
                chat_id,
                card.html(),
                parse_mode="HTML",
                reply_markup=reply_markup,
                link_preview_options=types.LinkPreviewOptions(is_disabled=True),
            )
            if message is None or message.message_id <= 0:
                raise UnconfirmedDelivery("消息送达状态未确认。")
            return message
