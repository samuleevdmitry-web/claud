"""Яндекс Метрика: Management API (счётчики, цели) и Reporting API (отчёты)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta

import requests

API_URL = "https://api-metrika.yandex.net"
PAGE_SIZE = 10_000
MAX_METRICS = 20  # ограничение Reporting API на один запрос
GOALS_PER_REQUEST = MAX_METRICS // 2  # на каждую цель — две метрики

GRAINS = {"day": "ym:s:date", "month": "ym:s:startOfMonth"}

# Срезы трафика; {attr} заменяется моделью атрибуции (lastsign, last, first).
SLICES = {
    "total": [],
    "source": ["ym:s:{attr}TrafficSource"],
    "utm": ["ym:s:{attr}UTMSource", "ym:s:{attr}UTMMedium", "ym:s:{attr}UTMCampaign"],
    "city": ["ym:s:regionCity"],
    "device": ["ym:s:deviceCategory"],
}

TRAFFIC_METRICS = [
    "ym:s:visits",
    "ym:s:users",
    "ym:s:pageviews",
    "ym:s:bounceRate",
    "ym:s:pageDepth",
    "ym:s:avgVisitDurationSeconds",
]

# У ошибки «уменьшите интервал дат» нет своего кода (error_type общий — query_error), только текст.
TOO_COMPLEX_MARKERS = ("слишком сложный", "too complex", "too complicated")

TOTAL_LABEL = "Всего"
NONE_KEY = "(none)"
NONE_LABEL = "Не определено"


class MetrikaError(RuntimeError):
    def __init__(self, message: str, too_complex: bool = False):
        super().__init__(message)
        self.too_complex = too_complex  # Метрика просит уменьшить период запроса


@dataclass(frozen=True)
class TrafficRow:
    grain: str
    period_start: date
    slice: str
    dim_key: str
    dim_label: str
    visits: int
    users: int
    pageviews: int
    bounce_rate: float | None
    page_depth: float | None
    avg_duration_sec: float | None


@dataclass(frozen=True)
class GoalRow:
    grain: str
    period_start: date
    slice: str
    dim_key: str
    goal_id: int
    reaches: int
    visits: int


@dataclass(frozen=True)
class Report:
    rows: list[dict]
    sampled: bool


class MetrikaClient:
    def __init__(
        self,
        token: str,
        session: requests.Session | None = None,
        max_retries: int = 4,
        backoff: float = 2.0,
    ):
        self.session = session or requests.Session()
        # Без токена заголовок не ставим: его может подставлять прокси (секрет облачного окружения).
        if token:
            self.session.headers["Authorization"] = f"OAuth {token}"
        self.max_retries = max_retries
        self.backoff = backoff

    def _get(self, path: str, params: dict | None = None) -> dict:
        attempt = 0
        while True:
            resp = self.session.get(API_URL + path, params=params, timeout=120)
            retryable = resp.status_code == 429 or resp.status_code >= 500
            if retryable and attempt < self.max_retries:
                time.sleep(self.backoff * 2**attempt)
                attempt += 1
                continue
            if resp.status_code in (401, 403):
                raise MetrikaError(
                    f"{path}: HTTP {resp.status_code}: {_error_message(resp)}. Проверьте токен "
                    "(YANDEX_OAUTH_TOKEN или секрет окружения) и доступ аккаунта к счётчику"
                )
            if resp.status_code != 200:
                message = _error_message(resp)
                too_complex = any(marker in message.lower() for marker in TOO_COMPLEX_MARKERS)
                raise MetrikaError(f"{path}: HTTP {resp.status_code}: {message}", too_complex=too_complex)
            return resp.json()

    def counters(self) -> list[dict]:
        return self._get("/management/v1/counters")["counters"]

    def goals(self, counter_id: int) -> list[dict]:
        return self._get(f"/management/v1/counter/{counter_id}/goals")["goals"]

    def report(
        self,
        counter_id: int,
        metrics: list[str],
        dimensions: list[str],
        date1: date,
        date2: date,
    ) -> Report:
        """Отчёт целиком, со всеми страницами."""
        rows: list[dict] = []
        sampled = False
        while True:
            data = self._get(
                "/stat/v1/data",
                {
                    "ids": counter_id,
                    "metrics": ",".join(metrics),
                    "dimensions": ",".join(dimensions),
                    "date1": date1.isoformat(),
                    "date2": date2.isoformat(),
                    "limit": PAGE_SIZE,
                    "offset": len(rows) + 1,  # строки в API нумеруются с единицы
                    "accuracy": "full",
                    "include_undefined": "true",
                    "lang": "ru",
                },
            )
            sampled = sampled or bool(data.get("sampled"))
            page = data.get("data", [])
            rows.extend(page)
            if not page or len(rows) >= data.get("total_rows", 0):
                return Report(rows, sampled)


def _error_message(resp: requests.Response) -> str:
    try:
        return str(resp.json().get("message") or resp.text[:300])
    except ValueError:
        return resp.text[:300]


def dimensions_for(grain: str, slice_: str, attribution: str) -> list[str]:
    return [GRAINS[grain]] + [d.format(attr=attribution) for d in SLICES[slice_]]


def goal_metrics(goal_ids: list[int]) -> list[str]:
    return [m for g in goal_ids for m in (f"ym:s:goal{g}reaches", f"ym:s:goal{g}visits")]


def grain_range(grain: str, since: date, until: date, today: date) -> tuple[date, date]:
    """Период запроса для зернистости.

    Месяцы перегружаются целиком, иначе крайние месяцы посчитаются частично
    и перезапишут полные данные; текущий месяц — по сегодняшний день.
    """
    if grain == "day":
        return since, until
    month_end = (until.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return since.replace(day=1), min(month_end, today)


def _dim_value(dim: dict) -> tuple[str, str]:
    """Ключ и подпись значения группировки; ключ — id, если API его отдаёт."""
    name = dim.get("name")
    key = dim.get("id")
    if key is None:
        key = name
    if key is None:
        return NONE_KEY, NONE_LABEL
    return str(key), NONE_LABEL if name is None else str(name)


def _slice_key(dims: list[dict]) -> tuple[str, str]:
    if not dims:
        return "", TOTAL_LABEL
    values = [_dim_value(d) for d in dims]
    return "|".join(k for k, _ in values), " / ".join(label for _, label in values)


def parse_traffic(rows: list[dict], grain: str, slice_: str) -> list[TrafficRow]:
    out = []
    for row in rows:
        period, *dims = row["dimensions"]
        key, label = _slice_key(dims)
        visits, users, pageviews, bounce_rate, page_depth, duration = row["metrics"]
        out.append(
            TrafficRow(
                grain=grain,
                period_start=date.fromisoformat(period["name"]),
                slice=slice_,
                dim_key=key,
                dim_label=label,
                visits=int(visits or 0),
                users=int(users or 0),
                pageviews=int(pageviews or 0),
                bounce_rate=bounce_rate,
                page_depth=page_depth,
                avg_duration_sec=duration,
            )
        )
    return out


def parse_goals(rows: list[dict], grain: str, slice_: str, goal_ids: list[int]) -> list[GoalRow]:
    out = []
    for row in rows:
        period, *dims = row["dimensions"]
        key, _ = _slice_key(dims)
        values = row["metrics"]
        for i, goal_id in enumerate(goal_ids):
            reaches, visits = int(values[2 * i] or 0), int(values[2 * i + 1] or 0)
            if reaches or visits:
                out.append(
                    GoalRow(grain, date.fromisoformat(period["name"]), slice_, key, goal_id, reaches, visits)
                )
    return out


def fetch_traffic(
    client: MetrikaClient,
    counter_id: int,
    grain: str,
    slice_: str,
    date1: date,
    date2: date,
    attribution: str,
) -> tuple[list[TrafficRow], bool]:
    report = report_by_parts(
        client, counter_id, TRAFFIC_METRICS, dimensions_for(grain, slice_, attribution), date1, date2
    )
    return parse_traffic(report.rows, grain, slice_), report.sampled


def fetch_goals(
    client: MetrikaClient,
    counter_id: int,
    grain: str,
    slice_: str,
    goal_ids: list[int],
    date1: date,
    date2: date,
    attribution: str,
) -> list[GoalRow]:
    rows = []
    for start in range(0, len(goal_ids), GOALS_PER_REQUEST):
        batch = goal_ids[start : start + GOALS_PER_REQUEST]
        report = report_by_parts(
            client, counter_id, goal_metrics(batch), dimensions_for(grain, slice_, attribution), date1, date2
        )
        rows.extend(parse_goals(report.rows, grain, slice_, batch))
    return rows


def report_by_parts(
    client: MetrikaClient,
    counter_id: int,
    metrics: list[str],
    dimensions: list[str],
    date1: date,
    date2: date,
) -> Report:
    """Отчёт за период; если Метрика считает запрос слишком сложным, период делится пополам.

    Деление идёт по границе месяца, чтобы месячная строка не собиралась из двух частей.
    """
    try:
        return client.report(counter_id, metrics, dimensions, date1, date2)
    except MetrikaError as exc:
        split = month_split_point(date1, date2)
        if not exc.too_complex or split is None:
            raise
    left = report_by_parts(client, counter_id, metrics, dimensions, date1, split - timedelta(days=1))
    right = report_by_parts(client, counter_id, metrics, dimensions, split, date2)
    return Report(left.rows + right.rows, left.sampled or right.sampled)


def month_split_point(date1: date, date2: date) -> date | None:
    """Первое число месяца в середине периода; None, если период внутри одного месяца."""
    months = (date2.year - date1.year) * 12 + date2.month - date1.month
    if months == 0:
        return None
    years, month = divmod(date1.month - 1 + (months + 1) // 2, 12)
    return date(date1.year + years, month + 1, 1)
