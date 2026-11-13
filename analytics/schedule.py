"""Когда запускать сбор: решение для цикла `python -m analytics schedule` (Docker-контейнер collector)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Moscow")
RUN_AFTER_HOUR = 6  # по Москве: к этому времени Метрика закрыла вчерашний день
RETRY_AFTER = timedelta(hours=1)  # после ошибки источник не дёргаем чаще раза в час
HISTORY_START = date(2025, 1, 1)  # с чего грузить историю в пустую базу


def last_due(now: datetime, run_hour: int, weekday: int | None = None, monthday: int | None = None) -> datetime:
    """Последний срок загрузки не позже now: каждый день, по дню недели (0 — понедельник) или по числу месяца."""
    due = now.replace(hour=run_hour, minute=0, second=0, microsecond=0)
    if monthday is not None:
        due = due.replace(day=monthday)
        if due > now:  # в этом месяце срок ещё не наступил — последний был в прошлом
            due = (due.replace(day=1) - timedelta(days=1)).replace(day=monthday)
    elif weekday is not None:
        due -= timedelta(days=(now.weekday() - weekday) % 7)
        if due > now:
            due -= timedelta(days=7)
    elif due > now:
        due -= timedelta(days=1)
    return due


def due_sources(
    now: datetime,
    runs: dict[str, dict],
    sources: list[str],
    default_days: dict[str, int],
    run_hour: int = RUN_AFTER_HOUR,
    monthly_on: dict[str, int] | None = None,
    weekly_on: dict[str, int] | None = None,
) -> list[tuple[str, date]]:
    """Какие источники загрузить сейчас и с какой даты.

    runs: источник → {"last_ok": время последней успешной загрузки, "last_attempt": время последнего запуска}.
    Источник без успешных загрузок получает всю историю. Если ноутбук был выключен дольше обычного
    окна перезагрузки, период расширяется до дня последней успешной загрузки, чтобы не было дыр.
    weekly_on: источник → день недели (0 — понедельник), если его загружают раз в неделю;
    monthly_on: источник → число месяца, если раз в месяц (Вордстат — 5-го). Остальные — каждый день.
    Если в срок ноутбук был выключен, загрузка пройдёт при первой проверке после него.
    """
    today = now.date()
    out = []
    for source in sources:
        due_at = last_due(now, run_hour, (weekly_on or {}).get(source), (monthly_on or {}).get(source))
        last_ok = runs.get(source, {}).get("last_ok")
        last_attempt = runs.get(source, {}).get("last_attempt")
        failed_recently = (
            last_attempt is not None
            and now - last_attempt < RETRY_AFTER
            and (last_ok is None or last_ok < last_attempt)
        )
        if failed_recently:
            continue
        if last_ok is None:
            out.append((source, HISTORY_START))
        elif last_ok < due_at:
            out.append((source, catch_up_since(today, last_ok, default_days[source])))
    return out


def catch_up_since(today: date, last_ok: datetime, days: int) -> date:
    """С какой даты перезагружать: обычное окно или, если загрузок давно не было, — с дня последней."""
    return min(today - timedelta(days=days), last_ok.astimezone(TZ).date() - timedelta(days=1))


def month_close_sources(now: datetime, runs: dict[str, dict], sources: list[str], default_days: dict[str, int],
                        run_hour: int = RUN_AFTER_HOUR) -> list[tuple[str, date]]:
    """Источники, не загружавшиеся с закрытия прошлого месяца (1-го числа после run_hour).

    При загрузке раз в неделю последние дни месяца могут ещё не попасть в базу к дню отчёта —
    перед отчётом такие источники догружаются.
    """
    closed = now.replace(day=1, hour=run_hour, minute=0, second=0, microsecond=0)
    out = []
    for source in sources:
        last_ok = runs.get(source, {}).get("last_ok")
        if last_ok is not None and last_ok < closed:
            out.append((source, catch_up_since(now.date(), last_ok, default_days[source])))
    return out
