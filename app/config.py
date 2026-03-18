import os
from pathlib import Path

BASE_URL = "https://www.apkmirror.com"
LIST_URL = "https://www.apkmirror.com/apk/google-inc/google-play-store/"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
TZ: str = os.getenv("TZ", "Asia/Shanghai")
DB_PATH: Path = Path(os.getenv("DB_PATH", "/data/app.db"))
DOWNLOAD_DIR: Path = Path(os.getenv("DOWNLOAD_DIR", "/data/downloads"))
CHECK_HOUR: int = int(os.getenv("CHECK_HOUR", "9"))
CHECK_MINUTE: int = int(os.getenv("CHECK_MINUTE", "0"))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
AUTO_INIT_OWNER: bool = os.getenv("AUTO_INIT_OWNER", "true").lower() == "true"
REQUIRE_SIGNATURE: str = os.getenv("REQUIRE_SIGNATURE", "3891").strip()
REQUIRE_VARIANT: str = os.getenv("REQUIRE_VARIANT", "bd32").strip().lower()
PREFER_APK: bool = os.getenv("PREFER_APK", "true").lower() == "true"
REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "60"))
DELETE_AFTER_PUSH: bool = os.getenv("DELETE_AFTER_PUSH", "false").lower() == "true"
MAX_KEEP_FILES: int = int(os.getenv("MAX_KEEP_FILES", "5"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
