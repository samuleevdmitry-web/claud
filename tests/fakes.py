"""Подставные HTTP-ответы и клиент Метрики для тестов (данные синтетические)."""

from __future__ import annotations

import re
from datetime import date

from analytics.sources.metrika import GRAINS, SLICES, TRAFFIC_METRICS, Report


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "", headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Отдаёт заранее заданные ответы по очереди и запоминает запросы."""

    def __init__(self, responses: list[FakeResponse]):
        self.headers: dict[str, str] = {}
        self.responses = list(responses)
        self.calls: list[tuple[str, dict | None]] = []

    def request(self, method, url, params=None, json=None, timeout=None, headers=None):
        self.calls.append((url, params if json is None else json))
        return self.responses.pop(0)


def dim(name, id_=None) -> dict:
    d = {"name": name}
    if id_ is not None:
        d["id"] = id_
    return d


class FakeMetrikaClient:
    """Отвечает на отчёты по таблице {(grain, slice): [(period, [dims], traffic, {goal_id: (reaches, visits)})]}.

    С фильтром «только люди» количества умножаются на humans_share — общий или по периодам {период: доля}.
    """

    def __init__(self, goals: list[dict], data: dict, name: str = "example.ru",
                 humans_share: float | dict[date, float] = 1.0):
        self._goals = goals
        self.data = data
        self.name = name
        self.humans_share = humans_share
        self.pages: dict[tuple[str, str | None], list[tuple[str, list[float]]]] = {}
        self.reports: list[tuple[list[str], list[str], date, date, str | None]] = []

    def counter(self, counter_id: int) -> dict:
        return {"id": counter_id, "name": self.name, "site2": {"site": "example.ru"}}

    def goals(self, counter_id: int) -> list[dict]:
        return self._goals

    def report(self, counter_id, metrics, dimensions, date1, date2, filters=None, top=None) -> Report:
        self.reports.append((metrics, dimensions, date1, date2, filters))
        if dimensions[0] not in GRAINS.values() or "ym:pv:" in (filters or ""):  # страницы и товары: {группировка: [(имя, [метрики])]}
            rows = [{"dimensions": [dim(name)], "metrics": values}
                    for name, values in self.pages.get((dimensions[0], filters), [])]
            return Report(rows[:top] if top else rows, sampled=False)
        grain = next(g for g, d in GRAINS.items() if d == dimensions[0])
        slice_ = next(name for name, spec in SLICES.items()
                      if [d.format(attr="lastsign") for d in spec.dims] == dimensions[1:])
        rows = []
        lead_ids = [int(g) for g in re.findall(r"ym:s:goal==(\d+)", filters or "")]
        for period, dims, traffic, goals in self.data.get((grain, slice_), []):
            if not date1 <= period <= date2:
                continue
            share = self.humans_share if isinstance(self.humans_share, float) else self.humans_share.get(period, 1.0)
            share = share if filters and "isRobot" in filters else 1.0
            if lead_ids:  # визиты с хоть одной целью-заявкой; в данных теста цели в одном визите не пересекаются
                visits = sum(goals.get(g, (0, 0))[1] for g in lead_ids) * share
                if visits:
                    rows.append({"dimensions": [dim(period.isoformat())] + dims, "metrics": [float(visits)]})
                continue
            values = []
            for m in metrics:
                if m in TRAFFIC_METRICS:
                    i = TRAFFIC_METRICS.index(m)
                    values.append(traffic[i] * share if i < 3 else traffic[i])  # визиты, посетители, просмотры
                else:
                    goal_id = int(m.removeprefix("ym:s:goal").removesuffix("reaches").removesuffix("visits"))
                    reaches, visits = goals.get(goal_id, (0, 0))
                    values.append(float(reaches if m.endswith("reaches") else visits) * share)
            rows.append({"dimensions": [dim(period.isoformat())] + dims, "metrics": values})
        if top:
            rows = sorted(rows, key=lambda r: -r["metrics"][0])[:top]
        return Report(rows, sampled=False)


class FakeWebmasterClient:
    """Вебмастер с синтетическими данными; дни вне search отдаются нулями, как у API, пока данных нет."""

    def __init__(self, search: dict[date, tuple[int, int, float, float]], queries: list[dict], query_rows: list[dict]):
        self.search = search
        self.queries = queries
        self.query_rows = query_rows

    @staticmethod
    def _pt(d: date, value) -> dict:
        return {"date": f"{d.isoformat()}T00:00:00.000+03:00", "value": value}

    def summary(self, host_id):
        return {"sqi": 430, "searchable_pages_count": 3394, "excluded_pages_count": 408,
                "site_problems": {"RECOMMENDATION": 1, "POSSIBLE_PROBLEM": 1}}

    def diagnostics(self, host_id):
        return {"THREATS": {"severity": "FATAL", "state": "ABSENT", "last_state_update": None},
                "INSIGNIFICANT_CGI_PARAMETER": {"severity": "RECOMMENDATION", "state": "PRESENT",
                                                "last_state_update": "2026-10-08T10:00:39.000+03:00"}}

    def search_history(self, host_id, date_from, date_to):
        days = [date.fromordinal(n) for n in range(date_from.toordinal(), date_to.toordinal() + 1)]
        values = [self.search.get(d, (0, 0, 0.0, 0.0)) for d in days]
        names = ["TOTAL_SHOWS", "TOTAL_CLICKS", "AVG_SHOW_POSITION", "AVG_CLICK_POSITION"]
        return {name: [self._pt(d, float(v[i])) for d, v in zip(days, values)] for i, name in enumerate(names)}

    def sqi_history(self, host_id, date_from, date_to):
        return [self._pt(date_from, 430)]

    def in_search_history(self, host_id, date_from, date_to):
        return [self._pt(date_from, 3300)]

    def search_events_history(self, host_id, date_from, date_to):
        return {"APPEARED_IN_SEARCH": [self._pt(date_from, 10)], "REMOVED_FROM_SEARCH": []}

    def indexing_history(self, host_id, date_from, date_to):
        return {"HTTP_2XX": [self._pt(date_from, 500)], "HTTP_4XX": [self._pt(date_from, 20)]}

    def popular_queries(self, host_id, date_from, date_to):
        return self.queries

    def query_analytics(self, host_id):
        return self.query_rows
