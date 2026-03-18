import html
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from telebot import TeleBot
from telebot.types import Message

from config import BOT_TOKEN, CHECK_INTERVAL, OWNER_ID
from database import (
    add_subscription,
    add_to_whitelist,
    get_all_subscribed_urls,
    get_apk_version,
    get_subscriptions,
    get_subscribers,
    get_whitelist,
    is_in_whitelist,
    is_new_version,
    remove_all_subscriptions,
    remove_from_whitelist,
    remove_subscription,
    update_apk_version,
)
from scraper import (
    cleanup_after_push,
    new_session,
    resolve_and_download,
    scrape_and_pick,
)
from selector import Variant

logger = logging.getLogger("apkmirror-bot")

bot = TeleBot(BOT_TOKEN, parse_mode="HTML")
check_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_allowed(message: Message) -> bool:
    """OWNER 或白名单用户才允许使用 Bot。"""
    uid = message.from_user.id
    return uid == OWNER_ID or is_in_whitelist(uid)


def _is_owner(message: Message) -> bool:
    return message.from_user.id == OWNER_ID


def _require_allowed(message: Message) -> bool:
    """通用权限检查（私聊 + 白名单/OWNER），其他用户静默丢弃。"""
    if message.chat.type != "private":
        return False
    return _is_allowed(message)


def _require_owner(message: Message) -> bool:
    """仅 OWNER 可用的命令检查，非 OWNER 静默丢弃。"""
    if message.chat.type != "private":
        return False
    return _is_owner(message)


def _caption_text(variant: Variant, apk_path: Path, sha256: str) -> str:
    size_mb = apk_path.stat().st_size / 1024 / 1024
    sigs = ", ".join(variant.signatures) or "—"
    archs = ", ".join(variant.architectures) or "—"
    return (
        f"<b>{html.escape(variant.app_name)}</b>\n"
        f"版本：<code>{html.escape(variant.release_version_name)}</code>\n"
        f"VersionCode：<code>{variant.version_code or '—'}</code>\n"
        f"类型：<code>{variant.type}</code>\n"
        f"签名：<code>{html.escape(sigs)}</code>\n"
        f"架构：<code>{html.escape(archs)}</code>\n"
        f"最低系统：<code>{html.escape(variant.min_android_text or '—')}</code>\n"
        f"DPI：<code>{variant.dpi or '—'}</code>\n"
        f"大小：<code>{size_mb:.2f} MB</code>\n"
        f"SHA256：<code>{sha256}</code>\n"
        f"来源：<a href=\"{variant.release_url}\">APKMirror</a>"
    )


def _send_apk_to_user(chat_id: int, variant: Variant, apk_path: Path, sha256: str) -> None:
    """向单个用户发送 APK 文件。"""
    caption = _caption_text(variant, apk_path, sha256)
    with apk_path.open("rb") as f:
        bot.send_document(
            chat_id,
            f,
            visible_file_name=apk_path.name,
            caption=caption,
            timeout=300,
        )


def _send_current_version(chat_id: int, apk_url: str) -> None:
    """首次订阅时：抓取当前最新版并发给该用户，若该 URL 无版本记录则设置基线。"""
    try:
        session = new_session()
        variant = scrape_and_pick(session, apk_url)
        apk_path, sha256 = resolve_and_download(session, variant)
        try:
            _send_apk_to_user(chat_id, variant, apk_path, sha256)
        except Exception:
            apk_path.unlink(missing_ok=True)
            raise
        if not get_apk_version(apk_url):
            update_apk_version(
                apk_url,
                last_variant_url=variant.variant_url,
                last_version_name=variant.release_version_name,
                last_version_code=variant.version_code,
                last_sha256=sha256,
                last_checked_at=_now_iso(),
                last_pushed_at=_now_iso(),
            )
        cleanup_after_push(apk_path)
    except Exception as e:
        logger.exception("首次推送失败：chat_id=%s url=%s", chat_id, apk_url)
        try:
            bot.send_message(chat_id, f"获取失败：{html.escape(str(e))}")
        except Exception:
            pass


def _check_single_url(apk_url: str) -> str:
    """检查单个 URL，有新版则推送给所有订阅者，返回状态描述。"""
    try:
        session = new_session()
        variant = scrape_and_pick(session, apk_url)
        apk_path, sha256 = resolve_and_download(session, variant)

        if not is_new_version(apk_url, variant.variant_url, variant.version_code, sha256):
            update_apk_version(apk_url, last_checked_at=_now_iso())
            apk_path.unlink(missing_ok=True)
            return f"无更新：{html.escape(apk_url.rstrip('/').split('/')[-1])} ({html.escape(variant.release_version_name)})"

        subscribers = get_subscribers(apk_url)
        ok = 0
        for sub_chat_id in subscribers:
            try:
                _send_apk_to_user(sub_chat_id, variant, apk_path, sha256)
                ok += 1
            except Exception:
                logger.exception("推送给 chat_id=%s 失败", sub_chat_id)

        now = _now_iso()
        update_apk_version(
            apk_url,
            last_variant_url=variant.variant_url,
            last_version_name=variant.release_version_name,
            last_version_code=variant.version_code,
            last_sha256=sha256,
            last_checked_at=now,
            last_pushed_at=now,
        )
        cleanup_after_push(apk_path)
        app_label = html.escape(apk_url.rstrip("/").split("/")[-1])
        return (
            f"新版本 {html.escape(variant.release_version_name)}（{app_label}）"
            f"：已推送 {ok}/{len(subscribers)}"
        )
    except Exception as e:
        logger.exception("检查失败：%s", apk_url)
        return f"检查失败 {html.escape(apk_url)}：{html.escape(str(e))}"


def run_check_all(triggered_by: Optional[int] = None) -> None:
    """定时任务 / /check 手动触发：检查所有订阅 URL。"""
    if not check_lock.acquire(blocking=False):
        if triggered_by:
            try:
                bot.send_message(triggered_by, "已有检查任务在运行中。")
            except Exception:
                pass
        return
    try:
        urls = get_all_subscribed_urls()
        if not urls:
            if triggered_by:
                try:
                    bot.send_message(triggered_by, "当前无订阅。")
                except Exception:
                    pass
            return
        results = [_check_single_url(url) for url in urls]
        if triggered_by:
            try:
                bot.send_message(triggered_by, "\n".join(results))
            except Exception:
                logger.exception("发送检查结果给 owner 失败")
    finally:
        check_lock.release()


# ---------------------------------------------------------------------------
# Bot 命令处理
# ---------------------------------------------------------------------------

@bot.message_handler(commands=["sub"])
def handle_sub(message: Message):
    if not _require_allowed(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "用法：/sub https://www.apkmirror.com/apk/...")
        return
    url = parts[1].strip()
    if not url.startswith("https://www.apkmirror.com/apk/"):
        bot.reply_to(message, "URL 格式不对，需以 https://www.apkmirror.com/apk/ 开头。")
        return
    added = add_subscription(message.chat.id, url)
    if not added:
        bot.reply_to(message, f"已在订阅该应用：\n<code>{html.escape(url)}</code>")
        return
    bot.reply_to(message, f"订阅成功！正在获取当前最新版本，请稍等……\n<code>{html.escape(url)}</code>")
    threading.Thread(
        target=_send_current_version,
        args=(message.chat.id, url),
        daemon=True,
    ).start()


@bot.message_handler(commands=["unsub"])
def handle_unsub(message: Message):
    if not _require_allowed(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) == 1:
        subs = get_subscriptions(message.chat.id)
        if not subs:
            bot.reply_to(message, "当前无订阅。")
            return
        lines = "\n".join(f"• <code>{html.escape(s)}</code>" for s in subs)
        bot.reply_to(
            message,
            f"用法：\n"
            f"  /unsub &lt;url&gt; — 取消指定订阅\n"
            f"  /unsub all — 取消全部订阅\n\n"
            f"当前订阅（{len(subs)} 个）：\n{lines}",
        )
        return
    arg = parts[1].strip()
    if arg == "all":
        count = remove_all_subscriptions(message.chat.id)
        bot.reply_to(message, f"已取消全部 {count} 个订阅。")
    else:
        removed = remove_subscription(message.chat.id, arg)
        if removed:
            bot.reply_to(message, "已取消订阅。")
        else:
            bot.reply_to(message, "未找到该订阅，请检查 URL 是否正确。")


@bot.message_handler(commands=["sublist"])
def handle_sublist(message: Message):
    if not _require_allowed(message):
        return
    subs = get_subscriptions(message.chat.id)
    if not subs:
        bot.reply_to(message, "当前无订阅。使用 /sub &lt;url&gt; 添加。")
    else:
        lines = "\n".join(f"• <code>{html.escape(s)}</code>" for s in subs)
        bot.reply_to(message, f"当前订阅（{len(subs)} 个）：\n{lines}")


@bot.message_handler(commands=["check"])
def handle_check(message: Message):
    if not _require_owner(message):
        return
    bot.reply_to(message, "开始检查所有订阅，请稍等。")
    threading.Thread(
        target=run_check_all,
        kwargs={"triggered_by": message.chat.id},
        daemon=True,
    ).start()


@bot.message_handler(commands=["status"])
def handle_status(message: Message):
    if not _require_owner(message):
        return
    urls = get_all_subscribed_urls()
    total_subs = sum(len(get_subscribers(u)) for u in urls)
    lines = ["<b>Bot 状态</b>", f"轮询间隔：<code>{CHECK_INTERVAL}m</code>", f"订阅 URL 数：<code>{len(urls)}</code>", f"订阅人次：<code>{total_subs}</code>"]
    for url in urls:
        ver = get_apk_version(url)
        app_label = html.escape(url.rstrip("/").split("/")[-1])
        version_str = html.escape(ver["last_version_name"]) if ver and ver["last_version_name"] else "—"
        checked_str = ver["last_checked_at"] if ver and ver["last_checked_at"] else "—"
        lines.append(f"\n📦 <code>{app_label}</code>\n  版本：{version_str}\n  检查：{checked_str}")
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(commands=["adduser"])
def handle_adduser(message: Message):
    if not _require_owner(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().lstrip("-").isdigit():
        bot.reply_to(message, "用法：/adduser <user_id>")
        return
    uid = int(parts[1].strip())
    added = add_to_whitelist(uid)
    if added:
        bot.reply_to(message, f"已添加用户 <code>{uid}</code> 到白名单。")
    else:
        bot.reply_to(message, f"用户 <code>{uid}</code> 已在白名单中。")


@bot.message_handler(commands=["deluser"])
def handle_deluser(message: Message):
    if not _require_owner(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().lstrip("-").isdigit():
        bot.reply_to(message, "用法：/deluser <user_id>")
        return
    uid = int(parts[1].strip())
    removed = remove_from_whitelist(uid)
    if removed:
        bot.reply_to(message, f"已从白名单移除用户 <code>{uid}</code>。")
    else:
        bot.reply_to(message, f"用户 <code>{uid}</code> 不在白名单中。")


@bot.message_handler(commands=["listusers"])
def handle_listusers(message: Message):
    if not _require_owner(message):
        return
    users = get_whitelist()
    if not users:
        bot.reply_to(message, "白名单为空。使用 /adduser &lt;user_id&gt; 添加。")
    else:
        lines = "\n".join(f"• <code>{uid}</code>" for uid in users)
        bot.reply_to(message, f"白名单（{len(users)} 人）：\n{lines}")
