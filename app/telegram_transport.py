"""Single-attempt Telegram requests; keep signed URL buttons out of logs."""

import logging

from telebot import apihelper


def telegram_request(method, url, *, params=None, files=None, **kwargs):
    if files:
        raise ValueError("File uploads are disabled; APKDL sends download links")
    # Telegram accepts POST for every Bot API method. Keep message/keyboard
    # fields out of query strings, including any signed Samsung URL.
    return apihelper._get_req_session().request(
        "post", url, data=params, allow_redirects=False, **kwargs
    )


def install_transport():
    # TeleBot DEBUG logs include both complete request parameters and responses
    # (including URL buttons echoed in callback messages).
    apihelper.logger.setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("urllib3.connectionpool").setLevel(logging.WARNING)
    # This supported hook also bypasses TeleBot's optional automatic retries.
    apihelper.CUSTOM_REQUEST_SENDER = telegram_request
