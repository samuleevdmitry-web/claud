"""Форматирование чисел по-русски и плитки показателей."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

NBSP = " "
MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь",
          "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]

Number = int | float | Decimal | None


def month_name(d: date) -> str:
    return f"{MONTHS[d.month - 1].capitalize()} {d.year}"


def month_short(d: date) -> str:
    return f"{MONTHS_SHORT[d.month - 1]} {d.year}"


def fmt_int(v: Number) -> str:
    if v is None:
        return "—"
    return f"{round(float(v)):,}".replace(",", NBSP)


def fmt_dec(v: Number, digits: int = 1) -> str:
    if v is None:
        return "—"
    return f"{float(v):,.{digits}f}".replace(",", NBSP).replace(".", ",")


def fmt_pct(v: Number, digits: int = 1) -> str:
    return "—" if v is None else f"{fmt_dec(v, digits)}%"


def fmt_duration(seconds: Number) -> str:
    if seconds is None:
        return "—"
    minutes, sec = divmod(round(float(seconds)), 60)
    return f"{minutes}{NBSP}мин {sec}{NBSP}с" if minutes else f"{sec}{NBSP}с"


def plural(n: Number, forms: tuple[str, str, str]) -> str:
    """Число со словом в нужной форме: plural(21, ("визит", "визита", "визитов")) → «21 визит»."""
    value = abs(round(float(n or 0)))
    if value % 10 == 1 and value % 100 != 11:
        form = forms[0]
    elif 2 <= value % 10 <= 4 and not 12 <= value % 100 <= 14:
        form = forms[1]
    else:
        form = forms[2]
    return f"{fmt_int(n)}{NBSP}{form}"


def fmt_change(cur: Number, prev: Number) -> str:
    """Относительное изменение со знаком для таблиц: «+18,6%», «−62,5%»."""
    if cur is None or not prev:
        return "—"
    change = (float(cur) - float(prev)) / float(prev) * 100
    sign = "+" if change > 0 else "−" if change < 0 else ""
    return f"{sign}{fmt_dec(abs(change))}%"


@dataclass(frozen=True)
class Delta:
    text: str
    tone: str  # good | bad | neutral — цвет; направление стрелки — по знаку


def delta(cur: Number, prev: Number, kind: str, good: str = "up") -> Delta | None:
    """Изменение к прошлому периоду.

    kind: pct — относительное (для количеств), pp — в процентных пунктах (для долей),
    abs — в единицах (для позиций, глубины). good: 'up' или 'down' — какое направление хорошее.
    """
    if cur is None or prev is None:
        return None
    cur, prev = float(cur), float(prev)
    if kind == "pct":
        if prev == 0:
            return None
        change = (cur - prev) / prev * 100
        text = f"{fmt_dec(abs(change))}%"
    elif kind == "pp":
        change = cur - prev
        text = f"{fmt_dec(abs(change))}{NBSP}п.п."
    else:
        change = cur - prev
        text = fmt_dec(abs(change), 2 if abs(change) < 1 else 1)
    if round(change, 1) == 0:
        return Delta("без изменений", "neutral")
    arrow = "▲" if change > 0 else "▼"
    tone = "good" if (change > 0) == (good == "up") else "bad"
    return Delta(f"{arrow}{NBSP}{text}", tone)


@dataclass(frozen=True)
class Tile:
    label: str
    value: str
    delta: Delta | None = None
    note: str = ""  # «июль: 17 175 · авг 2025: 98»
