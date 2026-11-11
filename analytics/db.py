"""PostgreSQL: схема и загрузка данных."""

from __future__ import annotations

from dataclasses import astuple
from datetime import date
from importlib import resources

import psycopg
from psycopg import sql

from analytics.sources.metrika import GoalRow, TrafficRow
from analytics.sources.webmaster import Problem


def connect(url: str) -> psycopg.Connection:
    # autocommit: каждая запись идёт в явном блоке conn.transaction() и фиксируется по его завершении
    return psycopg.connect(url, autocommit=True)


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
    grain: str,
    slice_: str,
    date_from: date,
    date_to: date,
    traffic: list[TrafficRow],
    sampled: bool,
    goal_rows: list[GoalRow],
) -> int:
    """Заменяет данные среза за период целиком, чтобы в базе не оставались строки, пропавшие из Метрики."""
    where = "counter_id = %s AND grain = %s AND slice = %s AND period_start BETWEEN %s AND %s"
    scope = (counter_id, grain, slice_, date_from, date_to)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(f"DELETE FROM metrika_traffic WHERE {where}", scope)
        cur.execute(f"DELETE FROM metrika_goal_stats WHERE {where}", scope)
        cur.executemany(
            """
            INSERT INTO metrika_traffic (counter_id, grain, period_start, slice, dim_key, dim_label, visits,
                                         users, pageviews, bounce_rate, page_depth, avg_duration_sec, sampled)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (counter_id, r.grain, r.period_start, r.slice, r.dim_key, r.dim_label, r.visits, r.users,
                 r.pageviews, r.bounce_rate, r.page_depth, r.avg_duration_sec, sampled)
                for r in traffic
            ],
        )
        cur.executemany(
            """
            INSERT INTO metrika_goal_stats (counter_id, grain, period_start, slice, dim_key, goal_id, reaches, visits)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (counter_id, r.grain, r.period_start, r.slice, r.dim_key, r.goal_id, r.reaches, r.visits)
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
