"""Сбербанк-АСТ (sberbank-ast.ru): единый поиск закупок /UnitedPurchaseList.html.

Страница сама получает выдачу запросом POST {API_BASE_URL}Processing/main (адрес — в
/build/apl_config.json) с windowCode «/EsOpenUnitedPurchaseList» и XML-фильтром; вход не нужен.
Ответ — результат Elasticsearch: PurchaseList[]._source с полями закупки, новые сверху, по 20 на
страницу (pagenum с 0). В выдаче все секции: 44-ФЗ (номер ЕИС — purchCode), 223-ФЗ (номер ЕИС —
IntegratorCode) и коммерческие. Время — московское. robots.txt у площадки нет.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import date
from xml.sax.saxutils import escape

from app.sources.base import HealthStatus, PoliteClient, SourceError, TenderDetails, TenderStub
from app.sources.util import MSK, is_future, parse_dt, to_decimal

BASE = "https://www.sberbank-ast.ru"
API = "/api/Processing/main"
PAGE_SIZE = 20
MAX_PAGES = 5
_EIS_RE = re.compile(r"^\d{11}$|^\d{19}$")
_CLOSED = ("не состоял", "заверш", "отмен", "заключ", "архив", "рассмотр", "подведен", "итог", "приостанов")


def search_body(query: str, page: int) -> dict:
    filter_xml = (
        f"<query><mainInput>{escape(query)}</mainInput><searchBarType>anyWord</searchBarType>"
        f"<pagenum>{page}</pagenum><pagesize>{PAGE_SIZE}</pagesize>"
        "<targetPageCode>OTUnitedPurchaseList</targetPageCode>"
        "<fields><field>hasSt14</field></fields></query>"
    )
    return {
        "windowCode": "/EsOpenUnitedPurchaseList",
        "actionType": "MONITOR",
        "actionCode": "default",
        "documentBody": "",
        "parm": "es",
        "filterBody": filter_xml,
    }


def _payload(data) -> dict:
    # Обычно ответ — сам объект Elasticsearch; страница на всякий случай разбирает и строку в «data».
    if isinstance(data, dict) and isinstance(data.get("data"), str):
        try:
            data = json.loads(data["data"])
        except ValueError as exc:
            raise SourceError("ответ поиска не разобран") from exc
    if not isinstance(data, dict) or "PurchaseList" not in data:
        raise SourceError("в ответе поиска нет PurchaseList")
    return data


def _law(source: str) -> str:
    if "44" in source:
        return "44-ФЗ"
    if "223" in source:
        return "223-ФЗ"
    return "коммерческая"


def parse_item(s: dict) -> TenderDetails:
    code = str(s.get("purchCode") or s.get("PurchaseCodeNoBidTerm") or "").strip()
    integrator = str(s.get("IntegratorCode") or "").strip()
    eis = code if _EIS_RE.match(code) else integrator if _EIS_RE.match(integrator) else None
    status = (s.get("purchStateName") or s.get("BidStatusName") or "").strip() or None
    deadline = parse_dt(s.get("RequestDate") or s.get("RequestAcceptDate"), MSK)
    if deadline and deadline.year > 2070:  # «01.01.2079» — срок не задан
        deadline = None
    closed = any(m in (status or "").lower() for m in _CLOSED)
    amount = s.get("purchAmountRUB") or s.get("purchAmount")
    codes = s.get("productCodes") or []
    return TenderDetails(
        source_code=SberbankAstAdapter.code,
        external_id=code or str(s.get("PurchaseId") or ""),
        url=s.get("objectHrefTerm") or f"{BASE}/UnitedPurchaseList.html",
        title=(s.get("purchName") or s.get("BidName") or "").strip(),
        eis_number=eis,
        customer_name=(s.get("CustomerFullName") or s.get("OrgFullName") or s.get("OrgName") or "").strip(),
        customer_inn=s.get("CustomerInn") or s.get("OrgInn") or None,
        region=s.get("RegionName") or None,
        delivery_place=s.get("GeoDataAddress") or "",
        law=_law(s.get("SourceTerm") or ""),
        procedure_type=s.get("PurchaseTypeName") or None,
        nmck=to_decimal(amount) if amount not in (None, "") else None,
        currency=s.get("purchCurrency") or "RUB",
        published_at=parse_dt(s.get("PublicDate"), MSK),
        application_deadline=deadline,
        status=status,
        is_open=False if closed else is_future(deadline),
        okpd2_codes=sorted({str(c) for c in codes if c}) if isinstance(codes, list) else [],
        revision="|".join(str(x or "") for x in (status, s.get("RequestDate"), s.get("lastChange"))),
        etp="Сбербанк-АСТ",
    )


def parse_search(data) -> tuple[list[TenderDetails], int]:
    """Закупки страницы и общее число найденных (Elasticsearch ограничивает его 10 000)."""
    payload = _payload(data)
    items = [
        parse_item(x["_source"])
        for x in payload.get("PurchaseList") or []
        if isinstance(x, dict) and isinstance(x.get("_source"), dict)
    ]
    return [d for d in items if d.external_id], int(payload.get("Total") or 0)


class SberbankAstAdapter:
    code = "sberbank_ast"
    title = "Сбербанк-АСТ"
    supports_codes = False
    supports_refresh = False  # карточки берутся из выдачи; открытые тендеры обновляются при поиске

    def __init__(self, client: PoliteClient | None = None, max_pages: int = MAX_PAGES):
        self.client = client or PoliteClient(self.code, base_url=BASE)
        self.max_pages = max_pages
        self._cache: dict[str, TenderDetails] = {}

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _page(self, query: str, page: int) -> tuple[list[TenderDetails], int]:
        r = await self.client.request("POST", API, json=search_body(query, page))
        if r.status_code >= 400:
            raise SourceError(f"HTTP {r.status_code} на запросе поиска")
        try:
            data = r.json()
        except ValueError as exc:
            raise SourceError("ответ поиска — не JSON") from exc
        return parse_search(data)

    async def search(self, query: str, published_from: date, published_to: date) -> AsyncIterator[TenderStub]:
        if query.startswith("code:"):
            return
        for page in range(self.max_pages):
            items, total = await self._page(query, page)
            older = False
            for d in items:
                if d.published_at and d.published_at.astimezone(MSK).date() < published_from:
                    older = True  # выдача отсортирована по дате публикации
                    continue
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
                    published_at=d.published_at,
                    application_deadline=d.application_deadline,
                    status=d.status,
                    revision=d.revision,
                )
            if older or len(items) < PAGE_SIZE or (page + 1) * PAGE_SIZE >= total:
                break

    async def fetch_details(self, stub: TenderStub) -> TenderDetails:
        cached = self._cache.get(stub.external_id)
        if cached is not None:
            return cached
        raise SourceError(f"закупка {stub.external_id} не найдена в выдаче")

    async def healthcheck(self) -> HealthStatus:
        try:
            await self._page("полотенце", 0)
        except SourceError as exc:
            return HealthStatus(False, str(exc))
        return HealthStatus(True, "поиск отвечает")
