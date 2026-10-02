"""ЕИС (44-ФЗ и 223-ФЗ) через ГосПлан API (https://gosplan.info).

Сам zakupki.gov.ru блокирует зарубежные IP, а ГосПлан отдаёт те же извещения ЕИС через REST.
Тестовый сервер работает без ключа (10 запросов в минуту), продуктовый — по ключу в заголовке
`apikey` (GOSPLAN_API_KEY).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta, timezone
from typing import Any
from urllib.parse import quote

from app.config import get_settings
from app.sources.base import (
    HealthStatus,
    PoliteClient,
    Position,
    SourceError,
    TenderDetails,
    TenderStub,
)
from app.sources.regions import region_name
from app.sources.util import MSK, as_list, day_start_msk, dig, is_future, parse_dt, to_decimal, to_float

LAWS = {"fz44": "44-ФЗ", "fz223": "223-ФЗ"}
PAGE = 100
MAX_SKIP = 1000


def eis_url(number: str, law: str) -> str:
    if law == "fz223":
        return f"https://zakupki.gov.ru/epz/order/notice/notice223/common-info.html?regNumber={number}"
    return f"https://zakupki.gov.ru/epz/order/extendedsearch/results.html?searchString={quote(number)}"


class GosplanAdapter:
    code = "eis_gosplan"
    title = "ЕИС через ГосПлан API"
    supports_codes = True

    def __init__(
        self, client: PoliteClient | None = None, base_url: str | None = None, api_key: str | None = None
    ):
        settings = get_settings()
        api_key = settings.gosplan_api_key if api_key is None else api_key
        base_url = (
            base_url
            or settings.gosplan_base_url
            or ("https://v2.gosplan.info" if api_key else "https://v2test.gosplan.info")
        )
        headers = {"apikey": api_key} if api_key else {}
        # Тестовый сервер: 10 запросов в минуту — держим паузу не меньше 6 секунд.
        delay = (6.5, 7.5) if not api_key else (None, None)
        self.client = client or PoliteClient(
            self.code, base_url=base_url, headers=headers, delay_min=delay[0], delay_max=delay[1]
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    # ----------------------------------------------------------------- поиск

    async def search(self, query: str, published_from: date, published_to: date) -> AsyncIterator[TenderStub]:
        """Ищет по тексту объекта закупки. Запрос вида «code:13.92.12» ищет по ОКПД2/КТРУ."""
        if query.startswith("code:"):
            base = {"classifier": query.removeprefix("code:")}
        else:
            text = query.strip()
            base = {"object_info": f'"{text}"' if " " in text else text}
        base["published_after"] = day_start_msk(published_from)
        for law in LAWS:
            skip = 0
            while skip <= MAX_SKIP:
                rows = await self.client.get_json(
                    f"/{law}/purchases", params={**base, "limit": PAGE, "skip": skip}
                )
                if not isinstance(rows, list):
                    raise SourceError(f"неожиданный ответ ГосПлан: {str(rows)[:200]}")
                for row in rows:
                    yield self._stub(row, law)
                if len(rows) < PAGE:
                    break
                skip += PAGE

    def _stub(self, row: dict[str, Any], law: str) -> TenderStub:
        number = row["purchase_number"]
        deadline = parse_dt(row.get("collecting_finished_at") or row.get("submission_close_at"))
        return TenderStub(
            source_code=self.code,
            external_id=f"{law}:{number}",
            url=eis_url(number, law),
            title=row.get("object_info") or "",
            eis_number=number,
            published_at=parse_dt(row.get("published_at")),
            application_deadline=deadline,
            revision=notice_revision(row.get("docs")) or str(row.get("published_at") or ""),
            raw={"law": law},
        )

    # ----------------------------------------------------------------- детали

    async def fetch_details(self, stub: TenderStub) -> TenderDetails:
        law, number = stub.external_id.split(":", 1)
        data = await self.client.get_json(f"/{law}/purchases/{number}")
        if law == "fz223":
            return parse_fz223(data, self.code)
        return parse_fz44(data, self.code)

    async def healthcheck(self) -> HealthStatus:
        try:
            await self.client.get_json("/fz44/purchases", params={"limit": 1})
        except SourceError as exc:
            return HealthStatus(False, str(exc))
        return HealthStatus(True, "ok")


def _is_notice(doc: dict[str, Any]) -> bool:
    kind = str(doc.get("doc_type") or "").lower()
    return ("notification" in kind or "notice" in kind) and "protocol" not in kind and "cancel" not in kind


def notice_revision(docs: Any) -> str:
    """Метка редакции извещения: время публикации последнего извещения (протоколы не считаются)."""
    dates = [str(d.get("published_at") or "") for d in as_list(docs) if isinstance(d, dict) and _is_notice(d)]
    return max(dates, default="")


def _latest_notice(data: dict[str, Any]) -> dict[str, Any]:
    docs = [d for d in as_list(data.get("docs")) if isinstance(d, dict) and isinstance(d.get("source"), dict)]
    notices = [d for d in docs if _is_notice(d)] or docs
    if not notices:
        return {}
    notices.sort(key=lambda d: str(d.get("published_at") or ""))
    return notices[-1]["source"]


def parse_fz44(data: dict[str, Any], source_code: str = "eis_gosplan") -> TenderDetails:
    number = data["purchase_number"]
    src = _latest_notice(data)
    notif = src.get("notificationInfo") or {}
    req = as_list(dig(notif, "customerRequirementsInfo", "customerRequirementInfo"))
    customers = [dig(r, "customer", "fullName") for r in req if dig(r, "customer", "fullName")]
    places = []
    for r in req:
        for place in as_list(dig(r, "contractConditionsInfo", "deliveryPlacesInfo", "byGARInfo")):
            text = ", ".join(p for p in (dig(place, "GARInfo", "GARAddres"), place.get("deliveryPlace")) if p)
            if text:
                places.append(text)
    objects = as_list(dig(notif, "purchaseObjectsInfo", "notDrugPurchaseObjectsInfo", "purchaseObject"))
    objects += as_list(dig(notif, "purchaseObjectsInfo", "drugPurchaseObjectsInfo", "drugPurchaseObjectInfo"))
    positions = []
    for obj in objects:
        code = (
            dig(obj, "KTRU", "code") or dig(obj, "OKPD2", "OKPDCode") or dig(obj, "KTRU", "OKPD2", "OKPDCode")
        )
        positions.append(
            Position(
                name=obj.get("name") or dig(obj, "KTRU", "name") or "",
                qty=to_float(dig(obj, "quantity", "value")),
                unit=dig(obj, "OKEI", "nationalCode") or dig(obj, "OKEI", "name"),
                code=code,
                price=to_float(obj.get("price")),
            )
        )
    codes = [*as_list(data.get("okpd2")), *as_list(data.get("ktru"))]
    deadline = parse_dt(
        dig(notif, "procedureInfo", "collectingInfo", "endDT") or data.get("collecting_finished_at")
    )
    responsible = dig(src, "purchaseResponsibleInfo", "responsibleOrgInfo", "fullName") or ""
    return TenderDetails(
        source_code=source_code,
        external_id=f"fz44:{number}",
        url=eis_url(number, "fz44"),
        title=data.get("object_info") or dig(src, "commonInfo", "purchaseObjectInfo") or "",
        eis_number=number,
        customer_name="; ".join(dict.fromkeys(customers)) or responsible,
        customer_inn=(as_list(data.get("customers")) or [None])[0],
        region=region_name(data.get("region")),
        delivery_place="; ".join(dict.fromkeys(places)),
        law="44-ФЗ",
        procedure_type=dig(src, "commonInfo", "placingWay", "name"),
        nmck=to_decimal(data.get("max_price")),
        currency=data.get("currency_code") or "RUB",
        published_at=parse_dt(data.get("published_at")),
        application_deadline=deadline,
        status=_stage(data.get("stage")),
        is_open=is_future(deadline),
        okpd2_codes=[c for c in codes if c],
        positions=positions,
        revision=notice_revision(data.get("docs")) or str(data.get("published_at") or ""),
        etp=dig(src, "commonInfo", "ETP", "name"),
    )


def parse_fz223(data: dict[str, Any], source_code: str = "eis_gosplan") -> TenderDetails:
    number = data["purchase_number"]
    src = _latest_notice(data)
    lots = as_list(dig(src, "lots", "lot"))
    positions, places, regions = [], [], []
    for lot in lots:
        lot_data = lot.get("lotData") or {}
        place = lot_data.get("deliveryPlace") or {}
        if place.get("address"):
            places.append(place["address"])
        if place.get("region"):
            regions.append(place["region"])
        items = as_list(dig(lot_data, "lotItems", "lotItem"))
        for item in items:
            name = item.get("additionalInfo") or dig(item, "okpd2", "name") or lot_data.get("subject") or ""
            positions.append(
                Position(
                    name=name,
                    qty=to_float(item.get("qty")),
                    unit=dig(item, "okei", "name"),
                    code=dig(item, "okpd2", "code"),
                )
            )
        if not items and lot_data.get("subject"):
            positions.append(Position(name=lot_data["subject"]))
    # В 223-ФЗ время указано без зоны, по времени заказчика («МСК+7» → offset = 7).
    offset = dig(src, "placer", "mainInfo", "timeZone", "offset") or dig(
        src, "customer", "mainInfo", "timeZone", "offset"
    )
    try:
        local_tz = timezone(timedelta(hours=3 + int(offset))) if offset is not None else MSK
    except (TypeError, ValueError):
        local_tz = MSK
    deadline = parse_dt(src.get("submissionCloseDateTime") or data.get("submission_close_at"), local_tz)
    return TenderDetails(
        source_code=source_code,
        external_id=f"fz223:{number}",
        url=eis_url(number, "fz223"),
        title=data.get("object_info") or src.get("name") or "",
        eis_number=number,
        customer_name=dig(src, "customer", "mainInfo", "fullName") or "",
        customer_inn=dig(src, "customer", "mainInfo", "inn") or data.get("customer"),
        region=(regions[0] if regions else None)
        or dig(src, "customer", "mainInfo", "region")
        or region_name(data.get("region")),
        delivery_place="; ".join(dict.fromkeys(places)),
        law="223-ФЗ",
        procedure_type=src.get("purchaseCodeName") or data.get("purchase_type"),
        nmck=to_decimal(data.get("max_price")),
        currency=data.get("currency_code") or "RUB",
        published_at=parse_dt(src.get("publicationDateTime") or data.get("published_at"), local_tz),
        application_deadline=deadline,
        status=_stage(data.get("stage")),
        is_open=is_future(deadline),
        okpd2_codes=[c for c in as_list(data.get("okpd2")) if c],
        positions=positions,
        revision=notice_revision(data.get("docs")) or str(data.get("published_at") or ""),
        etp=dig(src, "electronicPlaceInfo", "name"),
    )


_STAGES = {1: "Подача заявок", 2: "Работа комиссии", 3: "Закупка завершена", 4: "Закупка отменена"}


def _stage(stage) -> str | None:
    return _STAGES.get(stage, None if stage is None else f"Этап {stage}")
