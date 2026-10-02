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
        return self._apply(chat_id, card, reply_markup)

    def edit(self, chat_id, message_id, card, reply_markup=None):
        return self._apply(chat_id, card, reply_markup, message_id=message_id)

    def _apply(self, chat_id, card, reply_markup, message_id=None):
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
                method = "sendRichMessage"
                if message_id is not None:
                    # Bot API editMessageText accepts rich_message as well as text.
                    method = "editMessageText"
                    params["message_id"] = message_id
                try:
                    result = apihelper._make_request(
                        self.bot.token, method, method="post", params=params
                    )
                    message = types.Message.de_json(result)
                    if message is None or message.message_id <= 0:
                        raise UnconfirmedDelivery("富消息送达状态未确认。")
                    return message
                except apihelper.ApiTelegramException as exc:
                    code = exc.error_code
                    description = exc.description.casefold()
                    if message_id is not None and self._not_modified(exc):
                        return True
                    unsupported = (
                        code == 404 and description == "not found: method not found"
                    ) or (
                        message_id is not None
                        and code == 400
                        and description == "bad request: message text is empty"
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
            options = dict(
                parse_mode="HTML",
                reply_markup=reply_markup,
                link_preview_options=types.LinkPreviewOptions(is_disabled=True),
            )
            try:
                if message_id is None:
                    message = self.bot.send_message(chat_id, card.html(), **options)
                else:
                    message = self.bot.edit_message_text(
                        card.html(), chat_id=chat_id, message_id=message_id, **options
                    )
            except apihelper.ApiTelegramException as exc:
                if message_id is not None and self._not_modified(exc):
                    return True
                raise
            if message is None or message.message_id <= 0:
                raise UnconfirmedDelivery("消息送达状态未确认。")
            return message

    @staticmethod
    def _not_modified(exc):
        return exc.error_code == 400 and exc.description.casefold().startswith(
            "bad request: message is not modified"
        )
