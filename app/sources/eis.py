"""ЕИС напрямую (zakupki.gov.ru): расширенный поиск /epz/order/extendedsearch/results.html.

Выдача — серверный HTML, до 50 записей на страницу, фильтр по дате размещения и этапу
«Подача заявок» (af=on). В выдаче: закон, способ, номер, объект закупки, заказчик, начальная
цена, даты размещения/обновления и дата окончания подачи заявок (без времени). Точное время
окончания и место поставки берутся из карточки извещения (common-info); если карточка не
открылась, используется то, что было в выдаче. Нужен сертификат Минцифры (см. app/sources/tls.py).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta, timezone

from selectolax.parser import HTMLParser, Node

from app.sources.base import HealthStatus, PoliteClient, Position, SourceError, TenderDetails, TenderStub
from app.sources.util import MSK, is_future, parse_dt, to_decimal, to_float

BASE = "https://zakupki.gov.ru"
SEARCH = "/epz/order/extendedsearch/results.html"
MAX_PAGES = 10
_DATE_RE = re.compile(r"\d{2}\.\d{2}\.\d{4}")
_DEADLINE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})(?:\s+(\d{2}:\d{2}))?(?:\s*\(МСК\s*([+-]\s*\d+)?\))?")
_CODE_RE = re.compile(r"\b\d{2}\.\d{2}(?:\.\d{1,2}(?:\.\d{3})?)?(?:-\d{8})?\b")
_OKPD_RE = re.compile(r"^\d{2}\.\d{2}\.\d{2}\.\d{3}(?:-\d{8})?$")
_OPEN_STAGES = ("подача заявок",)


def _text(node: Node | None) -> str:
    return " ".join(node.text(separator=" ", strip=True).split()) if node else ""


def _abs(href: str) -> str:
    return BASE + href if href.startswith("/") else href


def _deadline(value: str | None) -> datetime | None:
    """«08.10.2026 10:00 (МСК+4)» → UTC; дата без времени — конец дня по Москве."""
    if not value:
        return None
    m = _DEADLINE_RE.search(value)
    if not m:
        return None
    day, hm, shift = m.groups()
    tz = MSK
    if shift:
        tz = timezone(timedelta(hours=3 + int(shift.replace(" ", ""))))
    if not hm:
        dt = parse_dt(day, tz)
        return dt + timedelta(hours=23, minutes=59) if dt else None
    return parse_dt(f"{day} {hm}", tz)


def parse_search(html: str) -> list[TenderDetails]:
    doc = HTMLParser(html)
    out: list[TenderDetails] = []
    for item in doc.css("div.search-registry-entry-block"):
        header = _text(item.css_first(".registry-entry__header-top__title"))
        number_link = item.css_first(".registry-entry__header-mid__number a")
        if number_link is None:
            continue
        number = re.sub(r"\D", "", _text(number_link))
        url = _abs(number_link.attributes.get("href") or "")
        if "/223/purchase/" in url:  # старый адрес карточки 223-ФЗ
            url = f"{BASE}/epz/order/notice/notice223/common-info.html?regNumber={number}"
        stage = _text(item.css_first(".registry-entry__header-mid__title"))
        values: dict[str, str] = {}
        for block in item.css(".registry-entry__body-block"):
            title = _text(block.css_first(".registry-entry__body-title"))
            value = block.css_first(".registry-entry__body-value") or block.css_first(
                ".registry-entry__body-href"
            )
            if title:
                values[title] = _text(value)
        dates: dict[str, str] = {}
        for t in item.css(".data-block__title"):
            v = t.next
            while v is not None and v.tag == "-text":
                v = v.next
            dates[_text(t)] = _text(v)
        price = _text(item.css_first(".price-block__value"))
        law = "44-ФЗ" if header.startswith("44") else "223-ФЗ" if header.startswith("223") else "коммерческая"
        deadline = _deadline(dates.get("Окончание подачи заявок"))
        customer_link = item.css_first(".registry-entry__body-href a")
        inn = (
            re.search(r"inn=(\d{10,12})", customer_link.attributes.get("href") or "")
            if customer_link
            else None
        )
        out.append(
            TenderDetails(
                source_code=EisAdapter.code,
                external_id=number,
                url=url,
                title=values.get("Объект закупки", ""),
                eis_number=number,
                customer_name=values.get("Заказчик")
                or values.get("Организация, осуществляющая размещение", ""),
                customer_inn=inn.group(1) if inn else None,
                law=law,
                procedure_type=re.sub(r"^(44|223)-ФЗ\s*", "", header) or None,
                nmck=to_decimal(re.sub(r"[^\d,]", "", price)) if price else None,
                published_at=parse_dt(dates.get("Размещено"), MSK),
                application_deadline=deadline,
                status=stage or None,
                is_open=stage.lower() in _OPEN_STAGES if stage else is_future(deadline),
                revision=dates.get("Обновлено") or dates.get("Размещено"),
            )
        )
    return out


def has_next_page(html: str) -> bool:
    return "paginator-button-next" in html


def _pairs(doc: HTMLParser) -> dict[str, str]:
    """Пары «заголовок → значение» карточки: 223-ФЗ — common-text__*, 44-ФЗ — section__*."""
    out: dict[str, str] = {}
    for title_cls, value_cls in (
        ("common-text__title", "common-text__value"),
        ("section__title", "section__info"),
    ):
        for t in doc.css(f".{title_cls}"):
            v = t.next
            while v is not None and v.tag == "-text":
                v = v.next
            key = _text(t)
            if key and v is not None and value_cls in (v.attributes.get("class") or ""):
                out.setdefault(key, _text(v))
    return out


def _first(values: dict[str, str], *prefixes: str) -> str:
    for prefix in prefixes:
        for key, value in values.items():
            if key.startswith(prefix) and value:
                return value
    return ""


def _codes_in(text: str) -> list[str]:
    return [m.group(0) for m in _CODE_RE.finditer(text) if _OKPD_RE.match(m.group(0))]


def _positions(doc: HTMLParser) -> list[Position]:
    """Объекты закупки 44-ФЗ: строки tableBlock__row — коды ОКПД2/КТРУ, наименование, единица,
    количество, цена. Код позиции — КТРУ, если есть, иначе ОКПД2."""
    out: list[Position] = []
    seen: set[tuple[str, str]] = set()
    for row in doc.css("tr.tableBlock__row"):
        cells = [_text(td) for td in row.css("td")]
        if len(cells) < 6:
            continue
        codes = _codes_in(cells[1])
        name = cells[2]
        if not codes or not re.search(r"[А-Яа-яЁё]{3}", name):
            continue
        code = next((c for c in codes if "-" in c), codes[0])
        if (name, code) in seen:
            continue
        seen.add((name, code))
        out.append(
            Position(
                name=name[:500],
                code=code,
                unit=cells[3] or None,
                qty=to_float(re.sub(r"[^\d,]", "", cells[4])),
                price=to_float(re.sub(r"[^\d,]", "", cells[5])),
            )
        )
    return out


def parse_card(html: str, base: TenderDetails) -> TenderDetails:
    doc = HTMLParser(html)
    values = _pairs(doc)
    if not values:
        raise SourceError("в карточке нет полей извещения")
    deadline = _deadline(
        _first(values, "Дата и время окончания срока подачи заявок", "Дата и время окончания")
    )
    title = _first(values, "Наименование объекта закупки", "Наименование закупки") or base.title
    customer = (
        _first(values, "Наименование организации", "Организация, осуществляющая размещение")
        or base.customer_name
    )
    place = _first(
        values,
        "Место поставки товара",
        "Место доставки",
        "Место поставки",
        "Место выполнения",
        "Место оказания",
    )
    positions = _positions(doc)
    # КТРУ «17.22.11.130-00000005» даёт и код ОКПД2 «17.22.11.130»
    codes = sorted({c for p in positions if p.code for c in {p.code, p.code.split("-")[0]}})
    d = TenderDetails(**{**base.__dict__})
    d.title = title
    d.customer_name = customer
    d.region = _first(values, "Регион") or base.region
    d.delivery_place = place or _first(values, "Место нахождения", "Почтовый адрес")
    d.application_deadline = deadline or base.application_deadline
    d.is_open = is_future(d.application_deadline) if base.is_open is not False else False
    d.positions = positions or base.positions
    d.okpd2_codes = codes or base.okpd2_codes
    d.etp = _first(values, "Наименование электронной площадки") or base.etp
    d.notice_text = " ".join(
        f"{k}: {v}" for k, v in values.items() if k.startswith(("Описание", "Требования", "Порядок подачи"))
    )[:5000]
    # Метка версии та же, что в выдаче («Обновлено»): иначе карточка запрашивалась бы каждый прогон.
    d.revision = base.revision
    return d


class EisAdapter:
    code = "eis"
    title = "ЕИС напрямую"
    supports_codes = False

    def __init__(self, client: PoliteClient | None = None, max_pages: int = MAX_PAGES):
        self.client = client or PoliteClient(self.code, base_url=BASE)
        self.max_pages = max_pages
        self._cache: dict[str, TenderDetails] = {}

    async def aclose(self) -> None:
        await self.client.aclose()

    @staticmethod
    def search_params(query: str, published_from: date, published_to: date, page: int) -> dict[str, str]:
        return {
            "searchString": query,
            "morphology": "on",
            "fz44": "on",
            "fz223": "on",
            "af": "on",
            "publishDateFrom": published_from.strftime("%d.%m.%Y"),
            "publishDateTo": published_to.strftime("%d.%m.%Y"),
            "sortBy": "PUBLISH_DATE",
            "sortDirection": "false",
            "recordsPerPage": "_50",
            "pageNumber": str(page),
        }

    async def search(self, query: str, published_from: date, published_to: date) -> AsyncIterator[TenderStub]:
        if query.startswith("code:"):
            return
        for page in range(1, self.max_pages + 1):
            r = await self.client.get(
                SEARCH, params=self.search_params(query, published_from, published_to, page)
            )
            if r.status_code >= 400:
                raise SourceError(f"HTTP {r.status_code} на странице поиска")
            items = parse_search(r.text)
            for d in items:
                self._cache[d.external_id] = d
                yield TenderStub(
                    source_code=self.code,
                    external_id=d.external_id,
                    url=d.url,
                    title=d.title,
                    eis_number=d.eis_number,
                    customer_name=d.customer_name,
                    published_at=d.published_at,
                    application_deadline=d.application_deadline,
                    status=d.status,
                    revision=d.revision,
                )
            if not items or not has_next_page(r.text):
                break

    async def fetch_details(self, stub: TenderStub) -> TenderDetails:
        base = self._cache.get(stub.external_id) or TenderDetails(
            source_code=self.code,
            external_id=stub.external_id,
            url=stub.url,
            title=stub.title,
            eis_number=stub.eis_number,
            customer_name=stub.customer_name,
            law="223-ФЗ" if "notice223" in stub.url or "/223/" in stub.url else "44-ФЗ",
            application_deadline=stub.application_deadline,
            status=stub.status,
            revision=stub.revision,
        )
        try:
            r = await self.client.get(stub.url)
        except SourceError as exc:
            if exc.status in ("blocked", "captcha") or stub.external_id not in self._cache:
                raise
            return base
        if r.status_code != 200:
            if stub.external_id in self._cache:
                return base  # карточка не открылась — хватит данных из выдачи
            raise SourceError(f"HTTP {r.status_code} на карточке")
        try:
            return parse_card(r.text, base)
        except SourceError:
            if stub.external_id in self._cache:
                return base
            raise

    async def healthcheck(self) -> HealthStatus:
        today = datetime.now(UTC).astimezone(MSK).date()
        try:
            r = await self.client.get(SEARCH, params=self.search_params("полотенце", today, today, 1))
        except SourceError as exc:
            return HealthStatus(False, str(exc))
        return HealthStatus(r.status_code == 200, f"HTTP {r.status_code}")
