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
