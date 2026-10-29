"""Проверка загрузки и представлений на настоящем PostgreSQL.

Запускается, если задан TEST_DATABASE_URL; база будет очищена.
"""

import os
from datetime import date

import pytest

from analytics import db
from analytics.sync import sync_metrika
from tests.fakes import FakeMetrikaClient, dim

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
    assert db.apply_schema(conn) == ["001_metrika.sql", "002_metrika_views.sql"]


def test_sync_builds_monthly_kpi(conn):
    client = FakeMetrikaClient(GOALS, month_data())
    loaded = sync_metrika(conn, client, COUNTER, frozenset({1}), "lastsign",
                          since=JUL, until=date(2026, 8, 31), today=date(2026, 9, 3), log=lambda _: None)
    assert loaded == (2 + 4) + (2 + 1)  # total: 2 месяца и 4 строки целей; source: 2 канала и 1 строка целей

    monthly = conn.execute(
        "SELECT visits, visits_prev, leads, leads_prev, conversion_pct, conversion_pct_prev, visits_yoy "
        "FROM metrika_monthly WHERE counter_id = %s AND month = %s",
        (COUNTER, AUG),
    ).fetchone()
    # заявка — только цель 1: клики по телефону в заявки не попадают
    assert monthly == (200, 100, 6, 2, 3.0, 2.0, None)

    by_source = conn.execute(
        "SELECT dim_key, leads, conversion_pct FROM metrika_kpi "
        "WHERE grain = 'month' AND slice = 'source' ORDER BY dim_key"
    ).fetchall()
    assert [(k, leads, float(c)) for k, leads, c in by_source] == [("direct", 0, 0.0), ("organic", 6, 3.75)]


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
        "SELECT dim_key, visits FROM metrika_traffic WHERE slice = 'source' ORDER BY dim_key"
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
    leads = conn.execute("SELECT leads FROM metrika_monthly WHERE month = %s", (AUG,)).fetchone()
    assert leads == (15,)


def test_sync_run_log(conn):
    run_id = db.start_run(conn, "metrika", AUG, date(2026, 8, 31))
    db.finish_run(conn, run_id, error="HTTP 403")
    assert conn.execute("SELECT status, error FROM sync_runs WHERE id = %s", (run_id,)).fetchone() == (
        "error", "HTTP 403"
    )
