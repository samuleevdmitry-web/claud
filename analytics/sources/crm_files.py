"""Ежемесячные выгрузки из Битрикс24 и 1С (Excel), которые кладутся в папку imports.

Распознаются два вида сводок:
- forms — «лиды с сайта в Б24 против заказов в 1С»: листы «Совпадения» и «Все лиды»;
- calls — «продажи с входящих звонков»: листы «Итог», «Все входящие лиды» и «Заказы со звонков».

Колонки ищутся по названиям, поэтому порядок колонок и лишние колонки не мешают. Персональные данные
(имена, названия клиентов, тексты обращений) не читаются вовсе; почта и телефон сразу превращаются в отпечатки
(analytics.privacy) — по ним лиды сводятся с базой рассылок, а сами адреса не сохраняются.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import openpyxl

from analytics import privacy

KINDS = {
    "forms": {"Совпадения", "Все лиды"},
    "calls": {"Итог", "Все входящие лиды"},
}
KIND_NAMES = {"forms": "лиды с сайта (формы)", "calls": "входящие звонки"}
# Версия разбора: файлы, загруженные прежней версией, загружаются заново, если ещё лежат в папке.
# 3 — отпечатки почты и телефона лидов для сведения с базой рассылок.
PARSER_VERSION = 3


class CrmFileError(Exception):
    pass


@dataclass(frozen=True)
class FormLead:
    lead_id: int
    created_at: datetime | None
    stage: str
    in_1c: bool
    orders: int = 0
    amount: float = 0.0
    closed: float = 0.0
    first_order: date | None = None
    match: str | None = None
    manager_1c: str | None = None
    order_nos: tuple[str, ...] = ()  # номера заказов 1С — чтобы не считать дважды заказ, пришедший и звонком
    category: str | None = None      # new | existing, если выгрузка различает; иначе неизвестно
    email_hashes: tuple[str, ...] = ()  # отпечатки почты (analytics.privacy), сами адреса не хранятся
    phone_hash: str | None = None


@dataclass(frozen=True)
class CallLead:
    lead_id: int
    called_at: datetime | None  # в выгрузке у части звонков даты нет
    call_type: str
    stage: str
    responsible: str | None
    line: str | None
    in_1c: bool
    phone_hash: str | None = None  # отпечаток номера звонившего


@dataclass(frozen=True)
class CallOrder:
    order_no: str
    category: str  # new | existing
    order_date: date | None
    amount: float
    status_1c: str | None
    lead_id: int | None
    manager_1c: str | None


@dataclass
class CrmFile:
    kind: str
    month: date
    summary: list[tuple[str, float | None, str | None]] = field(default_factory=list)
    form_leads: list[FormLead] = field(default_factory=list)
    call_leads: list[CallLead] = field(default_factory=list)
    call_orders: list[CallOrder] = field(default_factory=list)

    @property
    def rows(self) -> int:
        return len(self.form_leads) + len(self.call_leads) + len(self.call_orders)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def detect(sheetnames: list[str]) -> str | None:
    names = set(sheetnames)
    return next((kind for kind, needed in KINDS.items() if needed <= names), None)


def parse_file(path: Path) -> CrmFile:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        kind = detect(wb.sheetnames)
        if kind is None:
            raise CrmFileError(f"не похоже ни на одну знакомую выгрузку (листы: {', '.join(wb.sheetnames)})")
        sheets = {name: list(wb[name].iter_rows(values_only=True)) for name in wb.sheetnames}
    finally:
        wb.close()
    return _parse_forms(sheets) if kind == "forms" else _parse_calls(sheets)


# ----- разбор -----

def _text(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _num(v) -> float:
    if v is None or v == "":
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    return float(str(v).replace("\xa0", "").replace(" ", "").replace(",", "."))


def _int(v) -> int | None:
    try:
        return int(_num(v)) if v not in (None, "") else None
    except ValueError:
        return None


def _dt(v) -> datetime | None:
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    s = _text(v)
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _yes(v) -> bool:
    return (_text(v) or "").lower() in ("да", "yes", "true", "1")


def _table(rows: list[tuple], first_header: str = "ID лида") -> list[dict]:
    """Строки таблицы под заголовком, первая колонка которого — first_header; до первой пустой строки."""
    start = next((i for i, r in enumerate(rows) if r and _text(r[0]) == first_header), None)
    if start is None:
        return []
    header = [_text(h) for h in rows[start]]
    out = []
    for r in rows[start + 1:]:
        if not r or all(v in (None, "") for v in r):
            break
        out.append({h: v for h, v in zip(header, r) if h})
    return out


def _summary(rows: list[tuple]) -> list[tuple[str, float | None, str | None]]:
    """Пары «показатель — число» из шапки листа, до таблицы с лидами."""
    out = []
    for r in rows:
        if r and _text(r[0]) == "ID лида":
            break
        if not r or len(r) < 2 or not isinstance(r[0], str) or not isinstance(r[1], (int, float)):
            continue
        out.append((r[0].strip(), float(r[1]), _text(r[2]) if len(r) > 2 else None))
    return out


def _category(v) -> str | None:
    """«Новая продажа…» / «Новый клиент» → new, «Действующий клиент…» → existing."""
    s = (_text(v) or "").lower()
    if s.startswith("нов"):
        return "new"
    if s.startswith("действ") or s.startswith("постоян"):
        return "existing"
    return None


def _month(dates: list[datetime]) -> date:
    if not dates:
        raise CrmFileError("в файле нет ни одного лида с датой")
    year, month = Counter((d.year, d.month) for d in dates).most_common(1)[0][0]
    return date(year, month, 1)


def _parse_forms(sheets: dict[str, list[tuple]]) -> CrmFile:
    matched = {}
    for r in _table(sheets["Совпадения"]):
        lead_id = _int(r.get("ID лида"))
        if lead_id is None:
            continue
        first = _dt(r.get("Первый заказ"))
        matched[lead_id] = {
            "orders": _int(r.get("Заказов после лида")) or 0,
            "amount": _num(r.get("Сумма заказов, ₽")),
            "closed": _num(r.get("Закрыт / к закрытию, ₽")),
            "first_order": first.date() if first else None,
            "match": (_text(r.get("Надёжность")) or "").split(" ")[0] or None,
            "manager_1c": _text(r.get("Менеджер 1С")),
            "order_nos": tuple(n.strip() for n in (_text(r.get("Номера заказов 1С")) or "").replace(";", ",").split(",")
                               if n.strip()),
            "category": _category(r.get("Категория") or r.get("Тип клиента")),
        }
    leads = []
    for r in _table(sheets["Все лиды"]):
        lead_id, created = _int(r.get("ID лида")), _dt(r.get("Дата лида"))
        if lead_id is None:
            continue
        extra = matched.get(lead_id, {})
        leads.append(FormLead(lead_id, created, _text(r.get("Стадия лида")) or "—",
                              _yes(r.get("Найден в 1С")) or lead_id in matched,
                              extra.get("orders", 0), extra.get("amount", _num(r.get("Сумма заказов в 1С"))),
                              extra.get("closed", 0.0), extra.get("first_order"), extra.get("match"),
                              extra.get("manager_1c"), extra.get("order_nos", ()), extra.get("category"),
                              tuple(privacy.email_hashes(r.get("Email"))), privacy.phone_hash(r.get("Телефон"))))
    return CrmFile("forms", _month([lead.created_at for lead in leads if lead.created_at]), _summary(sheets["Совпадения"]),
                   form_leads=leads)


def _parse_calls(sheets: dict[str, list[tuple]]) -> CrmFile:
    leads = []
    for r in _table(sheets["Все входящие лиды"]):
        lead_id, called = _int(r.get("ID лида")), _dt(r.get("Дата звонка"))
        if lead_id is None:
            continue
        leads.append(CallLead(lead_id, called, _text(r.get("Тип звонка")) or "—", _text(r.get("Стадия лида")) or "—",
                              _text(r.get("Ответственный Б24")), _text(r.get("Линия (куда звонили)")),
                              _yes(r.get("Найден в 1С")), privacy.phone_hash(r.get("Телефон"))))
    orders = []
    for r in _table(sheets.get("Заказы со звонков", []), "Категория"):
        order_no = _text(r.get("Номер заказа"))
        if not order_no:
            continue  # строка «Итого»
        when = _dt(r.get("Дата заказа"))
        category = _category(r.get("Категория")) or "existing"
        orders.append(CallOrder(order_no, category, when.date() if when else None, _num(r.get("Сумма, ₽")),
                                _text(r.get("Статус 1С")), _int(r.get("ID лида")), _text(r.get("Менеджер 1С"))))
    return CrmFile("calls", _month([lead.called_at for lead in leads if lead.called_at]), _summary(sheets["Итог"]),
                   call_leads=leads, call_orders=orders)
