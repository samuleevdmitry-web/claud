"""Загрузка данных источников в базу."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import psycopg

from analytics import db
from analytics.sources.metrika import GRAINS, SLICES, MetrikaClient, fetch_goals, fetch_traffic, grain_range


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
) -> int:
    """Перегружает цели и все срезы трафика за период; возвращает число загруженных строк."""
    today = today or date.today()
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
