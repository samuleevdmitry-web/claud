from datetime import date

import pytest

from analytics.sources.metrika import (
    GOALS_PER_REQUEST,
    MetrikaClient,
    MetrikaError,
    TrafficRow,
    dimensions_for,
    fetch_goals,
    grain_range,
    parse_goals,
    parse_traffic,
)
from tests.fakes import FakeResponse, FakeSession, dim


def make_client(responses):
    session = FakeSession(responses)
    return MetrikaClient("token", session=session, backoff=0), session


def test_parse_traffic_source_slice():
    rows = [
        {
            "dimensions": [dim("2026-08-01"), dim("Переходы из поисковых систем", "organic")],
            "metrics": [100.0, 80.0, 300.0, 10.5, 3.0, 200.0],
        }
    ]
    assert parse_traffic(rows, "month", "source") == [
        TrafficRow("month", date(2026, 8, 1), "source", "organic", "Переходы из поисковых систем",
                   100, 80, 300, 10.5, 3.0, 200.0)
    ]


def test_parse_traffic_total_and_undefined_values():
    total = parse_traffic([{"dimensions": [dim("2026-08-01")], "metrics": [5, 4, 9, 0, 1.8, 60]}], "day", "total")
    assert (total[0].dim_key, total[0].dim_label) == ("", "Всего")

    utm = parse_traffic(
        [{"dimensions": [dim("2026-08-01"), dim("yandex"), dim(None), {"name": None, "id": None}],
          "metrics": [5, 4, 9, 0, 1.8, 60]}],
        "day",
        "utm",
    )
    assert (utm[0].dim_key, utm[0].dim_label) == ("yandex|(none)|(none)", "yandex / Не определено / Не определено")


def test_parse_goals_skips_zero_rows():
    rows = [
        {"dimensions": [dim("2026-08-01")], "metrics": [3.0, 2.0, 0.0, 0.0]},
        {"dimensions": [dim("2026-08-02")], "metrics": [0.0, 0.0, 0.0, 0.0]},
    ]
    goals = parse_goals(rows, "day", "total", [11, 22])
    assert [(g.period_start, g.goal_id, g.reaches, g.visits) for g in goals] == [(date(2026, 8, 1), 11, 3, 2)]


def test_report_reads_all_pages():
    client, session = make_client([
        FakeResponse(200, {"data": [{"n": 1}, {"n": 2}], "total_rows": 3, "sampled": False}),
        FakeResponse(200, {"data": [{"n": 3}], "total_rows": 3, "sampled": True}),
    ])
    report = client.report(1, ["ym:s:visits"], ["ym:s:date"], date(2026, 8, 1), date(2026, 8, 31))
    assert [r["n"] for r in report.rows] == [1, 2, 3]
    assert report.sampled is True
    assert [params["offset"] for _, params in session.calls] == [1, 3]
    assert session.headers["Authorization"] == "OAuth token"


def test_retries_on_rate_limit_then_succeeds():
    client, session = make_client([FakeResponse(429, {"message": "quota"}), FakeResponse(200, {"counters": []})])
    assert client.counters() == []
    assert len(session.calls) == 2


def test_error_message_from_api():
    client, _ = make_client([FakeResponse(403, {"message": "Access denied"})])
    with pytest.raises(MetrikaError, match="403: Access denied"):
        client.goals(1)


def test_without_token_leaves_authorization_to_proxy():
    session = FakeSession([FakeResponse(401, {"message": "Invalid oauth_token"})])
    client = MetrikaClient("", session=session)
    assert "Authorization" not in session.headers
    with pytest.raises(MetrikaError, match="401: Invalid oauth_token. Проверьте токен"):
        client.counters()


def test_dimensions_use_attribution():
    assert dimensions_for("month", "utm", "last") == [
        "ym:s:startOfMonth", "ym:s:lastUTMSource", "ym:s:lastUTMMedium", "ym:s:lastUTMCampaign"
    ]


@pytest.mark.parametrize(
    ("grain", "since", "until", "today", "expected"),
    [
        ("day", date(2026, 8, 15), date(2026, 9, 10), date(2026, 9, 10), (date(2026, 8, 15), date(2026, 9, 10))),
        # текущий месяц — по сегодня, первый месяц — с начала
        ("month", date(2026, 8, 15), date(2026, 9, 10), date(2026, 9, 10), (date(2026, 8, 1), date(2026, 9, 10))),
        # прошедший месяц перегружается до конца, а не до --until
        ("month", date(2026, 8, 15), date(2026, 8, 20), date(2026, 10, 1), (date(2026, 8, 1), date(2026, 8, 31))),
        ("month", date(2026, 12, 5), date(2026, 12, 6), date(2027, 1, 2), (date(2026, 12, 1), date(2026, 12, 31))),
    ],
)
def test_grain_range(grain, since, until, today, expected):
    assert grain_range(grain, since, until, today) == expected


def test_fetch_goals_splits_metrics_into_batches():
    goal_ids = list(range(1, GOALS_PER_REQUEST + 3))
    empty = {"data": [], "total_rows": 0}
    client, session = make_client([FakeResponse(200, empty), FakeResponse(200, empty)])
    fetch_goals(client, 1, "day", "total", goal_ids, date(2026, 8, 1), date(2026, 8, 1), "lastsign")
    metric_counts = [len(params["metrics"].split(",")) for _, params in session.calls]
    assert metric_counts == [2 * GOALS_PER_REQUEST, 4]
