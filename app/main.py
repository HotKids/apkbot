from datetime import datetime, timedelta
import logging
import signal
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler
import config


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
        raise RuntimeError("此 Bot 仍配置了 webhook，请先切换到轮询模式。")
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
            "APKDL started; Galaxy Store checks every %dm", config.CHECK_INTERVAL
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
