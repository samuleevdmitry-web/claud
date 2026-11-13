"""Проверка загрузки и представлений на настоящем PostgreSQL.

Запускается, если задан TEST_DATABASE_URL; база будет очищена.
"""

import os
from datetime import date

import pytest

from analytics import db
from analytics.sync import sync_metrika, sync_webmaster
from tests.fakes import FakeMetrikaClient, FakeWebmasterClient, dim

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL не задан")

COUNTER = 42
JUL, AUG = date(2026, 7, 1), date(2026, 8, 1)
ORGANIC = dim("Переходы из поисковых систем", "organic")
DIRECT = dim("Прямые заходы", "direct")
GOALS = [{"id": 1, "name": "Отправка формы", "type": "form"}, {"id": 2, "name": "Клик по телефону", "type": "phone"}]


@pytest.fixture
def conn():
    with db.connect(TEST_DATABASE_URL) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        db.apply_schema(conn)
        yield conn


def month_data(aug_direct_visits=40):
    return {
        ("month", "total"): [
            (JUL, [], [100, 90, 250, 20.0, 2.5, 150.0], {1: (2, 2), 2: (5, 5)}),
            (AUG, [], [200, 150, 500, 25.0, 2.5, 120.0], {1: (6, 5), 2: (9, 9)}),
        ],
        ("month", "source"): [
            (AUG, [ORGANIC], [160, 120, 420, 10.0, 2.6, 130.0], {1: (6, 5)}),
            (AUG, [DIRECT], [aug_direct_visits, 30, 80, 60.0, 2.0, 80.0], {}),
        ],
    }


def test_schema_is_idempotent(conn):
    assert db.apply_schema(conn) == [
        "001_metrika.sql", "002_metrika_views.sql", "003_webmaster.sql", "004_webmaster_views.sql", "005_maps.sql",
        "006_dashboard.sql", "007_wordstat.sql", "008_rabbit.sql",
    ]


def test_sync_builds_monthly_kpi(conn):
    client = FakeMetrikaClient(GOALS, month_data(), humans_share=0.5)
    loaded = sync_metrika(conn, client, COUNTER, frozenset({1}), "lastsign",
                          since=JUL, until=date(2026, 8, 31), today=date(2026, 9, 3), log=lambda _: None)
    # total: 2 месяца и 4 строки целей; source: 2 канала и 1 строка целей — для обеих аудиторий
    assert loaded == 2 * ((2 + 4) + (2 + 1))

    query = ("SELECT visits, visits_prev, leads, leads_prev, conversion_pct, conversion_pct_prev, visits_yoy "
             "FROM metrika_monthly WHERE counter_id = %s AND month = %s AND audience = %s")
    # заявка — только цель 1: клики по телефону в заявки не попадают
    assert conn.execute(query, (COUNTER, AUG, "all")).fetchone() == (200, 100, 6, 2, 3.0, 2.0, None)
    # «только люди» — отдельные строки, а не копия всего трафика
    assert conn.execute(query, (COUNTER, AUG, "humans")).fetchone() == (100, 50, 3, 1, 3.0, 2.0, None)

    by_source = conn.execute(
        "SELECT dim_key, leads, conversion_pct FROM metrika_kpi "
        "WHERE grain = 'month' AND slice = 'source' AND audience = 'all' ORDER BY dim_key"
    ).fetchall()
    assert [(k, leads, float(c)) for k, leads, c in by_source] == [("direct", 0, 0.0), ("organic", 6, 3.75)]


def test_humans_without_robots_reuse_all_traffic(conn):
    # роботы были только в июле: фильтр нужен только для июля, август «только людей» — копия всего трафика
    data = month_data()
    data[("month", "source")].append((JUL, [ORGANIC], [80, 70, 200, 10.0, 2.5, 120.0], {1: (2, 2)}))
    client = FakeMetrikaClient(GOALS, data, humans_share={JUL: 0.5})
    sync_metrika(conn, client, COUNTER, frozenset({1}), "lastsign",
                 since=JUL, until=date(2026, 8, 31), today=date(2026, 9, 3), log=lambda _: None)

    filtered = {(dims[1] if len(dims) > 1 else "total", d1, d2)
                for _, dims, d1, d2, filters in client.reports if filters and dims[0] == "ym:s:startOfMonth"}
    assert filtered == {("total", JUL, date(2026, 8, 31)), ("ym:s:lastsignTrafficSource", JUL, date(2026, 7, 31))}

    rows = conn.execute(
        "SELECT audience, period_start, dim_key, visits FROM metrika_traffic WHERE grain = 'month' AND slice = 'source' "
        "ORDER BY audience, period_start, dim_key"
    ).fetchall()
    assert rows == [
        ("all", JUL, "organic", 80), ("all", AUG, "direct", 40), ("all", AUG, "organic", 160),
        ("humans", JUL, "organic", 40), ("humans", AUG, "direct", 40), ("humans", AUG, "organic", 160),
    ]
    visits = conn.execute(
        "SELECT month, visits FROM metrika_monthly WHERE counter_id = %s AND audience = 'humans' ORDER BY month",
        (COUNTER,),
    ).fetchall()
    assert visits == [(JUL, 50), (AUG, 200)]


def test_resync_replaces_period_and_drops_stale_rows(conn):
    def run(data):
        client = FakeMetrikaClient(GOALS, data)
        sync_metrika(conn, client, COUNTER, frozenset({1}), "lastsign",
                     since=AUG, until=date(2026, 8, 31), today=date(2026, 9, 3), log=lambda _: None)

    run(month_data())
    data = month_data(aug_direct_visits=55)
    data[("month", "source")] = [row for row in data[("month", "source")] if row[1] != [ORGANIC]]
    run(data)

    rows = conn.execute(
        "SELECT dim_key, visits FROM metrika_traffic WHERE slice = 'source' AND audience = 'all' ORDER BY dim_key"
    ).fetchall()
    assert rows == [("direct", 55)]
    assert conn.execute("SELECT count(*) FROM metrika_goal_stats WHERE slice = 'source'").fetchone() == (0,)
    # июль вне перезагружаемого периода и не тронут
    assert conn.execute(
        "SELECT count(*) FROM metrika_traffic WHERE period_start = %s", (JUL,)
    ).fetchone() == (0,)


def test_lead_goals_follow_config(conn):
    client = FakeMetrikaClient(GOALS, month_data())
    kwargs = dict(since=AUG, until=date(2026, 8, 31), today=date(2026, 9, 3), log=lambda _: None)
    sync_metrika(conn, client, COUNTER, frozenset({1}), "lastsign", **kwargs)
    sync_metrika(conn, client, COUNTER, frozenset({1, 2}), "lastsign", **kwargs)
    leads = conn.execute("SELECT leads FROM metrika_monthly WHERE month = %s AND audience = 'all'", (AUG,)).fetchone()
    assert leads == (15,)


def test_sync_run_log(conn):
    run_id = db.start_run(conn, "metrika", AUG, date(2026, 8, 31))
    db.finish_run(conn, run_id, error="HTTP 403")
    assert conn.execute("SELECT status, error FROM sync_runs WHERE id = %s", (run_id,)).fetchone() == (
        "error", "HTTP 403"
    )


def qa_row(query, day, impressions, position):
    return {"text_indicator": {"type": "QUERY", "value": query}, "statistics": [
        {"date": day.isoformat(), "field": "IMPRESSIONS", "value": float(impressions)},
        {"date": day.isoformat(), "field": "CLICKS", "value": 0.0},
        {"date": day.isoformat(), "field": "POSITION", "value": position},
    ]}


def test_sync_webmaster(conn):
    host = "https:www.example.ru:443"
    search = {
        date(2026, 7, 31): (100, 10, 10.0, 2.0),
        date(2026, 8, 1): (300, 20, 8.0, 3.0),
        date(2026, 8, 2): (100, 20, 12.0, 4.0),
    }
    queries = [{"query_id": "a1", "query_text": "ткани оптом", "indicators": {"TOTAL_SHOWS": 400.0, "TOTAL_CLICKS": 40.0}}]
    day = date(2026, 8, 2)
    query_rows = [qa_row("q1", day, 50, 1.2), qa_row("q2", day, 30, 2.6), qa_row("q3", day, 20, 7.0),
                  qa_row("q4", day, 5, 60.0)]
    client = FakeWebmasterClient(search, queries, query_rows)
    sync_webmaster(conn, client, host, since=date(2026, 7, 1), until=date(2026, 8, 20), today=date(2026, 8, 20),
                   log=lambda _: None)

    aug = conn.execute(
        "SELECT shows, clicks, ctr_pct, avg_show_position, avg_click_position, days_with_data, shows_prev "
        "FROM webmaster_search_monthly WHERE host_id = %s AND month = %s", (host, date(2026, 8, 1))
    ).fetchone()
    # позиции взвешены: показов (8×300 + 12×100) / 400 = 9, кликов (3×20 + 4×20) / 40 = 3.5;
    # 3–20 августа данных ещё нет — их нет и в таблице
    assert [float(v) for v in aug] == [400, 40, 10.0, 9.0, 3.5, 2, 100]

    buckets = dict(conn.execute(
        "SELECT position_range, impressions FROM webmaster_position_buckets WHERE host_id = %s", (host,)
    ).fetchall())
    assert buckets == {"1": 50, "2-3": 30, "4-10": 20, "51+": 5}

    freshness = dict(conn.execute(
        "SELECT stream, last_date FROM webmaster_freshness WHERE host_id = %s", (host,)
    ).fetchall())
    assert freshness["search"] == date(2026, 8, 2) and freshness["query_analytics"] == day

    assert conn.execute(
        "SELECT count(*) FROM webmaster_queries_monthly WHERE host_id = %s", (host,)
    ).fetchone() == (2,)  # июль и август
    assert conn.execute(
        "SELECT state FROM webmaster_diagnostics WHERE host_id = %s AND problem = 'INSIGNIFICANT_CGI_PARAMETER'",
        (host,),
    ).fetchone() == ("PRESENT",)


def test_maps_monthly_reads_card_goals(conn):
    goals = [
        {"id": 11, "name": "Клик на позвонить - Олдос", "type": "action"},
        {"id": 12, "name": "Построить маршрут - Олдос", "type": "action"},
        {"id": 13, "name": "Переход на сайт - Олдос", "type": "action"},
        {"id": 14, "name": "Лайк - Олдос", "type": "action"},
    ]
    data = {("month", "total"): [
        (JUL, [], [2164, 1911, 3000, 60.0, 1.2, 40.0], {11: (95, 90), 12: (164, 150), 13: (203, 200)}),
        (AUG, [], [2804, 2520, 3900, 60.0, 1.2, 40.0], {11: (104, 99), 12: (160, 155), 13: (199, 190), 14: (5, 5)}),
    ]}
    client = FakeMetrikaClient(goals, data, name="Олдос")
    sync_metrika(conn, client, 777, frozenset(), "lastsign", since=JUL, until=date(2026, 8, 31),
                 today=date(2026, 9, 3), log=lambda _: None, kind="maps")
    # счётчик сайта в представление Карт не попадает
    sync_metrika(conn, FakeMetrikaClient(GOALS, month_data()), COUNTER, frozenset({1}), "lastsign",
                 since=JUL, until=date(2026, 8, 31), today=date(2026, 9, 3), log=lambda _: None)

    rows = conn.execute(
        "SELECT counter_id, name, card_visits, card_visits_prev, calls, calls_prev, routes, site, site_prev "
        "FROM maps_monthly WHERE month = %s AND audience = 'all'", (AUG,)
    ).fetchall()
    assert rows == [(777, "Олдос", 2804, 2164, 104, 95, 160, 199, 203)]
