"""Страница ключевых слов: запуск приложения с миграциями, предпросмотр, применение, проверка текста."""

import pytest
from fastapi.testclient import TestClient

import app.db as db_module
from app.config import get_settings


@pytest.fixture
def client(tmp_path, monkeypatch, keywords_file):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("APP_PASSWORD", "secret")
    monkeypatch.setenv("KEYWORDS_FILE", str(keywords_file))
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    get_settings.cache_clear()
    db_module._engine = None
    db_module._session_factory = None
    from app.main import app

    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()
    db_module._engine = None
    db_module._session_factory = None


AUTH = ("velmorium", "secret")


def test_requires_password(client):
    assert client.get("/keywords").status_code == 401
    assert client.get("/keywords", auth=("velmorium", "wrong")).status_code == 401
    assert client.get("/health").status_code == 200


def test_keywords_page_shows_bootstrapped_file(client):
    r = client.get("/keywords", auth=AUTH)
    assert r.status_code == 200
    assert "Velmorium_ключевые_слова_тендеры.xlsx" in r.text
    assert "полотенц*" in r.text


def test_try_classification(client):
    r = client.post(
        "/keywords/try",
        auth=AUTH,
        data={"title": "Закупка тапочек одноразовых", "customer": "", "positions": ""},
    )
    assert r.status_code == 200
    assert "Релевантный" in r.text
    assert '<mark class="hl-product"' in r.text


def test_preview_and_activate(client, small_workbook):
    with small_workbook.open("rb") as f:
        r = client.post(
            "/keywords/preview",
            auth=AUTH,
            files={
                "file": ("small.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            },
        )
    assert r.status_code == 200
    assert "Предпросмотр: small.xlsx" in r.text
    token = r.text.split('name="token" value="')[1].split('"')[0]
    r = client.post(
        "/keywords/activate",
        auth=AUTH,
        data={"token": token, "filename": "small.xlsx"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    r = client.get("/keywords", auth=AUTH)
    assert "<b>small.xlsx</b>" in r.text


def test_reclassify_button(client):
    r = client.post("/keywords/reclassify", auth=AUTH)
    assert "Пересчитано тендеров: 0" in r.text


def test_import_page_lists_queries_and_sources(client):
    r = client.get("/import", auth=AUTH)
    assert r.status_code == 200
    assert "гостиничный текстиль" in r.text
    assert "bidzaar" in r.text


def test_import_form_and_api(client, monkeypatch):
    payload = {
        "source": "bidzaar",
        "tenders": [{"external_id": "1", "title": "Поставка тапочек одноразовых для отеля"}],
    }
    import json

    r = client.post("/import", auth=AUTH, data={"data": json.dumps(payload, ensure_ascii=False)})
    assert r.status_code == 200 and "новых 1" in r.text

    r = client.post("/import", auth=AUTH, data={"data": '{"source": "nope", "tenders": []}'})
    assert r.status_code == 400 and "Неизвестная площадка" in r.text

    assert client.post("/api/import", content=json.dumps(payload)).status_code == 401
    monkeypatch.setenv("IMPORT_TOKEN", "tok")
    get_settings.cache_clear()
    r = client.post("/api/import", content=json.dumps(payload), headers={"X-Import-Token": "tok"})
    assert r.status_code == 200 and r.json()["new"] == 0
    r = client.get("/api/queries", auth=AUTH)
    assert "полотенце махровое" in r.json()["queries"]


def _seed(client):
    from datetime import UTC, datetime, timedelta

    from app.db import get_session_factory
    from app.services.ingest import ingest
    from app.services.keywords import get_active_keyword_set
    from app.sources.base import Position, TenderDetails

    with get_session_factory()() as s:
        version, ks = get_active_keyword_set(s)
        soon = datetime.now(UTC) + timedelta(days=1)
        later = datetime.now(UTC) + timedelta(days=10)
        t1 = ingest(
            s,
            TenderDetails(
                "eis_gosplan",
                "fz44:1",
                "https://e/1",
                "Поставка полотенец махровых",
                eis_number="1",
                customer_name="ФГБУ «Санаторий «Дюны»",
                region="Москва",
                application_deadline=later,
                positions=[Position("Полотенце махровое 50х90")],
            ),
            ks,
            version.id,
        ).tender
        t2 = ingest(
            s,
            TenderDetails(
                "onlinecontract",
                "2",
                "https://o/2",
                "Поставка подушек",
                customer_name="ООО «Ромашка»",
                application_deadline=soon,
            ),
            ks,
            version.id,
        ).tender
        s.commit()
        return t1.id, t2.id


def test_tender_list_tabs_and_card(client):
    t1, t2 = _seed(client)
    r = client.get("/", auth=AUTH)
    assert r.status_code == 200
    assert "Поставка полотенец махровых" in r.text and "Поставка подушек" in r.text  # вкладка «Новые»
    # ближайший срок — первым, с подсветкой
    assert r.text.index("Поставка подушек") < r.text.index("Поставка полотенец махровых")
    assert 'class="urgent"' in r.text
    r = client.get("/?tab=relevant", auth=AUTH)
    assert "Поставка полотенец махровых" in r.text and "Поставка подушек" not in r.text
    r = client.get("/?tab=all&q=подуш", auth=AUTH)
    assert "Найдено: 1" in r.text
    r = client.get("/?tab=all&source=onlinecontract", auth=AUTH)
    assert "Найдено: 1" in r.text

    r = client.get(f"/tenders/{t1}", auth=AUTH)
    assert r.status_code == 200
    assert '<mark class="hl-marker"' in r.text and '<mark class="hl-product"' in r.text
    assert "https://e/1" in r.text

    assert (
        client.post(f"/tenders/{t1}/status", auth=AUTH, data={"user_status": "в работе"}).status_code == 200
    )
    assert client.post(f"/tenders/{t1}/notes", auth=AUTH, data={"notes": "позвонить"}).status_code == 200
    assert "позвонить" in client.get(f"/tenders/{t1}", auth=AUTH).text
    assert client.get("/tenders/999999", auth=AUTH).status_code == 404


def test_mark_seen_and_header_counters(client):
    t1, t2 = _seed(client)
    r = client.get("/", auth=AUTH)
    assert "badge-relevant" in r.text
    client.post(f"/tenders/{t1}/seen", auth=AUTH)
    r = client.get("/?tab=new", auth=AUTH)
    assert "Поставка полотенец махровых" not in r.text
    client.post("/tenders/seen-all?tab=new", auth=AUTH)
    assert "Найдено: 0" in client.get("/?tab=new", auth=AUTH).text


def test_export_xlsx(client):
    import io

    import openpyxl

    _seed(client)
    r = client.get("/export.xlsx?tab=all", auth=AUTH)
    assert r.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    rows = list(wb.active.iter_rows(values_only=True))
    assert rows[0][0] == "Статус" and len(rows) == 3
    assert any("Поставка подушек" in str(row) for row in rows)


def test_sources_page_and_alerts(client):
    r = client.get("/sources", auth=AUTH)
    assert r.status_code == 200
    assert "ТЭК-Торг" in r.text and "ещё ни разу не проверена" in r.text
    # Тревога о непроверенных площадках видна на главном экране
    assert "не проверена" in client.get("/", auth=AUTH).text
    r = client.post("/sources/tektorg/toggle", auth=AUTH, follow_redirects=False)
    assert r.status_code == 303
    assert "выключена в настройках" in client.get("/sources", auth=AUTH).text


def test_runs_and_notifications_pages(client):
    assert client.get("/runs", auth=AUTH).status_code == 200
    assert client.get("/runs/progress", auth=AUTH).status_code == 200
    r = client.get("/notifications", auth=AUTH)
    assert "Загружена новая версия ключевых слов" in r.text
    client.post("/notifications/read-all", auth=AUTH)


def test_schedule_form(client):
    r = client.post(
        "/schedule",
        auth=AUTH,
        data={"interval_hours": "24", "start_time": "07:15", "enabled": "true"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    page = client.get("/sources", auth=AUTH).text
    assert 'value="07:15"' in page and 'value="24"' in page
    r = client.post("/schedule", auth=AUTH, data={"interval_hours": "24", "start_time": "утром"})
    assert "ЧЧ:ММ" in r.text
