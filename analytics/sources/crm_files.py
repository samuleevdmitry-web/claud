"""Ежемесячные выгрузки из Битрикс24 и 1С (Excel, .xlsx или .xls), которые кладутся в папку imports.

Распознаются два вида сводок:
- forms — «лиды с сайта в Б24 против заказов в 1С»: листы «Совпадения» и «Все лиды»;
- calls — «продажи с входящих звонков»: листы «Итог», «Все входящие лиды» и «Заказы со звонков».

- orders — выгрузка всех заказов 1С (любые листы): узнаётся по колонкам «номер заказа», «дата», «сумма».

Колонки ищутся по названиям, поэтому порядок колонок и лишние колонки не мешают. Персональные данные
(имена, названия клиентов, тексты обращений) не читаются вовсе; почта и телефон сразу превращаются в отпечатки
(analytics.privacy) — по ним лиды сводятся с базой рассылок, а сами адреса не сохраняются.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from analytics import privacy
from analytics.sources.excel import read_sheets

KINDS = {
    "forms": {"Совпадения", "Все лиды"},
    "calls": {"Итог", "Все входящие лиды"},
}
KIND_NAMES = {"forms": "лиды с сайта (формы)", "calls": "входящие звонки", "orders": "заказы 1С",
              "mailing_items": "позиции рассылок", "staff": "сотрудники", "staff_hidden": "сотрудники",
              "companies": "справочник компаний Б24"}
# Версия разбора: файлы, загруженные прежней версией, загружаются заново, если ещё лежат в папке.
# 3 — отпечатки почты и телефона лидов для сведения с базой рассылок.
# 4 — выгрузка всех заказов 1С; нераспознанные раньше файлы разбираются заново.
# 5 — итог разбора каждого файла сохраняется для страницы «Данные»: файлы читаются заново один раз.
# 6 — колонки узнаются по смыслу («Заказ 1С», «Номенклатура 1С»…), все листы книги, позиции заказов и рассылок.
# 7 — список сотрудников («Сотрудник», «E-Mail»): нераспознанный раньше файл пользователей читается заново.
# 8 — справочник компаний Б24 (HTML-«xls», CSV); у заказов — ключ названия компании и ИНН для сверки с ним.
# 9 — «Номенклатура из рассылки» — товар, а не рассылка; позиции заказа — с листа с суммой позиции.
PARSER_VERSION = 9


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


@dataclass(frozen=True)
class Order:
    """Заказ 1С из выгрузки всех заказов. Клиент — отпечатком названия, контакты — отпечатками."""
    order_no: str
    order_date: date
    amount: float
    status: str | None = None
    client_hash: str | None = None
    email_hashes: tuple[str, ...] = ()
    phone_hashes: tuple[str, ...] = ()
    items: tuple[str, ...] = ()   # номенклатура
    groups: tuple[str, ...] = ()  # группы номенклатуры
    # Сами название клиента, адреса и номера — в базу попадают только при EMAIL_LISTS=on (списки клиентов).
    client_name: str | None = None
    emails: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()
    company_key: str | None = None  # отпечаток названия без формы собственности — для сверки с компаниями Б24
    inn_hash: str | None = None


@dataclass
class CrmFile:
    kind: str
    month: date
    summary: list[tuple[str, float | None, str | None]] = field(default_factory=list)
    form_leads: list[FormLead] = field(default_factory=list)
    call_leads: list[CallLead] = field(default_factory=list)
    call_orders: list[CallOrder] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # чего не хватает в выгрузке
    columns: dict[str, str] = field(default_factory=dict)  # что из выгрузки заказов взято: поле → название колонки
    lines: int = 0  # строк с заказами (у построчной выгрузки больше, чем заказов)
    order_lines: list = field(default_factory=list)    # OrderLine: позиции заказов
    mailing_items: list = field(default_factory=list)  # MailingItem: позиции рассылок
    sheets_info: list[str] = field(default_factory=list)  # что нашлось на каждом листе
    staff: list = field(default_factory=list)  # StaffMember: сотрудники из файла пользователей
    companies: list = field(default_factory=list)  # Company: справочник компаний (контактов, лидов) Б24
    totals: bool = True  # суммы заказов целиком (список заказов), а не только части позиций

    @property
    def rows(self) -> int:
        return (len(self.form_leads) + len(self.call_leads) + len(self.call_orders) + len(self.orders)
                + len(self.order_lines) + len(self.mailing_items) + len(self.staff) + len(self.companies))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def detect(sheetnames: list[str]) -> str | None:
    names = set(sheetnames)
    return next((kind for kind, needed in KINDS.items() if needed <= names), None)


def parse_file(path: Path, sheets: dict[str, list[tuple]] | None = None) -> CrmFile:
    sheets = sheets if sheets is not None else read_sheets(path)
    kind = detect(list(sheets))
    if kind == "forms":
        return _parse_forms(sheets)
    if kind == "calls":
        return _parse_calls(sheets)
    parsed = _parse_orders_workbook(sheets)
    if parsed:
        return parsed
    raise CrmFileError("не похоже ни на одну знакомую выгрузку. Листы и заголовки: " + "; ".join(
        f"«{name}»: {', '.join(headers_preview(rows)) or 'пусто'}" for name, rows in sheets.items()))


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


def _month(dates: list[date]) -> date:
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


# ----- выгрузка всех заказов 1С и позиции рассылок -----

def _head(v) -> str:
    """Название колонки в едином виде: строчные, ё→е, без «, ₽» и лишних пробелов."""
    s = (_text(v) or "").lower().replace("ё", "е").replace("\n", " ")
    for tail in (", ₽", ", руб.", ", руб", "(руб.)", "(руб)", ", rub", "₽"):
        s = s.replace(tail, "")
    return " ".join(s.split()).strip(" :,.")


# Точные названия колонок; остальные узнаются по смыслу в _column_kind («Заказ 1С», «Номенклатура 1С»,
# «Клиент / контрагент», «Сумма позиции», «Дата рассылки»…).
ORDER_COLUMNS = {
    "order_no": ("номер заказа", "номер", "заказ", "заказ клиента", "номер документа", "документ", "№", "№ заказа"),
    "date": ("дата заказа", "дата", "дата документа", "дата создания", "создан", "дата создания заказа"),
    "amount": ("сумма заказа", "сумма документа", "сумма", "итого", "сумма с ндс", "сумма заказа с ндс"),
    "line_amount": ("сумма строки", "сумма по строке", "сумма позиции", "стоимость", "сумма товара"),
    "status": ("статус", "статус заказа", "статус 1с", "состояние", "состояние заказа", "текущее состояние"),
    "client": ("клиент", "клиент 1с", "контрагент", "покупатель", "партнер", "заказчик", "клиент (контрагент)"),
    "item": ("номенклатура", "товар", "наименование", "наименование товара", "позиция", "товары"),
    "group": ("группа номенклатуры", "группа", "товарная группа", "группа товаров", "вид номенклатуры",
              "категория товара", "категория номенклатуры", "раздел"),
}
_EXACT = {name: key for key, names in ORDER_COLUMNS.items() for name in names}
COLUMN_NAMES = {"order_no": "номер", "date": "дата", "amount": "сумма", "line_amount": "сумма строки",
                "status": "статус", "client": "клиент", "email": "почта", "phone": "телефон", "item": "номенклатура",
                "group": "группа", "color": "цвет", "mailing": "рассылка", "mailing_date": "дата рассылки",
                "person": "сотрудник", "company": "компания", "inn": "ИНН"}


def _column_kind(h: str) -> str | None:
    """Что за колонка — по названию. Порядок проверок важен: «Дата рассылки» — не дата заказа,
    «Количество для рассылки» и «Когда оформлен заказ» — ни то ни другое."""
    if not h:
        return None
    if "mail" in h or "почт" in h:
        return "email"
    if "телефон" in h:
        return "phone"
    if "рассыл" in h and h.startswith(("номенклатур", "товар", "позици", "наименование")):
        return "item"  # «Номенклатура из рассылки» — товар, а не название рассылки
    if "рассыл" in h:
        if "дата" in h:
            return "mailing_date"
        if any(w in h for w in ("цена", "колич", "сумм", "после", "источник", "через")):
            return None
        return "mailing"
    if h.startswith(("сотрудник", "пользователь")):
        return "person"
    if h == "инн" or h.startswith(("инн ", "инн/")):
        return "inn"
    if h in COMPANY_HEADERS or h.startswith(("название компании", "наименование компании", "наименование организации")):
        return "company"
    if h in _EXACT:
        return _EXACT[h]
    if h.startswith(("когда", "через", "количество", "цена")) or "после" in h:
        return None
    if "дата" in h:
        return "date" if "заказ" in h else None
    if "групп" in h:
        return "group"
    if h.startswith(("номенклатур", "наименование", "товар")):
        return "item"
    if h.startswith(("заказ ", "номер заказа", "№ заказа", "номер документа")) and not h.startswith("заказов"):
        return "order_no"
    if h.startswith(("клиент", "контрагент", "покупатель", "заказчик")):  # «Партнер» — только точным названием
        return "client"
    if "статус" in h or "состояни" in h:
        return "status"
    if h.startswith(("сумма", "стоимость")):
        return "line_amount" if h.startswith("стоимость") or any(w in h for w in ("позиц", "строк", "товар")) else "amount"
    if h.startswith("цвет"):
        return "color"
    return None


# Название компании в справочниках Б24 (компании, контакты, лиды); «Клиент»/«Контрагент» в заказах — это client.
COMPANY_HEADERS = ("название компании", "компания", "наименование компании", "наименование организации",
                   "организация", "название")


def _match_columns(header: list[str]) -> dict[str, int]:
    """Поле → номер колонки (первая подходящая). Почта и телефон — все такие колонки: email3, phone7…"""
    cols: dict[str, int] = {}
    for i, h in enumerate(header):
        kind = _column_kind(h)
        if kind in ("email", "phone"):
            cols.setdefault(kind, i)
            cols[f"{kind}{i}"] = i
        elif kind:
            cols.setdefault(kind, i)
    return cols


_ORDER_DATE = re.compile(r"от\s+(\d{2}\.\d{2}\.\d{4})")
_ORDER_IN_NAME = re.compile(r"(\S+)\s+от\s+\d{2}\.\d{2}\.\d{4}")


def _sheet_kind(rows: list[tuple]) -> tuple[str, int, dict[str, int]] | None:
    """Что на листе: заказы (номер заказа и дата — своей колонкой или в названии документа) или позиции рассылок
    (рассылка и номенклатура, без заказов). Заголовки ищутся в первых 30 строках."""
    for i, row in enumerate(rows[:30]):
        cols = _match_columns([_head(v) for v in row or ()])
        if "order_no" in cols and cols.keys() & {"amount", "line_amount", "item"}:
            col = cols["order_no"]
            if "date" in cols or any(_ORDER_DATE.search(_text(r[col]) or "") for r in rows[i + 1:i + 6]
                                     if r and col < len(r)):
                return "orders", i, cols
        if "mailing" in cols and "item" in cols and "order_no" not in cols:
            return "mailing_items", i, cols
        if "person" in cols and "email" in cols and not cols.keys() & {"order_no", "mailing"}:
            return "staff", i, cols
        if "company" in cols and cols.keys() & {"email", "phone"} and not cols.keys() & {"order_no", "mailing", "person"}:
            return "companies", i, cols
    return None


def find_orders_sheet(sheets: dict[str, list[tuple]]) -> tuple[str, int, dict[str, int]] | None:
    """Первый лист с заказами (для совместимости)."""
    for name, rows in sheets.items():
        found = _sheet_kind(rows)
        if found and found[0] == "orders":
            return name, found[1], found[2]
    return None


def headers_preview(rows: list[tuple], limit: int = 25) -> list[str]:
    """Для сообщения «файл не распознан»: строка, похожая на заголовки (≥3 ячеек-текстов без цифр), — без данных."""
    for row in rows[:30]:
        cells = [_text(v) for v in row or ()]
        texts = [c for c in cells if c and not any(ch.isdigit() for ch in c) and "@" not in c]
        if len(texts) >= 3:
            return [c[:40] for c in texts[:limit]]
    return []


def _order_no(v) -> str | None:
    """«Заказ клиента АТЦБ-026330 от 05.10.2026 12:00:00» → «АТЦБ-026330»; просто номер — как есть."""
    if isinstance(v, (int, float)):
        return str(int(v))
    s = _text(v)
    if not s:
        return None
    m = _ORDER_IN_NAME.search(s)
    return (m.group(1).lstrip("№") or None) if m else s


def item_norm(name: str | None) -> str:
    """Номенклатура в едином виде для сравнения позиций рассылки и заказа."""
    s = (name or "").lower().replace("ё", "е").replace("\xa0", " ")
    return " ".join(s.split())


@dataclass(frozen=True)
class OrderLine:
    order_no: str
    item: str
    color: str | None
    amount: float


@dataclass(frozen=True)
class StaffMember:
    """Сотрудник из файла пользователей: отпечатки адреса и телефона; имя и адрес хранятся только при EMAIL_LISTS."""
    name: str
    email: str | None
    email_hash: str | None
    phone_hash: str | None


def _parse_staff_sheet(rows: list[tuple], header_row: int, cols: dict[str, int]) -> list[StaffMember]:
    email_cols = [i for k, i in cols.items() if k.startswith("email") and k != "email"]
    phone_cols = [i for k, i in cols.items() if k.startswith("phone") and k != "phone"]
    out = []
    for row in rows[header_row + 1:]:
        if not row:
            continue
        name = _text(row[cols["person"]]) if cols["person"] < len(row) else None
        emails = [e for i in email_cols if i < len(row) for e in privacy.emails(row[i])]
        phones = [h for i in phone_cols if i < len(row) for h in privacy.phone_hashes(row[i])]
        if name and (emails or phones):
            out.append(StaffMember(name[:200], emails[0] if emails else None,
                                   privacy.email_hash(emails[0]) if emails else None, phones[0] if phones else None))
    return out


@dataclass(frozen=True)
class MailingItem:
    """Позиция рассылки: какой товар был в какой рассылке (тема рассылки — как её назвали в файле)."""
    mailing: str
    mailing_date: date | None
    item: str


def _names(rows: list[tuple], header_row: int, cols: dict[str, int]) -> dict[str, str]:
    header = rows[header_row]
    return {k: _text(header[i]) for k, i in cols.items() if not k[-1].isdigit() and i < len(header) and _text(header[i])}


def _parse_order_sheet(rows: list[tuple], header_row: int, cols: dict[str, int]) -> dict:
    """Строка листа — заказ или позиция заказа; строки одного заказа собираются вместе. Если на листе есть рассылка
    и дата рассылки (лист совпадений), из него берутся и позиции рассылок. Контакты и клиент — сразу отпечатками."""
    def cell(row, key):
        i = cols.get(key)
        return row[i] if i is not None and i < len(row) else None

    email_cols = [i for k, i in cols.items() if k.startswith("email") and k != "email"]
    phone_cols = [i for k, i in cols.items() if k.startswith("phone") and k != "phone"]
    acc: dict[str, dict] = {}
    lines: list[OrderLine] = []
    mailing_items: set[MailingItem] = set()
    count = 0
    for row in rows[header_row + 1:]:
        if not row or all(v in (None, "") for v in row):
            continue
        no = _order_no(cell(row, "order_no"))
        when = _dt(cell(row, "date"))
        if when is None:
            m = _ORDER_DATE.search(_text(cell(row, "order_no")) or "")
            when = _dt(m.group(1)) if m else None
        if not no or when is None or (_text(no) or "").lower().startswith("итог"):
            continue  # строка «Итого», подзаголовок или пустая
        count += 1
        o = acc.setdefault(no, {"date": when.date(), "amounts": [], "lines": 0.0, "status": None, "client": None,
                                "emails": [], "phones": [], "items": [], "groups": [], "name": None,
                                "plain_emails": [], "plain_phones": [], "inn": None})
        line_value = 0.0
        for key in ("amount", "line_amount"):
            if key in cols:
                try:
                    value = _num(cell(row, key))
                except ValueError:
                    value = 0.0
                if key == "amount":
                    o["amounts"].append(value)
                else:
                    o["lines"] += value
                    line_value = value
        o["status"] = _text(cell(row, "status")) or o["status"]
        o["client"] = o["client"] or privacy.fingerprint("client", _client_key(cell(row, "client")))
        o["name"] = o["name"] or (_text(cell(row, "client")) or "")[:200] or None
        o["inn"] = o["inn"] or inn_hash(cell(row, "inn"))
        for i in email_cols:
            value = row[i] if i < len(row) else None
            o["emails"] += [h for h in privacy.email_hashes(value) if h not in o["emails"]]
            o["plain_emails"] += [e for e in privacy.emails(value) if e not in o["plain_emails"]]
        for i in phone_cols:
            value = row[i] if i < len(row) else None
            o["phones"] += [h for h in privacy.phone_hashes(value) if h not in o["phones"]]
            o["plain_phones"] += [n for n in privacy.phones(value) if n not in o["plain_phones"]]
        for key, out in (("item", "items"), ("group", "groups")):
            t = _text(cell(row, key))
            if t and t not in o[out]:
                o[out].append(t[:200])
        item = _text(cell(row, "item"))
        if item:
            if "line_amount" not in cols and "amount" in cols:
                try:
                    line_value = _num(cell(row, "amount"))
                except ValueError:
                    line_value = 0.0
            lines.append(OrderLine(no, item[:300], _text(cell(row, "color")), line_value))
            mailing = _text(cell(row, "mailing"))
            if mailing:
                sent = _dt(cell(row, "mailing_date"))
                mailing_items.add(MailingItem(mailing[:200], sent.date() if sent else None, item[:300]))

    def total(o) -> float:
        amounts = o["amounts"]
        if not amounts:
            return o["lines"]
        if "line_amount" in cols:
            return max(amounts) or o["lines"]
        # одна колонка «Сумма» при построчной выгрузке: разные значения в строках заказа — это суммы строк,
        # одинаковые — сумма заказа, повторённая в каждой строке
        return sum(amounts) if "item" in cols and len(set(amounts)) > 1 else max(amounts)

    orders = {no: Order(no, o["date"], total(o), o["status"], o["client"], tuple(o["emails"]), tuple(o["phones"]),
                        tuple(o["items"]), tuple(o["groups"]), o["name"], tuple(o["plain_emails"]),
                        tuple(o["plain_phones"]), company_key(o["name"]), o["inn"])
              for no, o in acc.items()}
    return {"orders": orders, "lines": lines, "mailing_items": mailing_items, "count": count,
            "totals": "item" not in cols, "columns": _names(rows, header_row, cols), "cols": cols}


def _parse_mailing_sheet(rows: list[tuple], header_row: int, cols: dict[str, int]) -> set[MailingItem]:
    def get(row, key):
        return row[cols[key]] if key in cols and cols[key] < len(row) else None

    out = set()
    for row in rows[header_row + 1:]:
        if not row:
            continue
        mailing, item = _text(get(row, "mailing")), _text(get(row, "item"))
        if mailing and item:
            sent = _dt(get(row, "mailing_date"))
            out.add(MailingItem(mailing[:200], sent.date() if sent else None, item[:300]))
    return out


def _parse_orders_workbook(sheets: dict[str, list[tuple]]) -> CrmFile | None:
    """Все листы книги: заказы (список заказов, заказы по позициям, совпадения с рассылками) и позиции рассылок.
    Заказы с разных листов сводятся по номеру; позиции заказа берутся с листа, где их у этого заказа больше всего."""
    parts, items, info, staff, companies = [], set(), [], [], []
    for name, rows in sheets.items():
        found = _sheet_kind(rows)
        if not found:
            info.append(f"лист «{name}»: не распознан (колонки: {', '.join(headers_preview(rows)) or 'нет'})")
            continue
        kind, header_row, cols = found
        names = _names(rows, header_row, cols)
        cols_text = ", ".join(f"{COLUMN_NAMES.get(k, k)} ← «{v}»" for k, v in names.items())
        if kind == "staff":
            got = _parse_staff_sheet(rows, header_row, cols)
            staff += got
            info.append(f"лист «{name}»: сотрудники — {len(got)}; колонки: {cols_text}")
            continue
        if kind == "companies":
            got = _parse_company_sheet(rows, header_row, cols)
            companies += got
            what = "лиды Б24 (контакты)" if any("лид" in _head(v) for v in rows[header_row]) else "компании"
            info.append(f"лист «{name}»: {what} — {len(got)} (с почтой {sum(1 for c in got if c.email_hashes)}, "
                        f"с телефоном {sum(1 for c in got if c.phone_hashes)}); колонки: {cols_text}")
            continue
        if kind == "mailing_items":
            got = _parse_mailing_sheet(rows, header_row, cols)
            items |= got
            info.append(f"лист «{name}»: позиции рассылок — {len(got)} позиций в "
                        f"{len({x.mailing for x in got})} рассылках; колонки: {cols_text}")
            continue
        part = _parse_order_sheet(rows, header_row, cols)
        if not part["orders"]:
            info.append(f"лист «{name}»: заказов с номером и датой нет; колонки: {cols_text}")
            continue
        parts.append(part)
        items |= part["mailing_items"]
        extra = f", позиций рассылок {len(part['mailing_items'])}" if part["mailing_items"] else ""
        info.append(f"лист «{name}»: заказы — {len(part['orders'])} заказов ({part['count']} строк, с номенклатурой "
                    f"{len(part['lines'])}){extra}; колонки: {cols_text}")
    if not parts and not items:
        if staff:
            return CrmFile("staff", date(1970, 1, 1), staff=staff, sheets_info=info)  # список без месяца
        if companies:
            return CrmFile("companies", date(1970, 1, 1), companies=companies, sheets_info=info)  # справочник
        return None
    orders: dict[str, Order] = {}
    for part in sorted(parts, key=lambda x: not x["totals"]):  # сначала листы с суммой заказа целиком
        for no, o in part["orders"].items():
            was = orders.get(no)
            if was is None:
                orders[no] = o
                continue
            orders[no] = Order(no, was.order_date, was.amount or o.amount, was.status or o.status,
                               was.client_hash or o.client_hash,
                               tuple(dict.fromkeys(was.email_hashes + o.email_hashes)),
                               tuple(dict.fromkeys(was.phone_hashes + o.phone_hashes)),
                               tuple(dict.fromkeys(was.items + o.items)), tuple(dict.fromkeys(was.groups + o.groups)),
                               was.client_name or o.client_name, tuple(dict.fromkeys(was.emails + o.emails)),
                               tuple(dict.fromkeys(was.phones + o.phones)), was.company_key or o.company_key,
                               was.inn_hash or o.inn_hash)
    lines: list[OrderLine] = []
    by_order: dict[str, list[OrderLine]] = {}
    rank: dict[str, tuple[int, bool]] = {}
    for part in parts:
        mine: dict[str, list[OrderLine]] = {}
        for x in part["lines"]:
            mine.setdefault(x.order_no, []).append(x)
        priced = "line_amount" in part["cols"]  # при равном числе строк — лист с суммой позиции, а не заказа
        for no, xs in mine.items():
            if (len(xs), priced) > rank.get(no, (0, False)):
                by_order[no], rank[no] = xs, (len(xs), priced)
    for xs in by_order.values():
        lines += xs
    order_list = list(orders.values())
    notes = []
    if order_list:
        cols_all = set().union(*(set(p["cols"]) for p in parts))
        if not cols_all & {"email", "phone", "client"}:
            notes.append("нет ни почты, ни телефона, ни клиента — заказы не связать с базой рассылок")
        elif not cols_all & {"email", "phone"}:
            notes.append("нет почты и телефона клиента — связать с базой рассылок можно только через другие выгрузки")
        if not cols_all & {"item", "group"}:
            notes.append("нет номенклатуры — тему письма с купленным не сравнить")
    dates = [o.order_date for o in order_list] or [x.mailing_date for x in items if x.mailing_date]
    if not dates:
        raise CrmFileError("позиции рассылок без дат рассылки и без заказов — не к чему привязать")
    columns = {k: v for p in parts for k, v in p["columns"].items()}
    return CrmFile("orders" if order_list else "mailing_items", _month(dates), orders=order_list, notes=notes,
                   columns=columns, lines=sum(p["count"] for p in parts), order_lines=lines,
                   mailing_items=sorted(items, key=lambda x: (x.mailing, x.item)), sheets_info=info,
                   totals=any(p["totals"] for p in parts))


LEGAL_FORMS = {"ооо", "оао", "зао", "пао", "ао", "нао", "ип", "тоо", "чп", "пбоюл", "нко", "ано", "кфх", "гк", "тд"}
LEGAL_PHRASES = ("общество с ограниченной ответственностью", "индивидуальный предприниматель", "публичное акционерное общество",
                 "закрытое акционерное общество", "открытое акционерное общество", "акционерное общество")


def company_key(name) -> str | None:
    """Отпечаток названия компании для сверки 1С и Б24: без формы собственности, кавычек и порядка слов —
    «Карагод ИП», «ИП Карагод» и «ИП «Карагод»» совпадают. Общие карточки («Физическое лицо») — None."""
    s = (_text(name) or "").lower().replace("ё", "е")
    for phrase in LEGAL_PHRASES:
        s = s.replace(phrase, " ")
    words = [w for w in re.sub(r"[\"«»'`“”„.,()/\\\-–—№]+", " ", s).split() if w not in LEGAL_FORMS]
    if not words or GENERIC_CLIENT.match(" ".join(words)):
        return None
    return privacy.fingerprint("company", " ".join(sorted(words)))


def inn_hash(value) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return privacy.fingerprint("inn", digits) if len(digits) in (10, 12) else None


@dataclass(frozen=True)
class Company:
    """Компания (или контакт, лид) из справочника Б24: отпечатки названия, ИНН, почт и телефонов; название, почты и
    телефоны — только при EMAIL_LISTS=on."""
    name: str
    company_key: str | None
    inn_hash: str | None
    email_hashes: tuple[str, ...]
    phone_hashes: tuple[str, ...]
    emails: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()


def _parse_company_sheet(rows: list[tuple], header_row: int, cols: dict[str, int]) -> list[Company]:
    email_cols = [i for k, i in cols.items() if k.startswith("email") and k != "email"]
    phone_cols = [i for k, i in cols.items() if k.startswith("phone") and k != "phone"]
    out = []
    for row in rows[header_row + 1:]:
        if not row:
            continue
        name = _text(row[cols["company"]]) if cols["company"] < len(row) else None
        emails = list(dict.fromkeys(e for i in email_cols if i < len(row) for e in privacy.emails(row[i])))
        phones = list(dict.fromkeys(n for i in phone_cols if i < len(row) for n in privacy.phones(row[i])))
        if not name or not (emails or phones):
            continue
        inn = row[cols["inn"]] if "inn" in cols and cols["inn"] < len(row) else None
        out.append(Company(name[:300], company_key(name), inn_hash(inn),
                           tuple(privacy.fingerprint("email", e) for e in emails),
                           tuple(privacy.fingerprint("phone", n) for n in phones), tuple(emails), tuple(phones)))
    return out


# «Клиенты», под которыми в 1С идут разные люди: их заказы вместе не собираются, иначе смешаются чужие контакты.
GENERIC_CLIENT = re.compile(r"^(физ|частн|розн|покупател|клиент|без назв|не указ|аноним|разов|сайт|интернет)")


def _client_key(v) -> str | None:
    """Название клиента в едином виде — только чтобы собрать его заказы вместе (хранится отпечаток)."""
    s = (_text(v) or "").lower().replace("ё", "е")
    s = " ".join(re.sub(r"[\"«»'`]", "", s).split())
    return None if not s or GENERIC_CLIENT.match(s) else s
