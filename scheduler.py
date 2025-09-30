from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from storage import Alert, DBManager, Reminder

UTC = ZoneInfo("UTC")
KYIV = ZoneInfo("Europe/Kyiv")
logger = logging.getLogger(__name__)


class SchedulerManager:
    def __init__(self, db: DBManager, bot) -> None:
        self._db = db
        self._bot = bot
        self._scheduler = AsyncIOScheduler(timezone=UTC)
        self._started = False

    async def start(self) -> None:
        if not self._started:
            self._scheduler.start()
            self._started = True
            await self.reschedule_all()

    async def shutdown(self) -> None:
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False

    async def reschedule_all(self) -> None:
        now_utc = datetime.now(tz=UTC)
        alerts = await self._db.get_pending_alerts(now_utc)
        for alert, reminder in alerts:
            await self.schedule_alert(alert, reminder)

    async def schedule_alert(self, alert: Alert, reminder: Reminder) -> None:
        if alert.fired:
            return
        job_id = f"alert:{alert.id}"
        if self._scheduler.get_job(job_id):
            return
        run_date = alert.alert_ts_utc.astimezone(UTC)
        if run_date <= datetime.now(tz=UTC):
            return
        self._scheduler.add_job(
            self._fire_alert,
            "date",
            run_date=run_date,
            args=[alert.id],
            id=job_id,
            replace_existing=False,
        )
        logger.debug("Scheduled alert %s for reminder %s at %s", alert.id, reminder.id, run_date)

    async def schedule_alerts(
        self, alerts_with_reminders: Iterable[tuple[Alert, Reminder]]
    ) -> None:
        for alert, reminder in alerts_with_reminders:
            await self.schedule_alert(alert, reminder)

    async def create_jobs_for_alerts(self, alerts: Sequence[Alert]) -> None:
        for alert in alerts:
            reminder = await self._db.get_reminder(alert.reminder_id)
            if reminder:
                await self.schedule_alert(alert, reminder)

    async def _fire_alert(self, alert_id: int) -> None:
        data = await self._db.get_alert_with_reminder(alert_id)
        if not data:
            return
        alert, reminder = data
        if reminder.archived:
            await self._db.mark_alert_fired(alert.id)
            return
        try:
            await self._bot.send_message(
                chat_id=reminder.chat_id,
                text=(
                    "<b>Напоминание</b>\n"
                    f"{reminder.text}\n"
                    f"🕒 В {reminder.event_ts_utc.astimezone(KYIV).strftime('%d.%m.%Y %H:%M')}"
                ),
            )
        finally:
            await self._db.mark_alert_fired(alert.id)


__all__ = ["SchedulerManager"]
