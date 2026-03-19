import html
import logging
import re
import threading
import time
import uuid as _uuid_mod
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import telebot
from telebot import TeleBot
from telebot.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

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
    _release_path_from_variant_url,
    cleanup_after_push,
    fetch_app_name_from_rss,
    fetch_rss_latest_release_url,
    new_session,
    resolve_and_download,
    resolve_and_download_apkpure,
    resolve_package_to_apkmirror_url,
    resolve_package_to_apkpure_url,
    scrape_and_pick,
    scrape_and_pick_apkpure,
    search_apkmirror,
    search_apkpure,
)
from selector import Variant

logger = logging.getLogger("apkdl-bot")

if LOCAL_BOT_API_URL:
    telebot.apihelper.API_URL = LOCAL_BOT_API_URL + "/bot{0}/{1}"

bot = TeleBot(BOT_TOKEN, parse_mode="HTML")
check_lock = threading.Lock()
_dl_callbacks: dict[str, str] = {}        # uuid → apk_url
_search_sessions: dict[str, dict] = {}    # sid → {results, mode, chat_id}

# 标准包名：至少含一个点，仅 ASCII 字母数字 + _ + .
_PKG_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z0-9_]+)+$')
_SEARCH_PAGE_SIZE = 5


def _is_keyword(s: str) -> bool:
    """是否为宽泛关键词（非 URL、非标准包名）。"""
    if s.startswith("http"):
        return False
    return not _PKG_RE.match(s)


def _build_search_keyboard(sid: str, results: list, page: int) -> InlineKeyboardMarkup:
    """构建搜索结果分页键盘。results 元素为 (icon, name, url)。"""
    total = len(results)
    pages = max(1, (total + _SEARCH_PAGE_SIZE - 1) // _SEARCH_PAGE_SIZE)
    start = page * _SEARCH_PAGE_SIZE
    markup = InlineKeyboardMarkup()
    for i, (icon, name, _url) in enumerate(results[start:start + _SEARCH_PAGE_SIZE]):
        markup.add(InlineKeyboardButton(
            f"{icon} {name}",
            callback_data=f"sp:{sid}:{start + i}",
        ))
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀ 上一页", callback_data=f"sg:{sid}:{page - 1}"))
        nav.append(InlineKeyboardButton(f"📄 {page + 1}/{pages}", callback_data="noop"))
        if page < pages - 1:
            nav.append(InlineKeyboardButton("下一页 ▶", callback_data=f"sg:{sid}:{page + 1}"))
        markup.row(*nav)
    markup.add(InlineKeyboardButton("❌ 取消", callback_data=f"sc:{sid}"))
    return markup


def _do_keyword_search(message: Message, keyword: str, mode: str) -> None:
    """在后台线程中执行关键词搜索并展示结果键盘。"""
    try:
        status_msg = bot.reply_to(
            message, f"🔍 正在搜索 <b>{html.escape(keyword)}</b>……", parse_mode="HTML"
        )
    except Exception:
        return
    try:
        with ThreadPoolExecutor(max_workers=2) as ex:
            f_am = ex.submit(search_apkmirror, new_session(), keyword)
            f_ap = ex.submit(search_apkpure, new_session(), keyword)
            am_list = [("🟠", n, u) for n, u in (f_am.result() or [])]
            ap_list = [("🟢", n, u) for n, u in (f_ap.result() or [])]
        # 合并：按相关性升序 + 同分时交叉排列（APKMirror 先），显示全部结果
        def _rel(name: str) -> int:
            n, kw = name.lower(), keyword.lower()
            if n == kw:                         return 0
            if n.startswith(kw):                return 1
            if kw in n:                         return 2
            if any(w in n for w in kw.split()): return 3
            return 4
        # 排序键 (relevance, pos, source): 同分时按 position 交叉，AM(0) 优先 AP(1)
        # 效果：AM[0] AP[0] AM[1] AP[1] ...（同分段内交叉）
        keyed = (
            [(_rel(item[1]), i, 0, item) for i, item in enumerate(am_list)] +
            [(_rel(item[1]), i, 1, item) for i, item in enumerate(ap_list)]
        )
        keyed.sort(key=lambda x: (x[0], x[1], x[2]))
        results = [x[3] for x in keyed]
        if not results:
            bot.edit_message_text(
                f"❌ 未找到 <b>{html.escape(keyword)}</b> 相关应用。",
                message.chat.id, status_msg.message_id, parse_mode="HTML",
            )
            return
        sid = _uuid_mod.uuid4().hex[:8]
        _search_sessions[sid] = {
            "results": results,
            "mode": mode,
            "chat_id": message.chat.id,
        }
        markup = _build_search_keyboard(sid, results, 0)
        bot.edit_message_text(
            f"🔍 <b>{html.escape(keyword)}</b> 的搜索结果，请选择：",
            message.chat.id, status_msg.message_id,
            parse_mode="HTML", reply_markup=markup,
        )
    except Exception as e:
        logger.exception("关键词搜索失败：keyword=%s", keyword)
        try:
            bot.edit_message_text(
                f"❌ 搜索失败：{html.escape(str(e))}",
                message.chat.id, status_msg.message_id, parse_mode="HTML",
            )
        except Exception:
            pass


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
    arch_list = variant.architectures
    archs = "universal" if "universal" in arch_list else (", ".join(arch_list) or "—")
    today = datetime.now(ZoneInfo(TZ)).strftime("%Y-%m-%d")
    is_apkpure = "apkpure" in variant.release_url
    sig_line = "" if is_apkpure else f"签名：<code>{html.escape(', '.join(variant.signatures) or '—')}</code>\n"
    return (
        f"版本：<code>{html.escape(variant.release_version_name)}</code>\n"
        f"日期：<code>{today}</code>\n"
        f"类型：<code>{variant.type}</code>\n"
        + sig_line +
        f"架构：<code>{html.escape(archs)}</code>\n"
        f"最低系统：<code>{html.escape(variant.min_android_text or '—')}</code>\n"
        f"DPI：<code>{variant.dpi or '—'}</code>\n"
        f"大小：<code>{size_mb:.2f} MB</code>\n"
        f"SHA256：<code>{sha256}</code>\n"
        f"来源：<a href=\"{variant.release_url}\">{'APKPure' if is_apkpure else 'APKMirror'}</a>"
    )


def _send_apk_to_user(chat_id: int, variant: Variant, apk_path: Path, sha256: str) -> None:
    """向单个用户发送 APK 文件。"""
    caption = _caption_text(variant, apk_path, sha256)
    if "apkpure" in variant.release_url:
        # 保留 CDN 原始文件名，仅去掉磁盘去重 UUID 后缀（_xxxxxxxx）
        clean_stem = re.sub(r'_[0-9a-f]{8}$', '', apk_path.stem)
        file_name = clean_stem + apk_path.suffix
    else:
        file_name = (re.sub(r'\s+', "_", re.sub(r'[\\/*?:"<>|]', "_",
            variant.app_name)) + (".apkm" if variant.is_bundle else ".apk"))
    with apk_path.open("rb") as f:
        bot.send_document(
            chat_id,
            f,
            visible_file_name=file_name,
            caption=caption,
            timeout=300,
        )



def _check_single_url(apk_url: str) -> str:
    """检查单个 URL，有新版则通知订阅者（带下载按钮），返回状态描述。"""
    try:
        session = new_session()
        is_apkpure = "apkpure" in apk_url

        if is_apkpure:
            # APKPure：无 RSS，直接抓页面，按版本号字符串判断是否有更新
            variant = scrape_and_pick_apkpure(session, apk_url)
            db_row = get_apk_version(apk_url)
            if db_row and db_row["last_version_name"] and \
                    db_row["last_version_name"] == variant.release_version_name:
                update_apk_version(apk_url, last_checked_at=now_iso())
                return f"无更新：{html.escape(variant.app_name)}（{html.escape(variant.release_version_name)}）"
        else:
            # ── 快速 RSS 预检：无更新时直接返回，避免触发完整抓取 ──────────────
            rss_release_url = fetch_rss_latest_release_url(session, apk_url)
            if rss_release_url:
                db_row = get_apk_version(apk_url)
                if db_row and db_row["last_variant_url"]:
                    last_release_path = _release_path_from_variant_url(db_row["last_variant_url"])
                    rss_path = rss_release_url.split("apkmirror.com")[-1]
                    if rss_path == last_release_path:
                        update_apk_version(apk_url, last_checked_at=now_iso())
                        app_label = html.escape(apk_url.rstrip("/").split("/")[-1])
                        stored_ver_str = db_row["last_version_name"] or "—"
                        logger.info("RSS 预检：%s 无更新（%s）", apk_url, stored_ver_str)
                        return f"无更新：{app_label}（{html.escape(stored_ver_str)}）"
            # ── RSS 显示有新版本，或首次检查，或 RSS 不可用 → 走完整流程 ────────
            variant = scrape_and_pick(session, apk_url)

            if not is_new_version(apk_url, variant.variant_url, variant.version_code, "", variant.type):
                update_apk_version(apk_url, last_checked_at=now_iso())
                return f"无更新：{html.escape(variant.app_name)}（{html.escape(variant.release_version_name)}）"

        # 有新版本 — 发通知 + 下载按钮，不自动推送 APK
        uid = _uuid_mod.uuid4().hex[:8]
        _dl_callbacks[uid] = apk_url

        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("⏬ 下载 APK", callback_data=f"dl:{uid}"))

        source_label = "APKPure" if is_apkpure else "APKMirror"
        msg_text = (
            f"🆕 <b>{html.escape(variant.app_name)}</b> 有新版本\n\n"
            f"版本：<code>{html.escape(variant.release_version_name)}</code>\n"
            f"来源：<a href=\"{variant.release_url}\">{source_label}</a>"
        )

        subscribers = get_subscribers(apk_url)
        ok = 0
        for sub_chat_id in subscribers:
            try:
                bot.send_message(sub_chat_id, msg_text, reply_markup=markup)
                ok += 1
            except Exception:
                logger.exception("通知 chat_id=%s 失败", sub_chat_id)

        now = now_iso()
        update_apk_version(
            apk_url,
            last_variant_url=variant.variant_url,
            last_version_name=variant.release_version_name,
            last_version_code=variant.version_code,
            last_sha256="",
            last_type=variant.type,
            last_checked_at=now,
            last_pushed_at=now,
        )
        return (
            f"✅ {html.escape(variant.app_name)} 有新版本 {html.escape(variant.release_version_name)}"
            f"，已通知 {ok}/{len(subscribers)} 人"
        )
    except Exception as e:
        logger.exception("检查失败：%s", apk_url)
        app_label = html.escape(apk_url.rstrip("/").split("/")[-1])
        return f"❌ 检查失败 {app_label}：{html.escape(str(e))}"


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
            if mapped_url:
                url = mapped_url.split("?")[0].rstrip("/") + "/"
                bot.edit_message_text(
                    f"✅ 解析成功：\n<code>{html.escape(url)}</code>",
                    message.chat.id, status_msg.message_id, parse_mode="HTML",
                )
                return url
            # APKMirror 未收录 → 尝试 APKPure
            bot.edit_message_text(
                f"⚠️ APKMirror 未收录，正在尝试 APKPure……",
                message.chat.id, status_msg.message_id, parse_mode="HTML",
            )
            apkpure_url = resolve_package_to_apkpure_url(session, package_name)
            if apkpure_url:
                bot.edit_message_text(
                    f"✅ 解析成功（APKPure）：\n<code>{html.escape(apkpure_url)}</code>",
                    message.chat.id, status_msg.message_id, parse_mode="HTML",
                )
                return apkpure_url
            bot.edit_message_text(
                f"❌ APKMirror 与 APKPure 均未收录包名 <code>{html.escape(package_name)}</code>。",
                message.chat.id, status_msg.message_id, parse_mode="HTML",
            )
            return None
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

def _download_once(chat_id: int, apk_url: str, status_msg_id: Optional[int] = None) -> None:
    """一次性下载并发送，不写数据库。status_msg_id 为下载状态消息 ID，成功后编辑并 60s 自动删除。"""
    apk_path: Optional[Path] = None
    try:
        session = new_session()
        if "apkpure" in apk_url:
            variant = scrape_and_pick_apkpure(session, apk_url)
            apk_path, sha256 = resolve_and_download_apkpure(session, variant)
        else:
            variant = scrape_and_pick(session, apk_url)
            apk_path, sha256 = resolve_and_download(session, variant)
        _send_apk_to_user(chat_id, variant, apk_path, sha256)
        if status_msg_id:
            try:
                bot.edit_message_text("✅ 下载完成", chat_id, status_msg_id)
                time.sleep(60)
                bot.delete_message(chat_id, status_msg_id)
            except Exception:
                pass
    except Exception as e:
        logger.exception("一次性下载失败：chat_id=%s url=%s", chat_id, apk_url)
        err_text = f"❌ 下载失败：{html.escape(str(e))}"
        if status_msg_id:
            try:
                bot.edit_message_text(err_text, chat_id, status_msg_id, parse_mode="HTML")
            except Exception:
                _safe_send(chat_id, err_text)
        else:
            _safe_send(chat_id, err_text)
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
            "用法：/sub &lt;链接或包名&gt;\n\n"
            "订阅应用更新，有新版本时自动通知。支持以下格式：\n"
            "APKMirror 链接：<code>https://www.apkmirror.com/apk/…</code>\n"
            "APKPure 链接：  <code>https://apkpure.com/…</code>\n"
            "Play Store 链接：<code>https://play.google.com/store/apps/details?id=…</code>\n"
            "包名：          <code>com.android.chrome</code>\n"
            "关键词：        <code>豆包</code> / <code>wechat</code>（搜索后从列表选择）",
            parse_mode="HTML",
        )
        return

    input_str = parts[1].strip()
    if _is_keyword(input_str):
        threading.Thread(target=_do_keyword_search, args=(message, input_str, "sub"), daemon=True).start()
        return

    url = _resolve_to_apkmirror_url(message, input_str)
    if url is None:
        return

    added = add_subscription(message.chat.id, url)

    dl_uid = _uuid_mod.uuid4().hex[:8]
    _dl_callbacks[dl_uid] = url
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("⏬ 下载 APK", callback_data=f"dl:{dl_uid}"))

    if not added:
        bot.reply_to(message, "⚠️ 已订阅该应用。", reply_markup=markup)
        return
    bot.reply_to(message, f"✅ 订阅成功！\n<code>{html.escape(url)}</code>", reply_markup=markup)


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
            "APKMirror 链接：<code>https://www.apkmirror.com/apk/…</code>\n"
            "APKPure 链接：  <code>https://apkpure.com/…</code>\n"
            "Play Store 链接：<code>https://play.google.com/store/apps/details?id=…</code>\n"
            "包名：          <code>com.android.chrome</code>\n"
            "关键词：        <code>豆包</code> / <code>wechat</code>（搜索后从列表选择）",
            parse_mode="HTML",
        )
        return

    input_str = parts[1].strip()
    if _is_keyword(input_str):
        threading.Thread(target=_do_keyword_search, args=(message, input_str, "dl"), daemon=True).start()
        return

    url = _resolve_to_apkmirror_url(message, input_str)
    if url is None:
        return

    status_msg = bot.reply_to(message, f"⏬ 正在下载，请稍等……\n<code>{html.escape(url)}</code>")
    threading.Thread(target=_download_once, args=(message.chat.id, url, status_msg.message_id), daemon=True).start()


@bot.message_handler(commands=["unsub"])
def handle_unsub(message: Message):
    if not _require_allowed(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) == 1:
        subs = get_subscriptions(message.chat.id)
        if not subs:
            bot.reply_to(message, "当前无订阅。使用 /sub &lt;链接或包名&gt; 添加。")
            return
        lines = "\n".join(f"• <code>{html.escape(s)}</code>" for s in subs)
        bot.reply_to(
            message,
            f"用法：\n"
            f"  /unsub &lt;链接或包名&gt; — 取消指定订阅\n"
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


@bot.message_handler(commands=["list"])
def handle_sublist(message: Message):
    if not _require_allowed(message):
        return
    subs = get_subscriptions(message.chat.id)
    if not subs:
        bot.reply_to(message, "当前无订阅。使用 /sub &lt;链接或包名&gt; 添加。")
        return
    session = new_session()
    lines = "\n".join(
        f"• <b>{html.escape(fetch_app_name_from_rss(session, s))}</b>  "
        f"<a href=\"{html.escape(s)}\">{html.escape(s)}</a>"
        for s in subs
    )
    bot.reply_to(
        message,
        f"当前订阅（{len(subs)} 个）：\n{lines}",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


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


@bot.message_handler(commands=["add"])
def handle_adduser(message: Message):
    if not _require_owner(message):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 2 or not parts[1].strip().lstrip("-").isdigit():
        bot.reply_to(message, "用法：/add &lt;user_id&gt; [备注]")
        return
    uid = int(parts[1].strip())
    remark = parts[2].strip() if len(parts) > 2 else ""
    added = add_to_whitelist(uid, remark)
    remark_str = f"（{html.escape(remark)}）" if remark else ""
    if added:
        bot.reply_to(message, f"✅ 已添加用户 <code>{uid}</code>{remark_str} 到白名单。")
    else:
        bot.reply_to(message, f"⚠️ 用户 <code>{uid}</code> 已在白名单中。")


@bot.message_handler(commands=["del"])
def handle_deluser(message: Message):
    if not _require_owner(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().lstrip("-").isdigit():
        bot.reply_to(message, "用法：/del &lt;user_id&gt;")
        return
    uid = int(parts[1].strip())
    removed = remove_from_whitelist(uid)
    if removed:
        bot.reply_to(message, f"✅ 已从白名单移除用户 <code>{uid}</code>。")
    else:
        bot.reply_to(message, f"❌ 用户 <code>{uid}</code> 不在白名单中。")


@bot.message_handler(commands=["user"])
def handle_listusers(message: Message):
    if not _require_owner(message):
        return
    users = get_whitelist()
    if not users:
        bot.reply_to(message, "白名单为空。使用 /add &lt;user_id&gt; [备注] 添加。")
    else:
        lines = "\n".join(
            "• " + (f"{html.escape(remark)}  " if remark else "") + f"<code>{uid}</code>"
            for uid, remark in users
        )
        bot.reply_to(message, f"白名单（{len(users)} 人）：\n{lines}")


@bot.message_handler(commands=["help"])
def handle_help(message: Message):
    if not _require_owner(message):
        return
    bot.reply_to(
        message,
        "<b>🤖 APKDL TG Bot</b>\n"
        "\n"
        "<b>📦 订阅管理</b>\n"
        "/sub &lt;链接或包名&gt; — 订阅应用更新\n"
        "/unsub &lt;链接或包名&gt; — 取消订阅\n"
        "/list — 查看订阅列表\n"
        "\n"
        "<b>⏬ 立即下载</b>\n"
        "/dl &lt;链接或包名&gt; — 下载最新版 APK\n"
        "\n"
        "<b>支持格式：</b>\n"
        "APKMirror 链接：<code>https://www.apkmirror.com/apk/…</code>\n"
        "APKPure 链接：  <code>https://apkpure.com/…</code>\n"
        "Play Store 链接：<code>https://play.google.com/store/apps/details?id=…</code>\n"
        "包名：          <code>com.android.chrome</code>\n"
        "关键词：        <code>豆包</code> / <code>wechat</code>（搜索后从列表选择）\n"
        "\n"
        "<b>🔍 状态</b>\n"
        "/check — 立即检查所有订阅更新\n"
        "/status — 查看各订阅当前版本\n"
        "\n"
        "<b>👥 用户管理</b>\n"
        "/add &lt;id&gt; [备注] — 添加白名单用户\n"
        "/del &lt;id&gt; — 移除用户\n"
        "/user — 查看白名单\n"
        "/help — 显示本帮助",
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("dl:"))
def handle_dl_callback(call: CallbackQuery):
    uid = call.data[3:]
    apk_url = _dl_callbacks.get(uid)
    if not apk_url:
        bot.answer_callback_query(call.id, "⚠️ 链接已过期，请等待下次更新通知。")
        return
    bot.answer_callback_query(call.id, "⏬ 开始下载……")
    status_msg = bot.send_message(
        call.message.chat.id,
        f"⏬ 正在下载，请稍等……\n<code>{html.escape(apk_url)}</code>",
        parse_mode="HTML",
    )
    threading.Thread(target=_download_once, args=(call.message.chat.id, apk_url, status_msg.message_id), daemon=True).start()


@bot.callback_query_handler(func=lambda c: c.data in ("noop",) or c.data.startswith(("sp:", "sg:", "sc:")))
def handle_search_callback(call: CallbackQuery):
    data = call.data

    if data == "noop":
        bot.answer_callback_query(call.id)
        return

    if data.startswith("sc:"):
        sid = data[3:]
        _search_sessions.pop(sid, None)
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_text("❌ 已取消搜索。", call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        return

    if data.startswith("sg:"):
        _, sid, page_str = data.split(":", 2)
        sess = _search_sessions.get(sid)
        if not sess:
            bot.answer_callback_query(call.id, "⚠️ 会话已过期。")
            return
        bot.answer_callback_query(call.id)
        markup = _build_search_keyboard(sid, sess["results"], int(page_str))
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
        except Exception:
            pass
        return

    if data.startswith("sp:"):
        _, sid, idx_str = data.split(":", 2)
        sess = _search_sessions.pop(sid, None)
        if not sess:
            bot.answer_callback_query(call.id, "⚠️ 会话已过期。")
            return
        icon, name, apk_url = sess["results"][int(idx_str)]
        mode = sess["mode"]
        chat_id = sess["chat_id"]
        bot.answer_callback_query(call.id)
        label = "✅ 已选择" if mode == "sub" else "⏬ 准备下载"
        try:
            bot.edit_message_text(
                f"{label}：{icon} <b>{html.escape(name)}</b>",
                call.message.chat.id, call.message.message_id, parse_mode="HTML",
            )
        except Exception:
            pass

        if mode == "sub":
            added = add_subscription(chat_id, apk_url)
            dl_uid = _uuid_mod.uuid4().hex[:8]
            _dl_callbacks[dl_uid] = apk_url
            markup2 = InlineKeyboardMarkup()
            markup2.add(InlineKeyboardButton("⏬ 下载 APK", callback_data=f"dl:{dl_uid}"))
            reply_text = "⚠️ 已订阅该应用。" if not added else f"✅ 订阅成功！\n<code>{html.escape(apk_url)}</code>"
            _safe_send(chat_id, reply_text, reply_markup=markup2)
        else:
            status_msg = bot.send_message(
                chat_id, f"⏬ 正在下载，请稍等……\n<code>{html.escape(apk_url)}</code>", parse_mode="HTML",
            )
            threading.Thread(
                target=_download_once, args=(chat_id, apk_url, status_msg.message_id), daemon=True
            ).start()
