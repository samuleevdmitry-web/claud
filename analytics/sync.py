"""Загрузка данных источников в базу."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import astuple
from datetime import date, timedelta
from pathlib import Path

import psycopg

from analytics import db
from analytics.keywords import KeywordConfig
from analytics.sources.metrika import (
    AUDIENCES,
    GRAINS,
    NOT_FOUND_TITLE,
    SLICES,
    GoalRow,
    MetrikaClient,
    TrafficRow,
    fetch_goals,
    fetch_pages,
    fetch_products,
    fetch_traffic,
    grain_range,
)
from analytics.sources import crm_files
from analytics.sources.rabbit import MAX_DAYS, RabbitClient, parse_positions
from analytics.sources.wordstat import WordstatClient, WordstatError
from analytics.sources.webmaster import (
    WebmasterClient,
    parse_crawl,
    parse_diagnostics,
    parse_pages,
    parse_popular,
    parse_query_analytics,
    parse_search_history,
    parse_sqi,
)


def sync_metrika(
    conn: psycopg.Connection,
    client: MetrikaClient,
    counter_id: int,
    lead_goals: frozenset[int],
    attribution: str,
    since: date,
    until: date,
    today: date | None = None,
    log: Callable[[str], None] = print,
    kind: str = "site",
    only: set[str] | None = None,
    not_found_title: str = NOT_FOUND_TITLE,
    search_param: str = "q",
) -> int:
    """Перегружает цели и отчёты за период в двух вариантах — весь трафик и только люди.

    kind: 'site' — счётчик сайта (все отчёты, страницы и товары), 'maps' — счётчик карточки на Картах,
    который создаёт Яндекс Бизнес (только основные: сводка, источники, UTM, города, устройства).
    only: загрузить только эти отчёты (имена из SLICES и 'pages') — для догрузки истории новых отчётов;
    сводка грузится всегда: по ней видно, где были роботы.
    Возвращает число загруженных строк.
    """
    today = today or date.today()
    db.upsert_counter(conn, client.counter(counter_id), kind)
    goals = client.goals(counter_id)
    db.upsert_goals(conn, counter_id, goals, lead_goals)
    goal_ids = [g["id"] for g in goals]
    lead_ids = sorted(lead_goals & set(goal_ids))
    log(f"Счётчик {counter_id}: {len(goals)} целей, из них заявки: {lead_ids}")

    assert next(iter(SLICES)) == "total", "сводка нужна первой: по ней видно, в какие периоды были роботы"
    loaded = 0
    for grain in GRAINS:
        date_from, date_to = grain_range(grain, since, until, today)
        robot_free: set[date] = set()
        for slice_, spec in SLICES.items():
            if grain not in spec.grains or not (spec.core or kind == "site"):
                continue
            if only is not None and slice_ != "total" and slice_ not in only:
                continue
            fetched: dict[str, tuple[list[TrafficRow], bool, list[GoalRow]]] = {}
            for audience in AUDIENCES:
                if audience == "all" or slice_ == "total":
                    fetched[audience] = _fetch_slice(
                        client, counter_id, grain, slice_, goal_ids, lead_ids, date_from, date_to, attribution, audience
                    )
                    how = ""
                else:
                    fetched[audience], how = _humans_slice(
                        client, counter_id, grain, slice_, goal_ids, lead_ids, date_from, date_to, attribution,
                        fetched["all"], robot_free,
                    )
                traffic, sampled, goal_rows = fetched[audience]
                loaded += db.replace_metrika_slice(
                    conn, counter_id, audience, grain, slice_, date_from, date_to, traffic, sampled, goal_rows
                )
                note = " (данные семплированы)" if sampled else ""
                log(f"  {audience:<6} {grain:<5} {slice_:<14} {date_from}…{date_to}: "
                    f"трафик {len(traffic)}, цели {len(goal_rows)}{note}{how}")
            if slice_ == "total":
                robot_free = robot_free_periods(fetched["all"][0], fetched["humans"][0])
    if kind == "site" and (only is None or "pages" in only):
        for month, month_end in month_ranges(*grain_range("month", since, until, today)):
            pages = fetch_pages(client, counter_id, month, month_end, not_found_title, search_param)
            products = fetch_products(client, counter_id, month, month_end)
            loaded += db.replace_month_pages(conn, counter_id, month, pages, products)
            log(f"  {'all':<6} month pages          {month:%Y-%m}: страницы {len(pages)}, товары {len(products)}")
    return loaded


def _fetch_slice(client, counter_id, grain, slice_, goal_ids, lead_ids, date_from, date_to, attribution, audience):
    traffic, sampled = fetch_traffic(client, counter_id, grain, slice_, date_from, date_to, attribution, audience,
                                     lead_ids)
    goal_rows = fetch_goals(client, counter_id, grain, slice_, goal_ids, date_from, date_to, attribution, audience)
    return traffic, sampled, goal_rows


def robot_free_periods(all_rows: list[TrafficRow], humans_rows: list[TrafficRow]) -> set[date]:
    """Периоды сводки, где визитов «только людей» столько же, сколько всего: роботов не было."""
    humans = {r.period_start: r.visits for r in humans_rows}
    return {r.period_start for r in all_rows if humans.get(r.period_start, 0) == r.visits}


def _humans_slice(client, counter_id, grain, slice_, goal_ids, lead_ids, date_from, date_to, attribution, all_data,
                  robot_free):
    """Отчёт «только люди»: где роботов не было, это тот же отчёт без фильтра.

    С фильтром роботности Метрика отдаёт отчёты по городам, страницам входа и ОС не целиком — теряет
    мелкие строки и большую часть целей. Поэтому фильтр нужен только там, где роботы правда были:
    за остальные периоды берутся строки всего трафика (точные и без лишних запросов к API).
    """
    all_traffic, all_sampled, all_goals = all_data
    periods = {r.period_start for r in all_traffic}
    with_robots = periods - robot_free
    if not with_robots:
        return all_data, " — роботов не было, как весь трафик"
    # с фильтром запрашивается только отрезок, где роботы были
    first, last = min(with_robots), max(with_robots)
    if grain == "month":
        last = (last + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    traffic, sampled, goal_rows = _fetch_slice(
        client, counter_id, grain, slice_, goal_ids, lead_ids, max(first, date_from), min(last, date_to), attribution,
        "humans",
    )
    traffic = [r for r in traffic if r.period_start not in robot_free]
    traffic += [r for r in all_traffic if r.period_start in robot_free]
    goal_rows = [r for r in goal_rows if r.period_start not in robot_free]
    goal_rows += [r for r in all_goals if r.period_start in robot_free]
    clean = len(periods & robot_free)
    how = f" — {clean} из {len(periods)} периодов без роботов взяты из всего трафика" if clean else ""
    return (traffic, sampled or all_sampled, goal_rows), how


def month_ranges(since: date, until: date) -> Iterator[tuple[date, date]]:
    """Календарные месяцы периода: первый — с начала месяца, последний — по until."""
    start = since.replace(day=1)
    while start <= until:
        next_start = (start + timedelta(days=32)).replace(day=1)
        yield start, min(next_start - timedelta(days=1), until)
        start = next_start


def sync_webmaster(
    conn: psycopg.Connection,
    client: WebmasterClient,
    host_id: str,
    since: date,
    until: date,
    today: date | None = None,
    log: Callable[[str], None] = print,
) -> int:
    """Перегружает данные Вебмастера за период; возвращает число загруженных строк."""
    today = today or date.today()
    db.save_webmaster_state(
        conn, host_id, today, client.summary(host_id), parse_diagnostics(client.diagnostics(host_id))
    )

    def replace(table: str, fields: list[str], rows: list, date_from: date = since, date_to: date = until,
                date_column: str = "date") -> int:
        last = max((row[0] if isinstance(row, tuple) else row.date for row in rows), default=None)
        log(f"  {table:<26} {date_from}…{date_to}: {len(rows)} строк, данные по {last or '—'}")
        return db.replace_host_rows(conn, table, host_id, date_column, date_from, date_to, fields, rows)

    loaded = replace(
        "webmaster_search_daily",
        ["date", "shows", "clicks", "avg_show_position", "avg_click_position"],
        parse_search_history(client.search_history(host_id, since, until)),
    )
    loaded += replace("webmaster_sqi", ["date", "sqi"], parse_sqi(client.sqi_history(host_id, since, until)))
    loaded += replace(
        "webmaster_pages_in_search",
        ["date", "pages", "appeared", "removed"],
        parse_pages(
            client.in_search_history(host_id, since, until), client.search_events_history(host_id, since, until)
        ),
    )
    loaded += replace(
        "webmaster_crawl_daily",
        ["date", "http_2xx", "http_3xx", "http_4xx", "http_5xx", "other"],
        parse_crawl(client.indexing_history(host_id, since, until)),
    )
    for month_start, month_end in month_ranges(since, until):
        queries = parse_popular(client.popular_queries(host_id, month_start, month_end))
        loaded += replace(
            "webmaster_queries_monthly",
            ["month", "query_id", "query_text", "shows", "clicks", "avg_show_position", "avg_click_position"],
            [(month_start, *astuple(q)) for q in queries],
            month_start,
            month_start,
            "month",
        )

    query_days = parse_query_analytics(client.query_analytics(host_id))
    if query_days:
        loaded += replace(
            "webmaster_query_daily",
            ["date", "query", "top_url", "impressions", "clicks", "position", "demand"],
            query_days,
            min(q.date for q in query_days),
            max(q.date for q in query_days),
        )
    return loaded


def sync_wordstat(
    conn: psycopg.Connection,
    client: WordstatClient,
    config: KeywordConfig,
    since: date,
    today: date | None = None,
    log: Callable[[str], None] = print,
) -> int:
    """Частотность и формулировки всех фраз wordstat.toml по всем регионам.

    Один запрос отдаёт всю историю фразы, поэтому история перезагружается целиком с since.
    Ошибка одной фразы (например, опечатка в операторе) не останавливает остальные:
    они загружаются, а в конце поднимается ошибка со списком неудавшихся фраз.
    """
    today = today or date.today()
    db.replace_wordstat_keywords(conn, config.keywords)
    loaded, failed = 0, []
    phrases = config.phrases
    for region_id in config.region_ids:
        for i, phrase in enumerate(phrases, 1):
            try:
                months = client.monthly(phrase, region_id, since)
                top = client.top_requests(phrase, region_id)
            except WordstatError as exc:
                failed.append(str(exc))
                continue
            loaded += db.replace_wordstat_phrase(conn, phrase, region_id, since, months, today.replace(day=1), top)
            if i % 20 == 0 or i == len(phrases):
                log(f"  регион {region_id}: {i} из {len(phrases)} фраз")
    if failed:
        raise WordstatError(f"Не загрузились {len(failed)} фраз: " + "; ".join(failed[:5]))
    return loaded


def sync_rabbit(conn: psycopg.Connection, client: RabbitClient, until: date,
                log: Callable[[str], None] = print) -> int:
    """Позиции RabbitSeo за последние 366 дней — столько отдаёт гостевая страница за один запрос."""
    project = client.project()
    date_from = until - timedelta(days=MAX_DAYS - 1)
    rows = parse_positions(client.positions(date_from, until))
    days = sorted({r.date for r in rows})
    log(f"  {project}: {len({r.query for r in rows})} запросов, съёмов {len(days)}"
        + (f" ({days[0]}…{days[-1]})" if days else ""))
    return db.replace_rabbit_positions(conn, project, date_from, until, rows)


def import_crm_folder(conn: psycopg.Connection, folder: Path, log: Callable[[str], None] = print) -> list:
    """Загружает из папки новые выгрузки Битрикс24 и 1С (*.xlsx); уже загруженные (по содержимому) пропускает.

    Возвращает разобранные файлы — по ним видно, за какие месяцы появились данные.
    """
    if not folder.is_dir():
        return []
    done, out = db.imported_files(conn, crm_files.PARSER_VERSION), []
    for path in sorted(folder.glob("*.xlsx")):
        if path.name.startswith("~$"):
            continue  # временный файл открытого Excel
        sha = crm_files.sha256(path)
        if sha in done:
            continue
        try:
            parsed = crm_files.parse_file(path)
        except (crm_files.CrmFileError, ValueError, KeyError, OSError) as exc:
            log(f"  {path.name}: не загружен — {exc}")
            db.skip_crm_file(conn, sha, path.name)
            continue
        rows = db.save_crm_file(conn, sha, path.name, parsed, crm_files.PARSER_VERSION)
        log(f"  {path.name}: {crm_files.KIND_NAMES[parsed.kind]} за {parsed.month:%m.%Y}, {rows} строк")
        out.append(parsed)
    return out
