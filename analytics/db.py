"""PostgreSQL: схема и загрузка данных."""

from __future__ import annotations

from dataclasses import astuple
from datetime import date
from importlib import resources

import psycopg
from psycopg import sql

from analytics.keywords import Keyword
from analytics.sources.metrika import GoalRow, TrafficRow
from analytics.sources.webmaster import Problem
from analytics.sources.wordstat import MonthCount


def connect(url: str) -> psycopg.Connection:
    # autocommit: каждая запись идёт в явном блоке conn.transaction() и фиксируется по его завершении.
    # prepare_threshold=None: без серверных подготовленных запросов. Иначе после 5 одинаковых запросов
    # в соединении PostgreSQL берёт общий план, и выборки из представлений Метрики идут минуты вместо долей секунды.
    return psycopg.connect(url, autocommit=True, prepare_threshold=None)


def apply_schema(conn: psycopg.Connection) -> list[str]:
    """Применяет analytics/sql/*.sql по порядку; скрипты идемпотентны."""
    files = sorted(
        (f for f in resources.files("analytics").joinpath("sql").iterdir() if f.name.endswith(".sql")),
        key=lambda f: f.name,
    )
    with conn.transaction():
        for f in files:
            conn.execute(f.read_text(encoding="utf-8"))
    return [f.name for f in files]


def start_run(conn: psycopg.Connection, source: str, date_from: date, date_to: date) -> int:
    row = conn.execute(
        "INSERT INTO sync_runs (source, date_from, date_to) VALUES (%s, %s, %s) RETURNING id",
        (source, date_from, date_to),
    ).fetchone()
    return row[0]


def finish_run(
    conn: psycopg.Connection, run_id: int, rows_loaded: int | None = None, error: str | None = None
) -> None:
    conn.execute(
        "UPDATE sync_runs SET finished_at = now(), status = %s, rows_loaded = %s, error = %s WHERE id = %s",
        ("error" if error else "ok", rows_loaded, error, run_id),
    )


def upsert_counter(conn: psycopg.Connection, counter: dict, kind: str) -> None:
    site = (counter.get("site2") or {}).get("site") or counter.get("site")
    conn.execute(
        """
        INSERT INTO metrika_counters (counter_id, name, site, kind, updated_at) VALUES (%s, %s, %s, %s, now())
        ON CONFLICT (counter_id) DO UPDATE
        SET name = EXCLUDED.name, site = EXCLUDED.site, kind = EXCLUDED.kind, updated_at = now()
        """,
        (counter["id"], counter.get("name") or "", site, kind),
    )


def upsert_goals(conn: psycopg.Connection, counter_id: int, goals: list[dict], lead_ids: frozenset[int]) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO metrika_goals (counter_id, goal_id, name, type, is_lead, updated_at)
            VALUES (%s, %s, %s, %s, %s, now())
            ON CONFLICT (counter_id, goal_id) DO UPDATE
            SET name = EXCLUDED.name, type = EXCLUDED.type, is_lead = EXCLUDED.is_lead, updated_at = now()
            """,
            [(counter_id, g["id"], g["name"], g.get("type"), g["id"] in lead_ids) for g in goals],
        )


def replace_metrika_slice(
    conn: psycopg.Connection,
    counter_id: int,
    audience: str,
    grain: str,
    slice_: str,
    date_from: date,
    date_to: date,
    traffic: list[TrafficRow],
    sampled: bool,
    goal_rows: list[GoalRow],
) -> int:
    """Заменяет данные среза за период целиком, чтобы в базе не оставались строки, пропавшие из Метрики."""
    where = "counter_id = %s AND audience = %s AND grain = %s AND slice = %s AND period_start BETWEEN %s AND %s"
    scope = (counter_id, audience, grain, slice_, date_from, date_to)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(f"DELETE FROM metrika_traffic WHERE {where}", scope)
        cur.execute(f"DELETE FROM metrika_goal_stats WHERE {where}", scope)
        cur.executemany(
            """
            INSERT INTO metrika_traffic (counter_id, audience, grain, period_start, slice, dim_key, dim_label, visits,
                                         users, pageviews, bounce_rate, page_depth, avg_duration_sec, sampled,
                                         lead_visits)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (counter_id, audience, r.grain, r.period_start, r.slice, r.dim_key, r.dim_label, r.visits, r.users,
                 r.pageviews, r.bounce_rate, r.page_depth, r.avg_duration_sec, sampled, r.lead_visits)
                for r in traffic
            ],
        )
        cur.executemany(
            """
            INSERT INTO metrika_goal_stats (counter_id, audience, grain, period_start, slice, dim_key, goal_id,
                                            reaches, visits)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (counter_id, audience, r.grain, r.period_start, r.slice, r.dim_key, r.goal_id, r.reaches, r.visits)
                for r in goal_rows
            ],
        )
    return len(traffic) + len(goal_rows)


def replace_host_rows(
    conn: psycopg.Connection,
    table: str,
    host_id: str,
    date_column: str,
    date_from: date,
    date_to: date,
    fields: list[str],
    rows: list,
) -> int:
    """Заменяет строки сайта за период; rows — датаклассы или кортежи с полями в порядке fields."""
    columns = ["host_id", *fields]
    insert = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
        sql.SQL(", ").join([sql.Placeholder()] * len(columns)),
    )
    delete = sql.SQL("DELETE FROM {} WHERE host_id = %s AND {} BETWEEN %s AND %s").format(
        sql.Identifier(table), sql.Identifier(date_column)
    )
    values = [(host_id, *(row if isinstance(row, tuple) else astuple(row))) for row in rows]
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(delete, (host_id, date_from, date_to))
        cur.executemany(insert, values)
    return len(values)


def save_webmaster_state(
    conn: psycopg.Connection, host_id: str, snapshot_date: date, summary: dict, problems: list[Problem]
) -> None:
    """Сводка сайта на дату и текущее состояние проверок диагностики."""
    counts = summary.get("site_problems") or {}
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO webmaster_summary (host_id, snapshot_date, sqi, searchable_pages, excluded_pages,
                problems_fatal, problems_critical, problems_possible, problems_recommendation)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (host_id, snapshot_date) DO UPDATE SET
                sqi = EXCLUDED.sqi, searchable_pages = EXCLUDED.searchable_pages,
                excluded_pages = EXCLUDED.excluded_pages, problems_fatal = EXCLUDED.problems_fatal,
                problems_critical = EXCLUDED.problems_critical, problems_possible = EXCLUDED.problems_possible,
                problems_recommendation = EXCLUDED.problems_recommendation
            """,
            (host_id, snapshot_date, summary.get("sqi"), summary.get("searchable_pages_count"),
             summary.get("excluded_pages_count"), counts.get("FATAL", 0), counts.get("CRITICAL", 0),
             counts.get("POSSIBLE_PROBLEM", 0), counts.get("RECOMMENDATION", 0)),
        )
        cur.executemany(
            """
            INSERT INTO webmaster_diagnostics (host_id, problem, severity, state, last_state_update, checked_at)
            VALUES (%s, %s, %s, %s, %s, now())
            ON CONFLICT (host_id, problem) DO UPDATE SET
                severity = EXCLUDED.severity, state = EXCLUDED.state,
                last_state_update = EXCLUDED.last_state_update, checked_at = now()
            """,
            [(host_id, p.code, p.severity, p.state, p.last_state_update) for p in problems],
        )


def replace_wordstat_keywords(conn: psycopg.Connection, keywords: list[Keyword]) -> None:
    """Ключи — копия wordstat.toml: удалённые из файла фразы пропадают и из групп дашборда."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM wordstat_keywords")
        cur.executemany(
            "INSERT INTO wordstat_keywords (phrase, kind, direction, category, position) VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT DO NOTHING",
            [(k.phrase, k.kind, k.direction, k.category, i) for i, k in enumerate(keywords)],
        )


def replace_wordstat_phrase(
    conn: psycopg.Connection,
    phrase: str,
    region_id: int,
    since: date,
    months: list[MonthCount],
    top_month: date,
    top: list[tuple[str, int]],
) -> int:
    """Перезаписывает помесячную частотность фразы с since и её популярные формулировки за top_month."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM wordstat_monthly WHERE phrase = %s AND region_id = %s AND month >= %s",
                    (phrase, region_id, since))
        cur.executemany(
            "INSERT INTO wordstat_monthly (phrase, region_id, month, count, share) VALUES (%s, %s, %s, %s, %s)",
            [(phrase, region_id, m.month, m.count, m.share) for m in months],
        )
        cur.execute("DELETE FROM wordstat_top WHERE phrase = %s AND region_id = %s AND month = %s",
                    (phrase, region_id, top_month))
        cur.executemany(
            "INSERT INTO wordstat_top (phrase, region_id, month, query, count) VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT DO NOTHING",
            [(phrase, region_id, top_month, query, count) for query, count in top],
        )
    return len(months) + len(top)


def replace_rabbit_positions(conn: psycopg.Connection, project: str, date_from: date, date_to: date,
                             rows: list) -> int:
    """Позиции проекта за период целиком: RabbitSeo может уточнять прошлые съёмы."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM rabbit_positions WHERE project = %s AND date BETWEEN %s AND %s",
                    (project, date_from, date_to))
        cur.executemany(
            "INSERT INTO rabbit_positions (project, date, query, frequency, position) VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT DO NOTHING",
            [(project, r.date, r.query, r.frequency, r.position) for r in rows],
        )
    return len(rows)


def replace_month_pages(conn: psycopg.Connection, counter_id: int, month: date, pages: list, products: list) -> int:
    """Страницы и товары счётчика за месяц целиком."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM metrika_pages WHERE counter_id = %s AND month = %s", (counter_id, month))
        cur.execute("DELETE FROM metrika_products WHERE counter_id = %s AND month = %s", (counter_id, month))
        cur.executemany(
            "INSERT INTO metrika_pages (counter_id, month, kind, key, pageviews, users) VALUES (%s, %s, %s, %s, %s, %s)",
            [(counter_id, p.month, p.kind, p.key, p.pageviews, p.users) for p in pages],
        )
        cur.executemany(
            "INSERT INTO metrika_products (counter_id, month, product, basket_sum, basket_qty, purchased_sum,"
            " purchased_qty) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            [(counter_id, p.month, p.product, p.basket_sum, p.basket_qty, p.purchased_sum, p.purchased_qty)
             for p in products],
        )
    return len(pages) + len(products)


def mark_backfilled(conn: psycopg.Connection, counter_id: int, reports: set[str]) -> None:
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany("INSERT INTO report_backfills (counter_id, report) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                        [(counter_id, r) for r in sorted(reports)])


def imported_files(conn: psycopg.Connection) -> set[str]:
    return {sha for (sha,) in conn.execute("SELECT sha256 FROM crm_imports")}


def save_crm_file(conn: psycopg.Connection, sha: str, file_name: str, parsed) -> int:
    """Заменяет данные месяца этого вида выгрузки содержимым файла; повторная выгрузка за месяц — новая версия."""
    month, kind = parsed.month, parsed.kind
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM crm_summary WHERE month = %s AND kind = %s", (month, kind))
        cur.executemany("INSERT INTO crm_summary (month, kind, metric, value, note) VALUES (%s, %s, %s, %s, %s)"
                        " ON CONFLICT DO NOTHING", [(month, kind, m, v, n) for m, v, n in parsed.summary])
        if kind == "forms":
            cur.execute("DELETE FROM crm_form_leads WHERE month = %s", (month,))
            cur.executemany(
                "INSERT INTO crm_form_leads (lead_id, month, created_at, stage, in_1c, orders, amount, closed, first_order,"
                " match, manager_1c) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (lead_id) DO UPDATE SET month = EXCLUDED.month, created_at = EXCLUDED.created_at,"
                " stage = EXCLUDED.stage, in_1c = EXCLUDED.in_1c, orders = EXCLUDED.orders, amount = EXCLUDED.amount,"
                " closed = EXCLUDED.closed, first_order = EXCLUDED.first_order, match = EXCLUDED.match,"
                " manager_1c = EXCLUDED.manager_1c",
                [(x.lead_id, month, x.created_at, x.stage, x.in_1c, x.orders, x.amount, x.closed, x.first_order, x.match,
                  x.manager_1c) for x in parsed.form_leads],
            )
        else:
            cur.execute("DELETE FROM crm_call_leads WHERE month = %s", (month,))
            cur.execute("DELETE FROM crm_call_orders WHERE month = %s", (month,))
            cur.executemany(
                "INSERT INTO crm_call_leads (lead_id, month, called_at, call_type, stage, responsible, line, in_1c)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (lead_id) DO UPDATE SET month = EXCLUDED.month, called_at = EXCLUDED.called_at,"
                " call_type = EXCLUDED.call_type, stage = EXCLUDED.stage, responsible = EXCLUDED.responsible,"
                " line = EXCLUDED.line, in_1c = EXCLUDED.in_1c",
                [(x.lead_id, month, x.called_at, x.call_type, x.stage, x.responsible, x.line, x.in_1c)
                 for x in parsed.call_leads],
            )
            cur.executemany(
                "INSERT INTO crm_call_orders (month, order_no, category, order_date, amount, status_1c, lead_id, manager_1c)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                [(month, x.order_no, x.category, x.order_date, x.amount, x.status_1c, x.lead_id, x.manager_1c)
                 for x in parsed.call_orders],
            )
        cur.execute(
            "INSERT INTO crm_imports (sha256, file_name, kind, month, rows_loaded) VALUES (%s, %s, %s, %s, %s)"
            " ON CONFLICT (sha256) DO UPDATE SET imported_at = now()",
            (sha, file_name, kind, month, parsed.rows),
        )
    return parsed.rows


def skip_crm_file(conn: psycopg.Connection, sha: str, file_name: str) -> None:
    """Файл не распознан: запоминаем, чтобы не разбирать его снова при каждой проверке."""
    conn.execute("INSERT INTO crm_imports (sha256, file_name, kind, month, rows_loaded) VALUES (%s, %s, 'unknown',"
                 " '1970-01-01', 0) ON CONFLICT DO NOTHING", (sha, file_name))
