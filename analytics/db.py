"""PostgreSQL: схема и загрузка данных."""

from __future__ import annotations

from datetime import date
from importlib import resources

import psycopg

from analytics.sources.metrika import GoalRow, TrafficRow


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
