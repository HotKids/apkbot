"""Configuration can be imported by credential-free tools."""

import os
from pathlib import Path

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0") or "0")
TZ = os.getenv("TZ", "Asia/Shanghai")
DB_PATH = Path(os.getenv("DB_PATH", "/data/app.db"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "60"))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "1440"))
USER_AGENT = "APKDL/1.0 (Galaxy Store; Android 16; SM-S9480)"


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
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
