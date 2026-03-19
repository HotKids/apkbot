import html
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import telebot
from telebot import TeleBot
from telebot.types import Message

from config import BOT_TOKEN, CHECK_INTERVAL, LOCAL_BOT_API_URL, OWNER_ID, TZ
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
    now_iso,
    remove_all_subscriptions,
    remove_from_whitelist,
    remove_subscription,
    update_apk_version,
)
from scraper import (
    cleanup_after_push,
    new_session,
    resolve_and_download,
    resolve_package_to_apkmirror_url,
    scrape_and_pick,
)
from selector import Variant

logger = logging.getLogger("apkmirror-bot")

if LOCAL_BOT_API_URL:
    telebot.apihelper.API_URL = LOCAL_BOT_API_URL + "/bot{0}/{1}"

bot = TeleBot(BOT_TOKEN, parse_mode="HTML")
check_lock = threading.Lock()


def _safe_send(chat_id: int, text: str, **kwargs) -> None:
    try:
        bot.send_message(chat_id, text, **kwargs)
    except Exception:
        logger.warning("发送消息失败：chat_id=%s", chat_id)


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
    arch_list = variant.architectures
    archs = "universal" if "universal" in arch_list else (", ".join(arch_list) or "—")
    today = datetime.now(ZoneInfo(TZ)).strftime("%Y-%m-%d")
    return (
        f"版本：<code>{html.escape(variant.release_version_name)}</code>\n"
        f"日期：<code>{today}</code>\n"
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
            visible_file_name=re.sub(r'\s+', "_", re.sub(r'[\\/*?:"<>|]', "_",
                variant.app_name)) + (".apkm" if variant.is_bundle else ".apk"),
            caption=caption,
            timeout=300,
        )


def _send_current_version(chat_id: int, apk_url: str) -> None:
    """首次订阅时：抓取当前最新版并发给该用户，若该 URL 无版本记录则设置基线。"""
    apk_path: Optional[Path] = None
    try:
        session = new_session()
        variant = scrape_and_pick(session, apk_url)
        apk_path, sha256 = resolve_and_download(session, variant)
        _send_apk_to_user(chat_id, variant, apk_path, sha256)
        if not get_apk_version(apk_url):
            update_apk_version(
                apk_url,
                last_variant_url=variant.variant_url,
                last_version_name=variant.release_version_name,
                last_version_code=variant.version_code,
                last_sha256=sha256,
                last_type=variant.type,
                last_checked_at=now_iso(),
                last_pushed_at=now_iso(),
            )
    except Exception as e:
        logger.exception("首次推送失败：chat_id=%s url=%s", chat_id, apk_url)
        _safe_send(chat_id, f"❌ 获取失败：{html.escape(str(e))}")
    finally:
        if apk_path is not None:
            cleanup_after_push(apk_path)


def _check_single_url(apk_url: str) -> str:
    """检查单个 URL，有新版则推送给所有订阅者，返回状态描述。"""
    apk_path: Optional[Path] = None
    try:
        session = new_session()
        variant = scrape_and_pick(session, apk_url)
        apk_path, sha256 = resolve_and_download(session, variant)

        if not is_new_version(apk_url, variant.variant_url, variant.version_code, sha256, variant.type):
            update_apk_version(apk_url, last_checked_at=now_iso())
            return f"无更新：{html.escape(apk_url.rstrip('/').split('/')[-1])}（{html.escape(variant.release_version_name)}）"

        subscribers = get_subscribers(apk_url)
        ok = 0
        for sub_chat_id in subscribers:
            try:
                _send_apk_to_user(sub_chat_id, variant, apk_path, sha256)
                ok += 1
            except Exception:
                logger.exception("推送给 chat_id=%s 失败", sub_chat_id)

        now = now_iso()
        update_apk_version(
            apk_url,
            last_variant_url=variant.variant_url,
            last_version_name=variant.release_version_name,
            last_version_code=variant.version_code,
            last_sha256=sha256,
            last_type=variant.type,
            last_checked_at=now,
            last_pushed_at=now,
        )
        app_label = html.escape(apk_url.rstrip("/").split("/")[-1])
        return (
            f"✅ {app_label} 有新版本 {html.escape(variant.release_version_name)}"
            f"，已推送 {ok}/{len(subscribers)} 人"
        )
    except Exception as e:
        logger.exception("检查失败：%s", apk_url)
        app_label = html.escape(apk_url.rstrip("/").split("/")[-1])
        return f"❌ 检查失败 {app_label}：{html.escape(str(e))}"
    finally:
        if apk_path is not None:
            cleanup_after_push(apk_path)


def run_check_all(triggered_by: Optional[int] = None) -> None:
    """定时任务 / /check 手动触发：检查所有订阅 URL。"""
    if not check_lock.acquire(blocking=False):
        if triggered_by:
            _safe_send(triggered_by, "⚠️ 已有检查任务在运行中。")
        return
    try:
        urls = get_all_subscribed_urls()
        if not urls:
            if triggered_by:
                _safe_send(triggered_by, "当前无订阅。")
            return
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(_check_single_url, url): url for url in urls}
            results = [f.result() for f in as_completed(futures)]
        if triggered_by:
            _safe_send(triggered_by, "\n".join(results))
    finally:
        check_lock.release()


# ---------------------------------------------------------------------------
# 输入解析公共函数
# ---------------------------------------------------------------------------

def _resolve_to_apkmirror_url(message: Message, input_str: str) -> Optional[str]:
    """将用户输入解析为标准化的 APKMirror URL。解析失败时主动回复错误消息并返回 None。"""
    if re.match(r"^https?://(www\.)?apkmirror\.com/apk/", input_str):
        return input_str.split("?")[0].rstrip("/") + "/"

    package_name = None
    if "play.google.com" in input_str:
        m = re.search(r"[?&]id=([a-zA-Z0-9_.]+)", input_str)
        if m:
            package_name = m.group(1)
    elif re.match(r"^[a-zA-Z0-9_.]+$", input_str):
        package_name = input_str

    if package_name:
        status_msg = bot.reply_to(message, f"🔄 正在通过包名 <code>{html.escape(package_name)}</code> 搜索 APKMirror……")
        try:
            session = new_session()
            mapped_url = resolve_package_to_apkmirror_url(session, package_name)
            if not mapped_url:
                bot.edit_message_text(
                    f"❌ 未能在 APKMirror 找到包名 <code>{html.escape(package_name)}</code> 对应的应用。",
                    message.chat.id, status_msg.message_id, parse_mode="HTML",
                )
                return None
            url = mapped_url.split("?")[0].rstrip("/") + "/"
            bot.edit_message_text(
                f"✅ 解析成功：\n<code>{html.escape(url)}</code>",
                message.chat.id, status_msg.message_id, parse_mode="HTML",
            )
            return url
        except Exception as e:
            logger.exception("解析包名失败")
            bot.edit_message_text(
                f"❌ 解析包名时发生错误：{html.escape(str(e))}",
                message.chat.id, status_msg.message_id, parse_mode="HTML",
            )
            return None

    bot.reply_to(message, "❌ 无法识别的输入格式。请提供 APKMirror 链接、Google Play 链接或应用包名。")
    return None


# ---------------------------------------------------------------------------
# 一次性下载（不写数据库）
# ---------------------------------------------------------------------------

def _download_once(chat_id: int, apk_url: str) -> None:
    """一次性下载并发送，不写数据库。"""
    apk_path: Optional[Path] = None
    try:
        session = new_session()
        variant = scrape_and_pick(session, apk_url)
        apk_path, sha256 = resolve_and_download(session, variant)
        _send_apk_to_user(chat_id, variant, apk_path, sha256)
    except Exception as e:
        logger.exception("一次性下载失败：chat_id=%s url=%s", chat_id, apk_url)
        _safe_send(chat_id, f"❌ 下载失败：{html.escape(str(e))}")
    finally:
        if apk_path is not None:
            cleanup_after_push(apk_path)


# ---------------------------------------------------------------------------
# Bot 命令处理
# ---------------------------------------------------------------------------

@bot.message_handler(commands=["sub"])
def handle_sub(message: Message):
    if not _require_allowed(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(
            message,
            "用法：/sub &lt;链接或包名&gt;\n\n支持以下格式：\n"
            "1. <b>APKMirror 链接</b>\n"
            "   <code>https://www.apkmirror.com/apk/…</code>\n"
            "2. <b>Google Play 链接</b>\n"
            "   <code>https://play.google.com/store/apps/details?id=…</code>\n"
            "3. <b>应用包名</b>（如 <code>com.android.chrome</code>）",
        )
        return

    input_str = parts[1].strip()
    url = _resolve_to_apkmirror_url(message, input_str)
    if url is None:
        return

    added = add_subscription(message.chat.id, url)
    if not added:
        bot.reply_to(message, "🔄 已订阅该应用。正在为您手动抓取当前最新版本，请稍等……")
        threading.Thread(target=_send_current_version, args=(message.chat.id, url), daemon=True).start()
        return
    bot.reply_to(message, f"✅ 订阅成功！正在获取当前最新版本，请稍等……\n<code>{html.escape(url)}</code>")
    threading.Thread(
        target=_send_current_version,
        args=(message.chat.id, url),
        daemon=True,
    ).start()


@bot.message_handler(commands=["dl"])
def handle_dl(message: Message):
    if not _require_allowed(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(
            message,
            "用法：/dl &lt;链接或包名&gt;\n\n"
            "一次性下载并发送 APK，不创建订阅。支持以下格式：\n"
            "1. <b>APKMirror 链接</b>\n"
            "   <code>https://www.apkmirror.com/apk/…</code>\n"
            "2. <b>Google Play 链接</b>\n"
            "   <code>https://play.google.com/store/apps/details?id=…</code>\n"
            "3. <b>应用包名</b>（如 <code>com.android.chrome</code>）",
        )
        return

    input_str = parts[1].strip()
    url = _resolve_to_apkmirror_url(message, input_str)
    if url is None:
        return

    bot.reply_to(message, f"🔄 正在下载，请稍等……\n<code>{html.escape(url)}</code>")
    threading.Thread(target=_download_once, args=(message.chat.id, url), daemon=True).start()


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
        bot.reply_to(message, f"✅ 已取消全部 {count} 个订阅。")
    else:
        removed = remove_subscription(message.chat.id, arg)
        if removed:
            bot.reply_to(message, "✅ 已取消订阅。")
        else:
            bot.reply_to(message, "❌ 未找到该订阅，请检查 URL 是否正确。")


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
    bot.reply_to(message, "🔄 开始检查所有订阅，请稍等……")
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
        bot.reply_to(message, "用法：/adduser &lt;user_id&gt;")
        return
    uid = int(parts[1].strip())
    added = add_to_whitelist(uid)
    if added:
        bot.reply_to(message, f"✅ 已添加用户 <code>{uid}</code> 到白名单。")
    else:
        bot.reply_to(message, f"⚠️ 用户 <code>{uid}</code> 已在白名单中。")


@bot.message_handler(commands=["deluser"])
def handle_deluser(message: Message):
    if not _require_owner(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().lstrip("-").isdigit():
        bot.reply_to(message, "用法：/deluser &lt;user_id&gt;")
        return
    uid = int(parts[1].strip())
    removed = remove_from_whitelist(uid)
    if removed:
        bot.reply_to(message, f"✅ 已从白名单移除用户 <code>{uid}</code>。")
    else:
        bot.reply_to(message, f"❌ 用户 <code>{uid}</code> 不在白名单中。")


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
