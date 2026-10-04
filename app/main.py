from datetime import datetime, timedelta
import logging
import signal
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler
from telebot.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
)
import config


def register_commands(bot):
    public = [
        BotCommand("dl", "获取下载链接"),
        BotCommand("sub", "订阅应用更新"),
        BotCommand("unsub", "取消订阅"),
        BotCommand("list", "查看我的订阅"),
    ]
    owner = public + [
        BotCommand("check", "检查订阅更新"),
        BotCommand("status", "查看所有用户的订阅"),
        BotCommand("add", "将用户加入白名单"),
        BotCommand("del", "将用户移出白名单"),
        BotCommand("user", "查看白名单"),
        BotCommand("help", "查看帮助"),
    ]
    # Chat scopes replace the private-chat list, so the owner needs both sets.
    try:
        for scope, commands in (
            (BotCommandScopeAllPrivateChats(), public),
            (BotCommandScopeChat(config.OWNER_ID), owner),
        ):
            if bot.set_my_commands(commands, scope=scope) is not True:
                raise RuntimeError("Command registration was not confirmed")
    except Exception:
        raise RuntimeError("命令菜单注册未确认，请稍后重新启动。") from None


def main():
    config.validate_bot_config()
    from database import init_db
    from handlers import bot, run_check_all

    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    # Fail startup instead of reporting readiness while polling repeatedly
    # fails due to bad credentials, network errors, or a previous webhook.
    try:
        bot.get_me()
        webhook = bot.get_webhook_info()
    except Exception:
        raise RuntimeError("Telegram 启动检查失败，请检查 Bot Token 和网络。") from None
    if webhook.url:
        raise RuntimeError(
            "此 Bot 设置了 webhook，请先调用 deleteWebhook 删除后再启动。"
        )
    register_commands(bot)
    init_db()
    scheduler = BackgroundScheduler(timezone=config.TZ)
    scheduler.add_job(
        run_check_all,
        "interval",
        minutes=config.CHECK_INTERVAL,
        id="galaxy_check",
        max_instances=1,
        coalesce=True,
        # A plain interval would restart its countdown on every deploy, so
        # frequent rebuilds could postpone checks indefinitely.
        next_run_time=datetime.now(ZoneInfo(config.TZ)) + timedelta(minutes=1),
    )
    scheduler.start()
    previous_sigterm = signal.signal(signal.SIGTERM, lambda *_: bot.stop_polling())
    try:
        logging.getLogger("apkdl-bot").info(
            "apkbot started; Galaxy Store checks every %dm", config.CHECK_INTERVAL
        )
        bot.infinity_polling(
            timeout=30,
            long_polling_timeout=30,
            allowed_updates=["message", "callback_query"],
        )
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        scheduler.shutdown(wait=False)
        bot.stop_polling()


if __name__ == "__main__":
    main()
