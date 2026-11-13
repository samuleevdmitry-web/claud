"""Страница дашборда на настоящем PostgreSQL с синтетическими данными (нужен TEST_DATABASE_URL)."""

import os
from datetime import date, timedelta

import pytest

from analytics import db
from analytics.dashboard.pages import trend_months
from analytics.format import delta, fmt_change, fmt_duration, fmt_int, fmt_pct
from analytics.sync import sync_metrika, sync_webmaster
from tests.fakes import FakeMetrikaClient, FakeWebmasterClient, dim

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL не задан")


def test_formatting():
    assert fmt_int(23061) == "23 061"
    assert fmt_pct(25.384) == "25,4%"
    assert fmt_duration(153.9) == "2 мин 34 с"
    assert fmt_change(120, 100) == "+20,0%" and fmt_change(80, 100) == "−20,0%"
    assert delta(17.2, 18.1, "pp", good="down").tone == "good"
    assert delta(7.8, 7.67, "abs", good="down").tone == "bad"
    assert delta(100, 100, "pct").text == "без изменений"


def test_trend_months_keeps_gaps_and_drops_current():
    rows = [
        {"month": date(2026, 7, 1), "days_with_data": 31, "days_in_month": 31, "visits": 10},
        {"month": date(2026, 8, 1), "days_with_data": 12, "days_in_month": 31, "visits": 3},
        {"month": date(2026, 9, 1), "days_with_data": 30, "days_in_month": 30, "visits": 12},
        {"month": date(2026, 11, 1), "days_with_data": 10, "days_in_month": 30, "visits": 4},
    ]
    shown, skipped = trend_months(rows, current=date(2026, 11, 1), coverage=True)
    assert [(r["month"].month, r.get("visits")) for r in shown] == [(7, 10), (8, None), (9, 12)]
    assert skipped == ["авг 2026 (12 из 31 дн.)"]


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from analytics.dashboard.app import app

    for name, value in {
        "DATABASE_URL": TEST_DATABASE_URL or "",
        "METRIKA_COUNTERS": "42",
        "METRIKA_MAPS_COUNTERS": "",
        "WEBMASTER_HOSTS": "https:example.ru:443",
        "METRIKA_LEAD_GOALS": "1",
    }.items():
        monkeypatch.setenv(name, value)
    with db.connect(TEST_DATABASE_URL) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        db.apply_schema(conn)
    return TestClient(app)


@needs_db
def test_empty_database_explains_itself(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "Данных пока нет" in page.text


@needs_db
def test_first_load_in_progress(client):
    with db.connect(TEST_DATABASE_URL) as conn:
        db.upsert_counter(conn, {"id": 42, "name": "example.ru", "site2": {"site": "example.ru"}}, "site")
    page = client.get("/")
    assert page.status_code == 200
    assert "Данных пока нет" in page.text


@needs_db
def test_page_with_data(client):
    aug, sep = date(2026, 8, 1), date(2026, 9, 1)
    data = {
        ("month", "total"): [(aug, [], [1000, 800, 2500, 20.0, 2.5, 150.0], {1: (10, 9)}),
                             (sep, [], [1200, 900, 3000, 18.0, 2.6, 160.0], {1: (15, 14)})],
        ("month", "source"): [(sep, [dim("Переходы из поисковых систем", "organic")],
                               [700, 500, 2000, 9.0, 3.0, 200.0], {1: (12, 11)})],
    }
    goals = [{"id": 1, "name": "Отправил контактные данные", "type": "contact_data_sent"}]
    with db.connect(TEST_DATABASE_URL) as conn:
        sync_metrika(conn, FakeMetrikaClient(goals, data), 42, frozenset({1}), "lastsign",
                     since=aug, until=date(2026, 9, 30), today=date(2026, 10, 2), log=lambda _: None)
        search = {date(2026, 9, d): (100, 5, 8.0, 3.0) for d in range(1, 31)}
        sync_webmaster(conn, FakeWebmasterClient(search, [], []), "https:example.ru:443",
                       since=aug, until=date(2026, 9, 30), today=date(2026, 10, 2), log=lambda _: None)

    page = client.get("/", params={"month": "2026-09"})
    assert page.status_code == 200
    text = page.text
    assert "Сентябрь 2026" in text and "сравнение с августом 2026" in text
    assert "1 200" in text                      # визиты за сентябрь
    assert "▲ 20,0%" in text                    # +20% к августу
    assert "Переходы из поисковых систем" in text   # таблица каналов
    assert "3 000" in text                      # показы в поиске за сентябрь (100 × 30)
    assert 'id="chart-data"' in text


@needs_db
def test_every_section_renders(client):
    aug, sep = date(2026, 8, 1), date(2026, 9, 1)
    china = dim("Китай", "134")
    data = {
        ("month", "total"): [(aug, [], [1000, 800, 2500, 20.0, 2.5, 150.0], {1: (10, 9)}),
                             (sep, [], [1200, 900, 3000, 18.0, 2.6, 160.0], {1: (15, 14)})],
        ("month", "country"): [(sep, [dim("Россия", "225")], [1000, 750, 2600, 15.0, 2.8, 170.0], {1: (15, 14)}),
                               (sep, [china], [200, 150, 400, 60.0, 1.1, 20.0], {})],
        ("month", "hour"): [(sep, [dim(f"{h}:00", str(h))], [50, 40, 100, 18.0, 2.0, 100.0], {1: (1, 1)}) for h in range(24)],
    }
    goals = [{"id": 1, "name": "Отправил контактные данные", "type": "contact_data_sent"}]
    with db.connect(TEST_DATABASE_URL) as conn:
        sync_metrika(conn, FakeMetrikaClient(goals, data, humans_share=0.8), 42, frozenset({1}), "lastsign",
                     since=aug, until=date(2026, 9, 30), today=date(2026, 10, 2), log=lambda _: None)
    for path in ("/", "/traffic", "/traffic?tab=geo", "/traffic?tab=time", "/traffic?tab=sources", "/leads",
                 "/search", "/pf", "/recs", "/report", "/sales", "/keywords",
                 "/keywords/detail?k=ткань", "/demand", "/maps", "/status", "/traffic?tab=content",
                 "/traffic?tab=audience", "/traffic?tab=tech"):
        page = client.get(path, params={"month": "2026-09"})
        assert page.status_code == 200, path
    geo = client.get("/traffic", params={"month": "2026-09", "tab": "geo", "aud": "all"}).text
    assert "Аналитическая сводка" in geo and "Китай" in geo  # вывод про страны без продаж
    humans = client.get("/", params={"month": "2026-09", "aud": "humans"}).text
    assert "960" in humans  # 1200 визитов × 0,8 — «только люди» читаются отдельно
    status = client.get("/status", params={"month": "2026-09"}).text
    assert "Роботы в трафике" in status and "20,0%" in status


@needs_db
def test_pf_section_flags_bot_traces(client):
    sep, oct_ = date(2026, 9, 1), date(2026, 10, 1)
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(42)]
    yandex, google = dim("Яндекс", "yandex"), dim("Google", "google")
    data = {
        ("month", "total"): [(sep, [], [1000, 800, 2500, 20.0, 2.5, 150.0], {}), (oct_, [], [900, 700, 2200, 20.0, 2.5, 150.0], {})],
        ("day", "total"): [(d, [], [60, 50, 150, 20.0, 2.5, 150.0], {}) for d in days],
        # последнюю неделю Яндекс проседает вдвое, Google — нет
        ("day", "search_engine"): [(d, [yandex], [15 if d >= days[-7] else 30, 25, 60, 10.0, 2.5, 150.0], {}) for d in days]
                                  + [(d, [google], [30, 25, 60, 10.0, 2.5, 150.0], {}) for d in days],
    }
    with db.connect(TEST_DATABASE_URL) as conn:
        sync_metrika(conn, FakeMetrikaClient([], data), 42, frozenset(), "lastsign",
                     since=sep, until=days[-1], today=days[-1] + timedelta(days=1), log=lambda _: None)
        host = "https:example.ru:443"
        conn.execute("INSERT INTO webmaster_summary (host_id, snapshot_date) VALUES (%s, %s)", (host, days[-1]))
        rows = [(host, date(2026, 10, 5), "ткань для тюбингов", 1, 1, 12.0),
                (host, date(2026, 10, 5), "ленточная фурнитура", 9, 9, 11.0),
                (host, date(2026, 10, 6), "ткань для тюбингов example.ru", 3, 2, 1.0),
                (host, date(2026, 10, 6), "обычный запрос", 500, 4, 14.0)]
        conn.cursor().executemany("INSERT INTO webmaster_query_daily (host_id, date, query, impressions, clicks, position) "
                         "VALUES (%s, %s, %s, %s, %s, %s)", rows)
        conn.cursor().executemany("INSERT INTO rabbit_positions (project, date, query, frequency, position) VALUES (%s, %s, %s, %s, %s)",
                         [("example.ru", date(2026, 10, 6), q, 5, p) for q, p in (("ткань для тюбингов", 12),
                                                                                  ("ленточная фурнитура", 1))])
    page = client.get("/pf", params={"month": "2026-10", "aud": "all"}).text
    assert "Тревога" in page and "Похоже на санкции Яндекса" in page
    assert "ткань для тюбингов example.ru" in page
    overview = client.get("/", params={"month": "2026-10", "aud": "all"}).text
    assert "ПФ: Похоже на санкции" in overview


@needs_db
def test_monthly_report_file(client, tmp_path, monkeypatch):
    aug, sep = date(2026, 8, 1), date(2026, 9, 1)
    data = {("month", "total"): [(aug, [], [1000, 800, 2500, 20.0, 2.5, 150.0], {1: (10, 9)}),
                                 (sep, [], [1300, 900, 3000, 18.0, 2.6, 160.0], {1: (15, 14)})]}
    goals = [{"id": 1, "name": "Отправил контактные данные", "type": "contact_data_sent"}]
    with db.connect(TEST_DATABASE_URL) as conn:
        sync_metrika(conn, FakeMetrikaClient(goals, data), 42, frozenset({1}), "lastsign",
                     since=aug, until=date(2026, 9, 30), today=date(2026, 10, 2), log=lambda _: None)
    page = client.get("/report", params={"month": "2026-09"}).text
    assert "Показатели" in page and "Рекомендации" in page and "1\xa0300" in page

    from analytics.cli import make_reports
    from analytics.config import load_settings

    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    saved = make_reports(load_settings(), sep, date(2026, 10, 6))
    assert [p.name for p in saved] == ["2026-09 example.ru.html"]
    html = saved[0].read_text(encoding="utf-8")
    assert "Отчёт за сентябрь 2026 — example.ru" in html and "<style>" in html and "1\xa0300" in html
    with db.connect(TEST_DATABASE_URL) as conn:
        assert conn.execute("SELECT status FROM sync_runs WHERE source = 'report'").fetchone() == ("ok",)
    assert make_reports(load_settings(), date(2026, 3, 1), date(2026, 10, 6)) == []  # за март данных нет
