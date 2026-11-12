"""Яндекс Метрика: Management API (счётчики, цели) и Reporting API (отчёты)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta

import requests

from analytics.sources.http import send, yandex_session

API_URL = "https://api-metrika.yandex.net"
PAGE_SIZE = 10_000
MAX_METRICS = 20  # ограничение Reporting API на один запрос
GOALS_PER_REQUEST = MAX_METRICS // 2  # на каждую цель — две метрики

GRAINS = {"day": "ym:s:date", "month": "ym:s:startOfMonth"}

# Весь трафик и «только люди» — как сегмент Метрики «Поведение → Роботность → Люди».
AUDIENCES = {"all": None, "humans": "ym:s:isRobot=='No'"}


@dataclass(frozen=True)
class SliceSpec:
    """Отчёт Метрики: группировки, зернистость и ограничение на число строк.

    top — для отчётов с тысячами значений (страницы, фразы, сайты): за каждый месяц берётся
    топ-N строк по визитам. core — срез собирается и для счётчиков карточек на Картах.
    """

    dims: tuple[str, ...]
    grains: tuple[str, ...] = ("month",)
    top: int | None = None
    core: bool = False


# Отчёты по разделам Метрики; {attr} заменяется моделью атрибуции (lastsign, last, first).
SLICES = {
    # Сводка и источники
    "total": SliceSpec((), ("day", "month"), core=True),
    "source": SliceSpec(("ym:s:{attr}TrafficSource",), ("day", "month"), core=True),
    "utm": SliceSpec(("ym:s:{attr}UTMSource", "ym:s:{attr}UTMMedium", "ym:s:{attr}UTMCampaign"), ("day", "month"), core=True),
    "search_engine": SliceSpec(("ym:s:{attr}SearchEngineRoot",)),
    "search_phrase": SliceSpec(("ym:s:{attr}SearchPhrase",), top=100),
    "referrer": SliceSpec(("ym:s:{attr}ReferalSource",), top=100),
    "social": SliceSpec(("ym:s:{attr}SocialNetwork",)),
    "adv_engine": SliceSpec(("ym:s:{attr}AdvEngine",)),
    "recommendation": SliceSpec(("ym:s:{attr}RecommendationSystem",)),
    "messenger": SliceSpec(("ym:s:{attr}Messenger",)),
    # География
    "country": SliceSpec(("ym:s:regionCountry",)),
    "region": SliceSpec(("ym:s:regionArea",), top=100),
    "city": SliceSpec(("ym:s:regionCity",), ("day", "month"), core=True),
    # Аудитория
    "gender": SliceSpec(("ym:s:gender",)),
    "age": SliceSpec(("ym:s:ageInterval",)),
    "new_user": SliceSpec(("ym:s:isNewUser",)),
    "interest": SliceSpec(("ym:s:interest",)),
    # Технологии
    "device": SliceSpec(("ym:s:deviceCategory",), ("day", "month"), core=True),
    "os": SliceSpec(("ym:s:operatingSystemRoot",)),
    "browser": SliceSpec(("ym:s:browser",)),
    # Содержание и поведение
    "landing": SliceSpec(("ym:s:startURL",), top=100),
    "exit": SliceSpec(("ym:s:endURLPath",), top=100),
    "depth": SliceSpec(("ym:s:pageViewsInterval",)),
    "duration": SliceSpec(("ym:s:visitDurationInterval",)),
    # Время
    "hour": SliceSpec(("ym:s:hour",)),
    "weekday": SliceSpec(("ym:s:dayOfWeek",)),
}

TRAFFIC_METRICS = [
    "ym:s:visits",
    "ym:s:users",
    "ym:s:pageviews",
    "ym:s:bounceRate",
    "ym:s:pageDepth",
    "ym:s:avgVisitDurationSeconds",
]

# Суточные квоты: 5000 запросов на пользователя и на счётчик, сброс в 00:00 по Гринвичу (03:00 МСК).
# Повторять такие запросы до сброса бессмысленно.
DAILY_QUOTAS = {"quota_requests_by_uid", "quota_requests_by_counter_id"}
# Отчётный API: не больше 200 запросов за 5 минут на пользователя — между запросами не меньше 1,5 с.
STAT_INTERVAL = 1.6

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
        max_retries: int = 6,
        backoff: float = 2.0,
        quota_wait: float = 30.0,
        stat_interval: float = STAT_INTERVAL,
    ):
        self.session = yandex_session(token, session)
        self.max_retries = max_retries
        self.backoff = backoff
        # Квота на частоту запросов снимается через несколько минут — после 429 ждём не меньше 30 секунд.
        self.quota_wait = quota_wait
        self.stat_interval = stat_interval
        self._last_stat = 0.0

    def _get(self, path: str, params: dict | None = None) -> dict:
        if path.startswith("/stat/"):
            pause = self._last_stat + self.stat_interval - time.monotonic()
            if pause > 0:
                time.sleep(pause)
            self._last_stat = time.monotonic()
        resp = send(
            self.session, "GET", API_URL + path, params=params, max_retries=self.max_retries,
            backoff=self.backoff, quota_wait=self.quota_wait,
            give_up=lambda r: r.status_code == 429 and _error_type(r) in DAILY_QUOTAS,
        )
        if resp.status_code == 429 and _error_type(resp) in DAILY_QUOTAS:
            raise MetrikaError(
                f"{path}: исчерпана суточная квота API Метрики (5000 запросов), она обнулится в 03:00 по Москве. "
                f"{_error_message(resp)}"
            )
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

    def counter(self, counter_id: int) -> dict:
        return self._get(f"/management/v1/counter/{counter_id}")["counter"]

    def goals(self, counter_id: int) -> list[dict]:
        return self._get(f"/management/v1/counter/{counter_id}/goals")["goals"]

    def report(
        self,
        counter_id: int,
        metrics: list[str],
        dimensions: list[str],
        date1: date,
        date2: date,
        filters: str | None = None,
        top: int | None = None,
    ) -> Report:
        """Отчёт целиком, со всеми страницами; с top — только первые top строк по первой метрике."""
        rows: list[dict] = []
        sampled = False
        while True:
            params = {
                "ids": counter_id,
                "metrics": ",".join(metrics),
                "dimensions": ",".join(dimensions),
                "date1": date1.isoformat(),
                "date2": date2.isoformat(),
                "limit": top or PAGE_SIZE,
                "offset": len(rows) + 1,  # строки в API нумеруются с единицы
                "accuracy": "full",
                "include_undefined": "true",
                "lang": "ru",
            }
            if filters:
                params["filters"] = filters
            if top:
                params["sort"] = f"-{metrics[0]}"
            data = self._get("/stat/v1/data", params)
            sampled = sampled or bool(data.get("sampled"))
            page = data.get("data", [])
            rows.extend(page)
            if top or not page or len(rows) >= data.get("total_rows", 0):
                return Report(rows, sampled)


def _error_type(resp: requests.Response) -> str:
    try:
        errors = resp.json().get("errors") or [{}]
        return str(errors[0].get("error_type", ""))
    except ValueError:
        return ""


def _error_message(resp: requests.Response) -> str:
    try:
        return str(resp.json().get("message") or resp.text[:300])
    except ValueError:
        return resp.text[:300]


def dimensions_for(grain: str, slice_: str, attribution: str) -> list[str]:
    return [GRAINS[grain]] + [d.format(attr=attribution) for d in SLICES[slice_].dims]


def month_starts(date1: date, date2: date) -> list[tuple[date, date]]:
    """Календарные месяцы периода [date1, date2], обрезанные по его границам."""
    out, start = [], date1
    while start <= date2:
        next_month = (start.replace(day=1) + timedelta(days=32)).replace(day=1)
        out.append((start, min(next_month - timedelta(days=1), date2)))
        start = next_month
    return out


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


def parse_goals(rows: list[dict], grain: str, slice_: str, goal_ids: list[int], skip: int = 0) -> list[GoalRow]:
    """skip — сколько метрик в начале строки не относятся к целям (визиты для сортировки топ-отчётов)."""
    out = []
    for row in rows:
        period, *dims = row["dimensions"]
        key, _ = _slice_key(dims)
        values = row["metrics"][skip:]
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
    audience: str = "all",
) -> tuple[list[TrafficRow], bool]:
    spec, dims, rows, sampled = SLICES[slice_], dimensions_for(grain, slice_, attribution), [], False
    # топ-отчёт запрашивается помесячно: иначе топ-N был бы общим на весь период, а не на каждый месяц
    periods = month_starts(date1, date2) if spec.top else [(date1, date2)]
    for d1, d2 in periods:
        report = report_by_parts(client, counter_id, TRAFFIC_METRICS, dims, d1, d2, AUDIENCES[audience], spec.top)
        rows += parse_traffic(report.rows, grain, slice_)
        sampled = sampled or report.sampled
    return rows, sampled


def fetch_goals(
    client: MetrikaClient,
    counter_id: int,
    grain: str,
    slice_: str,
    goal_ids: list[int],
    date1: date,
    date2: date,
    attribution: str,
    audience: str = "all",
) -> list[GoalRow]:
    spec, dims, rows = SLICES[slice_], dimensions_for(grain, slice_, attribution), []
    # В топ-отчёте первой метрикой идут визиты: по ним сортировка, чтобы строки совпали с отчётом трафика.
    lead = ["ym:s:visits"] if spec.top else []
    per_request = (MAX_METRICS - len(lead)) // 2
    periods = month_starts(date1, date2) if spec.top else [(date1, date2)]
    for d1, d2 in periods:
        for start in range(0, len(goal_ids), per_request):
            batch = goal_ids[start : start + per_request]
            report = report_by_parts(
                client, counter_id, lead + goal_metrics(batch), dims, d1, d2, AUDIENCES[audience], spec.top
            )
            rows.extend(parse_goals(report.rows, grain, slice_, batch, skip=len(lead)))
    return rows


def report_by_parts(
    client: MetrikaClient,
    counter_id: int,
    metrics: list[str],
    dimensions: list[str],
    date1: date,
    date2: date,
    filters: str | None = None,
    top: int | None = None,
) -> Report:
    """Отчёт за период; если Метрика считает запрос слишком сложным, период делится пополам.

    Деление идёт по границе месяца, чтобы месячная строка не собиралась из двух частей.
    """
    try:
        return client.report(counter_id, metrics, dimensions, date1, date2, filters, top)
    except MetrikaError as exc:
        split = month_split_point(date1, date2)
        if not exc.too_complex or split is None:
            raise
    left = report_by_parts(client, counter_id, metrics, dimensions, date1, split - timedelta(days=1), filters, top)
    right = report_by_parts(client, counter_id, metrics, dimensions, split, date2, filters, top)
    return Report(left.rows + right.rows, left.sampled or right.sampled)


def month_split_point(date1: date, date2: date) -> date | None:
    """Первое число месяца в середине периода; None, если период внутри одного месяца."""
    months = (date2.year - date1.year) * 12 + date2.month - date1.month
    if months == 0:
        return None
    years, month = divmod(date1.month - 1 + (months + 1) // 2, 12)
    return date(date1.year + years, month + 1, 1)
