import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from bs4 import BeautifulSoup
from telebot import TeleBot
from telebot.apihelper import ApiTelegramException
from telebot.types import Message

BASE_URL = "https://www.apkmirror.com"
LIST_URL = "https://www.apkmirror.com/apk/google-inc/google-play-store/"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TZ = os.getenv("TZ", "Asia/Shanghai")
DB_PATH = Path(os.getenv("DB_PATH", "/data/app.db"))
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "/data/downloads"))
CHECK_HOUR = int(os.getenv("CHECK_HOUR", "9"))
CHECK_MINUTE = int(os.getenv("CHECK_MINUTE", "0"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
AUTO_INIT_OWNER = os.getenv("AUTO_INIT_OWNER", "true").lower() == "true"
REQUIRE_SIGNATURE = os.getenv("REQUIRE_SIGNATURE", "3891").strip()
REQUIRE_VARIANT = os.getenv("REQUIRE_VARIANT", "bd32").strip().lower()
PREFER_APK = os.getenv("PREFER_APK", "true").lower() == "true"
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "60"))
TG_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("apkmirror-bot")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required")

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

bot = TeleBot(BOT_TOKEN, parse_mode="HTML")
db_lock = threading.Lock()
check_lock = threading.Lock()


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": BASE_URL + "/",
        }
    )
    return s


@contextmanager
def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db_lock, db_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS channels (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                username TEXT,
                bound_by INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS releases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT NOT NULL,
                variant_url TEXT NOT NULL UNIQUE,
                file_name TEXT,
                sha256 TEXT,
                created_at INTEGER NOT NULL
            );
            """
        )


def get_setting(key: str) -> Optional[str]:
    with db_lock, db_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None


def set_setting(key: str, value: str) -> None:
    with db_lock, db_conn() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def owner_id() -> Optional[int]:
    value = get_setting("owner_id")
    return int(value) if value else None


def ensure_owner(user_id: int) -> bool:
    current = owner_id()
    if current:
        return current == user_id
    if AUTO_INIT_OWNER:
        set_setting("owner_id", str(user_id))
        logger.info("Owner initialized: %s", user_id)
        return True
    return False


def is_owner(user_id: int) -> bool:
    current = owner_id()
    return current is not None and current == user_id


def add_channel(chat_id: int, title: str, username: Optional[str], bound_by: int):
    with db_lock, db_conn() as conn:
        conn.execute(
            """
            INSERT INTO channels(chat_id, title, username, bound_by, created_at, active)
            VALUES(?, ?, ?, ?, ?, 1)
            ON CONFLICT(chat_id) DO UPDATE SET
                title=excluded.title,
                username=excluded.username,
                bound_by=excluded.bound_by,
                active=1
            """,
            (chat_id, title, username, bound_by, int(time.time())),
        )


def list_channels():
    with db_lock, db_conn() as conn:
        return conn.execute(
            "SELECT chat_id, title, username, bound_by, created_at, active FROM channels WHERE active = 1 ORDER BY created_at ASC"
        ).fetchall()


def deactivate_channel(chat_id: int):
    with db_lock, db_conn() as conn:
        conn.execute("UPDATE channels SET active = 0 WHERE chat_id = ?", (chat_id,))


def release_exists(variant_url: str) -> bool:
    with db_lock, db_conn() as conn:
        row = conn.execute("SELECT 1 FROM releases WHERE variant_url = ?", (variant_url,)).fetchone()
        return row is not None


def save_release(version: str, variant_url: str, file_name: str, sha256: str):
    with db_lock, db_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO releases(version, variant_url, file_name, sha256, created_at) VALUES(?, ?, ?, ?, ?)",
            (version, variant_url, file_name, sha256, int(time.time())),
        )


def session_get(session: requests.Session, url: str, **kwargs):
    resp = session.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
    resp.raise_for_status()
    return resp


def get_latest_release_page(session: requests.Session) -> str:
    r = session_get(session, LIST_URL)
    soup = BeautifulSoup(r.text, "lxml")
    links = soup.select('a[href*="/apk/google-inc/google-play-store/google-play-store-"]')
    seen = set()
    for a in links:
        href = a.get("href", "")
        if href in seen:
            continue
        seen.add(href)
        if href and "google-play-store-" in href and href.endswith("/"):
            return urljoin(BASE_URL, href)
    raise RuntimeError("Could not find latest release page")


def extract_version_from_url(url: str) -> str:
    m = re.search(r"google-play-store-([\d\-]+)-release", url)
    if m:
        return m.group(1).replace("-", ".")
    return "unknown"


def parse_variants_page(session: requests.Session, release_url: str):
    r = session_get(session, release_url)
    soup = BeautifulSoup(r.text, "lxml")

    candidates = []
    seen = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if not href or href in seen:
            continue
        if "/apk/google-inc/google-play-store/google-play-store-" not in href:
            continue
        if href.rstrip("/") == release_url.rstrip("/"):
            continue

        text_candidates = [a.get_text(" ", strip=True)]
        p = a.parent
        steps = 0
        while p is not None and steps < 4:
            text_candidates.append(p.get_text(" ", strip=True))
            p = p.parent
            steps += 1
        combined = " ".join(t for t in text_candidates if t)

        if REQUIRE_SIGNATURE not in combined:
            continue
        if REQUIRE_VARIANT not in combined.lower():
            continue

        is_apk = "APK" in combined
        is_bundle = "BUNDLE" in combined
        if PREFER_APK and not is_apk:
            continue

        seen.add(href)
        candidates.append(
            {
                "url": urljoin(BASE_URL, href),
                "text": combined,
                "is_apk": is_apk,
                "is_bundle": is_bundle,
            }
        )

    if not candidates:
        raise RuntimeError("No matching variant found for configured signature/variant")

    candidates.sort(key=lambda x: (not x["is_apk"], x["url"]))
    return candidates


def resolve_download_page(session: requests.Session, variant_url: str) -> str:
    r = session_get(session, variant_url)
    soup = BeautifulSoup(r.text, "lxml")

    for a in soup.select("a[href]"):
        text = a.get_text(" ", strip=True).lower()
        href = a.get("href", "")
        if "download apk" in text or "see available downloads" in text:
            return urljoin(BASE_URL, href)
    for a in soup.select('a[href*="/download/"]'):
        return urljoin(BASE_URL, a.get("href"))
    raise RuntimeError("Could not resolve download page")


def resolve_final_apk_url(session: requests.Session, download_page_url: str) -> str:
    r = session_get(session, download_page_url)
    soup = BeautifulSoup(r.text, "lxml")

    for a in soup.select("a[href]"):
        text = a.get_text(" ", strip=True).lower()
        href = a.get("href", "")
        if "download apk" in text or href.endswith(".apk"):
            return urljoin(BASE_URL, href)

    for a in soup.select('a[href*="/wp-content/"]'):
        return urljoin(BASE_URL, a.get("href"))

    raise RuntimeError("Could not resolve final apk URL")


def download_file(session: requests.Session, file_url: str) -> Path:
    with session.get(file_url, stream=True, timeout=300, allow_redirects=True) as r:
        r.raise_for_status()
        filename = None
        cd = r.headers.get("Content-Disposition", "")
        m = re.search(r'filename="?([^";]+)"?', cd)
        if m:
            filename = m.group(1)
        if not filename:
            filename = file_url.split("/")[-1].split("?")[0] or f"playstore_{int(time.time())}.apk"
        out_path = DOWNLOAD_DIR / filename
        with out_path.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 512):
                if chunk:
                    f.write(chunk)
    return out_path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def scrape_latest() -> dict:
    session = new_session()
    release_url = get_latest_release_page(session)
    version = extract_version_from_url(release_url)
    variants = parse_variants_page(session, release_url)
    picked = variants[0]
    download_page = resolve_download_page(session, picked["url"])
    final_url = resolve_final_apk_url(session, download_page)
    apk_path = download_file(session, final_url)
    file_hash = sha256_file(apk_path)
    return {
        "version": version,
        "release_url": release_url,
        "variant_url": picked["url"],
        "final_url": final_url,
        "apk_path": apk_path,
        "sha256": file_hash,
        "is_apk": picked["is_apk"],
    }


def caption_text(result: dict) -> str:
    size_mb = result["apk_path"].stat().st_size / 1024 / 1024
    return (
        "<b>Google Play Store 更新推送</b>\n"
        f"版本：<code>{result['version']}</code>\n"
        f"筛选：<code>{REQUIRE_SIGNATURE}</code><code>{REQUIRE_VARIANT}</code>\n"
        f"类型：<code>{'APK' if result['is_apk'] else 'BUNDLE'}</code>\n"
        f"大小：<code>{size_mb:.2f} MB</code>\n"
        f"SHA256：<code>{result['sha256']}</code>\n"
        f"来源：<a href=\"{result['release_url']}\">APKMirror</a>"
    )


def push_to_channels(result: dict) -> tuple[int, list[str]]:
    sent = 0
    failed = []
    for row in list_channels():
        chat_id = row["chat_id"]
        try:
            with result["apk_path"].open("rb") as f:
                bot.send_document(
                    chat_id,
                    f,
                    visible_file_name=result["apk_path"].name,
                    caption=caption_text(result),
                    timeout=300,
                )
            sent += 1
            time.sleep(1)
        except ApiTelegramException as e:
            failed.append(f"{chat_id}: {e}")
            logger.exception("Failed to send to channel %s", chat_id)
    return sent, failed


def run_check(triggered_by: Optional[int] = None) -> str:
    if not check_lock.acquire(blocking=False):
        return "已有检查任务在运行中。"
    try:
        result = scrape_latest()
        if release_exists(result["variant_url"]):
            return f"没有新版本。最近已处理：{result['version']}"

        sent, failed = push_to_channels(result)
        save_release(result["version"], result["variant_url"], result["apk_path"].name, result["sha256"])

        summary = f"发现新版本 {result['version']}，已推送到 {sent} 个频道。"
        if failed:
            summary += " 失败：" + " | ".join(failed)
        if triggered_by:
            try:
                bot.send_message(triggered_by, summary)
            except Exception:
                logger.exception("Failed to send summary to owner")
        return summary
    except Exception as e:
        logger.exception("Check failed")
        msg = f"检查失败：{e}"
        if triggered_by:
            try:
                bot.send_message(triggered_by, msg)
            except Exception:
                logger.exception("Failed to send failure notice to owner")
        return msg
    finally:
        check_lock.release()


def require_private_owner(message: Message) -> bool:
    if message.chat.type != "private":
        return False
    if ensure_owner(message.from_user.id) and owner_id() == message.from_user.id:
        return True
    if is_owner(message.from_user.id):
        return True
    bot.reply_to(message, "你不是当前 owner。")
    return False


@bot.message_handler(commands=["start"])
def handle_start(message: Message):
    if message.chat.type != "private":
        return
    if owner_id() is None and AUTO_INIT_OWNER:
        ensure_owner(message.from_user.id)
        bot.reply_to(
            message,
            "初始化成功，你已成为 owner。\n\n"
            "下一步：\n"
            "1. 把 bot 加到目标频道\n"
            "2. 给 bot 管理员权限（至少能发送消息/文件）\n"
            "3. 从频道转发任意一条消息给我，我会自动绑定频道\n\n"
            "常用命令：/help /channels /checknow",
        )
        return

    if is_owner(message.from_user.id):
        bot.reply_to(message, "Bot 在线。把目标频道里的任意一条消息转发给我即可绑定频道。")
    else:
        bot.reply_to(message, "Bot 在线。")


@bot.message_handler(commands=["help"])
def handle_help(message: Message):
    if not require_private_owner(message):
        return
    bot.reply_to(
        message,
        "<b>命令列表</b>\n"
        "/channels - 查看已绑定频道\n"
        "/unbind &lt;chat_id&gt; - 解绑频道\n"
        "/checknow - 立即检查一次\n"
        "/status - 查看当前配置\n\n"
        "<b>绑定频道</b>\n"
        "把目标频道中的任意一条消息转发给我即可。\n"
        "前提：bot 已加入频道并拥有发消息/发文件权限。",
    )


@bot.message_handler(commands=["channels"])
def handle_channels(message: Message):
    if not require_private_owner(message):
        return
    rows = list_channels()
    if not rows:
        bot.reply_to(message, "当前没有已绑定频道。")
        return
    lines = ["<b>已绑定频道</b>"]
    for row in rows:
        username = f"@{row['username']}" if row['username'] else "无用户名"
        lines.append(f"• <code>{row['chat_id']}</code> | {row['title']} | {username}")
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(commands=["status"])
def handle_status(message: Message):
    if not require_private_owner(message):
        return
    rows = list_channels()
    bot.reply_to(
        message,
        "<b>当前配置</b>\n"
        f"owner: <code>{owner_id()}</code>\n"
        f"频道数: <code>{len(rows)}</code>\n"
        f"signature: <code>{REQUIRE_SIGNATURE}</code>\n"
        f"variant: <code>{REQUIRE_VARIANT}</code>\n"
        f"优先 APK: <code>{str(PREFER_APK).lower()}</code>\n"
        f"计划时间: <code>{CHECK_HOUR:02d}:{CHECK_MINUTE:02d}</code>",
    )


@bot.message_handler(commands=["unbind"])
def handle_unbind(message: Message):
    if not require_private_owner(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "用法：/unbind -100xxxxxxxxxx")
        return
    try:
        chat_id = int(parts[1].strip())
    except ValueError:
        bot.reply_to(message, "chat_id 格式不对。")
        return
    deactivate_channel(chat_id)
    bot.reply_to(message, f"已解绑 <code>{chat_id}</code>")


@bot.message_handler(commands=["checknow"])
def handle_checknow(message: Message):
    if not require_private_owner(message):
        return
    bot.reply_to(message, "开始检查，请稍等。")
    threading.Thread(target=run_check, kwargs={"triggered_by": message.chat.id}, daemon=True).start()


@bot.message_handler(func=lambda m: m.chat.type == "private")
def handle_private_message(message: Message):
    if not is_owner(message.from_user.id):
        if owner_id() is None and AUTO_INIT_OWNER:
            ensure_owner(message.from_user.id)
            bot.reply_to(message, "你已成为 owner。发送 /help 查看命令。")
        return

    fwd = getattr(message, "forward_from_chat", None)
    if not fwd:
        return
    if getattr(fwd, "type", None) != "channel":
        return

    chat_id = int(fwd.id)
    title = getattr(fwd, "title", "") or "Untitled Channel"
    username = getattr(fwd, "username", None)

    try:
        member = bot.get_chat_member(chat_id, bot.get_me().id)
        status = getattr(member, "status", "")
        if status not in {"administrator", "creator"}:
            bot.reply_to(message, "我已经识别到频道，但我不是该频道管理员。请先把我设为管理员后再转发一次。")
            return
    except Exception:
        bot.reply_to(message, "识别到了频道，但无法确认管理员权限。请确认 bot 已在频道中且拥有发消息/发文件权限。")
        return

    add_channel(chat_id, title, username, message.from_user.id)
    bot.reply_to(
        message,
        f"已绑定频道：\n"
        f"标题：<b>{title}</b>\n"
        f"chat_id：<code>{chat_id}</code>\n"
        f"用户名：<code>{('@' + username) if username else '无'}</code>",
    )


scheduler = BackgroundScheduler(timezone=TZ)


def scheduled_job():
    logger.info("Running scheduled check")
    owner = owner_id()
    run_check(triggered_by=owner)


def main():
    init_db()
    scheduler.add_job(scheduled_job, "cron", hour=CHECK_HOUR, minute=CHECK_MINUTE, id="daily_check", replace_existing=True)
    scheduler.start()
    logger.info("Bot started. Scheduled daily check at %02d:%02d %s", CHECK_HOUR, CHECK_MINUTE, TZ)
    bot.infinity_polling(timeout=30, long_polling_timeout=30)


if __name__ == "__main__":
    main()
