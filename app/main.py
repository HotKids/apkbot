import logging

from apscheduler.schedulers.background import BackgroundScheduler

import config  # 最先 import：触发 BOT_TOKEN 校验 + 目录创建
from database import init_db, owner_id
from handlers import bot, run_check

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("apkmirror-bot")


def scheduled_job():
    logger.info("Running scheduled check")
    owner = owner_id()
    run_check(triggered_by=owner)


def main():
    init_db()
    scheduler = BackgroundScheduler(timezone=config.TZ)
    scheduler.add_job(
        scheduled_job,
        "cron",
        hour=config.CHECK_HOUR,
        minute=config.CHECK_MINUTE,
        id="daily_check",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Bot started. Scheduled daily check at %02d:%02d %s",
        config.CHECK_HOUR,
        config.CHECK_MINUTE,
        config.TZ,
    )
    bot.infinity_polling(timeout=30, long_polling_timeout=30)


if __name__ == "__main__":
    main()
