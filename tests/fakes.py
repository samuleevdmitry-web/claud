"""Подставные HTTP-ответы и клиент Метрики для тестов (данные синтетические)."""

from __future__ import annotations

from datetime import date

from analytics.sources.metrika import GRAINS, Report


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

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

    def request(self, method, url, params=None, json=None, timeout=None):
        self.calls.append((url, params if json is None else json))
        return self.responses.pop(0)


def dim(name, id_=None) -> dict:
    d = {"name": name}
    if id_ is not None:
        d["id"] = id_
    return d


class FakeMetrikaClient:
    """Отвечает на отчёты по таблице {(grain, slice): [(period, [dims], traffic, {goal_id: (reaches, visits)})]}."""

    def __init__(self, goals: list[dict], data: dict, name: str = "example.ru"):
        self._goals = goals
        self.data = data
        self.name = name
        self.reports: list[tuple[list[str], list[str], date, date]] = []

    def counter(self, counter_id: int) -> dict:
        return {"id": counter_id, "name": self.name, "site2": {"site": "example.ru"}}

    def goals(self, counter_id: int) -> list[dict]:
        return self._goals

    def report(self, counter_id, metrics, dimensions, date1, date2) -> Report:
        self.reports.append((metrics, dimensions, date1, date2))
        grain = next(g for g, d in GRAINS.items() if d == dimensions[0])
        slice_ = _slice_by_dimensions(dimensions[1:])
        rows = []
        for period, dims, traffic, goals in self.data.get((grain, slice_), []):
            if not date1 <= period <= date2:
                continue
            if metrics[0] == "ym:s:visits":
                values = list(traffic)
            else:
                values = []
                for m in metrics:
                    goal_id = int(m.removeprefix("ym:s:goal").removesuffix("reaches").removesuffix("visits"))
                    reaches, visits = goals.get(goal_id, (0, 0))
                    values.append(float(reaches if m.endswith("reaches") else visits))
            rows.append({"dimensions": [dim(period.isoformat())] + dims, "metrics": values})
        return Report(rows, sampled=False)


def _slice_by_dimensions(dims: list[str]) -> str:
    if not dims:
        return "total"
    if dims[0].endswith("TrafficSource"):
        return "source"
    if "UTM" in dims[0]:
        return "utm"
    return "city" if dims[0] == "ym:s:regionCity" else "device"


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
