"""APKDL commands. Private chats, durable callbacks, and confirmed-send state."""

from html import escape
import logging
import threading

import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
import database as db
from cards import Card, help_card, region_label, release_card, subscription_card
from galaxy_store import InvalidInput, StoreError, parse_input
from richmsg import RichMessenger
from scraper import GalaxyStore
from telegram_transport import install_transport

config.validate_bot_config()
install_transport()
bot = telebot.TeleBot(config.BOT_TOKEN, parse_mode="HTML")
messages = RichMessenger(bot)
logger = logging.getLogger("apkdl-bot")
check_lock = threading.Lock()
_link_slots = threading.BoundedSemaphore(2)


def allowed_user(user_id):
    return user_id == config.OWNER_ID or db.is_in_whitelist(user_id)


def allowed(message, owner=False):
    return bool(
        message.chat.type == "private"
        and message.from_user
        and (
            message.from_user.id == config.OWNER_ID
            if owner
            else allowed_user(message.from_user.id)
        )
    )


def keyboard(app, url=None):
    db.remember_app(app)
    markup = InlineKeyboardMarkup()
    buttons = []
    if url:
        buttons.append(InlineKeyboardButton("下载", url=url))
    buttons.append(
        InlineKeyboardButton(
            "刷新链接" if url else "获取链接", callback_data="gdl:" + app.key
        )
    )
    markup.row(*buttons)
    return markup


def edit_progress(chat_id, message_id, text):
    try:
        bot.edit_message_text(text, chat_id, message_id, parse_mode="HTML")
    except Exception:
        logger.warning("Progress edit failed; no replacement sent")


def _link_once(chat_id, app, progress_id):
    try:
        with GalaxyStore() as store:
            grant = store.download_link(app)
            release = grant.release
            notes = store.notes(release)
        markup = keyboard(app, grant.url)
        card = release_card(release, notes)
    except StoreError as exc:
        edit_progress(chat_id, progress_id, "获取链接失败：" + escape(str(exc)))
        return
    except Exception:
        edit_progress(chat_id, progress_id, "获取链接失败，请稍后重试。")
        logger.error(
            "Link preparation failed; diagnostic details suppressed to protect transient URLs"
        )
        return
    try:
        # Only the button carries the temporary Samsung URL; never fetch APK bytes.
        messages.send(chat_id, card, markup)
    except Exception:
        edit_progress(
            chat_id, progress_id, "链接消息送达未确认，请先检查聊天记录；未自动重发。"
        )
        logger.warning("Link message unconfirmed; no automatic resend or APK download")
    else:
        try:
            bot.delete_message(chat_id, progress_id)
        except Exception:
            edit_progress(chat_id, progress_id, "下载卡片已发送。")


def start_link(chat_id, app):
    if not _link_slots.acquire(blocking=False):
        bot.send_message(chat_id, "已有两个链接请求，请稍后再试。")
        return
    try:
        progress = bot.send_message(chat_id, "正在查询 Galaxy Store……")

        def worker():
            try:
                _link_once(chat_id, app, progress.message_id)
            finally:
                _link_slots.release()

        threading.Thread(target=worker, daemon=True).start()
    except Exception:
        _link_slots.release()
        raise


def check_app(app):
    # Scheduled checks never request downloadForRestore (2316) or APK bytes.
    with GalaxyStore() as store:
        release = store.metadata(app)
        notes = store.notes(release)
    db.cache_release(app, release, notes)
    for chat_id in db.pending_subscribers(app, release):
        if not allowed_user(chat_id):
            continue
        try:
            messages.send(
                chat_id,
                release_card(release, notes, update=True),
                keyboard(app),
            )
        except Exception:
            logger.warning("Notification unconfirmed; subscriber remains pending")
        else:
            db.mark_notified(chat_id, app, release)


def run_check_all(triggered_by=None):
    if not check_lock.acquire(blocking=False):
        if triggered_by:
            bot.send_message(triggered_by, "已有检查任务正在运行。")
        return
    succeeded = failed = 0
    try:
        for app in db.subscribed_apps():
            try:
                check_app(app)
                succeeded += 1
            except Exception:
                failed += 1
                logger.warning("Source check failed; previous cached state preserved")
        if triggered_by:
            bot.send_message(
                triggered_by,
                f"检查完成：{succeeded} 项查询成功，{failed} 项失败。通知按送达结果单独记录。",
            )
    finally:
        check_lock.release()


def argument(message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        raise InvalidInput("请输入包名或 Galaxy Store 详情链接，可追加 CN 或 US。")
    return parts[1]


def parsed_argument(message):
    try:
        return parse_input(argument(message))
    except InvalidInput as exc:
        bot.reply_to(message, escape(str(exc)))
        return None


@bot.message_handler(commands=["sub"])
def handle_sub(message):
    if not allowed(message):
        return
    app = parsed_argument(message)
    if app:
        added = db.add_subscription(message.chat.id, app)
        messages.send(
            message.chat.id,
            subscription_card(app, added),
            keyboard(app),
        )


@bot.message_handler(commands=["dl"])
def handle_dl(message):
    if not allowed(message):
        return
    app = parsed_argument(message)
    if app:
        start_link(message.chat.id, app)


@bot.message_handler(commands=["unsub"])
def handle_unsub(message):
    if not allowed(message):
        return
    try:
        text = argument(message)
        if text == "all":
            count = db.remove_all_subscriptions(message.chat.id)
        else:
            count = int(db.remove_subscription(message.chat.id, parse_input(text)))
        bot.reply_to(message, f"已取消 {count} 个订阅。")
    except InvalidInput:
        bot.reply_to(
            message, "用法：/unsub &lt;包名或详情链接&gt; [CN|US]，或 /unsub all"
        )


def send_cached(chat_id, all_users=False):
    rows = db.get_subscriptions(None if all_users else chat_id)
    if not rows:
        messages.send(
            chat_id,
            Card("APKDL · 状态" if all_users else "APKDL · 订阅", ("当前无订阅。",)),
        )
        return
    # Bound each card to fit ordinary HTML fallback as well as rich messages.
    for offset in range(0, len(rows), 6):
        lines = []
        for row in rows[offset : offset + 6]:
            release = db.cached_release(row)
            version = (
                f"{release.version_name[:100]} · {region_label(release.region)}"
                if release
                else "尚未查询"
            )
            lines.append(
                f"{row['package']} · {region_label(row['preference'])} — {version}"
                + (f" · 用户 {row['chat_id']}" if all_users else "")
            )
        messages.send(
            chat_id,
            Card(
                "APKDL · 缓存状态", tuple(lines), footer="缓存数据，不代表实时可用性。"
            ),
        )


@bot.message_handler(commands=["list"])
def handle_list(message):
    if allowed(message):
        send_cached(message.chat.id)


@bot.message_handler(commands=["status"])
def handle_status(message):
    if not allowed(message, owner=True):
        return
    send_cached(message.chat.id, all_users=True)
    count = db.legacy_count()
    if count:
        bot.reply_to(message, f"另保留 {count} 条旧来源订阅，均未启用。")


@bot.message_handler(commands=["app"])
def handle_app(message):
    if allowed(message):
        bot.reply_to(message, "旧版搜索已停用。请发送包名或 Galaxy Store 详情链接。")


@bot.message_handler(commands=["help"])
def handle_help(message):
    if allowed(message, owner=True):
        messages.send(message.chat.id, help_card())


@bot.message_handler(commands=["check"])
def handle_check(message):
    if allowed(message, owner=True):
        threading.Thread(
            target=run_check_all, kwargs={"triggered_by": message.chat.id}, daemon=True
        ).start()


@bot.message_handler(commands=["add", "del"])
def handle_users(message):
    if not allowed(message, owner=True):
        return
    parts = (message.text or "").split(maxsplit=2)
    if (
        len(parts) < 2
        or not parts[1].isascii()
        or not parts[1].isdigit()
        or not 0 < int(parts[1]) < 2**63
    ):
        bot.reply_to(
            message, "用法：/add &lt;user_id&gt; [备注] 或 /del &lt;user_id&gt;"
        )
        return
    if parts[0].split("@")[0] == "/add":
        changed = db.add_to_whitelist(
            int(parts[1]), parts[2][:200] if len(parts) > 2 else ""
        )
    else:
        changed = db.remove_from_whitelist(int(parts[1]))
    bot.reply_to(message, "白名单已更新。" if changed else "白名单未改变。")


@bot.message_handler(commands=["user"])
def handle_user_list(message):
    if not allowed(message, owner=True):
        return
    users = db.get_whitelist()
    for offset in range(0, max(1, len(users)), 10):
        lines = tuple(
            f"{uid} · {remark}" for uid, remark in users[offset : offset + 10]
        ) or ("白名单为空。",)
        messages.send(message.chat.id, Card("APKDL · 白名单", lines))


@bot.callback_query_handler(
    func=lambda call: bool(call.data and call.data.startswith("gdl:"))
)
def handle_link_callback(call):
    if not (
        call.message
        and call.message.chat.type == "private"
        and call.from_user
        and call.message.chat.id == call.from_user.id
        and allowed_user(call.from_user.id)
    ):
        bot.answer_callback_query(call.id, "无权使用。")
        return
    app = db.get_app(call.data[4:])
    if not app:
        bot.answer_callback_query(call.id, "此应用记录不存在，请重新输入包名。")
        return
    bot.answer_callback_query(call.id, "正在查询最新版本……")
    start_link(call.message.chat.id, app)


@bot.callback_query_handler(
    func=lambda call: bool(
        call.data
        and call.data.startswith(
            ("dl:", "apr:", "apg:", "apc:", "aas_", "aad_", "aah:", "aac:")
        )
    )
)
def handle_legacy_callback(call):
    bot.answer_callback_query(call.id, "旧来源已停用，请使用 Galaxy Store 包名。")


@bot.message_handler(
    content_types=["text"],
    func=lambda message: bool(message.text and not message.text.startswith("/")),
)
def handle_bare(message):
    if not allowed(message):
        return
    try:
        app = parse_input(message.text)
    except InvalidInput as exc:
        bot.reply_to(message, escape(str(exc)))
        return
    start_link(message.chat.id, app)
