"""ТЭК-Торг (tektorg.ru): поиск /223-fz/procedures?name=…&sort=datePublished_desc&page=N.

Страница — Next.js, выдача приходит в самой странице (__NEXT_DATA__ →
props.pageProps.initialReduxState.listingProcedures), по 15 процедур, новые сверху. Раздел
«Закупки» (223-ФЗ и коммерческие). robots.txt для всех роботов закрывает адреса с «?», кроме
«?name…», «?page…» и «?okpd…», — поэтому запрос всегда начинается с name. Внутренний API
сайта (POST /api/getProcedures) не используется.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import date

from app.sources.base import HealthStatus, PoliteClient, SourceError, TenderDetails, TenderStub
from app.sources.util import MSK, is_future, parse_dt, to_decimal

BASE = "https://www.tektorg.ru"
LIST = "/223-fz/procedures"
MAX_PAGES = 5
_EIS_RE = re.compile(r"^\d{11}$|^\d{19}$")
_NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
_OPEN = ("приём заявок", "прием заявок")


def _listing(html: str) -> dict:
    m = _NEXT_RE.search(html)
    if not m:
        raise SourceError("на странице нет данных выдачи (__NEXT_DATA__)")
    try:
        data = json.loads(m.group(1))
        return data["props"]["pageProps"]["initialReduxState"]["listingProcedures"]
    except (ValueError, KeyError, TypeError) as exc:
        raise SourceError(f"неожиданный формат выдачи: {exc!r}") from exc


def parse_item(p: dict) -> TenderDetails:
    dates = p.get("dates") or {}
    number = str(p.get("registryNumber") or "").strip()
    status = (p.get("statusName") or "").strip() or None
    deadline = parse_dt(dates.get("dateEndRegistration"), MSK)
    price = re.sub(r"[^\d.,]", "", p.get("sumPrice") or "")
    codes = sorted({c["code"] for c in p.get("okpd2") or [] if isinstance(c, dict) and c.get("code")})
    if status:
        is_open = status.lower() in _OPEN and is_future(deadline) is not False
    else:
        is_open = is_future(deadline)
    return TenderDetails(
        source_code=TektorgAdapter.code,
        external_id=str(p["id"]),
        url=f"{BASE}{LIST}/{p['id']}",
        title=(p.get("title") or "").strip(),
        eis_number=number if _EIS_RE.match(number) else None,
        customer_name=(p.get("organizerName") or "").strip(),
        customer_inn=p.get("inn") or None,
        law="223-ФЗ" if _EIS_RE.match(number) else "коммерческая",
        procedure_type=(p.get("typeCustomName") or p.get("typeName") or "").strip() or None,
        nmck=to_decimal(price) if price else None,
        published_at=parse_dt(dates.get("datePublished"), MSK),
        application_deadline=deadline,
        status=status,
        is_open=is_open,
        okpd2_codes=codes,
        revision="|".join(x or "" for x in (status, dates.get("dateEndRegistration"), p.get("sumPrice"))),
        etp="ТЭК-Торг",
    )


def parse_search(html: str) -> tuple[list[TenderDetails], int]:
    """Процедуры страницы и число страниц выдачи."""
    listing = _listing(html)
    items = [parse_item(p) for p in listing.get("data") or [] if isinstance(p, dict) and p.get("id")]
    return items, int(listing.get("totalPages") or 1)


class TektorgAdapter:
    code = "tektorg"
    title = "ТЭК-Торг"
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
            # Порядок параметров важен: robots.txt разрешает только адреса вида «?name…».
            params = {"name": query, "sort": "datePublished_desc"} | ({"page": page} if page > 1 else {})
            r = await self.client.get(LIST, params=params)
            if r.status_code >= 400:
                raise SourceError(f"HTTP {r.status_code} на странице поиска")
            items, total_pages = parse_search(r.text)
            older = False
            for d in items:
                if d.published_at and d.published_at.astimezone(MSK).date() < published_from:
                    older = True  # выдача отсортирована по дате — дальше только старые
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
            if older or not items or page >= total_pages:
                break

    async def fetch_details(self, stub: TenderStub) -> TenderDetails:
        cached = self._cache.get(stub.external_id)
        if cached is not None:
            return cached
        raise SourceError(f"процедура {stub.external_id} не найдена в выдаче")

    async def healthcheck(self) -> HealthStatus:
        try:
            r = await self.client.get(LIST, params={"name": "полотенце", "sort": "datePublished_desc"})
        except SourceError as exc:
            return HealthStatus(False, str(exc))
        return HealthStatus(r.status_code == 200, f"HTTP {r.status_code}")
