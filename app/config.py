import os
from pathlib import Path
from typing import Optional


def _parse_list(env_key: str, default: str = "") -> list[str]:
    raw = os.getenv(env_key, default).strip()
    return [s.strip() for s in raw.split(",") if s.strip()] if raw else []


def _parse_optional_int(env_key: str) -> Optional[int]:
    raw = os.getenv(env_key, "").strip()
    return int(raw) if raw else None


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
_chat_raw = os.getenv("TARGET_CHAT_ID", "").strip()
TARGET_CHAT_ID: int = int(_chat_raw) if _chat_raw else 0
CRON_SCHEDULE: str = os.getenv("CRON_SCHEDULE", "").strip()

# ---------- 可选：基础 ----------
TZ: str = os.getenv("TZ", "Asia/Shanghai")
DB_PATH: Path = Path(os.getenv("DB_PATH", "/data/app.db"))
DOWNLOAD_DIR: Path = Path(os.getenv("DOWNLOAD_DIR", "/data/downloads"))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "60"))

# ---------- 可选：APK 类型 ----------
PREFER_APK: bool = os.getenv("PREFER_APK", "true").lower() == "true"
ALLOW_BUNDLE: bool = os.getenv("ALLOW_BUNDLE", "false").lower() == "true"

# ---------- 可选：Variant 过滤 ----------
REQUIRED_SIGNATURES: list[str] = _parse_list("REQUIRED_SIGNATURES")
REQUIRED_ARCHITECTURES: list[str] = _parse_list("REQUIRED_ARCHITECTURES")
REQUIRED_DPI: str = os.getenv("REQUIRED_DPI", "").strip()
REQUIRED_DEVICE_TYPE: str = os.getenv("REQUIRED_DEVICE_TYPE", "").strip()
MIN_ANDROID_FLOOR: Optional[int] = _parse_optional_int("MIN_ANDROID_FLOOR")
MIN_ANDROID_CEILING: Optional[int] = _parse_optional_int("MIN_ANDROID_CEILING")
MATCH_KEYWORDS: list[str] = _parse_list("MATCH_KEYWORDS")
EXCLUDE_KEYWORDS: list[str] = _parse_list("EXCLUDE_KEYWORDS")

# ---------- 可选：文件清理 ----------
DELETE_AFTER_PUSH: bool = os.getenv("DELETE_AFTER_PUSH", "false").lower() == "true"
MAX_KEEP_FILES: int = int(os.getenv("MAX_KEEP_FILES", "5"))

# ---------- 启动校验 ----------
_missing = []
if not BOT_TOKEN:
    _missing.append("BOT_TOKEN")
if not OWNER_ID:
    _missing.append("OWNER_ID")
if not TARGET_CHAT_ID:
    _missing.append("TARGET_CHAT_ID")
if not CRON_SCHEDULE:
    _missing.append("CRON_SCHEDULE")
if _missing:
    raise RuntimeError(f"缺少必填环境变量：{', '.join(_missing)}")

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
