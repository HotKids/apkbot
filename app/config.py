import os
from pathlib import Path

# ---------- 常量 ----------
BASE_URL = "https://www.apkmirror.com"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

# ---------- 必填项 ----------
BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
_owner_raw = os.getenv("OWNER_ID", "").strip()
OWNER_ID: int = int(_owner_raw) if _owner_raw else 0

# ---------- 可选：基础 ----------
TZ: str = os.getenv("TZ", "Asia/Shanghai")
DB_PATH: Path = Path(os.getenv("DB_PATH", "/data/app.db"))
DOWNLOAD_DIR: Path = Path(os.getenv("DOWNLOAD_DIR", "/data/downloads"))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "60"))
CHECK_INTERVAL: int = int(os.getenv("CHECK_INTERVAL", "60"))  # 轮询间隔（分钟）

# ---------- 启动校验 ----------
_missing = []
if not BOT_TOKEN:
    _missing.append("BOT_TOKEN")
if not OWNER_ID:
    _missing.append("OWNER_ID")
if _missing:
    raise RuntimeError(f"缺少必填环境变量：{', '.join(_missing)}")

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
