"""Яндекс Вебмастер API v4: поисковые запросы, индексирование, ИКС, диагностика."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

import requests

from analytics.sources.http import send, yandex_session

API_URL = "https://api.webmaster.yandex.net/v4"
PAGE_SIZE = 500  # максимум API для списков запросов
SEARCH_INDICATORS = ["TOTAL_SHOWS", "TOTAL_CLICKS", "AVG_SHOW_POSITION", "AVG_CLICK_POSITION"]
CRAWL_INDICATORS = {"HTTP_2XX": "http_2xx", "HTTP_3XX": "http_3xx", "HTTP_4XX": "http_4xx",
                    "HTTP_5XX": "http_5xx", "OTHER": "other"}


class WebmasterError(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchDay:
    date: date
    shows: int
    clicks: int
    avg_show_position: float | None
    avg_click_position: float | None


@dataclass(frozen=True)
class QueryStat:
    query_id: str
    query_text: str
    shows: int
    clicks: int
    avg_show_position: float | None
    avg_click_position: float | None


@dataclass(frozen=True)
class QueryDay:
    date: date
    query: str
    top_url: str | None
    impressions: int
    clicks: int
    position: float | None
    demand: int | None


@dataclass(frozen=True)
class PagesDay:
    date: date
    pages: int | None
    appeared: int | None
    removed: int | None


@dataclass(frozen=True)
class CrawlDay:
    date: date
    http_2xx: int
    http_3xx: int
    http_4xx: int
    http_5xx: int
    other: int


@dataclass(frozen=True)
class Problem:
    code: str
    severity: str
    state: str
    last_state_update: str | None


class WebmasterClient:
    def __init__(
        self,
        token: str,
        session: requests.Session | None = None,
        max_retries: int = 4,
        backoff: float = 2.0,
    ):
        self.session = yandex_session(token, session)
        self.max_retries = max_retries
        self.backoff = backoff
        self._user_id: int | None = None

    def _request(self, method: str, path: str, **kwargs) -> dict:
        resp = send(
            self.session, method, API_URL + path, max_retries=self.max_retries, backoff=self.backoff, **kwargs
        )
        if resp.status_code != 200:
            hint = ""
            if resp.status_code in (401, 403):
                hint = (". Проверьте токен (нужны права webmaster:hostinfo и webmaster:verify) "
                        "и доступ аккаунта к сайту")
            raise WebmasterError(f"{path}: HTTP {resp.status_code}: {_error_message(resp)}{hint}")
        return resp.json()

    def user_id(self) -> int:
        if self._user_id is None:
            self._user_id = self._request("GET", "/user")["user_id"]
        return self._user_id

    def _host(self, host_id: str) -> str:
        return f"/user/{self.user_id()}/hosts/{host_id}"

    def hosts(self) -> list[dict]:
        return self._request("GET", f"/user/{self.user_id()}/hosts")["hosts"]

    def summary(self, host_id: str) -> dict:
        return self._request("GET", self._host(host_id) + "/summary")

    def diagnostics(self, host_id: str) -> dict:
        return self._request("GET", self._host(host_id) + "/diagnostics")["problems"]

    def _history(self, host_id: str, path: str, date_from: date, date_to: date, **params) -> dict:
        params = {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), **params}
        return self._request("GET", self._host(host_id) + path, params=params)

    def sqi_history(self, host_id: str, date_from: date, date_to: date) -> list[dict]:
        return self._history(host_id, "/sqi-history", date_from, date_to)["points"]

    def search_history(self, host_id: str, date_from: date, date_to: date) -> dict:
        return self._history(
            host_id, "/search-queries/all/history", date_from, date_to, query_indicator=SEARCH_INDICATORS
        )["indicators"]

    def in_search_history(self, host_id: str, date_from: date, date_to: date) -> list[dict]:
        return self._history(host_id, "/search-urls/in-search/history", date_from, date_to)["history"]

    def search_events_history(self, host_id: str, date_from: date, date_to: date) -> dict:
        return self._history(host_id, "/search-urls/events/history", date_from, date_to)["indicators"]

    def indexing_history(self, host_id: str, date_from: date, date_to: date) -> dict:
        return self._history(host_id, "/indexing/history", date_from, date_to)["indicators"]

    def popular_queries(self, host_id: str, date_from: date, date_to: date) -> list[dict]:
        """Все популярные запросы за период (Вебмастер отдаёт их страницами по 500)."""
        queries: list[dict] = []
        while True:
            data = self._history(
                host_id, "/search-queries/popular", date_from, date_to,
                order_by="TOTAL_SHOWS", query_indicator=SEARCH_INDICATORS, limit=PAGE_SIZE, offset=len(queries),
            )
            page = data.get("queries", [])
            queries.extend(page)
            if not page or len(queries) >= data.get("count", 0):
                return queries

    def query_analytics(self, host_id: str) -> list[dict]:
        """Аналитика запросов: дневная статистика каждого запроса за последние 14 дней с данными."""
        rows: list[dict] = []
        while True:
            data = self._request("POST", self._host(host_id) + "/query-analytics/list", json={
                "offset": len(rows),
                "limit": PAGE_SIZE,
                "device_type_indicator": "ALL",
                "text_indicator": "QUERY",
                "region_ids": [],
                "filters": {"statistic_filters": [], "text_filters": []},
            })
            page = data.get("text_indicator_to_statistics", [])
            rows.extend(page)
            if not page or len(rows) >= data.get("count", 0):
                return rows


def _error_message(resp: requests.Response) -> str:
    try:
        data = resp.json()
        return str(data.get("error_message") or data.get("error_code") or resp.text[:300])
    except ValueError:
        return resp.text[:300]


def _day(value: str) -> date:
    """Дата из ответа API: '2026-10-01T23:00:00.000+03:00' — день по московскому времени."""
    return date.fromisoformat(value[:10])


def parse_search_history(indicators: dict) -> list[SearchDay]:
    """Показы и клики по дням.

    Дни после последнего дня с показами или кликами отбрасываются: Вебмастер отдаёт за них нули,
    пока данные не готовы, и в базе это выглядело бы как полная потеря трафика из поиска.
    """
    by_day: dict[date, dict[str, float]] = defaultdict(dict)
    for indicator, points in indicators.items():
        for point in points:
            by_day[_day(point["date"])][indicator] = point["value"]
    days = sorted(by_day)
    with_data = [d for d in days if by_day[d].get("TOTAL_SHOWS") or by_day[d].get("TOTAL_CLICKS")]
    if not with_data:
        return []
    out = []
    for d in days:
        if d > with_data[-1]:
            break
        v = by_day[d]
        shows, clicks = int(v.get("TOTAL_SHOWS") or 0), int(v.get("TOTAL_CLICKS") or 0)
        out.append(SearchDay(
            d, shows, clicks,
            v.get("AVG_SHOW_POSITION") if shows else None,
            v.get("AVG_CLICK_POSITION") if clicks else None,
        ))
    return out


def parse_popular(queries: list[dict]) -> list[QueryStat]:
    out = []
    for q in queries:
        ind = q.get("indicators", {})
        out.append(QueryStat(
            q["query_id"], q["query_text"],
            int(ind.get("TOTAL_SHOWS") or 0), int(ind.get("TOTAL_CLICKS") or 0),
            ind.get("AVG_SHOW_POSITION"), ind.get("AVG_CLICK_POSITION"),
        ))
    return out


def parse_query_analytics(rows: list[dict]) -> list[QueryDay]:
    # Ключ — день и запрос: если запрос попадёт на две страницы выдачи API, останется одна строка.
    out: dict[tuple[date, str], QueryDay] = {}
    for row in rows:
        query = row["text_indicator"]["value"]
        top_url = (row.get("popular_complementary_indicator") or {}).get("value")
        by_day: dict[date, dict[str, float]] = defaultdict(dict)
        for stat in row.get("statistics", []):
            by_day[_day(stat["date"])][stat["field"]] = stat["value"]
        for d, v in sorted(by_day.items()):
            demand = v.get("DEMAND")
            out[d, query] = QueryDay(
                d, query, top_url,
                int(v.get("IMPRESSIONS") or 0), int(v.get("CLICKS") or 0),
                v.get("POSITION"), None if demand is None else int(demand),
            )
    return list(out.values())


def parse_pages(in_search: list[dict], events: dict) -> list[PagesDay]:
    """Страницы в поиске и события «появилось/исключено»; при нескольких замерах за день — последний."""
    pages = {_day(p["date"]): int(p["value"]) for p in in_search}
    appeared = {_day(p["date"]): int(p["value"]) for p in events.get("APPEARED_IN_SEARCH", [])}
    removed = {_day(p["date"]): int(p["value"]) for p in events.get("REMOVED_FROM_SEARCH", [])}
    days = sorted(set(pages) | set(appeared) | set(removed))
    return [PagesDay(d, pages.get(d), appeared.get(d), removed.get(d)) for d in days]


def parse_crawl(indicators: dict) -> list[CrawlDay]:
    """Страницы, загруженные роботом, по кодам ответа; замеры внутри дня суммируются."""
    by_day: dict[date, dict[str, int]] = defaultdict(lambda: dict.fromkeys(CRAWL_INDICATORS.values(), 0))
    for indicator, points in indicators.items():
        field = CRAWL_INDICATORS.get(indicator)
        if field is None:
            continue
        for point in points:
            by_day[_day(point["date"])][field] += int(point["value"])
    return [CrawlDay(d, **v) for d, v in sorted(by_day.items())]


def parse_sqi(points: list[dict]) -> list[tuple[date, int]]:
    return sorted({_day(p["date"]): int(p["value"]) for p in points}.items())


def parse_diagnostics(problems: dict) -> list[Problem]:
    return [
        Problem(code, p["severity"], p["state"], p.get("last_state_update"))
        for code, p in sorted(problems.items())
    ]
