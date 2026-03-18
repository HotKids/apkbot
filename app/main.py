import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import config  # 最先 import：触发必填项校验 + 目录创建
from database import get_apk_url, init_db
from handlers import bot, run_check

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("apkmirror-bot")


def scheduled_job() -> None:
    logger.info("Scheduled check triggered")
    run_check(triggered_by=None)


def main() -> None:
    init_db()
    scheduler = BackgroundScheduler(timezone=config.TZ)
    scheduler.add_job(
        scheduled_job,
        CronTrigger.from_crontab(config.CRON_SCHEDULE, timezone=config.TZ),
        id="apk_check",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Bot started. Target: %s | Cron: %s (%s)",
        get_apk_url() or "未配置", config.CRON_SCHEDULE, config.TZ,
    )
    bot.infinity_polling(timeout=30, long_polling_timeout=30)


if __name__ == "__main__":
    main()
