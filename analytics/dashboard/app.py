"""Дашборд: одна страница с разделами, как в ежемесячном отчёте.

Запуск: uvicorn analytics.dashboard.app:app --port 8080
"""

from __future__ import annotations

import tomllib
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from analytics import db
from analytics.config import Settings, load_settings
from analytics.keywords import load_keywords
from analytics.dashboard import queries as q
from analytics.dashboard.format import (
    MONTHS_SHORT,
    Tile,
    delta,
    fmt_change,
    fmt_dec,
    fmt_duration,
    fmt_int,
    fmt_pct,
    month_name,
    month_short,
)

HERE = Path(__file__).parent
STALE_DAYS = 5  # с какого отставания данных Вебмастера показывать предупреждение
FULL_MONTH = 0.9  # месяц, где данные есть меньше чем за 90% дней, на графиках не показывается
TZ = ZoneInfo("Europe/Moscow")
SOURCE_NAMES = {"metrika": "Метрика: сайты", "maps": "Метрика: Карты", "webmaster": "Вебмастер"}
STREAM_NAMES = {"search": "статистика запросов", "query_analytics": "аналитика запросов",
                "pages_in_search": "страницы в поиске", "crawl": "обход роботом", "sqi": "ИКС"}

app = FastAPI(title="Аналитика", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters.update(
    int=fmt_int, dec=fmt_dec, pct=fmt_pct, duration=fmt_duration, month=month_name, month_short=month_short,
    change=fmt_change, local=lambda dt: dt.astimezone(TZ).strftime("%d.%m.%Y %H:%M") if dt else "—",
)


@app.get("/health")
def health() -> PlainTextResponse:
    return PlainTextResponse("ok")


@app.get("/", response_class=HTMLResponse)
def index(request: Request, site: int | None = None, month: str | None = None) -> HTMLResponse:
    settings = load_settings()
    try:
        with db.connect(settings.database_url) as conn:
            context = build_context(conn, settings, site, month, date.today())
    except psycopg.OperationalError as exc:
        context = {"error": f"Нет подключения к базе: {exc}"}
    return templates.TemplateResponse(request, "index.html", context)


def _shift_month(d: date, months: int) -> date:
    index = d.year * 12 + d.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _parse_month(value: str | None) -> date | None:
    try:
        return date.fromisoformat(f"{value}-01") if value else None
    except ValueError:
        return None


def chart(kind: str, labels: list[str], series: list[tuple[str, list]], fmt=fmt_int, selected: str | None = None) -> dict:
    """Данные графика для страницы и та же таблица для тех, кому нужны точные значения."""
    return {
        "kind": kind,
        "labels": labels,
        "series": [{"name": name, "data": [None if v is None else float(v) for v in values]} for name, values in series],
        "selected": selected if selected in labels else None,
        "table": {
            "columns": [name for name, _ in series],
            "rows": [[label, *[fmt(values[i]) for _, values in series]] for i, label in enumerate(labels)],
        },
    }


def trend_months(rows: list[dict], current: date, coverage: bool) -> tuple[list[dict], list[str]]:
    """Месяцы для графика тренда подряд, без пропусков на оси, и подписи пропущенных месяцев.

    Текущий месяц ещё не закончился и на графике выглядел бы обвалом. С coverage пропускаются месяцы,
    где данные есть меньше чем за 90% дней (провалы счётчика, Вебмастера). Пропущенный месяц остаётся
    на оси пустым местом: если склеить соседей, разрыв в данных станет незаметен.
    """
    by_month, skipped = {}, []
    for r in rows:
        if r["month"] >= current:
            continue
        if coverage and r["days_with_data"] < r["days_in_month"] * FULL_MONTH:
            skipped.append(f"{month_short(r['month'])} ({r['days_with_data']} из {r['days_in_month']} дн.)")
            continue
        by_month[r["month"]] = r
    if not by_month:
        return [], skipped
    months, m = [], min(by_month)
    while m <= max(by_month):
        months.append(by_month.get(m, {"month": m}))
        m = _shift_month(m, 1)
    return months, skipped


def seasonality(series: dict[date, int]) -> str:
    """Пик и спад спроса: месяц с самой большой и самой маленькой средней долей в году по полным годам."""
    years: dict[int, dict[int, int]] = defaultdict(dict)
    for month, value in series.items():
        years[month.year][month.month] = value
    full = [months for months in years.values() if len(months) == 12 and sum(months.values()) > 0]
    if not full:
        return "—"
    share = {m: sum(year[m] / sum(year.values()) for year in full) / len(full) for m in range(1, 13)}
    peak, low = max(share, key=share.get), min(share, key=share.get)
    return f"{MONTHS_SHORT[peak - 1]} · {MONTHS_SHORT[low - 1]}"


def demand_context(conn: psycopg.Connection, settings: Settings, site: dict, month: date) -> dict | None:
    """Раздел «Спрос»: Вордстат по региону сайта. None — если Вордстат не настроен или данных нет."""
    try:
        region = load_keywords(settings.wordstat_keywords_path).regions.get(site.get("site") or "")
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return None
    latest = region and q.wordstat_latest_month(conn, region.id)
    if not latest:
        return None
    wmonth = min(month, latest)  # Вордстат публикует месяц с задержкой
    prev, year_ago = _shift_month(wmonth, -1), _shift_month(wmonth, -12)

    def tile(label: str, series: dict[date, int]) -> Tile:
        cur, before, yoy = series.get(wmonth), series.get(prev), series.get(year_ago)
        note = " · ".join(p for p in (
            before is not None and f"{month_short(prev)}: {fmt_int(before)}",
            yoy is not None and f"{month_short(year_ago)}: {fmt_int(yoy)}",
        ) if p)
        return Tile(label, fmt_int(cur), delta(cur, before, "pct"), note)

    groups = q.wordstat_group_series(conn, region.id)
    brand = {r["month"]: r["demand"] for r in groups if r["kind"] == "brand"}
    by_category: dict[tuple[str, str], dict[date, int]] = defaultdict(dict)
    for r in groups:
        if r["kind"] == "category":
            by_category[r["direction"], r["category"]][r["month"]] = r["demand"]
    market: dict[str, dict[date, int]] = defaultdict(dict)
    for r in q.wordstat_market(conn, region.id):
        market[r["phrase"]][r["month"]] = r["count"]

    directions: dict[str, dict] = {}
    totals: dict[str, dict[date, int]] = defaultdict(dict)
    for r in q.wordstat_direction_series(conn, region.id):
        totals[r["direction"]][r["month"]] = r["demand"]
    for c in q.wordstat_categories(conn):
        series = by_category.get((c["direction"], c["category"]), {})
        d = directions.setdefault(c["direction"], {"name": c["direction"], "rows": []})
        d["rows"].append({
            "category": c["category"], "phrases": c["phrases"], "demand": series.get(wmonth),
            "prev": series.get(prev), "yoy": series.get(year_ago), "season": seasonality(series),
        })
    for name, d in directions.items():
        t = totals.get(name, {})
        d.update(demand=t.get(wmonth), prev=t.get(prev), yoy=t.get(year_ago), season=seasonality(t))

    months_axis = sorted(m for m in brand if m <= latest)
    labels = [month_short(m) for m in months_axis]
    main = [name for name in ("Ткани", "ПВХ", "Фурнитура") if name in totals]
    return {
        "region": region.name,
        "month": wmonth,
        "prev": prev,
        "year_ago": year_ago,
        "lagging": wmonth < month,
        "tiles": [tile("Брендовый спрос (B2B)", brand)] + [tile(f"«{p}»", s) for p, s in market.items()],
        "directions": list(directions.values()),
        "brand_queries": q.wordstat_brand_queries(conn, region.id),
        "charts": {
            "demand_brand": chart("bar", labels, [("Брендовые запросы", [brand.get(m) for m in months_axis])],
                                  selected=month_short(wmonth)),
            "demand_directions": chart("line", labels, [(n, [totals[n].get(m) for m in months_axis]) for n in main],
                                       selected=month_short(wmonth)),
        },
    }


def build_context(conn: psycopg.Connection, settings: Settings, site_id: int | None, month_param: str | None,
                  today: date) -> dict:
    status = [{**row, "name": SOURCE_NAMES.get(row["source"], row["source"])} for row in q.sync_status(conn)]
    sites = q.counters(conn, "site", settings.metrika_counters)
    if not sites:
        return {"empty": True, "status": status}
    site = next((s for s in sites if s["counter_id"] == site_id), sites[0])
    counter_id = site["counter_id"]

    available = q.months(conn, counter_id)
    if not available:  # сайт уже в базе, но первая загрузка ещё не дошла до месяцев
        return {"empty": True, "status": status}
    last_full = _shift_month(today.replace(day=1), -1)
    month = _parse_month(month_param)
    if month not in available:
        month = last_full if last_full in available else available[0]
    prev, year_ago = _shift_month(month, -1), _shift_month(month, -12)
    in_progress = month == today.replace(day=1)

    def note(prev_value, yoy_value=None, fmt=fmt_int, yoy_partial=False) -> str:
        parts = []
        if prev_value is not None:
            parts.append(f"{month_short(prev)}: {fmt(prev_value)}")
        if yoy_value is not None:
            parts.append(f"{month_short(year_ago)}: {fmt(yoy_value)}" + (" (неполные данные)" if yoy_partial else ""))
        return " · ".join(parts)

    alerts: list[str] = []
    if in_progress:
        alerts.append(f"{month_name(month)} ещё идёт: данные по {today.day - 1} число, "
                      f"а сравнение — с полным прошлым месяцем.")

    # Сайт: трафик и заявки
    m = q.site_month(conn, counter_id, month) or {}
    days_in_year_ago = (_shift_month(year_ago, 1) - year_ago).days
    yoy_partial = m.get("visits_yoy") is not None and (m.get("yoy_days_with_data") or 0) < days_in_year_ago * 0.9
    traffic_tiles = [
        Tile("Визиты", fmt_int(m.get("visits")), delta(m.get("visits"), m.get("visits_prev"), "pct"),
             note(m.get("visits_prev"), m.get("visits_yoy"), yoy_partial=yoy_partial)),
        Tile("Посетители", fmt_int(m.get("users")), delta(m.get("users"), m.get("users_prev"), "pct"),
             note(m.get("users_prev"), m.get("users_yoy"), yoy_partial=yoy_partial)),
        Tile("Отказы", fmt_pct(m.get("bounce_rate")),
             delta(m.get("bounce_rate"), m.get("bounce_rate_prev"), "pp", good="down"),
             note(m.get("bounce_rate_prev"), fmt=fmt_pct)),
        Tile("Глубина просмотра", f"{fmt_dec(m.get('page_depth'))} стр.",
             delta(m.get("page_depth"), m.get("page_depth_prev"), "abs"),
             note(m.get("page_depth_prev"), fmt=fmt_dec)),
        Tile("Время на сайте", fmt_duration(m.get("avg_duration_sec")),
             delta(m.get("avg_duration_sec"), m.get("avg_duration_sec_prev"), "pct"),
             note(m.get("avg_duration_sec_prev"), fmt=fmt_duration)),
    ]
    lead_tiles = [
        Tile("Заявки с сайта", fmt_int(m.get("leads")), delta(m.get("leads"), m.get("leads_prev"), "pct"),
             note(m.get("leads_prev"), m.get("leads_yoy"), yoy_partial=yoy_partial)),
        Tile("Конверсия в заявку", fmt_pct(m.get("conversion_pct"), 2),
             delta(m.get("conversion_pct"), m.get("conversion_pct_prev"), "pp"),
             note(m.get("conversion_pct_prev"), fmt=lambda v: fmt_pct(v, 2))),
        Tile("Контакты", fmt_int(m.get("contacts")), delta(m.get("contacts"), m.get("contacts_prev"), "pct"),
             "клики по телефону, почте и мессенджерам"),
    ]
    if yoy_partial:
        alerts.append(f"В {month_short(year_ago)} счётчик сайта собирал данные не каждый день — "
                      f"сравнение с прошлым годом за этот месяц некорректно.")

    current = today.replace(day=1)
    trend, skipped_site = trend_months(q.site_trend(conn, counter_id), current, coverage=True)
    labels = [month_short(r["month"]) for r in trend]
    daily = q.site_daily(conn, counter_id, month)
    charts = {
        "site_trend": chart("line", labels, [("Визиты", [r.get("visits") for r in trend]),
                                             ("Посетители", [r.get("users") for r in trend])], selected=month_short(month)),
        "site_daily": chart("bar", [r["day"].strftime("%d.%m") for r in daily],
                            [("Визиты", [r["visits"] for r in daily])]),
        "leads_trend": chart("line", labels, [("Заявки", [r.get("leads") for r in trend]),
                                              ("Контакты", [r.get("contacts") for r in trend])], selected=month_short(month)),
    }

    # Поиск Яндекса
    search = None
    host_id = q.host_for_site(conn, counter_id)
    if host_id:
        sm = q.search_month(conn, host_id, month)
        partial_month = bool(sm) and sm["days_with_data"] < sm["days_in_month"]
        freshness = q.search_freshness(conn, host_id)
        stale = [f for f in freshness if f["stream"] != "sqi" and f["days_stale"] is not None
                 and f["days_stale"] > STALE_DAYS]
        if stale:
            oldest = max(stale, key=lambda f: f["days_stale"])
            alerts.append(
                f"Вебмастер не обновлял данные {oldest['days_stale']} дн. "
                f"({', '.join(STREAM_NAMES.get(f['stream'], f['stream']) + ' — по ' + f['last_date'].strftime('%d.%m') for f in stale)}). "
                f"Цифры поиска за последние недели неполные."
            )
        search_tiles = []
        if sm:
            def cmp(*args, **kwargs):
                # за неполный месяц сумма заведомо меньше полного прошлого — сравнение не показываем
                return None if partial_month else delta(*args, **kwargs)

            search_tiles = [
                Tile("Показы", fmt_int(sm["shows"]), cmp(sm["shows"], sm["shows_prev"], "pct"),
                     note(sm["shows_prev"])),
                Tile("Клики", fmt_int(sm["clicks"]), cmp(sm["clicks"], sm["clicks_prev"], "pct"),
                     note(sm["clicks_prev"])),
                Tile("CTR", fmt_pct(sm["ctr_pct"], 2), cmp(sm["ctr_pct"], sm["ctr_pct_prev"], "pp"),
                     note(sm["ctr_pct_prev"], fmt=lambda v: fmt_pct(v, 2))),
                Tile("Средняя позиция показа", fmt_dec(sm["avg_show_position"], 2),
                     cmp(sm["avg_show_position"], sm["avg_show_position_prev"], "abs", good="down"),
                     note(sm["avg_show_position_prev"], fmt=lambda v: fmt_dec(v, 2))),
                Tile("Средняя позиция клика", fmt_dec(sm["avg_click_position"], 2),
                     cmp(sm["avg_click_position"], sm["avg_click_position_prev"], "abs", good="down"),
                     note(sm["avg_click_position_prev"], fmt=lambda v: fmt_dec(v, 2))),
            ]
        s_trend, skipped_search = trend_months(q.search_trend(conn, host_id), current, coverage=True)
        s_labels = [month_short(r["month"]) for r in s_trend]
        charts["search_shows"] = chart("bar", s_labels, [("Показы", [r.get("shows") for r in s_trend])],
                                       selected=month_short(month))
        charts["search_clicks"] = chart("bar", s_labels, [("Клики", [r.get("clicks") for r in s_trend])],
                                        selected=month_short(month))
        buckets = q.position_buckets(conn, host_id, month)
        if buckets:
            total = sum(b["impressions"] for b in buckets["ranges"]) or 1
            for b in buckets["ranges"]:
                b["share"] = b["impressions"] / total * 100
        search = {
            "host": host_id.split(":")[1],
            "tiles": search_tiles,
            "coverage": partial_month and f"данные за {sm['days_with_data']} из {sm['days_in_month']} дней, "
                                          f"поэтому сравнения с прошлым месяцем нет",
            "skipped": skipped_search,
            "queries": q.top_queries(conn, host_id, month),
            "buckets": buckets,
        }

    # Карты
    maps = []
    for card in q.counters(conn, "maps", settings.metrika_maps_counters):
        mm = q.maps_month(conn, card["counter_id"], month)
        if not mm:
            continue
        tiles = [
            Tile(label, fmt_int(mm[key]), delta(mm[key], mm[f"{key}_prev"], "pct"), note(mm[f"{key}_prev"]))
            for label, key in (("Визиты карточки", "card_visits"), ("Звонки", "calls"),
                               ("Маршруты", "routes"), ("Переходы на сайт", "site"))
        ]
        m_trend, _ = trend_months(q.maps_trend(conn, card["counter_id"]), current, coverage=False)
        chart_id = f"maps_{card['counter_id']}"
        if any(r.get("calls") or r.get("routes") or r.get("site") for r in m_trend):
            charts[chart_id] = chart("line", [month_short(r["month"]) for r in m_trend], [
                ("Звонки", [r.get("calls") for r in m_trend]),
                ("Маршруты", [r.get("routes") for r in m_trend]),
                ("Переходы на сайт", [r.get("site") for r in m_trend]),
            ], selected=month_short(month))
        maps.append({"name": card["name"], "tiles": tiles, "chart": chart_id if chart_id in charts else None})

    demand = demand_context(conn, settings, site, month)
    if demand:
        charts.update(demand.pop("charts"))

    return {
        "demand": demand,
        "sites": sites,
        "site": site,
        "month": month,
        "prev": prev,
        "months": available,
        "alerts": alerts,
        "traffic_tiles": traffic_tiles,
        "lead_tiles": lead_tiles,
        "skipped_site": skipped_site,
        "channels": q.channels(conn, counter_id, month),
        "search": search,
        "maps": maps,
        "charts": charts,
        "status": status,
    }
