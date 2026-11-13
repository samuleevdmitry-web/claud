"""Страницы дашборда: что показать в каждом разделе и какие выводы сделать.

Каждая функция раздела получает соединение с базой, настройки и выбранные фильтры (сайт, месяц, аудиторию)
и возвращает контекст для своего шаблона. Сводки строит analytics.insights по тем же цифрам, что в таблицах.
"""

from __future__ import annotations

import tomllib
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

import psycopg

from analytics import insights as ins
from analytics.config import Settings
from analytics.dashboard import queries as q
from analytics.format import (
    MONTHS_SHORT,
    Tile,
    delta,
    fmt_dec,
    fmt_duration,
    fmt_int,
    fmt_pct,
    month_name,
    month_short,
)
from analytics.guide import GUIDE
from analytics.keywords import load_keywords

STALE_DAYS = 5  # с какого отставания данных Вебмастера показывать предупреждение
FULL_MONTH = 0.9  # месяц, где данные есть меньше чем за 90% дней, на графиках не показывается
AUDIENCES = {"humans": "Только люди", "all": "Весь трафик"}
STREAM_NAMES = {"search": "статистика запросов", "query_analytics": "аналитика запросов",
                "pages_in_search": "страницы в поиске", "crawl": "обход роботом", "sqi": "ИКС"}
SOURCE_NAMES = {"metrika": "Метрика: сайты", "maps": "Метрика: Карты", "webmaster": "Вебмастер",
                "wordstat": "Вордстат", "rabbit": "RabbitSeo", "report": "Отчёт за месяц",
                "metrika_backfill": "Метрика: догрузка новых отчётов"}

# Вкладки раздела «Трафик»: отчёты Метрики по группам, как в её интерфейсе.
TRAFFIC_TABS = {
    "summary": ("Сводка", []),
    "sources": ("Источники", [
        ("source", "Каналы"), ("search_engine", "Поисковые системы"), ("adv_engine", "Рекламные системы"),
        ("referrer", "Сайты"), ("social", "Социальные сети"), ("recommendation", "Рекомендательные системы"),
        ("messenger", "Мессенджеры"), ("utm", "UTM-метки"), ("search_phrase", "Поисковые фразы"),
        ("direct_campaign", "Директ: кампании"), ("direct_phrase", "Директ: фразы и условия показа"),
    ]),
    "geo": ("География", [("country", "Страны"), ("region", "Регионы"), ("city", "Города")]),
    "audience": ("Аудитория", [("gender", "Пол"), ("age", "Возраст"), ("new_user", "Новые и вернувшиеся"),
                               ("visit_number", "Какой по счёту визит"), ("days_since_first", "Дней с первого визита"),
                               ("interest", "Интересы")]),
    "tech": ("Технологии", [("device", "Устройства"), ("os", "Операционные системы"), ("browser", "Браузеры"),
                            ("phone_brand", "Производители телефонов"), ("screen", "Разрешение экрана")]),
    "content": ("Содержание", [("landing_section", "Разделы входа"), ("landing", "Страницы входа"),
                               ("exit", "Страницы выхода"), ("depth", "Глубина просмотра"),
                               ("duration", "Время на сайте")]),
    "time": ("Время", [("hour", "По часам"), ("weekday", "По дням недели")]),
}
# Отчёты, где порядок строк задан самим значением (часы, дни недели, интервалы), а не числом визитов.
ORDERED = {"hour", "weekday", "age", "depth", "duration", "visit_number", "days_since_first"}
CHARTED = {"hour", "weekday", "age"}  # к этим отчётам — столбики визитов
# С фильтром роботности Метрика отдаёт эти отчёты не целиком: теряет мелкие строки и большую часть целей.
# В месяцы без роботов сборщик берёт их из всего трафика, в месяцы с роботами — помечаем.
FILTER_GAPS = {"city", "landing", "os"}
# Отчёты по видам источников: строка «Не определено» в них — визиты не из этого источника, её не показываем.
SUBSOURCE = {"search_engine", "adv_engine", "referrer", "social", "recommendation", "messenger", "utm",
             "search_phrase", "direct_campaign", "direct_phrase"}


@dataclass
class Filters:
    sites: list[dict]
    site: dict
    audience: str
    months: list[date]
    month: date
    today: date

    @property
    def prev(self) -> date:
        return shift_month(self.month, -1)

    @property
    def year_ago(self) -> date:
        return shift_month(self.month, -12)

    @property
    def counter_id(self) -> int:
        return self.site["counter_id"]

    def query(self, **override) -> str:
        """Строка запроса с текущими фильтрами — чтобы переход по меню их не сбрасывал."""
        params = {"site": self.counter_id, "month": self.month.strftime("%Y-%m"), "aud": self.audience, **override}
        return "?" + urlencode({k: v for k, v in params.items() if v is not None})


def shift_month(d: date, months: int) -> date:
    index = d.year * 12 + d.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def parse_month(value: str | None) -> date | None:
    try:
        return date.fromisoformat(f"{value}-01") if value else None
    except ValueError:
        return None


def resolve_filters(conn: psycopg.Connection, settings: Settings, site_id: int | None, month: str | None,
                    audience: str | None, today: date) -> Filters | None:
    """Фильтры из адреса страницы; None — если данных Метрики ещё нет (идёт первая загрузка)."""
    sites = q.counters(conn, "site", settings.metrika_counters)
    if not sites:
        return None
    site = next((s for s in sites if s["counter_id"] == site_id), sites[0])
    last_full = shift_month(today.replace(day=1), -1)
    if audience not in AUDIENCES:
        # «Только люди» по умолчанию — если их история уже загружена; иначе весь трафик
        humans = q.months(conn, site["counter_id"], "humans")
        audience = "humans" if last_full in humans or (humans and not q.months(conn, site["counter_id"], "all")) else "all"
    available = q.months(conn, site["counter_id"], audience)
    if not available:
        return None
    chosen = parse_month(month)
    if chosen not in available:
        chosen = last_full if last_full in available else available[0]
    return Filters(sites, site, audience, available, chosen, today)


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
        m = shift_month(m, 1)
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


def _note(f: Filters, prev_value, yoy_value=None, fmt=fmt_int, yoy_partial: bool = False) -> str:
    parts = []
    if prev_value is not None:
        parts.append(f"{month_short(f.prev)}: {fmt(prev_value)}")
    if yoy_value is not None:
        parts.append(f"{month_short(f.year_ago)}: {fmt(yoy_value)}" + (" (неполные данные)" if yoy_partial else ""))
    return " · ".join(parts)


def _site_basics(conn: psycopg.Connection, f: Filters) -> tuple[dict, bool]:
    m = q.site_month(conn, f.counter_id, f.audience, f.month) or {}
    m["contact_kinds"] = q.contact_kinds(conn, f.counter_id)
    days_year_ago = (shift_month(f.year_ago, 1) - f.year_ago).days
    yoy_partial = m.get("visits_yoy") is not None and (m.get("yoy_days_with_data") or 0) < days_year_ago * FULL_MONTH
    return m, yoy_partial


def _traffic_tiles(f: Filters, m: dict, yoy_partial: bool) -> list[Tile]:
    return [
        Tile("Визиты", fmt_int(m.get("visits")), delta(m.get("visits"), m.get("visits_prev"), "pct"),
             _note(f, m.get("visits_prev"), m.get("visits_yoy"), yoy_partial=yoy_partial)),
        Tile("Посетители", fmt_int(m.get("users")), delta(m.get("users"), m.get("users_prev"), "pct"),
             _note(f, m.get("users_prev"), m.get("users_yoy"), yoy_partial=yoy_partial)),
        Tile("Отказы", fmt_pct(m.get("bounce_rate")),
             delta(m.get("bounce_rate"), m.get("bounce_rate_prev"), "pp", good="down"),
             _note(f, m.get("bounce_rate_prev"), fmt=fmt_pct)),
        Tile("Глубина просмотра", f"{fmt_dec(m.get('page_depth'))} стр.",
             delta(m.get("page_depth"), m.get("page_depth_prev"), "abs"), _note(f, m.get("page_depth_prev"), fmt=fmt_dec)),
        Tile("Время на сайте", fmt_duration(m.get("avg_duration_sec")),
             delta(m.get("avg_duration_sec"), m.get("avg_duration_sec_prev"), "pct"),
             _note(f, m.get("avg_duration_sec_prev"), fmt=fmt_duration)),
    ]


CONTACT_KINDS = {"phone": "клики по телефону", "email": "клики по почте", "messenger": "переходы в мессенджеры"}


def _lead_tiles(f: Filters, m: dict, yoy_partial: bool) -> list[Tile]:
    """Заявки — визиты с хоть одной целью-заявкой; контакты — клики по контактам, которые заявками не считаются."""
    tiles = [
        Tile("Заявки с сайта", fmt_int(m.get("leads")), delta(m.get("leads"), m.get("leads_prev"), "pct"),
             _note(f, m.get("leads_prev"), m.get("leads_yoy"), yoy_partial=yoy_partial)),
        Tile("Конверсия в заявку", fmt_pct(m.get("conversion_pct"), 2),
             delta(m.get("conversion_pct"), m.get("conversion_pct_prev"), "pp"),
             _note(f, m.get("conversion_pct_prev"), fmt=lambda v: fmt_pct(v, 2))),
    ]
    kinds = [CONTACT_KINDS[k] for k in CONTACT_KINDS if k in m.get("contact_kinds", ())]
    if kinds:
        tiles.append(Tile("Контакты", fmt_int(m.get("contacts")), delta(m.get("contacts"), m.get("contacts_prev"), "pct"),
                          ", ".join(kinds)))
    return tiles


def _robots(conn: psycopg.Connection, f: Filters) -> tuple[float | None, float | None]:
    robots = q.robots_by_month(conn, f.counter_id)
    cur, prev = robots.get(f.month), robots.get(f.prev)
    return (cur and float(cur["robots_pct"] or 0)), (prev and float(prev["robots_pct"] or 0))


def _traffic_charts(conn: psycopg.Connection, f: Filters) -> tuple[dict, list[str], list[dict]]:
    trend, skipped = trend_months(q.site_trend(conn, f.counter_id, f.audience), f.today.replace(day=1), coverage=True)
    labels = [month_short(r["month"]) for r in trend]
    daily = q.site_daily(conn, f.counter_id, f.audience, f.month)
    charts = {
        "site_trend": chart("line", labels, [("Визиты", [r.get("visits") for r in trend]),
                                             ("Посетители", [r.get("users") for r in trend])], selected=month_short(f.month)),
        "site_daily": chart("bar", [r["day"].strftime("%d.%m") for r in daily], [("Визиты", [r["visits"] for r in daily])]),
        "leads_trend": chart("line", labels, [("Заявки", [r.get("leads") for r in trend]),
                                              ("Контакты", [r.get("contacts") for r in trend])], selected=month_short(f.month)),
    }
    return charts, skipped, daily


def _slice_report(conn: psycopg.Connection, f: Filters, slice_: str, title: str, total: int, site_conv: float,
                  site_bounce: float) -> tuple[dict, list[ins.Finding]]:
    rows = q.slice_rows(conn, f.counter_id, f.audience, slice_, f.month)
    if slice_ in SUBSOURCE:
        rows = [r for r in rows if not ins.is_undefined(r)]
    if slice_ in ORDERED:
        rows.sort(key=lambda r: (not r["dim_key"].isdigit(), int(r["dim_key"]) if r["dim_key"].isdigit() else 0,
                                 r["dim_label"]))
    findings = ins.slice_findings(slice_, title, rows, total, site_conv, site_bounce, f.prev)
    for r in rows:
        r["share"] = r["visits"] / total * 100 if total else None
        if r["dim_label"].startswith("http"):
            r["dim_label"] = "/" + r["dim_label"].split("://", 1)[1].partition("/")[2]
    report = {"slice": slice_, "title": title, "rows": rows, "chart": None, "note": None, "hint": GUIDE.get(slice_)}
    if f.audience == "humans" and slice_ in FILTER_GAPS and (_robots(conn, f)[0] or 0) > 0:
        report["note"] = ("В этом месяце были роботы, а с фильтром «только люди» Метрика отдаёт этот отчёт "
                          "не целиком: мелкие строки и часть заявок по ним потеряны. Для полной картины "
                          "переключитесь на «Весь трафик».")
    if slice_ in CHARTED and rows:
        report["chart"] = chart("bar", [r["dim_label"] for r in rows], [("Визиты", [r["visits"] for r in rows])])
    return report, findings


# ----- Разделы -----

def overview(conn: psycopg.Connection, settings: Settings, f: Filters) -> dict:
    m, yoy_partial = _site_basics(conn, f)
    robots, robots_prev = _robots(conn, f)
    charts, skipped, daily = _traffic_charts(conn, f)
    findings = ins.traffic_summary(m, robots, robots_prev, daily, f.prev, yoy_partial, f.audience)
    total, conv, bounce = m.get("visits") or 0, float(m.get("conversion_pct") or 0), float(m.get("bounce_rate") or 0)
    for slice_, title in (("source", "Каналы"), ("country", "Страны"), ("device", "Устройства")):
        findings += _slice_report(conn, f, slice_, title, total, conv, bounce)[1]
    search = search_page(conn, settings, f)
    demand = demand_page(conn, settings, f)
    maps = maps_page(conn, settings, f)
    findings += search.get("findings", []) + (demand or {}).get("findings", []) + maps.get("findings", [])
    pf = pf_page(conn, settings, f)  # итог — отдельной плашкой над сводкой
    todo = recommendations(conn, settings, f, pf)
    tiles = [_traffic_tiles(f, m, yoy_partial)[0]] + _lead_tiles(f, m, yoy_partial)[:2]
    if search.get("tiles"):
        tiles += search["tiles"][:2]
    if demand:
        tiles.append(demand["tiles"][0])
    return {"tiles": tiles, "summary": ins.summarize(findings, limit=5),
            "pf_alert": pf["verdict"] if pf["status"] != "ok" else None, "pf_status": pf["status"],
            "todo": todo[:3], "todo_total": len(todo), "priorities": ins.PRIORITIES,
            "charts": {"site_trend": charts["site_trend"], "leads_trend": charts["leads_trend"]}, "skipped": skipped}


def traffic(conn: psycopg.Connection, settings: Settings, f: Filters, tab: str) -> dict:
    tab = tab if tab in TRAFFIC_TABS else "summary"
    m, yoy_partial = _site_basics(conn, f)
    total, conv, bounce = m.get("visits") or 0, float(m.get("conversion_pct") or 0), float(m.get("bounce_rate") or 0)
    context = {"tab": tab, "tabs": {k: v[0] for k, v in TRAFFIC_TABS.items()}, "reports": [], "charts": {}}
    if tab == "summary":
        robots, robots_prev = _robots(conn, f)
        charts, skipped, daily = _traffic_charts(conn, f)
        findings = ins.traffic_summary(m, robots, robots_prev, daily, f.prev, yoy_partial, f.audience)
        report, more = _slice_report(conn, f, "source", "Каналы", total, conv, bounce)
        context.update(tiles=_traffic_tiles(f, m, yoy_partial), skipped=skipped, reports=[report],
                       charts={k: charts[k] for k in ("site_trend", "site_daily")})
        findings += more
    else:
        findings = []
        for slice_, title in TRAFFIC_TABS[tab][1]:
            report, more = _slice_report(conn, f, slice_, title, total, conv, bounce)
            context["reports"].append(report)
            findings += more
            if report["chart"]:
                context["charts"][f"slice_{slice_}"] = report["chart"]
        rows = {r["slice"]: r["rows"] for r in context["reports"]}
        if tab == "sources":
            findings += ins.direct_findings(rows["direct_campaign"], rows["direct_phrase"], conv)
        elif tab == "audience":
            findings += ins.loyalty_findings(rows["visit_number"])
        elif tab == "content":
            context["pages"] = content_pages(conn, f, total)
            findings = context["pages"]["findings"] + findings
    context["summary"] = ins.summarize(findings)
    context["site_conv"], context["site_bounce"] = conv, bounce
    return context


def content_pages(conn: psycopg.Connection, f: Filters, total_visits: int) -> dict:
    """Отчёты по просмотрам страниц за месяц: 404 и откуда на них приходят, поиск по сайту, популярное, заголовки."""
    nf = q.not_found_visits(conn, f.counter_id, f.month)
    urls = q.pages(conn, f.counter_id, f.month, "not_found", 15)
    refs = q.pages(conn, f.counter_id, f.month, "not_found_from", 10)
    search = [r for r in q.pages(conn, f.counter_id, f.month, "site_search", 31) if r["key"].strip()][:30]
    domain = (f.site.get("site") or "").removeprefix("www.")
    for r in urls:
        r["label"] = ins.short_url(r["key"])
    for r in refs:
        host = urlsplit(r["key"]).netloc.removeprefix("www.")
        own = host.endswith(domain) if domain else True
        r["label"] = "(без источника)" if not r["key"] else ins.short_url(r["key"]) if own else r["key"].split("://")[-1]
    return {
        "available": q.has_pages(conn, f.counter_id, f.month),
        "nf": nf, "nf_share": (nf["visits"] or 0) / total_visits * 100 if total_visits else None,
        "not_found": urls, "not_found_from": refs, "site_search": search,
        "popular": q.pages(conn, f.counter_id, f.month, "page", 20),
        "titles": q.pages(conn, f.counter_id, f.month, "title", 15),
        "hints": GUIDE,
        "findings": ins.not_found_findings(nf, total_visits, urls, refs, (f.site.get("site") or "").removeprefix("www."))
                    + ins.site_search_findings(search),
    }


def shop(conn: psycopg.Connection, f: Filters, goals: list[dict]) -> dict:
    """Электронная коммерция: путь «корзина → заказ» по целям и товары."""
    def visits(kind: str) -> int | None:
        found = [g["visits"] for g in goals if g["type"] == kind]
        return sum(found) if found else None

    products = q.products(conn, f.counter_id, f.month, 25)
    totals = q.products_total(conn, f.counter_id, f.month)
    cart, purchase = visits("e_cart"), visits("e_purchase")
    return {"products": products, "totals": totals, "cart_visits": cart, "purchase_visits": purchase,
            "findings": ins.products_findings(products, totals, cart, purchase), "hint": GUIDE["products"]}


def leads(conn: psycopg.Connection, settings: Settings, f: Filters) -> dict:
    m, yoy_partial = _site_basics(conn, f)
    total, conv, bounce = m.get("visits") or 0, float(m.get("conversion_pct") or 0), float(m.get("bounce_rate") or 0)
    charts, _, _ = _traffic_charts(conn, f)
    goals = q.goals(conn, f.counter_id, f.audience, f.month)
    findings = ins.goals_findings(goals, f.prev)
    cross = []
    for slice_, title, short in (("source", "Заявки по каналам", "Каналы"),
                                 ("device", "Заявки по устройствам", "Устройства"),
                                 ("city", "Заявки по городам", "Города"),
                                 ("landing", "Заявки по страницам входа", "Страницы входа")):
        report, more = _slice_report(conn, f, slice_, short, total, conv, bounce)
        report["title"] = title
        report["rows"] = sorted((r for r in report["rows"] if r["leads"] or r["contacts"]),
                                key=lambda r: -(r["leads"] or 0))[:15]
        cross.append(report)
        findings += [x for x in more if "конверт" in x.text or "заяв" in x.text]
    store = shop(conn, f, goals)
    findings += store["findings"]
    return {"tiles": _lead_tiles(f, m, yoy_partial), "goals": goals, "cross": cross, "shop": store,
            "charts": {"leads_trend": charts["leads_trend"]}, "summary": ins.summarize(findings)}


def search_page(conn: psycopg.Connection, settings: Settings, f: Filters) -> dict:
    host_id = q.host_for_site(conn, f.counter_id)
    if not host_id:
        return {}
    sm = q.search_month(conn, host_id, f.month)
    partial = bool(sm) and sm["days_with_data"] < sm["days_in_month"]
    freshness = q.search_freshness(conn, host_id)
    stale = [x for x in freshness if x["stream"] != "sqi" and (x["days_stale"] or 0) > STALE_DAYS]
    stale_days = max((x["days_stale"] for x in stale), default=None)

    def cmp(*args, **kwargs):
        # за неполный месяц сумма заведомо меньше полного прошлого — сравнение не показываем
        return None if partial else delta(*args, **kwargs)

    tiles = []
    if sm:
        tiles = [
            Tile("Показы в поиске", fmt_int(sm["shows"]), cmp(sm["shows"], sm["shows_prev"], "pct"), _note(f, sm["shows_prev"])),
            Tile("Клики из поиска", fmt_int(sm["clicks"]), cmp(sm["clicks"], sm["clicks_prev"], "pct"), _note(f, sm["clicks_prev"])),
            Tile("CTR", fmt_pct(sm["ctr_pct"], 2), cmp(sm["ctr_pct"], sm["ctr_pct_prev"], "pp"),
                 _note(f, sm["ctr_pct_prev"], fmt=lambda v: fmt_pct(v, 2))),
            Tile("Средняя позиция показа", fmt_dec(sm["avg_show_position"], 2),
                 cmp(sm["avg_show_position"], sm["avg_show_position_prev"], "abs", good="down"),
                 _note(f, sm["avg_show_position_prev"], fmt=lambda v: fmt_dec(v, 2))),
            Tile("Средняя позиция клика", fmt_dec(sm["avg_click_position"], 2),
                 cmp(sm["avg_click_position"], sm["avg_click_position_prev"], "abs", good="down"),
                 _note(f, sm["avg_click_position_prev"], fmt=lambda v: fmt_dec(v, 2))),
        ]
    trend, skipped = trend_months(q.search_trend(conn, host_id), f.today.replace(day=1), coverage=True)
    labels = [month_short(r["month"]) for r in trend]
    buckets = q.position_buckets(conn, host_id, f.month)
    if buckets:
        total = sum(b["impressions"] for b in buckets["ranges"]) or 1
        for b in buckets["ranges"]:
            b["share"] = b["impressions"] / total * 100
    movers = q.position_movers(conn, host_id, f.month)
    problems = q.search_problems(conn, host_id)
    findings = ins.search_findings(sm, partial, movers, stale_days, problems, f.prev)
    rabbit = rabbit_block(conn, f.site.get("site") or "")
    if rabbit:
        findings += rabbit["findings"]
    alerts = []
    if stale:
        alerts.append(f"Вебмастер не обновлял данные {stale_days} дн. ("
                      + ", ".join(f"{STREAM_NAMES.get(x['stream'], x['stream'])} — по {x['last_date']:%d.%m}" for x in stale)
                      + "). Цифры поиска за последние недели неполные.")
    return {
        "host": host_id.split(":")[1],
        "tiles": tiles,
        "partial": partial and f"данные за {sm['days_with_data']} из {sm['days_in_month']} дней, "
                               "поэтому сравнения с прошлым месяцем нет",
        "skipped": skipped,
        "queries": q.top_queries(conn, host_id, f.month),
        "movers": {k: v[:20] for k, v in movers.items()},
        "movers_count": {k: len(v) for k, v in movers.items()},
        "buckets": buckets,
        "problems": problems,
        "summary_info": q.search_summary(conn, host_id),
        "alerts": alerts,
        "findings": findings,
        "summary": ins.summarize(findings),
        "rabbit": rabbit,
        "charts": {
            "search_shows": chart("bar", labels, [("Показы", [r.get("shows") for r in trend])], selected=month_short(f.month)),
            "search_clicks": chart("bar", labels, [("Клики", [r.get("clicks") for r in trend])], selected=month_short(f.month)),
            **({"rabbit_daily": rabbit["chart"]} if rabbit else {}),
        },
    }


def rabbit_block(conn: psycopg.Connection, project: str, compare_days: int = 7) -> dict | None:
    """Позиции RabbitSeo: последний съём против съёма неделей раньше (или самого раннего, если истории меньше)."""
    daily = q.rabbit_daily(conn, project)
    if not daily:
        return None
    now = daily[-1]
    target = now["date"] - timedelta(days=compare_days)
    then = next((d for d in reversed(daily) if d["date"] <= target), daily[0])
    compare = q.rabbit_compare(conn, project, now["date"], then["date"])
    tiles = [
        Tile("В ТОП-3", fmt_int(now["top3"]), delta(now["top3"], then["top3"], "pct"), f"{then['date']:%d.%m}: {then['top3']}"),
        Tile("В ТОП-10", fmt_int(now["top10"]), delta(now["top10"], then["top10"], "pct"), f"{then['date']:%d.%m}: {then['top10']}"),
        Tile("Найдено в выдаче", f"{now['found']} из {now['queries']}", delta(now["found"], then["found"], "pct"),
             f"{then['date']:%d.%m}: {then['found']}"),
    ]
    labels = [d["date"].strftime("%d.%m") for d in daily]
    return {
        "now": now["date"], "then": then["date"], "tiles": tiles,
        "lists": {k: v[:25] for k, v in compare.items()},
        "counts": {k: len(v) for k, v in compare.items()},
        "findings": ins.rabbit_findings(compare, now, then),
        "chart": chart("line", labels, [("ТОП-3", [d["top3"] for d in daily]), ("ТОП-10", [d["top10"] for d in daily]),
                                        ("Найдено", [d["found"] for d in daily])]),
    }


def demand_page(conn: psycopg.Connection, settings: Settings, f: Filters) -> dict | None:
    """Вордстат по региону сайта. None — если Вордстат не настроен или данных нет."""
    try:
        region = load_keywords(settings.wordstat_keywords_path).regions.get(f.site.get("site") or "")
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return None
    latest = region and q.wordstat_latest_month(conn, region.id)
    if not latest:
        return None
    wmonth = min(f.month, latest)  # Вордстат публикует месяц с задержкой
    prev, year_ago = shift_month(wmonth, -1), shift_month(wmonth, -12)

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
    category_rows = []
    for c in q.wordstat_categories(conn):
        series = by_category.get((c["direction"], c["category"]), {})
        row = {"category": c["category"], "phrases": c["phrases"], "demand": series.get(wmonth),
               "prev": series.get(prev), "yoy": series.get(year_ago), "season": seasonality(series)}
        directions.setdefault(c["direction"], {"name": c["direction"], "rows": []})["rows"].append(row)
        category_rows.append(row)
    for name, d in directions.items():
        t = totals.get(name, {})
        d.update(demand=t.get(wmonth), prev=t.get(prev), yoy=t.get(year_ago), season=seasonality(t))

    findings = ins.demand_findings(
        {"cur": brand.get(wmonth), "yoy": brand.get(year_ago)},
        [{"phrase": p, "cur": s.get(wmonth), "yoy": s.get(year_ago)} for p, s in market.items()],
        category_rows, year_ago,
    )
    months_axis = sorted(m for m in brand if m <= latest)
    labels = [month_short(m) for m in months_axis]
    main = [name for name in ("Ткани", "ПВХ", "Фурнитура") if name in totals]
    return {
        "region": region.name,
        "month": wmonth,
        "prev": prev,
        "year_ago": year_ago,
        "lagging": wmonth < f.month,
        "tiles": [tile("Брендовый спрос (B2B)", brand)] + [tile(f"«{p}»", s) for p, s in market.items()],
        "directions": list(directions.values()),
        "brand_queries": q.wordstat_brand_queries(conn, region.id),
        "findings": findings,
        "summary": ins.summarize(findings),
        "charts": {
            "demand_brand": chart("bar", labels, [("Брендовые запросы", [brand.get(m) for m in months_axis])],
                                  selected=month_short(wmonth)),
            "demand_directions": chart("line", labels, [(n, [totals[n].get(m) for m in months_axis]) for n in main],
                                       selected=month_short(wmonth)),
        },
    }


def maps_page(conn: psycopg.Connection, settings: Settings, f: Filters) -> dict:
    cards, charts = [], {}
    for card in q.counters(conn, "maps", settings.metrika_maps_counters):
        mm = q.maps_month(conn, card["counter_id"], f.audience, f.month)
        if not mm:
            continue
        tiles = [
            Tile(label, fmt_int(mm[key]), delta(mm[key], mm[f"{key}_prev"], "pct"), _note(f, mm[f"{key}_prev"]))
            for label, key in (("Визиты карточки", "card_visits"), ("Звонки", "calls"),
                               ("Маршруты", "routes"), ("Переходы на сайт", "site"))
        ]
        m_trend, _ = trend_months(q.maps_trend(conn, card["counter_id"], f.audience), f.today.replace(day=1), coverage=False)
        chart_id = f"maps_{card['counter_id']}"
        if any(r.get("calls") or r.get("routes") or r.get("site") for r in m_trend):
            charts[chart_id] = chart("line", [month_short(r["month"]) for r in m_trend], [
                ("Звонки", [r.get("calls") for r in m_trend]),
                ("Маршруты", [r.get("routes") for r in m_trend]),
                ("Переходы на сайт", [r.get("site") for r in m_trend]),
            ], selected=month_short(f.month))
        cards.append({"name": card["name"], "tiles": tiles, "chart": chart_id if chart_id in charts else None, "data": mm})
    findings = ins.maps_findings(cards, f.prev)
    return {"cards": cards, "charts": charts, "findings": findings, "summary": ins.summarize(findings)}


def status_page(conn: psycopg.Connection, settings: Settings, f: Filters | None) -> dict:
    status = [{**row, "name": SOURCE_NAMES.get(row["source"], row["source"])} for row in q.sync_status(conn)]
    robots = []
    if f:
        robots = [r for r in q.robots_by_month(conn, f.counter_id).values() if r["month"] < f.today.replace(day=1)]
    return {"status": status, "robots": robots}


# ----- ПФ и риски -----

def _brand_words(settings: Settings, domain: str) -> set[str]:
    """Слова бренда: имя домена и первые слова брендовых фраз Вордстата («олдос», «oldos»)."""
    words = {domain.split(".")[0].lower()} if domain else set()
    try:
        config = load_keywords(settings.wordstat_keywords_path)
    except (FileNotFoundError, tomllib.TOMLDecodeError):
        return words
    return words | {k.phrase.split()[0].lower() for k in config.keywords if k.kind == "brand"}


def pf_page(conn: psycopg.Connection, settings: Settings, f: Filters, benefit_days: int = 21) -> dict:
    """Следы накрутки ПФ и признаки санкций — по последним данным, независимо от выбранного месяца."""
    domain = (f.site.get("site") or "").removeprefix("www.")
    host_id = q.host_for_site(conn, f.counter_id)
    window = q.pf_window(conn, host_id, domain) if host_id else None
    brand = _brand_words(settings, domain)
    domain_rows = [r for r in (q.pf_domain_queries(conn, host_id, domain, domain) if host_id and domain else [])
                   if ins.brand_free(r["query"], domain, brand)]
    weeks = q.engine_weekly(conn, f.counter_id, f.audience)

    daily = q.rabbit_daily(conn, domain)
    now = daily[-1] if daily else None
    week_ago = now and next((d for d in reversed(daily) if d["date"] <= now["date"] - timedelta(days=7)), daily[0])
    start = now and next((d for d in reversed(daily) if d["date"] <= now["date"] - timedelta(days=benefit_days)),
                         daily[0])
    week_rows = q.rabbit_pairs(conn, domain, now["date"], week_ago["date"]) if now and week_ago is not now else []
    pairs = q.rabbit_pairs(conn, domain, now["date"], start["date"]) if now else []

    signals = [
        ins.pf_far_ctr(window), ins.pf_full_ctr(window), ins.pf_domain(domain_rows, domain),
        ins.pf_engine_share(weeks),
        ins.pf_rabbit_drop(week_rows, now, week_ago),
        ins.pf_benefit(pairs, start if start is not now else None),
    ]
    status, verdict = ins.pf_verdict(signals)

    charts = {}
    if weeks:
        labels = [w["week"].strftime("%d.%m") for w in weeks[-26:]]
        charts["pf_engines"] = chart("line", labels, [("Яндекс", [w["yandex"] for w in weeks[-26:]]),
                                                      ("Google", [w["google"] for w in weeks[-26:]])])
        charts["pf_share"] = chart("line", labels, [("Доля Яндекса, %", [
            round(ins.ratio_pct(w["yandex"], w["yandex"] + w["google"]), 1) for w in weeks[-26:]])], fmt=fmt_pct)
    clicks = q.pf_daily(conn, host_id, domain, domain) if host_id and domain else []
    if clicks:
        charts["pf_clicks"] = chart("bar", [d["date"].strftime("%d.%m") for d in clicks], [
            ("Запросы проекта RabbitSeo", [d["boosted"] for d in clicks]),
            (f"«запрос + {domain}»", [d["domain"] for d in clicks]),
        ])
    return {
        "status": status, "verdict": verdict, "signals": signals, "signal_names": ins.SIGNAL_NAMES,
        "window": window, "domain": domain, "domain_rows": domain_rows[:30], "domain_total": len(domain_rows),
        # частотные запросы, которые хоть раз были в выдаче; не найденные — только числом
        "frequent": [r for r in pairs if r["frequency"] >= ins.FREQUENT
                     and (r["position"] is not None or r["position_prev"] is not None)],
        "frequent_absent": sum(1 for r in pairs if r["frequency"] >= ins.FREQUENT
                               and r["position"] is None and r["position_prev"] is None),
        "benefit_from": start and start["date"],
        "weeks": weeks, "charts": charts, "findings": ins.pf_findings(signals),
    }


# ----- Отчёт за месяц -----

def report_page(conn: psycopg.Connection, settings: Settings, f: Filters) -> dict:
    """Отчёт за месяц: показатели, главное, что сделать, выводы каждого раздела и оговорки о данных.

    Собирается из тех же построителей, что разделы дашборда, — цифры и выводы совпадают с ними.
    """
    m, yoy_partial = _site_basics(conn, f)
    main = overview(conn, settings, f)
    search = search_page(conn, settings, f)
    demand = demand_page(conn, settings, f)
    maps = maps_page(conn, settings, f)
    pf = pf_page(conn, settings, f)

    kpi = [("Сайт", _traffic_tiles(f, m, yoy_partial) + _lead_tiles(f, m, yoy_partial))]
    if search.get("tiles"):
        kpi.append(("Поиск Яндекса", search["tiles"]))
    if demand:
        kpi.append((f"Спрос, Вордстат ({demand['region']}, {month_name(demand['month']).lower()})", demand["tiles"]))
    for card in maps["cards"]:
        kpi.append((f"Карты: {card['name']}", card["tiles"]))

    sections = [("Трафик", traffic(conn, settings, f, "summary")["summary"])]
    for tab, (title, _) in TRAFFIC_TABS.items():
        if tab != "summary":
            sections.append((f"Трафик: {title.lower()}", traffic(conn, settings, f, tab)["summary"]))
    sections += [("Заявки и цели", leads(conn, settings, f)["summary"]), ("Поиск Яндекса", search.get("summary")),
                 ("Спрос", demand and demand["summary"]), ("Карты", maps["summary"])]
    sections = [(title, s) for title, s in sections if s]

    def collect(kind: str) -> list[str]:
        out: list[str] = []
        for _, summary in sections:
            for k, _, texts in summary.groups:
                out += [t for t in texts if k == kind and t not in out]
        return out

    todo = collect("check")
    recs = recommendations(conn, settings, f, pf)
    caveats = list(search.get("alerts", []))
    if f.month >= f.today.replace(day=1):
        caveats.insert(0, f"{month_name(f.month)} ещё не закончился: цифры за неполный месяц, сравнение с прошлым "
                          "месяцем занижено.")
    if main["skipped"]:
        caveats.append("На графиках по месяцам нет неполных месяцев: " + ", ".join(main["skipped"]) + ".")
    if demand and demand["lagging"]:
        caveats.append(f"Вордстат публикует месяц с задержкой: спрос — за {month_name(demand['month']).lower()}.")
    caveats.append(f"Метрика — в варианте «{AUDIENCES[f.audience].lower()}».")
    return {
        # в «Главном» без «что проверить»: эти пункты собраны в «Что сделать»
        "kpi": kpi, "main": ins.Summary([g for g in main["summary"].groups if g[0] != "check"]),
        "pf": pf, "sections": sections, "recs": recs, "priorities": ins.PRIORITIES,
        "todo": todo[:12], "questions": collect("question")[:10], "caveats": caveats,
        "generated": datetime.now(ZoneInfo("Europe/Moscow")),
    }


# ----- Рекомендации -----

def recommendations(conn: psycopg.Connection, settings: Settings, f: Filters, pf: dict | None = None) -> list:
    """Что сделать, чтобы росли трафик и заявки, — по данным месяца (правила — insights.recommend)."""
    m, _ = _site_basics(conn, f)
    total, conv = m.get("visits") or 0, float(m.get("conversion_pct") or 0)

    def rows(slice_: str) -> list[dict]:
        return q.slice_rows(conn, f.counter_id, f.audience, slice_, f.month)

    goals = q.goals(conn, f.counter_id, f.audience, f.month)
    content = content_pages(conn, f, total)
    data = {
        "total": total, "conv": conv, "leads": m.get("leads") or 0, "pf": pf or pf_page(conn, settings, f),
        "domain": (f.site.get("site") or "").removeprefix("www."),
        "nf": content["nf"], "not_found": content["not_found"], "not_found_from": content["not_found_from"],
        "site_search": content["site_search"], "shop": shop(conn, f, goals),
        **{s: [r for r in rows(s) if not ins.is_undefined(r)] if s in SUBSOURCE else rows(s)
           for s in ("source", "direct_campaign", "direct_phrase", "landing_section", "device", "visit_number",
                     "country")},
    }
    return ins.recommend(data)


def recommendations_page(conn: psycopg.Connection, settings: Settings, f: Filters) -> dict:
    return {"items": recommendations(conn, settings, f), "priorities": ins.PRIORITIES}
