"""Online Contract (onlinecontract.ru) — публичный JSON API https://api.onlc.ru, без авторизации.

Сайт — одностраничное приложение; его страница «Закупки» берёт данные из
GET /purchases/v1/public/procedures. Параметры выяснены по ответам API на неверные значения:
filters[search], filters[status] (1 — приём заявок открыт), filters[from] (дата публикации,
ISO), sort, limit, offset, include=owner, total=true.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

from app.sources.base import HealthStatus, PoliteClient, Position, SourceError, TenderDetails, TenderStub
from app.sources.util import is_future, parse_dt, to_decimal, to_float

API = "https://api.onlc.ru"
BASE = "/purchases/v1/public/procedures"
PAGE = 50
MAX_OFFSET = 1000
STATUS_OPEN = 1
_TAG_RE = re.compile(r"<[^>]+>")


def procedure_url(item: dict[str, Any]) -> str:
    section = "zakupki-223fz" if item.get("fz223") else "tenders"
    return f"https://onlinecontract.ru/{section}/{item['id']}"


class OnlineContractAdapter:
    code = "onlinecontract"
    title = "Online Contract"
    supports_codes = False

    def __init__(self, client: PoliteClient | None = None):
        self.client = client or PoliteClient(self.code, base_url=API, headers={"Accept": "application/json"})

    async def aclose(self) -> None:
        await self.client.aclose()

    async def search(self, query: str, published_from: date, published_to: date) -> AsyncIterator[TenderStub]:
        if query.startswith("code:"):
            return
        offset = 0
        while offset <= MAX_OFFSET:
            data = await self.client.get_json(
                BASE,
                params={
                    "filters[search]": query,
                    "filters[status]": STATUS_OPEN,
                    "filters[from]": published_from.isoformat(),
                    "sort": "-id",
                    "limit": PAGE,
                    "offset": offset,
                    "include": "owner",
                },
            )
            rows = data.get("data") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                raise SourceError(f"неожиданный ответ Online Contract: {str(data)[:200]}")
            for item in rows:
                yield self._stub(item)
            if len(rows) < PAGE:
                break
            offset += PAGE

    def _stub(self, item: dict[str, Any]) -> TenderStub:
        owner = item.get("owner") or {}
        return TenderStub(
            source_code=self.code,
            external_id=str(item["id"]),
            url=procedure_url(item),
            title=(item.get("name") or "").strip(),
            customer_name=owner.get("fullName") or owner.get("name") or "",
            application_deadline=parse_dt(item.get("date")),
            status=item.get("status"),
            revision="|".join(str(item.get(k) or "") for k in ("status", "date", "startPrice")),
            raw={"fz223": item.get("fz223")},
        )

    async def fetch_details(self, stub: TenderStub) -> TenderDetails:
        data = await self.client.get_json(f"{BASE}/{stub.external_id}", params={"include": "owner"})
        positions = await self.client.get_json(f"{BASE}/{stub.external_id}/positions")
        return parse_procedure(data.get("data") or {}, (positions or {}).get("data") or [])

    async def healthcheck(self) -> HealthStatus:
        try:
            await self.client.get_json(BASE, params={"limit": 1})
        except SourceError as exc:
            return HealthStatus(False, str(exc))
        return HealthStatus(True, "ok")


def _clean(value: Any) -> str:
    text = _TAG_RE.sub("", str(value or "")).strip()
    return "" if text.lower() == "недоступно для просмотра" else text


def parse_procedure(item: dict[str, Any], positions: list[dict[str, Any]]) -> TenderDetails:
    owner = item.get("owner") or {}
    deadline = parse_dt(item.get("date"))
    places = [_clean(item.get("deliveryPlace"))] + [_clean(p.get("deliveryPlace")) for p in positions]
    notice = "\n".join(
        f"{label}: {_clean(item.get(key))}"
        for key, label in (
            ("deliveryTerms", "Условия поставки"),
            ("deliveryTime", "Срок поставки"),
            ("parameter", "Параметры"),
        )
        if _clean(item.get(key))
    )
    requirements = [
        f"{(p.get('name') or '').strip()}: {_clean(p['ownerCondi'])}"
        for p in positions
        if _clean(p.get("ownerCondi"))
    ]
    notice = "\n".join(filter(None, [notice, *requirements]))
    law = "223-ФЗ" if item.get("fz223") or item.get("fz223u") else "коммерческая"
    return TenderDetails(
        source_code=OnlineContractAdapter.code,
        external_id=str(item["id"]),
        url=procedure_url(item),
        title=(item.get("name") or "").strip(),
        customer_name=owner.get("fullName") or owner.get("name") or "",
        delivery_place="; ".join(dict.fromkeys(p for p in places if p)),
        law=law,
        procedure_type=item.get("type"),
        nmck=to_decimal(item.get("startPrice")),
        currency="RUB" if item.get("currencyCode") in (None, "RUR", "RUB") else item["currencyCode"],
        application_deadline=deadline,
        status=item.get("status"),
        is_open=is_future(deadline),
        positions=[
            Position(
                name=(p.get("name") or "").strip(),
                qty=to_float(p.get("totalCount")),
                unit=p.get("unit"),
                price=to_float(p.get("price")),
            )
            for p in positions
        ],
        notice_text=notice,
        revision="|".join(str(item.get(k) or "") for k in ("status", "date", "startPrice")),
    )
