import os
from datetime import date

import pytest

from analytics import db
from analytics.dashboard import queries as q
from analytics.sources.rabbit import PositionRow, RabbitClient, RabbitError, parse_positions
from analytics.sync import sync_rabbit
from tests.fakes import FakeResponse, FakeSession

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL не задан")

# Как отдаёт гостевая страница: индекс в pos — дней назад от today, checked — дни со съёмом.
DATA = {
    "today": "2026-11-12",
    "from": "2026-10-30",
    "checked": [0, 1, 7],
    "rows": [
        {"q": "ткань оксфорд", "freq": 900, "pos": [4, 5, None, None, None, None, None, 8]},     # выросла 8 → 4
        {"q": "антистатическая ткань", "freq": 113, "pos": [3, 2, None, None, None, None, None, 2]},  # просела
        {"q": "пвх ткань", "freq": 500, "pos": [None, None, None, None, None, None, None, 6]},  # выпала
        {"q": "мембрана", "freq": 300, "pos": [9, None, None, None, None, None, None, None]},    # появилась
        {"q": "антивандальная ткань", "freq": 106, "pos": [None] * 8},                         # ни разу
    ],
}


def test_index_is_days_back_from_today():
    rows = parse_positions(DATA)
    oxford = {r.date: r.position for r in rows if r.query == "ткань оксфорд"}
    assert oxford == {date(2026, 11, 12): 4, date(2026, 11, 11): 5, date(2026, 11, 5): 8}
    # дни без съёма не попадают, а «не найден» в день съёма — попадает как None
    assert PositionRow(date(2026, 11, 5), "антивандальная ткань", 106, None) in rows
    assert len(rows) == 5 * 3


def test_broken_link_is_explained():
    session = FakeSession([FakeResponse(404, text="not found")])
    with pytest.raises(RabbitError, match="гостевой доступ"):
        RabbitClient("https://rabbit-seo.ru/s/abc", session=session, backoff=0).positions(date(2026, 1, 1), date(2026, 1, 2))


class FakeRabbit:
    def project(self):
        return "example.ru"

    def positions(self, date_from, date_to):
        return DATA


@needs_db
def test_sync_and_compare():
    with db.connect(TEST_DATABASE_URL) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        db.apply_schema(conn)
        assert sync_rabbit(conn, FakeRabbit(), date(2026, 11, 12), log=lambda _: None) == 15
        daily = q.rabbit_daily(conn, "example.ru")
        assert [(d["date"], d["top3"], d["top10"], d["found"]) for d in daily] == [
            (date(2026, 11, 5), 1, 3, 3), (date(2026, 11, 11), 1, 2, 2), (date(2026, 11, 12), 1, 3, 3),
        ]
        compare = q.rabbit_compare(conn, "example.ru", date(2026, 11, 12), date(2026, 11, 5))
        assert {k: [r["query"] for r in v] for k, v in compare.items()} == {
            "up": ["ткань оксфорд"], "down": ["антистатическая ткань"], "lost": ["пвх ткань"],
            "new": ["мембрана"], "never": ["антивандальная ткань"],
        }
