from datetime import date

import pytest

from analytics.sources.webmaster import (
    CrawlDay,
    PagesDay,
    SearchDay,
    WebmasterClient,
    WebmasterError,
    parse_crawl,
    parse_pages,
    parse_query_analytics,
    parse_search_history,
)
from analytics.sync import month_ranges
from tests.fakes import FakeResponse, FakeSession


def point(day, value, time="00:00:00"):
    return {"date": f"{day}T{time}.000+03:00", "value": value}


def test_search_history_drops_trailing_days_without_data():
    indicators = {
        "TOTAL_SHOWS": [point("2026-10-07", 100.0), point("2026-10-08", 0.0), point("2026-10-09", 50.0),
                        point("2026-10-10", 0.0), point("2026-10-11", 0.0)],
        "TOTAL_CLICKS": [point("2026-10-07", 5.0), point("2026-10-08", 0.0), point("2026-10-09", 2.0),
                         point("2026-10-10", 0.0), point("2026-10-11", 0.0)],
        "AVG_SHOW_POSITION": [point("2026-10-07", 7.5), point("2026-10-08", 0.0), point("2026-10-09", 8.0),
                              point("2026-10-10", 0.0), point("2026-10-11", 0.0)],
        "AVG_CLICK_POSITION": [point("2026-10-07", 2.0), point("2026-10-08", 0.0), point("2026-10-09", 3.0),
                               point("2026-10-10", 0.0), point("2026-10-11", 0.0)],
    }
    assert parse_search_history(indicators) == [
        SearchDay(date(2026, 10, 7), 100, 5, 7.5, 2.0),
        # ноль внутри периода — настоящий ноль, позиции при нём не определены
        SearchDay(date(2026, 10, 8), 0, 0, None, None),
        SearchDay(date(2026, 10, 9), 50, 2, 8.0, 3.0),
    ]
    assert parse_search_history({"TOTAL_SHOWS": [point("2026-11-01", 0.0)]}) == []


def test_crawl_sums_measurements_within_a_day():
    indicators = {
        "HTTP_2XX": [point("2026-01-01", 3, "09:00:00"), point("2026-01-01", 293, "23:00:00")],
        "HTTP_4XX": [point("2026-01-01", 7, "23:00:00"), point("2026-01-02", 1)],
        "SOMETHING_NEW": [point("2026-01-01", 99)],
    }
    assert parse_crawl(indicators) == [
        CrawlDay(date(2026, 1, 1), 296, 0, 7, 0, 0),
        CrawlDay(date(2026, 1, 2), 0, 0, 1, 0, 0),
    ]


def test_pages_merge_in_search_and_events():
    in_search = [point("2026-10-05", 3267, "10:00:09"), point("2026-10-07", 3394)]
    events = {"APPEARED_IN_SEARCH": [point("2026-10-07", 267)], "REMOVED_FROM_SEARCH": [point("2026-10-06", 5)]}
    assert parse_pages(in_search, events) == [
        PagesDay(date(2026, 10, 5), 3267, None, None),
        PagesDay(date(2026, 10, 6), None, None, 5),
        PagesDay(date(2026, 10, 7), 3394, 267, None),
    ]


def test_query_analytics_pivots_fields_and_dedupes():
    row = {
        "text_indicator": {"type": "QUERY", "value": "ткани оптом"},
        "popular_complementary_indicator": {"type": "URL", "value": "/catalog/"},
        "statistics": [
            {"date": "2026-10-01", "field": "IMPRESSIONS", "value": 40.0},
            {"date": "2026-10-01", "field": "CLICKS", "value": 3.0},
            {"date": "2026-10-01", "field": "POSITION", "value": 2.4},
            {"date": "2026-10-01", "field": "DEMAND", "value": 120.0},
            {"date": "2026-10-01", "field": "CTR", "value": 7.5},
        ],
    }
    days = parse_query_analytics([row, row])
    assert len(days) == 1
    d = days[0]
    assert (d.date, d.query, d.top_url, d.impressions, d.clicks, d.position, d.demand) == (
        date(2026, 10, 1), "ткани оптом", "/catalog/", 40, 3, 2.4, 120
    )


def test_popular_queries_read_all_pages():
    page = lambda n: [{"query_id": str(i), "query_text": f"q{i}", "indicators": {}} for i in range(n)]
    session = FakeSession([
        FakeResponse(200, {"user_id": 7}),
        FakeResponse(200, {"count": 501, "queries": page(500)}),
        FakeResponse(200, {"count": 501, "queries": page(1)}),
    ])
    client = WebmasterClient("", session=session)
    assert len(client.popular_queries("https:example.ru:443", date(2026, 8, 1), date(2026, 8, 31))) == 501
    url, params = session.calls[2]
    assert url.endswith("/user/7/hosts/https:example.ru:443/search-queries/popular")
    assert params["offset"] == 500 and params["limit"] == 500


def test_access_error_mentions_required_scopes():
    session = FakeSession([FakeResponse(403, {"error_code": "ACCESS_FORBIDDEN", "error_message": "Required scope: COMMON"})])
    with pytest.raises(WebmasterError, match="Required scope: COMMON. Проверьте токен.*webmaster:verify"):
        WebmasterClient("", session=session).user_id()


def test_month_ranges():
    assert list(month_ranges(date(2026, 8, 15), date(2026, 10, 3))) == [
        (date(2026, 8, 1), date(2026, 8, 31)),
        (date(2026, 9, 1), date(2026, 9, 30)),
        (date(2026, 10, 1), date(2026, 10, 3)),
    ]
