"""apkbot commands. Private chats, durable callbacks, and confirmed-send state."""

from html import escape
from contextlib import contextmanager
import logging
import threading
from dataclasses import replace
from weakref import WeakValueDictionary

import telebot
from telebot.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
)

import config
import database as db
from cards import (
    Card,
    Entry,
    app_title,
    code,
    help_card,
    release_card,
    short,
    subscription_card,
    version_line,
)
from galaxy_store import USAGE, InvalidInput, StoreError, parse_input
from richmsg import RichMessenger
from scraper import GalaxyStore
from telegram_transport import install_transport

config.validate_bot_config()
install_transport()
bot = telebot.TeleBot(
    config.BOT_TOKEN, parse_mode="HTML", disable_web_page_preview=True
)
messages = RichMessenger(bot)
logger = logging.getLogger("apkdl-bot")
check_lock = threading.Lock()
_link_slots = threading.BoundedSemaphore(2)
_operation_registry_lock = threading.Lock()
_app_operations = WeakValueDictionary()
_card_operations = WeakValueDictionary()


def _operation_lock(registry, key):
    with _operation_registry_lock:
        lock = registry.get(key)
        if lock is None:
            lock = threading.Lock()
            registry[key] = lock
        return lock


@contextmanager
def _app_operation(app):
    # Selection through publication has one owner; completion order must not
    # let an older in-flight result replace newer application state.
    lock = _operation_lock(_app_operations, app.key)
    acquired = lock.acquire(timeout=config.REQUEST_TIMEOUT)
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()


def allowed_user(user_id):
    return user_id == config.OWNER_ID or db.is_in_whitelist(user_id)


def allowed(message, owner=False):
    if message.chat.type != "private" or not message.from_user:
        return False
    granted = (
        message.from_user.id == config.OWNER_ID
        if owner
        else allowed_user(message.from_user.id)
    )
    if not granted:
        bot.reply_to(
            message,
            "此操作仅限管理员。" if owner else "当前账户无使用权限。",
        )
    return granted


def keyboard(app, url=None):
    db.remember_app(app)
    markup = InlineKeyboardMarkup()
    buttons = []
    buttons.append(
        InlineKeyboardButton(
            "刷新" if url else "获取下载链接",
            callback_data=("grf:" if url else "gdl:") + app.key,
        )
    )
    if url:
        buttons.append(InlineKeyboardButton("下载", url=url))
    markup.row(*buttons)
    return markup


def edit_progress(chat_id, message_id, text):
    try:
        bot.edit_message_text(
            text,
            chat_id,
            message_id,
            parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
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


def _link_once(
    chat_id, app, progress_id, callback_id=None, *, refresh=False, querying=True
):
    with _app_operation(app) as acquired:
        if acquired:
            return _link_owned(
                chat_id, app, progress_id, callback_id,
                refresh=refresh, querying=querying,
            )
        if callback_id:
            answer_link_callback(callback_id, "当前请求较多，请稍后重试。")
        else:
            edit_progress(chat_id, progress_id, "当前请求较多，请稍后重试。")


def _link_owned(
    chat_id, app, progress_id, callback_id=None, *, refresh=False, querying=True
):
    def report(text):
        if not callback_id:
            edit_progress(chat_id, progress_id, escape(text))
        else:
            answer_link_callback(callback_id, text)

    failure = (
        "下载链接更新失败"
        if refresh
        else (
            "应用信息查询失败" if querying and not callback_id else "下载链接获取失败"
        )
    )

    store = None
    try:
        with GalaxyStore() as store:
            grant = store.download_link(app)
            release = grant.release
            release, notes = store.details(release)
        db.cache_release(app, release, notes)
        markup = keyboard(app, grant.url)
        card = release_card(release, notes)
    except StoreError as exc:
        if not refresh and getattr(store, "link_stage", None) == "download":
            failure = "下载链接获取失败"
        report(f"{failure}：{exc}")
        return
    except Exception:
        if not refresh and getattr(store, "link_stage", None) == "download":
            failure = "下载链接获取失败"
        report(f"{failure}：请稍后重试。")
        logger.error(
            "Link preparation failed; diagnostic details suppressed to protect transient URLs"
        )
        return
    try:
        # Only the button carries the temporary Samsung URL; never fetch APK bytes.
        messages.edit(chat_id, progress_id, card, markup)
    except Exception:
        if callback_id:
            answer_link_callback(callback_id, "暂时无法确认操作结果，请查看当前卡片。")
        # An edit timeout may have succeeded. Do not overwrite the same card
        # with an error message, retry the edit, or send a duplicate.
        logger.warning("Link edit unconfirmed; original message left in place")
    else:
        if callback_id:
            answer_link_callback(
                callback_id, "下载链接已更新。" if refresh else "已获取下载链接。"
            )


def start_link(
    chat_id, app, message_id=None, callback_id=None, *, refresh=False, querying=True
):
    def busy():
        if callback_id:
            answer_link_callback(callback_id, "当前请求较多，请稍后重试。")
        else:
            bot.send_message(chat_id, "当前请求较多，请稍后重试。")

    card_lock = None
    if message_id is not None:
        card_lock = _operation_lock(_card_operations, (chat_id, message_id))
        if not card_lock.acquire(blocking=False):
            busy()
            return
    if not _link_slots.acquire(blocking=False):
        if card_lock:
            card_lock.release()
        busy()
        return
    try:
        if message_id is None:
            message_id = bot.send_message(
                chat_id,
                "正在查询应用信息，请稍候。"
                if querying
                else "正在获取下载链接，请稍候。",
            ).message_id

        def worker():
            try:
                _link_once(
                    chat_id,
                    app,
                    message_id,
                    callback_id,
                    refresh=refresh,
                    querying=querying,
                )
            finally:
                if card_lock:
                    card_lock.release()
                _link_slots.release()

        threading.Thread(target=worker, daemon=True).start()
    except Exception:
        if card_lock:
            card_lock.release()
        _link_slots.release()
        raise


def notify_release(app, release, notes):
    for chat_id in db.pending_subscribers(app, release):
        if not allowed_user(chat_id):
            continue
        try:
            messages.send(chat_id, release_card(release, notes, update=True), keyboard(app))
        except Exception:
            logger.warning("Notification unconfirmed; subscriber remains pending")
        else:
            db.mark_notified(chat_id, app, release)


def _check_app(app, store, candidate=None):
    with _app_operation(app) as acquired:
        if not acquired:
            raise StoreError("当前请求较多，请稍后重试。")
        previous, previous_notes = db.app_cache(app)
        try:
            selected = store.update_metadata(candidate) if candidate else None
        except StoreError:
            selected = None
        selected = selected or store.metadata(app)
        if (previous and selected.identity == previous.identity
            and selected.version_name == previous.version_name and selected.size == previous.size
            and previous.updated_date is not None):
            # Already matched details remain valid for this exact store version.
            selected = replace(selected, updated_date=previous.updated_date)
            notes = previous_notes
        else:
            selected, notes = store.details(selected)
        db.cache_release(app, selected, notes)
        notify_release(app, selected, notes)


def check_app(app):
    # Scheduled checks never authorize downloads or request APK bytes.
    with GalaxyStore() as store:
        _check_app(app, store)


def run_check_all(triggered_by=None):
    if not check_lock.acquire(blocking=False):
        if triggered_by:
            bot.send_message(triggered_by, "当前请求较多，请稍后重试。")
        return
    succeeded = failed = 0
    try:
        apps = db.subscribed_apps()
        batch = {}
        if apps:
            with GalaxyStore() as store:
                for region in ("US", "CN"):
                    groups = {}
                    for app in apps:
                        baseline, _ = db.app_cache(app)
                        requested = "CN" if app.region == "CN" else "US"
                        if requested == region and baseline and baseline.region == region and baseline.version_code > 0:
                            previous = groups.get(app.package)
                            if previous is None or baseline.version_code < previous.version_code:
                                groups[app.package] = baseline
                    if groups:
                        try:
                            batch[region] = store.batch_updates(list(groups.values()), region)
                        except StoreError:
                            logger.warning("Batch update query failed; checking applications individually")
                for app in apps:
                    try:
                        region = "CN" if app.region == "CN" else "US"
                        row = batch.get(region, {}).get(app.package)
                        # A missing/partial row says nothing about availability.
                        # The single-app path also preserves AUTO's US→CN policy.
                        _check_app(app, store, row)
                        succeeded += 1
                    except Exception:
                        failed += 1
                        cached, notes = db.app_cache(app)
                        if cached:
                            notify_release(app, cached, notes)
                        logger.warning("Source check failed; previous cached state preserved")
        if triggered_by:
            bot.send_message(triggered_by, f"更新检查已完成。{succeeded} 项查询成功，{failed} 项查询失败。")
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
    except InvalidInput:
        command = (message.text or "").split()[0].split("@")[0]
        bot.reply_to(
            message, escape(f"输入格式不正确。正确格式：{command} <包名或链接> [CN|US]")
        )
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
            subscription_card(
                app,
                added,
                db.last_release(
                    app.package, None if app.region == "AUTO" else app.region
                ),
                db.last_app_name(app.package),
            ),
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
            f"已取消 {count} 项订阅。",
        )
    except InvalidInput:
        bot.reply_to(
            message,
            "输入格式不正确。正确格式：/unsub &lt;包名或链接&gt; [CN|US] 或 /unsub all",
        )


def subscription_entry(row, all_users):
    preference = row["preference"]
    release = db.cached_release(row) or db.last_release(
        row["package"], None if preference == "AUTO" else preference
    )
    name = db.last_app_name(row["package"])
    head = (app_title(row["package"], name),)
    if all_users:
        head += (f" · 用户 {row['chat_id']}",)
    quote = (
        version_line(
            release.version_name,
            preference
            if preference != "AUTO" and release.region == preference
            else None,
        )
        if release and release.version_name
        else "暂无版本信息。"
    )
    return Entry(head, (quote, "\n包名：", code(row["package"])))


def list_keyboard(rows):
    # One button per app, two per row; each opens a new download card and
    # leaves the list in place.
    buttons = []
    for row in rows:
        label = db.last_app_name(row["package"]) or row["package"]
        buttons.append(
            InlineKeyboardButton(
                short(label, 24), callback_data="ldl:" + row["app_key"]
            )
        )
    markup = InlineKeyboardMarkup()
    for offset in range(0, len(buttons), 2):
        markup.row(*buttons[offset : offset + 2])
    return markup


def send_cached(chat_id, all_users=False):
    rows = db.get_subscriptions(None if all_users else chat_id)
    title = "订阅状态" if all_users else "我的订阅"
    if not rows:
        messages.send(chat_id, Card(f"{title}（0 项）", ("暂无订阅记录。",)))
        return
    # Bound each card to fit ordinary HTML fallback as well as rich messages.
    page_size = 5
    for offset in range(0, len(rows), page_size):
        page = rows[offset : offset + page_size]
        messages.send(
            chat_id,
            Card(
                f"{title}（{len(rows)} 项）",
                entries=tuple(subscription_entry(row, all_users) for row in page),
                footer="数据来自最近一次查询。",
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
        bot.reply_to(message, f"另有 {count} 项旧版订阅已停用，历史记录已保留。")


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
        bot.reply_to(
            message,
            "输入格式不正确。正确格式：/add &lt;用户ID&gt; [备注] 或 /del &lt;用户ID&gt;",
        )
        return
    user_id = int(parts[1])
    if parts[0].split("@")[0] == "/add":
        changed = db.add_to_whitelist(user_id, parts[2][:200] if len(parts) > 2 else "")
        text = f"已将用户 {user_id} 加入白名单。" if changed else "该用户已在白名单中。"
    else:
        changed = db.remove_from_whitelist(user_id)
        text = f"已将用户 {user_id} 移出白名单。" if changed else "该用户未在白名单中。"
    bot.reply_to(message, text)


@bot.message_handler(commands=["user"])
def handle_user_list(message):
    if not allowed(message, owner=True):
        return
    users = db.get_whitelist()
    for offset in range(0, max(1, len(users)), 9):
        lines = tuple(
            (code(str(uid)), f" · {remark}") if remark else code(str(uid))
            for uid, remark in users[offset : offset + 9]
        ) or ("当前白名单为空。",)
        messages.send(message.chat.id, Card("白名单", lines))


def is_refresh_callback(call):
    if call.data.startswith("grf:"):
        return True
    # Earlier download cards used gdl for both actions; their URL button
    # distinguishes refresh from a subscription's get-link action.
    markup = getattr(call.message, "reply_markup", None)
    if markup and any(button.url for row in markup.keyboard for button in row):
        return True
    rich = (getattr(call.message, "json", None) or {}).get("rich_message", {})
    return any(
        "url" in button
        for block in rich.get("blocks", [])
        if block.get("type") == "buttons"
        for button in block.get("buttons", [])
    )


@bot.callback_query_handler(
    func=lambda call: bool(call.data and call.data.startswith(("gdl:", "grf:", "ldl:")))
)
def handle_link_callback(call):
    if not (
        call.message
        and call.message.chat.type == "private"
        and call.from_user
        and call.message.chat.id == call.from_user.id
        and allowed_user(call.from_user.id)
    ):
        bot.answer_callback_query(call.id, "当前账户无使用权限。")
        return
    app = db.get_app(call.data[4:])
    if not app:
        bot.answer_callback_query(call.id, "该按钮已失效，请重新发送应用包名。")
        return
    if call.data.startswith("ldl:"):
        # List buttons open a new download card; the list itself stays.
        answer_link_callback(call.id, "正在获取下载链接，请稍候。")
        start_link(call.message.chat.id, app, querying=False)
        return
    start_link(
        call.message.chat.id,
        app,
        message_id=call.message.message_id,
        callback_id=call.id,
        refresh=is_refresh_callback(call),
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
    bot.answer_callback_query(call.id, "该按钮已失效，请重新发送应用包名。")


@bot.message_handler(
    content_types=["text"],
    func=lambda message: bool(message.text and not message.text.startswith("/")),
)
def handle_bare(message):
    if not allowed(message):
        return
    try:
        app = parse_input(message.text)
    except InvalidInput:
        bot.reply_to(message, escape("输入格式不正确。正确格式：<包名或链接> [CN|US]"))
        return
    start_link(message.chat.id, app)
