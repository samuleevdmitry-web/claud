"""ЭТП «Фабрикант» (fabrikant.ru): поиск /procedure/search?query=…, серверный HTML (Next.js).

В выдаче: номер, номер ЕИС (если есть), статус, секция, название, организатор, заказчик, даты
публикации и окончания приёма заявок, начальная цена. Листание — page_number / page_limit
(до 40 на страницу). Выдача упорядочена по релевантности, поэтому берём несколько первых
страниц и фильтруем по дате публикации и статусу сами.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal

from selectolax.parser import HTMLParser

from app.sources.base import HealthStatus, PoliteClient, SourceError, TenderDetails, TenderStub
from app.sources.util import MSK, is_future, parse_dt, to_decimal

BASE = "https://www.fabrikant.ru"
PAGE_LIMIT = 40
MAX_PAGES = 3
_EIS_RE = re.compile(r"^\d{11}$|^\d{19}$")
_LABELS = ("Организатор", "Заказчик", "Дата публикации", "Дата окончания приёма заявок", "Осталось")
_OPEN = ("прием заявок", "приём заявок", "идёт приём заявок", "идет прием заявок")


def parse_search(html: str) -> list[TenderDetails]:
    doc = HTMLParser(html)
    out: list[TenderDetails] = []
    for card in doc.css('div[data-slot="card"][data-id]'):
        parts = [p for p in card.text(separator="\x1f", strip=True).split("\x1f") if p.strip()]
        values: dict[str, str] = {}
        for i, part in enumerate(parts):
            if part in _LABELS and i + 1 < len(parts) and parts[i + 1] not in _LABELS:
                values.setdefault(part, parts[i + 1])
        links = [a.attributes.get("href") or "" for a in card.css("a")]
        eis_link = next((h for h in links if "zakupki.gov.ru" in h), "")
        url = next((h for h in links if h.startswith("http") and "zakupki.gov.ru" not in h), "")
        eis = re.search(r"searchString=(\d+)", eis_link)
        title = ""
        for a in card.css("a"):
            text = a.text(strip=True)
            if text and "zakupki.gov.ru" not in (a.attributes.get("href") or "") and len(text) > len(title):
                title = text
        status = next(
            (
                p
                for p in parts
                if p.lower() in _OPEN
                or p in ("Заключение контракта", "Работа комиссии", "Подведение итогов", "Завершена")
            ),
            None,
        )
        section = next((p for p in parts if p.startswith("Закупки ") or p.startswith("Продажи")), "")
        number_idx = parts.index("№") if "№" in parts else -1
        procedure_number = parts[number_idx + 1] if number_idx >= 0 and number_idx + 1 < len(parts) else ""
        nmck, currency = _price(parts)
        deadline = parse_dt(values.get("Дата окончания приёма заявок"), MSK)
        law = "44-ФЗ" if "44-ФЗ" in section else "223-ФЗ" if "223-ФЗ" in section else "коммерческая"
        out.append(
            TenderDetails(
                source_code=FabrikantAdapter.code,
                external_id=card.attributes["data-id"],
                url=url or BASE,
                title=title,
                eis_number=eis.group(1) if eis and _EIS_RE.match(eis.group(1)) else None,
                customer_name=values.get("Заказчик") or values.get("Организатор") or "",
                law=law,
                procedure_type=parts[0] if parts else None,
                nmck=nmck,
                currency=currency,
                published_at=parse_dt(values.get("Дата публикации"), MSK),
                application_deadline=deadline,
                status=status,
                is_open=(status or "").lower() in _OPEN if status else is_future(deadline),
                notice_text=f"Организатор: {values['Организатор']}"
                if values.get("Заказчик") and values.get("Организатор")
                else "",
                revision="|".join(
                    x or "" for x in (status, values.get("Дата окончания приёма заявок"), procedure_number)
                ),
            )
        )
    return out


_NUMBER_RE = re.compile(r"^\d[\d\s]*$")


def _price(parts: list[str]) -> tuple[Decimal | None, str]:
    """Начальная цена: «183 390», «,», «00», «RUB» после дат. Часть карточек приходит без цены."""
    for i, part in enumerate(parts):
        if not _NUMBER_RE.match(part) or i == 0 or parts[i - 1] in ("№",):
            continue
        integer = re.sub(r"\s", "", part)
        cents = (
            parts[i + 2] if i + 2 < len(parts) and parts[i + 1] == "," and parts[i + 2].isdigit() else "00"
        )
        tail = parts[i + 3] if cents != "00" or (i + 3 < len(parts) and parts[i + 1] == ",") else ""
        currency = tail if tail in ("RUB", "USD", "EUR", "CNY") else "RUB"
        # Номер процедуры («№ 308033») и «6 дней» сюда не попадают: цена идёт после блока дат.
        if any(p.startswith("Дата") for p in parts[:i]):
            return to_decimal(f"{integer}.{cents}"), currency
    return None, "RUB"


class FabrikantAdapter:
    code = "fabrikant"
    title = "ЭТП Фабрикант"
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
        for page in range(1, self.max_pages + 1):
            r = await self.client.get(
                "/procedure/search", params={"query": query, "page_number": page, "page_limit": PAGE_LIMIT}
            )
            if r.status_code >= 400:
                raise SourceError(f"HTTP {r.status_code} на странице поиска")
            items = parse_search(r.text)
            for d in items:
                if d.is_open is False:
                    continue
                if d.published_at and d.published_at.astimezone(MSK).date() < published_from:
                    continue
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
            if len(items) < PAGE_LIMIT:
                break

    async def fetch_details(self, stub: TenderStub) -> TenderDetails:
        cached = self._cache.get(stub.external_id)
        if cached is not None:
            return cached
        raise SourceError(f"процедура {stub.external_id} не найдена в выдаче")

    async def healthcheck(self) -> HealthStatus:
        try:
            r = await self.client.get("/procedure/search", params={"query": "полотенце"})
        except SourceError as exc:
            return HealthStatus(False, str(exc))
        return HealthStatus(r.status_code == 200, f"HTTP {r.status_code}")
