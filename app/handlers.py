"""APKDL commands. Private chats, durable callbacks, and confirmed-send state."""

from html import escape
import logging
import threading

import telebot
from telebot.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyParameters,
)

import config
import database as db
from cards import (
    Card,
    Entry,
    bold,
    code,
    help_card,
    region_label,
    release_card,
    short,
    subscription_card,
)
from galaxy_store import USAGE, InvalidInput, StoreError, parse_input
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
        buttons.append(InlineKeyboardButton("⬇️ 下载", url=url))
    buttons.append(
        InlineKeyboardButton(
            "🔄 刷新" if url else "🔗 获取下载链接", callback_data="gdl:" + app.key
        )
    )
    markup.row(*buttons)
    return markup


def edit_progress(chat_id, message_id, text):
    try:
        bot.edit_message_text(text, chat_id, message_id, parse_mode="HTML")
    except Exception:
        logger.warning("Progress edit failed; no replacement sent")


def answer_link_callback(callback_id, text):
    try:
        bot.answer_callback_query(callback_id, text)
    except Exception:
        # Slow source requests can outlive Telegram's callback query window.
        logger.warning("Link callback acknowledgement unavailable")
        return False
    return True


def reply_under(chat_id, message_id, text):
    try:
        bot.send_message(
            chat_id,
            escape(text),
            reply_parameters=ReplyParameters(
                message_id, allow_sending_without_reply=True
            ),
        )
    except Exception:
        logger.warning("Refresh failure notice unconfirmed")


def _link_once(chat_id, app, progress_id, callback_id=None):
    def report(text):
        if not callback_id:
            edit_progress(chat_id, progress_id, escape(text))
        elif not answer_link_callback(callback_id, text):
            # The refresh outlived its callback. Reply under the card instead of
            # overwriting it, so a failure is never silent.
            reply_under(chat_id, progress_id, text)

    try:
        with GalaxyStore() as store:
            grant = store.download_link(app)
            release = grant.release
            release, notes = store.details(release)
        db.cache_release(app, release, notes)
        markup = keyboard(app, grant.url)
        card = release_card(release, notes)
    except StoreError as exc:
        report("获取下载链接失败：" + str(exc))
        return
    except Exception:
        report("获取下载链接失败，请稍后重试。")
        logger.error(
            "Link preparation failed; diagnostic details suppressed to protect transient URLs"
        )
        return
    try:
        # Only the button carries the temporary Samsung URL; never fetch APK bytes.
        messages.edit(chat_id, progress_id, card, markup)
    except Exception:
        if callback_id:
            answer_link_callback(
                callback_id, "卡片可能没有更新，请查看后再点「刷新」。"
            )
        # An edit timeout may have succeeded. Do not overwrite the same card
        # with an error message, retry the edit, or send a duplicate.
        logger.warning("Link edit unconfirmed; original message left in place")
    else:
        if callback_id:
            answer_link_callback(callback_id, "已获取新链接，请点「下载」。")


def start_link(chat_id, app, message_id=None, callback_id=None):
    if not _link_slots.acquire(blocking=False):
        if callback_id:
            answer_link_callback(callback_id, "正在处理其他请求，请稍后再试。")
        else:
            bot.send_message(chat_id, "正在处理其他请求，请稍后再试。")
        return
    try:
        if message_id is None:
            message_id = bot.send_message(chat_id, "正在查询 Galaxy Store……").message_id

        def worker():
            try:
                _link_once(chat_id, app, message_id, callback_id)
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
        release, notes = store.details(release)
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
            bot.send_message(triggered_by, "已有检查正在进行，请稍后再试。")
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
                f"检查完成：{succeeded} 个应用查询成功，{failed} 个失败。有新版本的订阅已单独通知。",
            )
    finally:
        check_lock.release()


def argument(message):
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2:
        raise InvalidInput(USAGE)
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
            subscription_card(app, added, db.last_app_name(app.package)),
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
        elif len(text.split()) == 1:
            # No region given: remove this package in every region.
            count = db.remove_package_subscriptions(
                message.chat.id, parse_input(text).package
            )
        else:
            count = int(db.remove_subscription(message.chat.id, parse_input(text)))
        bot.reply_to(
            message,
            f"已取消 {count} 个订阅。"
            if count
            else "没有匹配的订阅，可用 /list 查看。",
        )
    except InvalidInput:
        bot.reply_to(
            message, "用法：/unsub &lt;包名或链接&gt; [CN|US]；取消全部用 /unsub all"
        )


def subscription_entry(row, all_users):
    release = db.cached_release(row)
    name = db.last_app_name(row["package"])
    head = (
        (bold(short(name, 100)) if name else code(row["package"])),
        f" · {region_label(row['preference'])}",
    )
    if all_users:
        head += (f" · 用户 {row['chat_id']}",)
    quote = (
        (
            "版本 ",
            bold(short(release.version_name, 100)),
            f" · {region_label(release.region)}",
        )
        if release
        else "版本待检查"
    )
    # With a name on the head line, the package goes to the quote's credit.
    return Entry(head, quote, row["package"] if name else "")


def list_keyboard(rows):
    # One button per app, two per row; each opens a new download card and
    # leaves the list in place.
    buttons = []
    for row in rows:
        label = db.last_app_name(row["package"]) or row["package"]
        buttons.append(
            InlineKeyboardButton(
                "⬇️ " + short(label, 24), callback_data="ldl:" + row["app_key"]
            )
        )
    markup = InlineKeyboardMarkup()
    for offset in range(0, len(buttons), 2):
        markup.row(*buttons[offset : offset + 2])
    return markup


def send_cached(chat_id, all_users=False):
    rows = db.get_subscriptions(None if all_users else chat_id)
    title = "APKDL · 订阅状态" if all_users else "APKDL · 我的订阅"
    if not rows:
        empty = "暂无任何订阅。" if all_users else "还没有订阅，发送 /sub <包名> 添加。"
        messages.send(chat_id, Card(title, (empty,)))
        return
    # Bound each card to fit ordinary HTML fallback as well as rich messages.
    for offset in range(0, len(rows), 6):
        page = rows[offset : offset + 6]
        messages.send(
            chat_id,
            Card(
                f"{title} · {len(rows)} 项",
                entries=tuple(subscription_entry(row, all_users) for row in page),
                footer="版本为上次检查的结果，不是实时数据。",
            ),
            None if all_users else list_keyboard(page),
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
        bot.reply_to(
            message, f"另有 {count} 条旧版来源的订阅已停用，仅保留在数据库中。"
        )


@bot.message_handler(commands=["app"])
def handle_app(message):
    if allowed(message):
        bot.reply_to(
            message, "关键词搜索已停用，请直接发送包名或 Galaxy Store 详情链接。"
        )


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
        bot.reply_to(message, "用法：/add &lt;用户ID&gt; [备注] 或 /del &lt;用户ID&gt;")
        return
    user_id = int(parts[1])
    if parts[0].split("@")[0] == "/add":
        changed = db.add_to_whitelist(user_id, parts[2][:200] if len(parts) > 2 else "")
        text = (
            f"已将 {user_id} 加入白名单。" if changed else f"{user_id} 已在白名单中。"
        )
    else:
        changed = db.remove_from_whitelist(user_id)
        text = (
            f"已将 {user_id} 移出白名单。" if changed else f"{user_id} 不在白名单中。"
        )
    bot.reply_to(message, text)


@bot.message_handler(commands=["user"])
def handle_user_list(message):
    if not allowed(message, owner=True):
        return
    users = db.get_whitelist()
    for offset in range(0, max(1, len(users)), 10):
        lines = tuple(
            (code(str(uid)), f" · {remark}") if remark else code(str(uid))
            for uid, remark in users[offset : offset + 10]
        ) or ("白名单为空。",)
        messages.send(message.chat.id, Card("APKDL · 白名单", lines))


@bot.callback_query_handler(
    func=lambda call: bool(call.data and call.data.startswith(("gdl:", "ldl:")))
)
def handle_link_callback(call):
    if not (
        call.message
        and call.message.chat.type == "private"
        and call.from_user
        and call.message.chat.id == call.from_user.id
        and allowed_user(call.from_user.id)
    ):
        bot.answer_callback_query(call.id, "你没有使用权限。")
        return
    app = db.get_app(call.data[4:])
    if not app:
        bot.answer_callback_query(call.id, "找不到这条应用记录，请重新发送包名。")
        return
    if call.data.startswith("ldl:"):
        # List buttons open a new download card; the list itself stays.
        answer_link_callback(call.id, "正在获取下载链接……")
        start_link(call.message.chat.id, app)
        return
    start_link(
        call.message.chat.id,
        app,
        message_id=call.message.message_id,
        callback_id=call.id,
    )


@bot.callback_query_handler(
    func=lambda call: bool(
        call.data
        and call.data.startswith(
            ("dl:", "apr:", "apg:", "apc:", "aas_", "aad_", "aah:", "aac:")
        )
    )
)
def handle_legacy_callback(call):
    bot.answer_callback_query(call.id, "这个按钮来自已停用的旧版本，请重新发送包名。")


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
