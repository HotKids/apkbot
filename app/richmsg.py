"""Synchronous TeleBot adapter; ambiguous sends never trigger a second send."""

import json
import logging
import re
import threading
from telebot import apihelper, types

logger = logging.getLogger("apkdl-bot")


def rejection_text(exc):
    # Telegram's reason helps fix a card; never let a signed URL reach logs.
    return re.sub(r"https?://\S+", "<url>", exc.description)[:200]


class UnconfirmedDelivery(RuntimeError):
    pass


def button_blocks(reply_markup):
    """Inline keyboard → one `buttons` block per row, drawn inside the card.

    External downloads are green and callbacks (refresh / get link) blue.
    The inline keyboard form has no styles.
    """
    rows = reply_markup.to_dict()["inline_keyboard"] if reply_markup else []
    blocks = []
    for row in rows:
        buttons = []
        for button in row:
            button = dict(button)
            if "url" in button:
                button["style"] = "success"
            elif "callback_data" in button:
                button["style"] = "primary"
            buttons.append(button)
        if buttons:
            blocks.append({"type": "buttons", "buttons": buttons})
    return blocks


class RichMessenger:
    def __init__(self, bot):
        self.bot = bot
        self.supported = True
        self.buttons = True
        self._lock = threading.Lock()

    def send(self, chat_id, card, reply_markup=None):
        return self._apply(chat_id, card, reply_markup)

    def edit(self, chat_id, message_id, card, reply_markup=None):
        return self._apply(chat_id, card, reply_markup, message_id=message_id)

    def _apply(self, chat_id, card, reply_markup, message_id=None):
        # Serialize capability detection so concurrent first sends cannot race.
        with self._lock:
            if self.supported:
                embed = self.buttons and bool(button_blocks(reply_markup))
                for inside in (True, False) if embed else (False,):
                    try:
                        return self._rich(
                            chat_id, card, reply_markup, message_id, inside
                        )
                    except apihelper.ApiTelegramException as exc:
                        if message_id is not None and self._not_modified(exc):
                            return True
                        if inside and self._buttons_rejected(exc):
                            # The card was refused, not sent: keep the card and
                            # move the buttons back under it for this run.
                            self.buttons = False
                            logger.warning(
                                "Card buttons rejected, using inline keyboard: %s",
                                rejection_text(exc),
                            )
                            continue
                        if self._unsupported(exc, message_id):
                            self.supported = False
                        elif not self._malformed(exc):
                            raise
                        # Otherwise a fallback is silent and a broken card
                        # looks like a plain message with no clue why.
                        logger.warning(
                            "Rich card rejected, sent as HTML: %s", rejection_text(exc)
                        )
                        # A specific Bot API rejection proves this card was not sent.
                        # Generic 404, auth errors, 429, timeouts and network loss escape.
                        break
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

    def _rich(self, chat_id, card, reply_markup, message_id, inside):
        blocks = card.blocks()
        params = {"chat_id": chat_id}
        if inside:
            # Embedded buttons replace reply_markup; sending both shows them twice.
            blocks += button_blocks(reply_markup)
        elif reply_markup is not None:
            params["reply_markup"] = json.dumps(
                reply_markup.to_dict(), ensure_ascii=False
            )
        params["rich_message"] = json.dumps({"blocks": blocks}, ensure_ascii=False)
        method = "sendRichMessage"
        if message_id is not None:
            # Bot API editMessageText accepts rich_message as well as text.
            method = "editMessageText"
            params["message_id"] = message_id
        result = apihelper._make_request(
            self.bot.token, method, method="post", params=params
        )
        message = types.Message.de_json(result)
        if message is None or message.message_id <= 0:
            raise UnconfirmedDelivery("富消息送达状态未确认。")
        return message

    @staticmethod
    def _buttons_rejected(exc):
        description = exc.description.casefold()
        return exc.error_code == 400 and (
            '"buttons"' in description
            or ("buttons" in description and "unsupported" in description)
        )

    @staticmethod
    def _unsupported(exc, message_id):
        description = exc.description.casefold()
        return (
            exc.error_code == 404 and description == "not found: method not found"
        ) or (
            message_id is not None
            and exc.error_code == 400
            and description == "bad request: message text is empty"
        )

    @staticmethod
    def _malformed(exc):
        description = exc.description.casefold()
        return exc.error_code == 400 and any(
            text in description
            for text in (
                "can't parse rich message json object",
                "object expected as inputrichmessageblock",
                "unsupported inputrichmessageblock type",
                "can't parse inputrichmessageblock",
                "can't parse inputrichblock:",
            )
        )

    @staticmethod
    def _not_modified(exc):
        return exc.error_code == 400 and exc.description.casefold().startswith(
            "bad request: message is not modified"
        )
