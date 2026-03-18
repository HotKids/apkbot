import logging
import threading
import time
from typing import Optional

from telebot import TeleBot
from telebot.apihelper import ApiTelegramException
from telebot.types import Message

from config import (
    AUTO_INIT_OWNER,
    BOT_TOKEN,
    CHECK_HOUR,
    CHECK_MINUTE,
    PREFER_APK,
    REQUIRE_SIGNATURE,
    REQUIRE_VARIANT,
)
from database import (
    add_channel,
    deactivate_channel,
    ensure_owner,
    is_owner,
    list_channels,
    owner_id,
    release_exists,
    save_release,
)
from scraper import cleanup_after_push, scrape_latest

logger = logging.getLogger("apkmirror-bot")

bot = TeleBot(BOT_TOKEN, parse_mode="HTML")
check_lock = threading.Lock()


def require_private_owner(message: Message) -> bool:
    if message.chat.type != "private":
        return False
    if ensure_owner(message.from_user.id):
        return True
    bot.reply_to(message, "你不是当前 owner。")
    return False


def caption_text(result: dict) -> str:
    size_mb = result["apk_path"].stat().st_size / 1024 / 1024
    return (
        "<b>Google Play Store 更新推送</b>\n"
        f"版本：<code>{result['version']}</code>\n"
        f"筛选：<code>{REQUIRE_SIGNATURE}</code> <code>{REQUIRE_VARIANT}</code>\n"
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
            logger.exception("发送到频道 %s 失败", chat_id)
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
        cleanup_after_push(result["apk_path"])

        summary = f"发现新版本 {result['version']}，已推送到 {sent} 个频道。"
        if failed:
            summary += " 失败：" + " | ".join(failed)
        if triggered_by:
            try:
                bot.send_message(triggered_by, summary)
            except Exception:
                logger.exception("发送摘要给 owner 失败")
        return summary
    except Exception as e:
        logger.exception("检查失败")
        msg = f"检查失败：{e}"
        if triggered_by:
            try:
                bot.send_message(triggered_by, msg)
            except Exception:
                logger.exception("发送失败通知给 owner 失败")
        return msg
    finally:
        check_lock.release()


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
        username = f"@{row['username']}" if row["username"] else "无用户名"
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
