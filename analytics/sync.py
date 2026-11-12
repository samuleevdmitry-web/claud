"""Загрузка данных источников в базу."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import astuple
from datetime import date, timedelta

import psycopg

from analytics import db
from analytics.keywords import KeywordConfig
from analytics.sources.metrika import GRAINS, SLICES, MetrikaClient, fetch_goals, fetch_traffic, grain_range
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
) -> int:
    """Перегружает цели и все срезы трафика за период; возвращает число загруженных строк.

    kind: 'site' — счётчик сайта, 'maps' — счётчик карточки на Картах, который создаёт Яндекс Бизнес.
    """
    today = today or date.today()
    db.upsert_counter(conn, client.counter(counter_id), kind)
    goals = client.goals(counter_id)
    db.upsert_goals(conn, counter_id, goals, lead_goals)
    goal_ids = [g["id"] for g in goals]
    log(f"Счётчик {counter_id}: {len(goals)} целей, из них заявки: {sorted(lead_goals & set(goal_ids))}")

    loaded = 0
    for grain in GRAINS:
        date_from, date_to = grain_range(grain, since, until, today)
        for slice_ in SLICES:
            traffic, sampled = fetch_traffic(client, counter_id, grain, slice_, date_from, date_to, attribution)
            goal_rows = fetch_goals(client, counter_id, grain, slice_, goal_ids, date_from, date_to, attribution)
            loaded += db.replace_metrika_slice(
                conn, counter_id, grain, slice_, date_from, date_to, traffic, sampled, goal_rows
            )
            note = " (данные семплированы)" if sampled else ""
            log(f"  {grain:<5} {slice_:<6} {date_from}…{date_to}: трафик {len(traffic)}, цели {len(goal_rows)}{note}")
    return loaded


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
