"""Configuration can be imported by credential-free tools."""

import os
from pathlib import Path
from urllib.parse import urlsplit

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or "0")
TZ = os.getenv("TZ", "Asia/Shanghai")
DB_PATH = Path(os.getenv("DB_PATH", "/data/app.db"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "60"))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "1440"))
LOCAL_BOT_API_URL = os.getenv("LOCAL_BOT_API_URL", "").rstrip("/")
PUBLIC_DOWNLOAD_BASE_URL = os.getenv("PUBLIC_DOWNLOAD_BASE_URL", "").strip().rstrip("/")
DOWNLOAD_BIND_HOST = os.getenv("DOWNLOAD_BIND_HOST", "127.0.0.1")
DOWNLOAD_BIND_PORT = int(os.getenv("DOWNLOAD_BIND_PORT", "8080"))
USER_AGENT = "APKDL/1.0 (Galaxy Store; Android 16; SM-S9480)"
MAX_APK_BYTES = 2_000_000_000
DOWNLOAD_DEADLINE = 1800


def validate_bot_config():
    missing = [
        name
        for name, ok in (("BOT_TOKEN", bool(BOT_TOKEN)), ("OWNER_ID", OWNER_ID > 0))
        if not ok
    ]
    if missing:
        raise RuntimeError("缺少必填环境变量：" + ", ".join(missing))
    if REQUEST_TIMEOUT <= 0 or CHECK_INTERVAL <= 0:
        raise RuntimeError("REQUEST_TIMEOUT 和 CHECK_INTERVAL 必须为正整数")
    try:
        url = urlsplit(PUBLIC_DOWNLOAD_BASE_URL)
        valid = (
            url.scheme == "https"
            and bool(url.hostname)
            and url.username is None
            and url.password is None
            and not url.path
            and not url.query
            and not url.fragment
            and (url.port is None or 0 < url.port <= 65535)
            and "\\" not in PUBLIC_DOWNLOAD_BASE_URL
            and all(33 <= ord(c) < 127 for c in PUBLIC_DOWNLOAD_BASE_URL)
        )
    except ValueError:
        valid = False
    if not valid:
        raise RuntimeError(
            "请配置 PUBLIC_DOWNLOAD_BASE_URL 为公网 HTTPS 地址，不带路径、参数或凭据。"
        )
    if not 0 < DOWNLOAD_BIND_PORT <= 65535:
        raise RuntimeError("DOWNLOAD_BIND_PORT 必须为有效端口")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
