"""Росэлторг (roseltorg.ru): поиск /procedures/search, серверный HTML, 10 лотов на страницу.

Выдача включает госзакупки (номер ЕИС), корпоративные и коммерческие процедуры. Даты публикации
в выдаче нет, поэтому окно поиска не применяется: берём первые страницы по каждому запросу,
открытые процедуры (статус «Прием заявок» и срок не истёк). robots.txt раздел /procedures/ не
запрещает.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date

from selectolax.parser import HTMLParser, Node

from app.sources.base import HealthStatus, PoliteClient, SourceError, TenderDetails, TenderStub
from app.sources.util import is_future, parse_dt, to_decimal

BASE = "https://www.roseltorg.ru"
MAX_PAGES = 5
_EIS_RE = re.compile(r"^\d{11}$|^\d{19}$")
_INN_RE = re.compile(r"ИНН\s*(\d{10,12})")
_DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})\s*в\s*(\d{2}:\d{2})")


def _text(node: Node | None) -> str:
    return " ".join(node.text(separator=" ", strip=True).split()) if node else ""


def parse_search(html: str) -> list[TenderDetails]:
    doc = HTMLParser(html)
    out: list[TenderDetails] = []
    for item in doc.css("div.js-etp-procedure-grid-item"):
        number = item.attributes.get("data-feature-favorite-lots-procedure-number") or ""
        lot = item.attributes.get("data-feature-favorite-lots-lot-number") or "1"
        if not number:
            continue
        link = item.css_first(".search-results__subject a")
        href = link.attributes.get("href") if link else f"/procedure/{number}/{lot}"
        section = _text(item.css_first(".search-results__section"))
        customer_p = item.css_first(".search-results__customer p")
        inn = _INN_RE.search(customer_p.attributes.get("title") or "") if customer_p else None
        status = _text(item.css_first(".search-results__status"))
        status = re.sub(r"\s*\d+\s*дн\.?$", "", status).strip()
        price_node = item.css_first(".search-results__sum p.desktop")
        price = re.sub(r"[^\d,]", "", _text(price_node)) if price_node else ""
        m = _DATE_RE.search(_text(item.css_first("time.search-results__time")))
        deadline = parse_dt(f"{m.group(1)} {m.group(2)}") if m else None
        region = re.sub(r"^\d+\.\s*", "", _text(item.css_first(".search-results__region")))
        law = "44-ФЗ" if "44-ФЗ" in section else "223-ФЗ" if "223-ФЗ" in section else "коммерческая"
        tags = [_text(t) for t in item.css(".search-results__tags:not(.mobile) a.chip")]
        out.append(
            TenderDetails(
                source_code=RoseltorgAdapter.code,
                external_id=f"{number}/{lot}",
                url=BASE + href if href.startswith("/") else href,
                title=_text(link) or _text(item.css_first(".search-results__subject")),
                eis_number=number if _EIS_RE.match(number) else None,
                customer_name=_text(item.css_first(".search-results__customer a")),
                customer_inn=inn.group(1) if inn else None,
                region=region or None,
                law=law,
                procedure_type=_text(item.css_first(".search-results__type")) or None,
                nmck=to_decimal(price) if price and price not in ("0,00",) else None,
                application_deadline=deadline,
                status=status or None,
                is_open=is_future(deadline),
                notice_text=("Теги: " + ", ".join(tags)) if tags else "",
                revision="|".join(x or "" for x in (status, m.group(0) if m else "", price)),
            )
        )
    return out


def has_next_page(html: str) -> bool:
    return 'rel="next"' in html


class RoseltorgAdapter:
    code = "roseltorg"
    title = "Росэлторг"
    supports_codes = False
    supports_refresh = False  # карточки берутся из выдачи; открытые тендеры обновляются при поиске

    def __init__(self, client: PoliteClient | None = None, max_pages: int = MAX_PAGES):
        self.client = client or PoliteClient(self.code, base_url=BASE)
        self.max_pages = max_pages
        self._cache: dict[str, TenderDetails] = {}

    async def aclose(self) -> None:
        await self.client.aclose()

    async def search(self, query: str, published_from: date, published_to: date) -> AsyncIterator[TenderStub]:
        if query.startswith("code:"):
            return
        for page in range(self.max_pages):
            r = await self.client.get("/procedures/search", params={"query_field": query, "page": page})
            if r.status_code >= 400:
                raise SourceError(f"HTTP {r.status_code} на странице поиска")
            items = parse_search(r.text)
            for d in items:
                if d.is_open is False:
                    continue
                self._cache[d.external_id] = d
                yield TenderStub(
                    source_code=self.code,
                    external_id=d.external_id,
                    url=d.url,
                    title=d.title,
                    eis_number=d.eis_number,
                    customer_name=d.customer_name,
                    application_deadline=d.application_deadline,
                    status=d.status,
                    revision=d.revision,
                )
            if not items or not has_next_page(r.text):
                break

    async def fetch_details(self, stub: TenderStub) -> TenderDetails:
        # Карточка из выдачи уже содержит всё, что есть в поиске; отдельной страницы не запрашиваем.
        cached = self._cache.get(stub.external_id)
        if cached is not None:
            return cached
        raise SourceError(f"процедура {stub.external_id} не найдена в выдаче")

    async def healthcheck(self) -> HealthStatus:
        try:
            r = await self.client.get("/procedures/search", params={"query_field": "полотенце"})
        except SourceError as exc:
            return HealthStatus(False, str(exc))
        return HealthStatus(r.status_code == 200, f"HTTP {r.status_code}")
