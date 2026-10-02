from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")


def as_list(value: Any) -> list:
    """В JSON из XML одиночный элемент приходит объектом, несколько — списком."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def dig(data: Any, *path: str, default: Any = None) -> Any:
    cur = data
    for key in path:
        if isinstance(cur, list):
            cur = cur[0] if cur else None
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def parse_dt(value: Any, default_tz: timezone | ZoneInfo = MSK) -> datetime | None:
    """ISO-дата/время; без зоны считаем московским временем."""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=default_tz)
    return dt.astimezone(UTC)


def to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace(" ", "").replace(",", "."))
    except InvalidOperation:
        return None


def to_float(value: Any) -> float | None:
    d = to_decimal(value)
    return float(d) if d is not None else None


def day_start_msk(day) -> str:
    return datetime(day.year, day.month, day.day, tzinfo=MSK).isoformat()


def utcnow() -> datetime:
    return datetime.now(UTC)


def is_future(dt: datetime | None, slack: timedelta = timedelta(0)) -> bool | None:
    if dt is None:
        return None
    return dt + slack > utcnow()


def aware(dt: datetime | None) -> datetime | None:
    """SQLite возвращает время без зоны; в БД всё хранится в UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
