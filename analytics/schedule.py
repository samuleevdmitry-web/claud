"""Когда запускать сбор: решение для ежедневного цикла `python -m analytics schedule`."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Moscow")
RUN_AFTER_HOUR = 6  # по Москве: к этому времени Метрика закрыла вчерашний день
RETRY_AFTER = timedelta(hours=1)  # после ошибки источник не дёргаем чаще раза в час
HISTORY_START = date(2025, 1, 1)  # с чего грузить историю в пустую базу


def due_sources(
    now: datetime,
    runs: dict[str, dict],
    sources: list[str],
    default_days: dict[str, int],
    run_hour: int = RUN_AFTER_HOUR,
) -> list[tuple[str, date]]:
    """Какие источники загрузить сейчас и с какой даты.

    runs: источник → {"last_ok": время последней успешной загрузки, "last_attempt": время последнего запуска}.
    Источник без успешных загрузок получает всю историю. Если ноутбук был выключен дольше обычного
    окна перезагрузки, период расширяется до дня последней успешной загрузки, чтобы не было дыр.
    """
    due_at = now.replace(hour=run_hour, minute=0, second=0, microsecond=0)
    today = now.date()
    out = []
    for source in sources:
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
        elif now >= due_at and last_ok < due_at:
            since = today - timedelta(days=default_days[source])
            out.append((source, min(since, last_ok.astimezone(TZ).date() - timedelta(days=1))))
    return out
