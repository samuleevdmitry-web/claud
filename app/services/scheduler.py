"""Планировщик прогонов: APScheduler с хранилищем заданий в БД.

Интервальный триггер (по умолчанию 48 часов) с первой точкой в заданное время суток, а не
cron «*/2» — тот сбивается на границе месяца. Интервал и время запуска берутся из
настроек в интерфейсе (таблица app_settings), а если их там нет — из .env.
Двойной запуск исключён: max_instances=1 и блокировка прогона в БД (runner.start_run).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AppSetting

log = logging.getLogger(__name__)

JOB_ID = "periodic_run"
_scheduler: AsyncIOScheduler | None = None


@dataclass
class Schedule:
    interval_hours: int
    start_time: str  # «ЧЧ:ММ» по часовому поясу приложения
    enabled: bool = True


def get_schedule(session: Session) -> Schedule:
    s = get_settings()
    values = {
        row.key: row.value for row in session.query(AppSetting).filter(AppSetting.key.like("schedule.%"))
    }
    try:
        interval = int(values.get("schedule.interval_hours", s.run_interval_hours))
    except ValueError:
        interval = s.run_interval_hours
    return Schedule(
        interval_hours=max(1, interval),
        start_time=values.get("schedule.start_time", s.run_start_time),
        enabled=values.get("schedule.enabled", "1") == "1",
    )


def save_schedule(session: Session, schedule: Schedule) -> None:
    parse_time(schedule.start_time)  # проверка формата
    for key, value in {
        "schedule.interval_hours": str(int(schedule.interval_hours)),
        "schedule.start_time": schedule.start_time,
        "schedule.enabled": "1" if schedule.enabled else "0",
    }.items():
        row = session.get(AppSetting, key)
        if row is None:
            session.add(AppSetting(key=key, value=value))
        else:
            row.value = value


def parse_time(value: str) -> time:
    try:
        hh, mm = value.strip().split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"время запуска «{value}» должно быть в формате ЧЧ:ММ") from exc


def first_run_at(schedule: Schedule, now: datetime | None = None) -> datetime:
    """Ближайший момент «ЧЧ:ММ» в часовом поясе приложения — точка отсчёта интервала."""
    tz = ZoneInfo(get_settings().timezone)
    now = (now or datetime.now(tz)).astimezone(tz)
    at = datetime.combine(now.date(), parse_time(schedule.start_time), tzinfo=tz)
    return at if at > now else at + timedelta(days=1)


def build_trigger(schedule: Schedule, now: datetime | None = None) -> IntervalTrigger:
    return IntervalTrigger(
        hours=schedule.interval_hours,
        start_date=first_run_at(schedule, now),
        timezone=ZoneInfo(get_settings().timezone),
    )


async def scheduled_run() -> None:
    """Задание планировщика (ссылка на функцию хранится в БД, поэтому она на уровне модуля)."""
    from app.services.jobs import launch_run

    if not launch_run("schedule"):
        log.warning("Плановый прогон пропущен: предыдущий ещё идёт")


def start_scheduler(schedule: Schedule) -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    scheduler = AsyncIOScheduler(
        jobstores={
            "default": SQLAlchemyJobStore(url=get_settings().database_url, tablename="scheduler_jobs")
        },
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 6 * 3600},
        timezone=ZoneInfo(get_settings().timezone),
    )
    scheduler.start()
    _scheduler = scheduler
    apply_schedule(schedule)
    return scheduler


def apply_schedule(schedule: Schedule) -> None:
    """Создаёт или пересоздаёт задание по настройкам расписания."""
    if _scheduler is None:
        return
    if not schedule.enabled:
        if _scheduler.get_job(JOB_ID):
            _scheduler.remove_job(JOB_ID)
        return
    existing = _scheduler.get_job(JOB_ID)
    trigger = build_trigger(schedule)
    if existing and _same(existing.trigger, trigger):
        return  # расписание не менялось — сохраняем накопленную очередь запусков
    _scheduler.add_job(scheduled_run, trigger, id=JOB_ID, name="Прогон по площадкам", replace_existing=True)
    log.info("Расписание: каждые %s ч, первый запуск %s", schedule.interval_hours, trigger.start_date)


def _same(a, b: IntervalTrigger) -> bool:
    return (
        isinstance(a, IntervalTrigger)
        and a.interval == b.interval
        and a.start_date.timetz().replace(tzinfo=None) == b.start_date.timetz().replace(tzinfo=None)
    )


def next_run_time() -> datetime | None:
    if _scheduler is None:
        return None
    job = _scheduler.get_job(JOB_ID)
    return job.next_run_time if job else None


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
