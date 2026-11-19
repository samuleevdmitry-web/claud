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
                 "/search", "/pf", "/recs", "/report", "/sales", "/email", "/keywords",
                 "/keywords/detail?k=ткань", "/demand", "/maps", "/status", "/traffic?tab=content",
                 "/traffic?tab=audience", "/traffic?tab=tech"):
        page = client.get(path, params={"month": "2026-09"})
        assert page.status_code == 200, path
        assert "не построен" not in page.text and "Не всё удалось построить" not in page.text, path
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


@needs_db
def test_email_month_without_orders(client):
    """Рассылки сентября с отчётами по адресам, а заказы 1С — только за октябрь: страница не падает, а говорит,
    что заказов за месяц нет; рассылка без заказов за её 14 дней не считается «без заказов»."""
    from analytics import privacy
    from analytics.sources.unisender import Delivery, parse_campaign, parse_stats

    sep = date(2026, 9, 1)
    with db.connect(TEST_DATABASE_URL) as conn:
        sync_metrika(conn, FakeMetrikaClient([], {("month", "total"): [(sep, [], [1000, 800, 2500, 20.0, 2.5, 150.0], {})]}),
                     42, frozenset(), "lastsign", since=sep, until=date(2026, 9, 30), today=date(2026, 10, 2),
                     log=lambda _: None)
        early = parse_campaign({"id": 1, "start_time": "2026-09-05 10:00:00", "list_id": 1, "subject": "Флис"})
        late = parse_campaign({"id": 2, "start_time": "2026-09-25 10:00:00", "list_id": 1, "subject": "Мембраны"})
        db.save_unisender(conn, {1: "Оптовики"}, [early, late],
                          [parse_stats(1, {"sent": 10, "delivered": 10}), parse_stats(2, {"sent": 10, "delivered": 10})])
        h = privacy.email_hash("buyer@example.ru")
        for cid in (1, 2):
            db.save_recipients(conn, cid, [Delivery(h, "ok_read", 2), Delivery(privacy.email_hash("x@y.ru"), "ok_delivered", 1)],
                               [])
        conn.execute("INSERT INTO orders_1c (order_no, order_date, amount, status, email_hashes) VALUES"
                     " ('А-1', '2026-10-02', 1000, 'Закрыт', %s), ('А-2', '2026-10-30', 10, 'Закрыт', '{}')", ([h],))
    resp = client.get("/email?site=42&month=2026-09")
    assert resp.status_code == 200, resp.text[-2000:]
    assert "Заказов за сентябрь" in resp.text
    from analytics.config import load_settings
    from analytics.dashboard import pages
    with db.connect(TEST_DATABASE_URL) as conn:
        f = pages.resolve_filters(conn, load_settings(), 42, "2026-09", None, date(2026, 11, 19))
        mail = pages.email_data(conn, load_settings(), f)["mail"]
    rows = {r["campaign_id"]: r for r in mail["campaigns"]}
    # у рассылки 05.09 все 14 дней — до начала выгрузки заказов (с 01.10): её заказы неизвестны, а не нулевые;
    # у рассылки 25.09 известна только часть окна — в сравнение открывших и не открывших она не входит
    assert rows[1]["coverage"] == "none" and rows[2]["coverage"] == "partial"
    assert rows[2]["opened_ordered"] == 1 and mail["lift"] is None
    assert "нет выгрузки заказов" in resp.text


def test_guard_keeps_the_rest_of_the_page():
    from analytics.dashboard import guard
    from analytics.dashboard.app import templates

    problems = guard.start()
    html = templates.env.from_string('до {% guard "Таблица X" %}{{ 1 / 0 }}{% endguard %} после').render()
    assert html.startswith("до ") and html.endswith(" после")
    assert "Таблица X</strong> — не построен: ZeroDivisionError" in html
    assert guard.safe("Блок Y", lambda: 1 / 0, default=[]) == []
    assert [p.title for p in problems] == ["Таблица X", "Блок Y"]


@needs_db
def test_broken_section_does_not_break_other_pages(client, monkeypatch):
    """E-mail сломался: обзор и рекомендации строятся без него и пишут, что не построено; сам раздел показывает
    причину вместо «Internal Server Error», меню остаётся."""
    from analytics.dashboard import pages

    sep = date(2026, 9, 1)
    with db.connect(TEST_DATABASE_URL) as conn:
        sync_metrika(conn, FakeMetrikaClient([], {("month", "total"): [(sep, [], [1000, 800, 2500, 20.0, 2.5, 150.0], {})]}),
                     42, frozenset(), "lastsign", since=sep, until=date(2026, 9, 30), today=date(2026, 10, 2),
                     log=lambda _: None)

    def broken(*args, **kwargs):
        raise RuntimeError("сломалось")

    monkeypatch.setattr(pages, "email_data", broken)
    for path in ("/", "/recs", "/report"):
        page = client.get(path, params={"month": "2026-09"})
        assert page.status_code == 200, path
        assert "«E-mail» — RuntimeError: сломалось" in page.text, path
    email = client.get("/email", params={"month": "2026-09"})
    assert email.status_code == 200 and "Раздел не построен: RuntimeError: сломалось" in email.text
    assert "<h1>E-mail</h1>" in email.text
    assert 'href="/traffic' in email.text  # меню на месте


def mailing_positions_file(path):
    """Как «Заказы_по_позициям_рассылок»: листы «Совпадения», «Общий список», «Позиции рассылок»."""
    import openpyxl

    lotus, salmi = "МЕМБРАННАЯ ТКАНЬ ЛОТУС ВО ТПУ 3К/3К, 145г/м2 1,45м", "КУРТОЧНАЯ ТКАНЬ САЛМИ 22YSD"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Совпадения"
    ws.append(["Заказ 1С", "Номенклатура 1С", "Цвет", "Количество для рассылки", "Цена из рассылки", "Сумма позиции, ₽",
               "Телефон", "Электронная почта", "Дата заказа", "Дата рассылки", "Когда оформлен заказ", "Рассылка",
               "Клиент / контрагент", "Источник количества"])
    rows = [
        ("АТЦБ-1", lotus, "черный", 246, 205, 50430, "17-344-51-40", "A@x.ru", "09.11.2026", "21.10.2026", "Мембранная ткань ЛОТУС", "Анна ИП"),
        ("АТЦБ-1", lotus, "серый", 1, 205, 205, "17-344-51-40", "A@x.ru", "09.11.2026", "21.10.2026", "Мембранная ткань ЛОТУС", "Анна ИП"),
        ("АТЦБ-2", lotus, "черный", 1, 205, 205, "Нет в исходном файле", "other@y.ru", "10.11.2026", "21.10.2026", "Мембранная ткань ЛОТУС", "Борис"),
        ("АТЦБ-3", lotus, "серый", 5, 205, 1000, "Нет в исходном файле", "d@x.ru", "25.10.2026", "21.10.2026", "Мембранная ткань ЛОТУС", "Дина ИП"),
        ("АТЦБ-4", salmi, "чёрный", 100, 122, 12200, "Нет в исходном файле", "a@x.ru", "12.11.2026", "29.10.2026", "Новые цвета САЛМИ", "Анна ИП"),
        ("АТЦБ-5", "КАТАФОТ 50мм прямоугольник", "жёлтый", 200, 26, 5200, "Представление=8(495)544 51-97",
         "Нет в исходном файле", "13.11.2026", "13.10.2026", "Акция на световозвращающие материалы", "Игнаткина ИП"),
        ("АТЦБ-7", lotus, "черный", 1, 205, 205, "", "a@x.ru", "05.12.2026", "21.10.2026", "Мембранная ткань ЛОТУС", "Анна ИП"),
    ]
    for no, item, color, qty, price, amount, phone, email, day, sent, mailing, client in rows:
        ws.append([no, item, color, qty, price, amount, phone, email, day, sent, "Заказ через N дней после рассылки",
                   mailing, client, "Продано"])
    ws = wb.create_sheet("Общий список")
    ws.append(["Заказ 1С", "Дата заказа", "Номенклатура 1С", "Цвет", "Сумма позиции, ₽", "Клиент / контрагент", "Электронная почта"])
    for no, day, item, color, amount, client, email in [
        ("АТЦБ-1", "09.11.2026", lotus, "черный", 50430, "Анна ИП", "a@x.ru"),
        ("АТЦБ-1", "09.11.2026", lotus, "серый", 205, "Анна ИП", "a@x.ru"),
        ("АТЦБ-1", "09.11.2026", "ФЛИС 280", "синий", 3000, "Анна ИП", "a@x.ru"),
        ("АТЦБ-6", "25.10.2026", "ФЛИС 280", "синий", 700, "Вера", "b@x.ru"),
    ]:
        ws.append([no, day, item, color, amount, client, email])
    ws = wb.create_sheet("Позиции рассылок")
    ws.append(["Рассылка", "Дата рассылки", "Номенклатура 1С", "Цвет", "Цена"])
    for mailing, sent, item in [("Мембранная ткань ЛОТУС", "21.10.2026", lotus),
                                ("Мембранная ткань ЛОТУС", "21.10.2026", "МЕМБРАНА ЛОТУС 2К"),
                                ("Новые цвета САЛМИ", "29.10.2026", salmi),
                                ("Акция на световозвращающие материалы", "13.10.2026", "КАТАФОТ 50мм прямоугольник")]:
        ws.append([mailing, sent, item, "", 205])
    wb.save(path)


@needs_db
def test_mailing_items_pivot(client, tmp_path):
    """Позиции рассылок × позиции заказов × отчёт о доставке: кто купил товар из рассылки, получил ли он письмо."""
    from analytics import privacy
    from analytics.config import load_settings
    from analytics.dashboard import pages
    from analytics.sources.unisender import (
        Contact,
        Delivery,
        parse_campaign,
        parse_stats,
    )
    from analytics.sync import import_crm_folder

    oct_ = date(2026, 10, 1)
    mailing_positions_file(tmp_path / "Заказы_по_позициям_рассылок.xlsx")
    h = privacy.email_hash
    with db.connect(TEST_DATABASE_URL) as conn:
        sync_metrika(conn, FakeMetrikaClient([], {("month", "total"): [(oct_, [], [1000, 800, 2500, 20.0, 2.5, 150.0], {})]}),
                     42, frozenset(), "lastsign", since=oct_, until=date(2026, 10, 31), today=date(2026, 11, 2),
                     log=lambda _: None)
        logs = []
        import_crm_folder(conn, tmp_path, log=logs.append)
        text = "\n".join(logs)
        assert "лист «Совпадения»: заказы" in text and "лист «Позиции рассылок»: позиции рассылок — 4 позиций в 3 рассылках" in text
        assert "номенклатура ← «Номенклатура 1С»" in text and "рассылка ← «Рассылка»" in text
        # у АТЦБ-1 позиции — с листа, где их больше (3 строки «Общего списка», а не 2 «Совпадений»)
        assert conn.execute("SELECT count(*) FROM order_lines WHERE order_no = 'АТЦБ-1'").fetchone()[0] == 3
        campaigns = [parse_campaign({"id": 201, "start_time": "2026-10-21 10:00:00", "list_id": 1, "subject": "Мембрана ЛОТУС — в наличии"}),
                     parse_campaign({"id": 202, "start_time": "2026-10-29 10:00:00", "list_id": 1, "subject": "САЛМИ: новые цвета"}),
                     parse_campaign({"id": 203, "start_time": "2026-10-21 12:00:00", "list_id": 2, "subject": "Тест ЛОТУС"})]
        db.save_unisender(conn, {1: "Оптовики", 2: "Сотрудники"}, campaigns, [parse_stats(c.campaign_id, {}) for c in campaigns])
        base = ["a@x.ru", "b@x.ru", "c@x.ru", "d@x.ru"] + [f"e{i}@x.ru" for i in range(20)]
        db.replace_email_base(conn, [Contact(h(e), None, "active", (1,)) for e in base])
        db.save_recipients(conn, 201, [Delivery(h("a@x.ru"), "ok_read", 2), Delivery(h("b@x.ru"), "ok_delivered", 1),
                                       Delivery(h("c@x.ru"), "ok_link_visited", 3)], [])
        db.save_recipients(conn, 202, [Delivery(h("a@x.ru"), "ok_delivered", 1), Delivery(h("d@x.ru"), "ok_delivered", 1)], [])
        db.save_recipients(conn, 203, [Delivery(h("d@x.ru"), "ok_read", 2)], [])  # список сотрудников — не в счёт
        # АТЦБ-6 — новый клиент со звонка (раздел «Продажи»): в общей таблице заказов — отдельно, не от рассылок
        conn.execute("INSERT INTO crm_call_orders (month, order_no, category, order_date, amount) VALUES"
                     " ('2026-10-01', 'АТЦБ-6', 'new', '2026-10-25', 700)")
        f = pages.resolve_filters(conn, load_settings(), 42, "2026-10", None, date(2026, 11, 19))
        data = pages.email_data(conn, load_settings(), f)
        items = data["items"]
    classes = {c["key"]: c["orders"] for c in data["mail"]["classes"]}
    assert classes["new_lead"] == 1 and classes["no_mail"] == 1 and data["mail"]["mail_orders"] == 0
    rows = {r["mailing"]: r for r in items["rows"]}
    lotus, salmi, promo = rows["Мембранная ткань ЛОТУС"], rows["Новые цвета САЛМИ"], rows["Акция на световозвращающие материалы"]
    assert [c["campaign_id"] for c in lotus["campaigns"]] == [201] and lotus["items"] == 2
    # купили ЛОТУС за 35 дней: АТЦБ-1 (получила и открыла письмо), АТЦБ-3 (в базе, письма не было), АТЦБ-2 (не из базы);
    # АТЦБ-7 — через 45 дней, не в счёт; ФЛИС из АТЦБ-1 — не товар рассылки
    assert (lotus["bought"]["orders"], lotus["bought"]["amount"]) == (3, 51840.0)
    assert (lotus["received"]["orders"], lotus["received"]["amount"], lotus["opened_buyers"]["orders"]) == (1, 50635.0, 1)
    assert (lotus["rest_base"]["orders"], lotus["outside"]["orders"]) == (1, 1)
    assert (lotus["delivered"], lotus["opened"]) == (3, 2)
    assert round(lotus["rate_r"], 1) == 33.3 and round(lotus["rate_n"], 2) == 4.76 and round(lotus["uplift"], 2) == 0.86
    assert (salmi["received"]["orders"], salmi["opened_buyers"]["orders"], salmi["received_14"]["orders"]) == (1, 0, 1)
    assert not promo["campaigns"] and promo["bought"]["orders"] == 1 and promo["rate_r"] is None
    page = client.get("/email", params={"month": "2026-10"}).text
    assert "не построен" not in page and "Не всё удалось построить" not in page
    assert "Рассылки октябрь 2026 и покупки товаров из них" in page and "письмо в UniSender не найдено" in page
    assert "51 840 ₽" in page.replace("\xa0", " ")
    assert "Товары из рассылок купили в 5" in page.replace("\xa0", " ")
    assert "Новые клиенты из лидов с сайта и звонков" in page and "Не связаны с рассылками" in page


@needs_db
def test_recipient_lists(client, tmp_path, monkeypatch):
    """EMAIL_LISTS=on: кто из сотрудников открыл письма (имена из файла сотрудников), кто из базы открывает, а кто нет."""
    import openpyxl

    from analytics import privacy
    from analytics.config import load_settings
    from analytics.sources.unisender import Delivery, parse_campaign, parse_stats
    from analytics.sync import import_crm_folder

    monkeypatch.setenv("EMAIL_LISTS", "on")
    oct_ = date(2026, 10, 1)
    h = privacy.email_hash
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "users (6)"
    ws.append(["Сотрудник", "E-Mail", "Мобильный телефон"])
    for row in (["Иванов Иван", "ivanov@oldos.ru", "+7 900 000-00-01"], ["Петрова Анна", "petrova@oldos.ru", ""],
                ["Сидоров Олег", "sidorov@oldos.ru", ""], ["Новиков Пётр", "novikov@oldos.ru", ""]):
        ws.append(row)
    wb.save(tmp_path / "пользователи битрикс.xlsx")
    with db.connect(TEST_DATABASE_URL) as conn:
        sync_metrika(conn, FakeMetrikaClient([], {("month", "total"): [(oct_, [], [1000, 800, 2500, 20.0, 2.5, 150.0], {})]}),
                     42, frozenset(), "lastsign", since=oct_, until=date(2026, 10, 31), today=date(2026, 11, 2),
                     log=lambda _: None)
        logs = []
        import_crm_folder(conn, tmp_path, log=logs.append, keep_contacts=True)
        assert any("сотрудники — 4; имена и адреса сохранены" in x for x in logs)
        campaigns = [parse_campaign({"id": 301, "start_time": "2026-10-05 10:00:00", "list_id": 2, "subject": "Новинки для отдела продаж"}),
                     parse_campaign({"id": 302, "start_time": "2026-10-06 10:00:00", "list_id": 1, "subject": "Мембраны"})]
        db.save_unisender(conn, {1: "Оптовики", 2: "Сотрудники"}, campaigns, [parse_stats(c.campaign_id, {}) for c in campaigns])
        db.save_recipients(conn, 301, [Delivery(h("ivanov@oldos.ru"), "ok_link_visited", 3),
                                       Delivery(h("petrova@oldos.ru"), "ok_delivered", 1),
                                       Delivery(h("sidorov@oldos.ru"), "ok_read", 2)], [])
        clients = [Delivery(h("buyer@x.ru"), "ok_read", 2, "buyer@x.ru"), Delivery(h("sleep@x.ru"), "ok_delivered", 1, "sleep@x.ru"),
                   Delivery(h("ivanov@oldos.ru"), "ok_read", 2, "ivanov@oldos.ru")]  # сотрудник в базе — не клиент
        db.save_recipients(conn, 302, clients, [])
        db.save_email_contacts(conn, [(d.email_hash, d.email) for d in clients])
        conn.execute("INSERT INTO orders_1c (order_no, order_date, amount, status, email_hashes) VALUES"
                     " ('А-1', '2026-10-10', 5000, 'Закрыт', %s)", ([h("buyer@x.ru")],))
        # клиенты, которых нет в базе рассылок: один с почтой и двумя заказами, другой только с телефоном
        conn.execute("INSERT INTO orders_1c (order_no, order_date, amount, status, client_hash, email_hashes, phone_hashes,"
                     " client_name, emails, phones) VALUES"
                     " ('Б-1', '2026-10-12', 1000, 'Закрыт', 'c-new', %s, '{}', 'ООО Новый', '{new@client.ru}', '{}'),"
                     " ('Б-2', '2026-10-20', 2000, 'Закрыт', 'c-new', %s, '{}', 'ООО Новый', '{new@client.ru}', '{}'),"
                     " ('Б-3', '2026-10-21', 700, 'Закрыт', NULL, '{}', %s, 'Физическое лицо', '{}', '{9174706444}')",
                     ([h("new@client.ru")], [h("new@client.ru")], [privacy.phone_hash("9174706444")]))
        assert load_settings().email_lists
    page = " ".join(client.get("/email", params={"month": "2026-10"}).text.split())
    assert "не построен" not in page and "Не всё удалось построить" not in page
    assert "Открывали хотя бы одно — <strong>2</strong> из 3" in page  # сотрудники: Иванов и Сидоров
    assert "Петрова Анна" in page and "Иванов Иван · перешёл по ссылке" in page
    assert "Есть в файле сотрудников, но письма не приходили — 1" in page and "Новиков Пётр" in page
    assert "buyer@x.ru" in page and "sleep@x.ru" in page and '<details class="list">' in page
    # база без сотрудников: получили buyer и sleep, открыл только buyer
    assert "получили 2 адресов (без сотрудников). Открывали хотя бы одно — <strong>1</strong>" in page
    assert "Клиенты, которых нет в базе рассылок — 2, с почтой — 1" in page
    assert "ООО Новый" in page and "new@client.ru" in page and "+7 917 470-64-44" in page
    outside = client.get("/email/contacts.csv", params={"month": "2026-10", "kind": "outside"}).text
    assert outside.splitlines()[1:] == ["new@client.ru;ООО Новый;;2;3000;20.10.2026",
                                        ";Физическое лицо;+79174706444;1;700;21.10.2026"]
    csv_text = client.get("/email/contacts.csv", params={"month": "2026-10", "kind": "active"}).text
    assert "buyer@x.ru;1;1;0;06.10.2026;06.10.2026;1;5000;10.10.2026" in csv_text and "ivanov" not in csv_text
    monkeypatch.setenv("EMAIL_LISTS", "")
    page = client.get("/email", params={"month": "2026-10"}).text
    assert "Списки с адресами и именами выключены" in page and "buyer@x.ru" not in page and "Петрова" not in page
    assert "выключены" in client.get("/email/contacts.csv", params={"month": "2026-10", "kind": "active"}).text


def test_staff_file_is_recognised(tmp_path):
    import openpyxl

    from analytics import privacy
    from analytics.sources.crm_files import parse_file

    wb = openpyxl.Workbook()
    wb.active.title = "users (6)"
    wb.active.append(["Сотрудник", "E-Mail", "Мобильный телефон"])
    wb.active.append(["Иванов Иван", "Ivanov@Oldos.ru", "+7 900 000-00-01"])
    wb.save(tmp_path / "u.xlsx")
    f = parse_file(tmp_path / "u.xlsx")
    assert f.kind == "staff" and f.staff[0].name == "Иванов Иван"
    assert f.staff[0].email_hash == privacy.email_hash("ivanov@oldos.ru") and f.staff[0].phone_hash
