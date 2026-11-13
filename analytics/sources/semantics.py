"""Семантическое ядро из Google Таблицы (или её выгрузки в Excel).

Узнаётся по листам, у которых первая строка начинается с «Фраза», «Частотность…»; дальше — колонки-даты
с позициями на эту дату («--» — нет в выдаче). Каждый такой лист — поисковая система: «Яндекс», «Google».
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import openpyxl
import requests

ENGINES = {"яндекс": "yandex", "yandex": "yandex", "google": "google", "гугл": "google"}
SHEET_ID = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")


class SemanticsError(Exception):
    pass


@dataclass
class Semantics:
    keywords: dict[str, tuple[str, int | None]] = field(default_factory=dict)  # нормализованная → (фраза, частотность)
    positions: list[tuple[str, str, date, int | None]] = field(default_factory=list)  # (норм., система, дата, позиция)

    @property
    def rows(self) -> int:
        return len(self.keywords)


def normalize(phrase: str) -> str:
    """Как сравниваются фразы таблицы и запросы Вебмастера: регистр, «ё» и лишние пробелы не важны."""
    return re.sub(r"\s+", " ", phrase.lower().replace("ё", "е")).strip()


def _header(row) -> bool:
    return bool(row) and str(row[0] or "").strip().lower() == "фраза" and "частот" in str(row[1] or "").lower()


def is_semantics(sheets: dict[str, list[tuple]]) -> bool:
    return any(rows and _header(rows[0]) for rows in sheets.values())


def parse_sheets(sheets: dict[str, list[tuple]]) -> Semantics:
    out = Semantics()
    for title, rows in sheets.items():
        if not rows or not _header(rows[0]):
            continue
        engine = ENGINES.get(title.strip().lower(), title.strip().lower())
        dates = {}
        for i, h in enumerate(rows[0][2:], start=2):
            when = h if isinstance(h, (date, datetime)) else _date(h)
            if when:
                dates[i] = when.date() if isinstance(when, datetime) else when
        seen: set[str] = set()
        for r in rows[1:]:
            phrase = str(r[0] or "").strip() if r else ""
            if not phrase:
                continue
            norm = normalize(phrase)
            if norm in seen:
                continue  # та же фраза ещё раз (другой регистр, пробелы) — берётся первая строка
            seen.add(norm)
            freq = r[1] if len(r) > 1 and isinstance(r[1], (int, float)) else None
            if norm not in out.keywords or (freq is not None and out.keywords[norm][1] is None):
                out.keywords[norm] = (phrase, int(freq) if freq is not None else None)
            for i, when in dates.items():
                v = r[i] if i < len(r) else None
                out.positions.append((norm, engine, when, int(v) if isinstance(v, (int, float)) else None))
    if not out.keywords:
        raise SemanticsError("в таблице нет листа с колонками «Фраза», «Частотность»")
    return out


def _date(v) -> date | None:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def read_workbook(path: Path) -> dict[str, list[tuple]]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return {name: list(wb[name].iter_rows(values_only=True)) for name in wb.sheetnames}
    finally:
        wb.close()


def export_url(sheet_url: str) -> str:
    """Ссылка на Google Таблицу → адрес её выгрузки в Excel."""
    m = SHEET_ID.search(sheet_url)
    if not m:
        raise SemanticsError("SEMANTICS_URL не похож на ссылку на Google Таблицу")
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=xlsx"


def download(sheet_url: str, target: Path, session: requests.Session | None = None) -> Path:
    """Скачивает таблицу в Excel. Работает, если у таблицы открыт доступ по ссылке (просмотр)."""
    resp = (session or requests).get(export_url(sheet_url), timeout=60, allow_redirects=True)
    if resp.status_code != 200 or resp.content[:2] != b"PK":
        raise SemanticsError("Google не отдал таблицу: откройте доступ «Все, у кого есть ссылка — читатель» "
                             "или кладите выгрузку таблицы (Excel) в папку imports")
    target.write_bytes(resp.content)
    return target
