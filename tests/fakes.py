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

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self.responses.pop(0)


def dim(name, id_=None) -> dict:
    d = {"name": name}
    if id_ is not None:
        d["id"] = id_
    return d


class FakeMetrikaClient:
    """Отвечает на отчёты по таблице {(grain, slice): [(period, [dims], traffic, {goal_id: (reaches, visits)})]}."""

    def __init__(self, goals: list[dict], data: dict):
        self._goals = goals
        self.data = data
        self.reports: list[tuple[list[str], list[str], date, date]] = []

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
