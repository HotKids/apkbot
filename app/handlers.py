import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from telebot import TeleBot
from telebot.apihelper import ApiTelegramException
from telebot.types import Message

from config import (
    BOT_TOKEN,
    CRON_SCHEDULE,
    OWNER_ID,
    TARGET_CHAT_ID,
)
from database import (
    get_apk_url,
    get_state,
    is_already_pushed,
    save_download,
    set_setting,
    update_state,
)
from scraper import (
    Variant,
    cleanup_after_push,
    new_session,
    resolve_and_download,
    scrape_and_pick,
)

logger = logging.getLogger("apkmirror-bot")

bot = TeleBot(BOT_TOKEN, parse_mode="HTML")
check_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_owner(message: Message) -> bool:
    return message.from_user.id == OWNER_ID


def _require_private_owner(message: Message) -> bool:
    if message.chat.type != "private":
        return False
    if _is_owner(message):
        return True
    bot.reply_to(message, "权限不足。")
    return False


def _caption_text(variant: Variant, apk_path: Path, sha256: str) -> str:
    size_mb = apk_path.stat().st_size / 1024 / 1024
    sigs = ", ".join(variant.signatures) or "—"
    archs = ", ".join(variant.architectures) or "—"
    return (
        f"<b>{variant.app_name}</b>\n"
        f"版本：<code>{variant.release_version_name}</code>\n"
        f"VersionCode：<code>{variant.version_code or '—'}</code>\n"
        f"类型：<code>{variant.type}</code>\n"
        f"签名：<code>{sigs}</code>\n"
        f"架构：<code>{archs}</code>\n"
        f"最低系统：<code>{variant.min_android_text or '—'}</code>\n"
        f"DPI：<code>{variant.dpi or '—'}</code>\n"
        f"大小：<code>{size_mb:.2f} MB</code>\n"
        f"SHA256：<code>{sha256}</code>\n"
        f"来源：<a href=\"{variant.release_url}\">APKMirror</a>"
    )


def _push_to_channel(variant: Variant, apk_path: Path, sha256: str) -> None:
    """向 TARGET_CHAT_ID 发送文件，失败则抛出异常。"""
    with apk_path.open("rb") as f:
        bot.send_document(
            TARGET_CHAT_ID,
            f,
            visible_file_name=apk_path.name,
            caption=_caption_text(variant, apk_path, sha256),
            timeout=300,
        )


def run_check(triggered_by: Optional[int] = None) -> str:
    if not check_lock.acquire(blocking=False):
        return "已有检查任务在运行中。"
    try:
        now = _now_iso()
        apk_url = get_apk_url()
        if not apk_url:
            msg = "未配置监控地址，请先发送 /sub <url>"
            if triggered_by:
                bot.send_message(triggered_by, msg)
            return msg
        session = new_session()
        variant = scrape_and_pick(session, apk_url)
        apk_path, sha256 = resolve_and_download(session, variant)

        if is_already_pushed(variant.variant_url, variant.version_code, sha256):
            update_state(last_checked_at=now, last_status="skipped",
                         last_version_name=variant.release_version_name)
            msg = f"没有新版本（{variant.release_version_name}）"
            logger.info(msg)
            if triggered_by:
                try:
                    bot.send_message(triggered_by, msg)
                except Exception:
                    logger.exception("发送消息给 owner 失败")
            return msg

        _push_to_channel(variant, apk_path, sha256)
        save_download(
            apk_path.name, str(apk_path),
            apk_path.stat().st_size, sha256, variant.variant_url,
        )
        update_state(
            last_release_url=variant.release_url,
            last_variant_url=variant.variant_url,
            last_version_name=variant.release_version_name,
            last_version_code=variant.version_code,
            last_sha256=sha256,
            last_checked_at=now,
            last_pushed_at=now,
            last_status="ok",
            last_error=None,
        )
        cleanup_after_push(apk_path)

        summary = f"已推送 {variant.release_version_name}（{variant.type}）"
        logger.info(summary)
        if triggered_by:
            try:
                bot.send_message(triggered_by, summary)
            except Exception:
                logger.exception("发送摘要给 owner 失败")
        return summary

    except Exception as e:
        logger.exception("检查失败")
        err = str(e)
        try:
            update_state(last_checked_at=_now_iso(), last_status="failed", last_error=err)
        except Exception:
            pass
        msg = f"检查失败：{err}"
        if triggered_by:
            try:
                bot.send_message(triggered_by, msg)
            except Exception:
                logger.exception("发送失败通知给 owner 失败")
        return msg

    finally:
        check_lock.release()


# ---------------------------------------------------------------------------
# Bot 命令处理
# ---------------------------------------------------------------------------

@bot.message_handler(commands=["status"])
def handle_status(message: Message):
    if not _require_private_owner(message):
        return
    state = get_state()
    if not state:
        bot.reply_to(message, "尚无运行记录。")
        return
    bot.reply_to(
        message,
        "<b>运行状态</b>\n"
        f"目标：<code>{get_apk_url() or '未配置'}</code>\n"
        f"频道：<code>{TARGET_CHAT_ID}</code>\n"
        f"计划：<code>{CRON_SCHEDULE}</code>\n"
        f"上次检查：<code>{state['last_checked_at'] or '—'}</code>\n"
        f"上次推送：<code>{state['last_pushed_at'] or '—'}</code>\n"
        f"最新版本：<code>{state['last_version_name'] or '—'}</code>\n"
        f"状态：<code>{state['last_status'] or '—'}</code>\n"
        f"错误：<code>{state['last_error'] or '无'}</code>",
    )


@bot.message_handler(commands=["checknow"])
def handle_checknow(message: Message):
    if not _require_private_owner(message):
        return
    bot.reply_to(message, "开始检查，请稍等。")
    threading.Thread(
        target=run_check,
        kwargs={"triggered_by": message.chat.id},
        daemon=True,
    ).start()


@bot.message_handler(commands=["sub"])
def handle_sub(message: Message):
    if not _require_private_owner(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "用法：/sub https://www.apkmirror.com/apk/...")
        return
    url = parts[1].strip()
    if not url.startswith("https://www.apkmirror.com/apk/"):
        bot.reply_to(message, "URL 格式不对，需以 https://www.apkmirror.com/apk/ 开头。")
        return
    set_setting("apk_url", url)
    bot.reply_to(message, f"已设置监控地址：\n<code>{url}</code>")
