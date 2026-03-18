import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

import config  # 最先 import：触发必填项校验 + 目录创建
from database import init_db
from handlers import bot, run_check_all

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("apkmirror-bot")


def scheduled_job() -> None:
    logger.info("定时检查触发")
    run_check_all(triggered_by=None)


def main() -> None:
    init_db()
    scheduler = BackgroundScheduler(timezone=config.TZ)
    scheduler.add_job(
        scheduled_job,
        IntervalTrigger(minutes=config.CHECK_INTERVAL, timezone=config.TZ),
        id="apk_check",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Bot 已启动，检查间隔：%dm（%s）", config.CHECK_INTERVAL, config.TZ)
    bot.infinity_polling(timeout=30, long_polling_timeout=30)


if __name__ == "__main__":
    main()
