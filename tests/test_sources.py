"""Парсинг реальных ответов площадок (фикстуры сняты с живых API) и поведение HTTP-клиента."""

import asyncio
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.sources.base import PoliteClient, SourceBlocked, SourceCaptcha, SourceError, TenderStub
from app.sources.gosplan import GosplanAdapter, parse_fz44, parse_fz223
from app.sources.onlinecontract import OnlineContractAdapter, parse_procedure

FIX = Path(__file__).parent / "fixtures"


def load(path: str):
    return json.loads((FIX / path).read_text(encoding="utf-8"))


async def _no_sleep(_):
    return None


def client_for(handler, base_url="https://example.test") -> PoliteClient:
    return PoliteClient(
        "test",
        base_url=base_url,
        transport=httpx.MockTransport(handler),
        delay_min=0,
        delay_max=0,
        sleep=_no_sleep,
    )


async def collect(gen):
    return [x async for x in gen]


# --------------------------------------------------------------------------- ГосПлан / ЕИС


def test_gosplan_fz44_search_fixture_shape():
    rows = load("gosplan/fz44_search.json")
    assert isinstance(rows, list) and rows
    stub = GosplanAdapter(client=client_for(lambda r: httpx.Response(200, json=[])))._stub(rows[0], "fz44")
    assert stub.eis_number == rows[0]["purchase_number"]
    assert stub.external_id == f"fz44:{rows[0]['purchase_number']}"
    assert stub.title
    assert stub.published_at is not None and stub.published_at.tzinfo is not None
    assert stub.revision


def test_gosplan_parse_fz44_detail():
    d = parse_fz44(load("gosplan/fz44_purchase.json"))
    assert d.law == "44-ФЗ"
    assert d.eis_number == "0335300053926000082"
    assert d.title == "Постельное белье"
    assert "ПИОНЕРСКИЙ" in d.customer_name
    assert d.customer_inn == "3910002326"
    assert d.region == "Калининградская область"
    assert "Пионерский" in d.delivery_place
    assert d.nmck == Decimal("80183.5")
    assert d.procedure_type == "Электронный аукцион"
    assert d.application_deadline is not None
    assert d.positions and d.positions[0].name == "Простыни из хлопчатобумажных тканей"
    assert d.positions[0].code == "13.92.12.111-00000002"
    assert d.positions[0].qty == 50 and d.positions[0].unit == "шт"
    assert "13.92.12.111-00000002" in d.okpd2_codes
    assert d.etp == "РОСЭЛТОРГ (АО«ЕЭТП»)"
    # протокол, опубликованный после извещения, не меняет метку редакции извещения
    assert d.revision == "2026-09-23T06:10:31.567000"


def test_gosplan_parse_fz223_detail():
    d = parse_fz223(load("gosplan/fz223_purchase.json"))
    assert d.law == "223-ФЗ"
    assert d.eis_number == "32616409047"
    assert "СПОРТ" in d.customer_name
    assert d.customer_inn == "2722130161"
    assert d.region == "Хабаровский край"
    assert "Тихоокеанская" in d.delivery_place
    assert [p.code for p in d.positions] == ["13.92.12.119", "13.92.12.114", "13.92.12.114"]
    assert d.positions[1].name == "Комплекты постельного белья из хлопчатобумажных тканей"
    assert d.positions[1].qty == 140
    assert "notice223" in d.url
    # 09:00 по времени заказчика (МСК+7 = UTC+10, Хабаровск) = 23:00 UTC предыдущего дня
    assert d.application_deadline.isoformat() == "2026-10-05T23:00:00+00:00"


def test_gosplan_search_paginates_and_uses_phrase(monkeypatch):
    calls = []
    page = load("gosplan/fz44_search.json")

    def handler(request: httpx.Request):
        calls.append(dict(request.url.params))
        law = request.url.path.split("/")[1]
        if law == "fz44" and request.url.params["skip"] == "0":
            return httpx.Response(200, json=(page * 10)[:100])  # полная страница → нужна следующая
        if law == "fz44":
            return httpx.Response(200, json=page[:3])
        return httpx.Response(200, json=load("gosplan/fz223_search.json"))

    adapter = GosplanAdapter(client=client_for(handler, "https://v2test.gosplan.info"))
    stubs = asyncio.run(collect(adapter.search("постельное белье", date(2026, 9, 1), date(2026, 10, 2))))
    assert len(calls) == 3
    assert calls[0]["object_info"] == '"постельное белье"'
    assert calls[0]["published_after"].startswith("2026-09-01T00:00:00+03:00")
    assert calls[1]["skip"] == "100"
    assert any(s.external_id.startswith("fz223:") for s in stubs)


def test_gosplan_code_query():
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json=[])

    adapter = GosplanAdapter(client=client_for(handler))
    asyncio.run(collect(adapter.search("code:13.92.12", date(2026, 9, 1), date(2026, 10, 1))))
    assert seen[0]["classifier"] == "13.92.12" and "object_info" not in seen[0]


def test_gosplan_fetch_details_routes_by_law():
    def handler(request):
        if request.url.path == "/fz223/purchases/32616409047":
            return httpx.Response(200, json=load("gosplan/fz223_purchase.json"))
        return httpx.Response(404)

    adapter = GosplanAdapter(client=client_for(handler))
    stub = TenderStub("eis_gosplan", "fz223:32616409047", "", "x")
    assert asyncio.run(adapter.fetch_details(stub)).law == "223-ФЗ"


# --------------------------------------------------------------------------- Online Contract


def test_onlinecontract_search_fixture():
    data = load("onlinecontract/search_open.json")
    adapter = OnlineContractAdapter(client=client_for(lambda r: httpx.Response(200, json=data)))
    stubs = asyncio.run(collect(adapter.search("поставка", date(2026, 9, 1), date(2026, 10, 2))))
    assert len(stubs) == len(data["data"]) or len(stubs) >= 1
    s = stubs[0]
    assert s.url.startswith("https://onlinecontract.ru/")
    assert s.customer_name
    assert s.status == "Опубликован"


def test_onlinecontract_search_params_and_paging():
    seen = []
    full = {
        "data": [
            {
                "id": i,
                "name": f"Закупка {i}",
                "status": "Опубликован",
                "date": "2026-10-10T10:00:00+03:00",
                "owner": {"name": "ООО"},
            }
            for i in range(50)
        ],
        "meta": {},
    }

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(
            200, json=full if request.url.params["offset"] == "0" else {"data": [], "meta": {}}
        )

    adapter = OnlineContractAdapter(client=client_for(handler))
    stubs = asyncio.run(collect(adapter.search("полотенца", date(2026, 9, 1), date(2026, 10, 2))))
    assert len(stubs) == 50 and len(seen) == 2
    assert seen[0]["filters[search]"] == "полотенца"
    assert seen[0]["filters[status]"] == "1"
    assert seen[0]["filters[from]"] == "2026-09-01"
    assert seen[0]["include"] == "owner"


def test_onlinecontract_parse_procedure_with_positions():
    d = parse_procedure(
        load("onlinecontract/procedure_towels.json")["data"],
        load("onlinecontract/procedure_towels_positions.json")["data"],
    )
    assert d.title == "Полотенца махровые"
    assert d.customer_name == 'ОАО "Казаньоргсинтез"'
    assert d.law == "коммерческая"
    assert d.nmck == Decimal("37988.56")
    assert d.positions[0].name.startswith("Полотенце махровое")
    assert d.positions[0].qty == 58
    assert "460гр/м2" in d.notice_text  # технические требования позиции попадают в текст ТЗ
    assert d.delivery_place == ""  # «недоступно для просмотра» не сохраняем
    assert d.is_open is False


# --------------------------------------------------------------------------- HTTP-клиент


def test_client_retries_429_with_retry_after():
    attempts = []
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(429, headers={"retry-after": "21"})
        return httpx.Response(200, json={"ok": True})

    async def go():
        async with PoliteClient(
            "t",
            base_url="https://x.test",
            transport=httpx.MockTransport(handler),
            delay_min=0,
            delay_max=0,
            sleep=fake_sleep,
        ) as c:
            return await c.get_json("/a")

    assert asyncio.run(go()) == {"ok": True}
    assert len(attempts) == 3
    assert all(s >= 22 for s in sleeps if s > 0)


def test_client_gives_up_after_retries_on_5xx():
    async def go():
        async with client_for(lambda r: httpx.Response(503)) as c:
            await c.get("/a")

    with pytest.raises(SourceError):
        asyncio.run(go())


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(403), SourceBlocked),
        (
            httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<title>Ваш сеанс работы на ЭТП был прерван нашей системой защиты от ботов</title>",
            ),
            SourceBlocked,
        ),
        (
            httpx.Response(200, headers={"content-type": "text/html"}, text='<div class="smartcaptcha">'),
            SourceCaptcha,
        ),
    ],
)
def test_client_detects_blocks_and_captcha(response, error):
    async def go():
        async with client_for(lambda r: response) as c:
            await c.get("/a")

    with pytest.raises(error):
        asyncio.run(go())


def test_client_detects_captcha_redirect():
    def handler(request):
        if request.url.path == "/":
            return httpx.Response(
                302, headers={"location": "https://example.test/tmgrdfrend/showcaptcha?x=1"}
            )
        return httpx.Response(200, text="captcha")

    async def go():
        async with client_for(handler) as c:
            await c.get("/")

    with pytest.raises(SourceCaptcha):
        asyncio.run(go())


def test_client_keeps_pause_between_requests():
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    async def go():
        async with PoliteClient(
            "t",
            base_url="https://x.test",
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})),
            delay_min=2,
            delay_max=5,
            sleep=fake_sleep,
        ) as c:
            for _ in range(3):
                await c.get("/a")

    asyncio.run(go())
    assert len(sleeps) == 2 and all(1.5 < s <= 5 for s in sleeps)


def test_client_sends_user_agent():
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, json={})

    async def go():
        async with client_for(handler) as c:
            await c.get("/")

    asyncio.run(go())
    assert seen["ua"].startswith("VelmoriumTenderMonitor")


def test_gosplan_fz223_deadline_from_documentation_delivery():
    d = parse_fz223(load("gosplan/fz223_purchase_no_close.json"))
    # 05.10.2026 23:59 по времени заказчика (МСК+4 = UTC+7)
    assert d.application_deadline.isoformat() == "2026-10-05T16:59:00+00:00"
