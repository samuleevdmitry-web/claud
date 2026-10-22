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


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    """Фикстуры сняты 02.10.2026 (Росэлторг, Фабрикант, ЕИС) и 22.10.2026 (ТЭК-Торг): «сейчас» —
    3 октября, иначе открытые на момент съёмки закупки со временем стали бы закрытыми."""
    from datetime import UTC, datetime

    import app.sources.util as util

    monkeypatch.setattr(util, "utcnow", lambda: datetime(2026, 10, 3, 9, 0, tzinfo=UTC))


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


# --------------------------------------------------------------------------- Росэлторг и Фабрикант


def test_roseltorg_parse_search_fixture():
    from app.sources.roseltorg import has_next_page, parse_search

    html = (FIX / "roseltorg/search_polotenca.html").read_text(encoding="utf-8")
    items = parse_search(html)
    assert len(items) == 10
    first = items[0]
    assert first.external_id == "32616427976/1" and first.eis_number == "32616427976"
    assert first.title == "Поставка полотенцесушителей"
    assert first.law == "223-ФЗ"
    assert first.customer_inn == "7709436992"
    assert "ЦЕНТР МАТЕРИАЛЬНО-ТЕХНИЧЕСКОГО" in first.customer_name
    assert first.region == "г. Москва"
    assert first.status == "Прием заявок"
    assert first.nmck == Decimal("195000.00")
    assert first.application_deadline.isoformat() == "2026-10-08T07:00:00+00:00"
    assert first.url == "https://www.roseltorg.ru/procedure/32616427976/1"
    commercial = next(d for d in items if d.external_id.startswith("SP10045625"))
    assert commercial.eis_number is None and commercial.law == "коммерческая"
    assert has_next_page(html)


def test_roseltorg_search_skips_closed_and_pages():
    from app.sources.roseltorg import RoseltorgAdapter

    html = (FIX / "roseltorg/search_polotenca.html").read_text(encoding="utf-8")
    pages = []

    def handler(request):
        pages.append(request.url.params["page"])
        return httpx.Response(200, text=html if request.url.params["page"] == "0" else "<html></html>")

    adapter = RoseltorgAdapter(client=client_for(handler, "https://www.roseltorg.ru"))
    stubs = asyncio.run(collect(adapter.search("полотенца", date(2026, 9, 1), date(2026, 10, 2))))
    assert pages == ["0", "1"]
    assert "SP10045625/1" not in {s.external_id for s in stubs}  # срок подачи 2025 года — закрыта
    details = asyncio.run(adapter.fetch_details(stubs[0]))
    assert details.title == stubs[0].title


def test_fabrikant_parse_search_fixture():
    from app.sources.fabrikant import parse_search

    items = parse_search((FIX / "fabrikant/search_polotenca.html").read_text(encoding="utf-8"))
    assert len(items) == 10
    first = items[0]
    assert first.external_id == "679704884"
    assert first.eis_number == "0372200119926000112" and first.law == "44-ФЗ"
    assert first.title.startswith("Поставка полотенец бумажных")
    assert first.customer_name == 'СПБ ГБУЗ "ГОРОДСКАЯ ПОЛИКЛИНИКА № 96"'
    assert first.published_at.isoformat() == "2026-10-02T14:17:00+00:00"
    assert first.application_deadline.isoformat() == "2026-10-09T07:00:00+00:00"
    assert first.nmck == Decimal("183390.00")
    assert first.url.startswith("https://44.fabrikant.ru/")
    assert first.is_open is True
    rosatom = items[1]
    assert rosatom.law == "коммерческая" and rosatom.status == "Идёт приём заявок"
    assert "СМОЛЕНСКАЯ АЭС-СЕРВИС" in rosatom.customer_name
    closed = next(d for d in items if d.external_id == "679665242")
    assert closed.is_open is False and closed.nmck == Decimal("6132.00")


def test_fabrikant_search_filters_window_and_status():
    from app.sources.fabrikant import FabrikantAdapter

    html = (FIX / "fabrikant/search_polotenca.html").read_text(encoding="utf-8")
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, text=html)

    adapter = FabrikantAdapter(client=client_for(handler, "https://www.fabrikant.ru"))
    stubs = asyncio.run(collect(adapter.search("полотенца", date(2026, 10, 1), date(2026, 10, 2))))
    ids = {s.external_id for s in stubs}
    assert seen[0]["query"] == "полотенца" and seen[0]["page_limit"] == "40"
    assert len(seen) == 1  # меньше 40 на странице — дальше не листаем
    assert "679665242" not in ids  # «Заключение контракта»
    assert "679662098" not in ids  # опубликована 30.09, раньше окна
    assert "679704884" in ids


# --------------------------------------------------------------------------- ЕИС напрямую


def test_eis_parse_search_fixture():
    from app.sources.eis import has_next_page, parse_search

    html = (FIX / "eis/search_polotenca.html").read_text(encoding="utf-8")
    items = parse_search(html)
    assert len(items) == 50
    first = items[0]
    assert first.eis_number == first.external_id == "0372200119926000112"
    assert first.law == "44-ФЗ" and first.procedure_type == "Запрос котировок в электронной форме"
    assert first.title.startswith("Поставка полотенец бумажных")
    assert first.nmck == Decimal("183390.00")
    assert first.status == "Подача заявок"
    # Дата без времени — конец дня по Москве
    assert first.application_deadline.isoformat() == "2026-10-09T20:59:00+00:00"
    assert first.url.endswith("/zk20/view/common-info.html?regNumber=0372200119926000112")
    fz223 = next(d for d in items if d.law == "223-ФЗ")
    assert fz223.customer_inn == "7448064962"
    assert fz223.url.endswith("/notice223/common-info.html?regNumber=32616427240")
    assert has_next_page(html)


def test_eis_parse_card223_takes_exact_deadline():
    from app.sources.eis import parse_card, parse_search

    base = parse_search((FIX / "eis/search_polotenca.html").read_text(encoding="utf-8"))[0]
    d = parse_card((FIX / "eis/card223.html").read_text(encoding="utf-8"), base)
    assert d.title == "Поставка полотенцесушителей"
    assert d.application_deadline.isoformat() == "2026-10-08T07:00:00+00:00"  # 10:00 МСК
    assert d.etp == "АКЦИОНЕРНОЕ ОБЩЕСТВО «ЕДИНАЯ ЭЛЕКТРОННАЯ ТОРГОВАЯ ПЛОЩАДКА»"
    assert d.customer_name.startswith("ГОСУДАРСТВЕННОЕ АВТОНОМНОЕ УЧРЕЖДЕНИЕ")
    assert d.revision == base.revision


def test_eis_deadline_with_customer_timezone():
    from app.sources.eis import _deadline

    assert _deadline("09.10.2026 10:00 (МСК+4)").isoformat() == "2026-10-09T03:00:00+00:00"


def test_eis_search_params_and_card_fallback():
    from app.sources.eis import EisAdapter

    html = (FIX / "eis/search_polotenca.html").read_text(encoding="utf-8")
    seen = []

    def handler(request):
        if "extendedsearch" in request.url.path:
            seen.append(dict(request.url.params))
            return httpx.Response(
                200, text=html if request.url.params["pageNumber"] == "1" else "<html></html>"
            )
        return httpx.Response(404, text="нет")

    adapter = EisAdapter(client=client_for(handler, "https://zakupki.gov.ru"))
    stubs = asyncio.run(collect(adapter.search("полотенца", date(2026, 9, 30), date(2026, 10, 2))))
    assert len(stubs) == 50
    assert seen[0]["publishDateFrom"] == "30.09.2026" and seen[0]["af"] == "on"
    assert [p["pageNumber"] for p in seen] == ["1", "2"]
    # Карточка не открылась — остаются данные из выдачи
    d = asyncio.run(adapter.fetch_details(stubs[0]))
    assert d.title.startswith("Поставка полотенец бумажных")
    # Обновление старого тендера без выдачи: ошибка карточки — это ошибка
    with pytest.raises(SourceError):
        asyncio.run(EisAdapter(client=client_for(handler)).fetch_details(stubs[0]))


def test_eis_parse_card44_positions_and_region():
    from app.sources.eis import parse_card, parse_search

    items = parse_search((FIX / "eis/search_polotenca.html").read_text(encoding="utf-8"))
    base = next(d for d in items if d.external_id == "0860200000826008057")
    d = parse_card((FIX / "eis/card44_ea.html").read_text(encoding="utf-8"), base)
    assert d.region == "Саратовская обл"
    assert d.application_deadline.isoformat() == "2026-10-14T04:00:00+00:00"  # 08:00 МСК+1
    assert d.etp == "РОСЭЛТОРГ (АО«ЕЭТП»)"
    assert d.delivery_place.startswith("Российская Федерация, обл. Саратовская")
    assert len(d.positions) == 1  # строка «Преимущество …» — не позиция
    p = d.positions[0]
    assert p.name.startswith("Полотенце бумажное") and p.code == "17.22.11.130-00000005"
    assert (p.qty, p.unit, p.price) == (1800.0, "Упаковка", 170.85)
    assert d.okpd2_codes == ["17.22.11.130", "17.22.11.130-00000005"]


# --------------------------------------------------------------------------- ТЭК-Торг


def test_tektorg_parse_search_fixture():
    from app.sources.tektorg import parse_search

    items, total_pages = parse_search((FIX / "tektorg/search_polotenca.html").read_text(encoding="utf-8"))
    assert len(items) == 15 and total_pages == 10
    first = items[0]
    assert first.external_id == "20077067"
    assert first.url == "https://www.tektorg.ru/223-fz/procedures/20077067"
    assert first.title == "Туалетная бумага и бумажные полотенца"
    assert first.status == "Приём заявок" and first.is_open is True
    assert first.nmck == Decimal("572283.72")
    assert first.customer_inn == "6663039677"
    assert first.okpd2_codes == ["17.22.11.110"]
    assert first.application_deadline.isoformat() == "2026-10-30T06:00:00+00:00"
    fz223 = next(d for d in items if d.eis_number)
    assert fz223.eis_number == "32616347508" and fz223.law == "223-ФЗ"
    assert all(d.is_open is False for d in items if d.status == "Архив")


def test_tektorg_search_uses_allowed_url_and_stops_at_old_items():
    from app.sources.tektorg import TektorgAdapter

    html = (FIX / "tektorg/search_polotenca.html").read_text(encoding="utf-8")
    urls = []

    def handler(request):
        urls.append(str(request.url))
        return httpx.Response(200, text=html)

    adapter = TektorgAdapter(client=client_for(handler, "https://www.tektorg.ru"))
    stubs = asyncio.run(collect(adapter.search("полотенца", date(2026, 10, 1), date(2026, 10, 22))))
    # robots.txt разрешает только «?name…»: name — первый параметр
    assert len(urls) == 1 and "/223-fz/procedures?name=" in urls[0]
    assert [s.external_id for s in stubs] == ["20077067"]  # остальные старше окна или закрыты
    d = asyncio.run(adapter.fetch_details(stubs[0]))
    assert d.title == "Туалетная бумага и бумажные полотенца"


def test_tektorg_page_without_data_is_an_error():
    from app.sources.tektorg import parse_search

    with pytest.raises(SourceError):
        parse_search("<html><body>нет данных</body></html>")


def test_eis_search_dates_are_approximate_until_card():
    from app.sources.eis import EisAdapter, parse_card, parse_search

    items = parse_search((FIX / "eis/search_polotenca.html").read_text(encoding="utf-8"))
    base = next(d for d in items if d.external_id == "0860200000826008057")
    assert base.approximate == {"application_deadline", "published_at"}
    d = parse_card((FIX / "eis/card44_ea.html").read_text(encoding="utf-8"), base)
    assert d.approximate == {"published_at"}  # в карточке точное время окончания подачи
    # robots.txt ЕИС: Crawl-delay 60
    adapter = EisAdapter()
    assert adapter.client.delay_min >= 60
    asyncio.run(adapter.aclose())


# --------------------------------------------------------------------------- Сбербанк-АСТ


def test_sberbank_parse_search_fixture():
    from app.sources.sberbank_ast import parse_search

    items, total = parse_search(load("sberbank_ast/search_polotenca.json"))
    assert len(items) == 20 and total == 10000
    first = items[0]  # 223-ФЗ: номер ЕИС — IntegratorCode
    assert first.external_id == "SBR003-260782140600001.1"
    assert first.eis_number == "32616496930" and first.law == "223-ФЗ"
    assert first.url == "https://utp.sberbank-ast.ru/Trade/NBT/PurchaseView/14/0/0/4265811"
    assert first.customer_inn == "7627002095" and first.region == "Ярославская область"
    assert first.nmck == Decimal("153425.41")
    assert first.application_deadline.isoformat() == "2026-10-30T04:00:00+00:00"  # 07:00 МСК
    assert "13.92.24.190" in first.okpd2_codes
    fz44 = next(d for d in items if d.external_id == "0859200001126012743")
    assert fz44.eis_number == "0859200001126012743" and fz44.law == "44-ФЗ"
    assert fz44.title == "Поставка мягкого инвентаря"
    failed = next(d for d in items if d.external_id == "SBR003-260611945600005.1")
    assert failed.is_open is False  # «Не состоялся(-ась)»
    no_deadline = next(d for d in items if d.external_id == "0356200000126000039")
    assert no_deadline.application_deadline is None and no_deadline.is_open is False


def test_sberbank_search_posts_filter_and_stops_at_old_items():
    from app.sources.sberbank_ast import SberbankAstAdapter

    data = load("sberbank_ast/search_polotenca.json")
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=data)

    adapter = SberbankAstAdapter(client=client_for(handler, "https://www.sberbank-ast.ru"))
    stubs = asyncio.run(
        collect(adapter.search("полотенца & <халаты>", date(2026, 10, 21), date(2026, 10, 22)))
    )
    assert len(bodies) == 1  # на первой странице уже есть закупки старше окна
    body = bodies[0]
    assert body["windowCode"] == "/EsOpenUnitedPurchaseList" and body["parm"] == "es"
    assert "<mainInput>полотенца &amp; &lt;халаты&gt;</mainInput>" in body["filterBody"]
    assert "<pagenum>0</pagenum>" in body["filterBody"]
    ids = {s.external_id for s in stubs}
    assert "0146200002926000007" in ids  # 22.10, приём заявок
    assert "SBR003-260611945600005.1" not in ids  # не состоялась
    assert "0372200070326000025" not in ids  # опубликована 20.10 — раньше окна
    d = asyncio.run(adapter.fetch_details(stubs[0]))
    assert d.title == stubs[0].title


def test_sberbank_unexpected_answer_is_an_error():
    from app.sources.sberbank_ast import parse_search

    with pytest.raises(SourceError):
        parse_search({"error": "нет доступа"})
