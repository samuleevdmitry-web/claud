"""Выборки для дашборда. Показатели считают представления в базе (docs/metrics.md), здесь — только чтение."""

from __future__ import annotations

from datetime import date

import psycopg

MONTH = "%s::date"
PREV = "(%s::date - interval '1 month')::date"


def _all(conn: psycopg.Connection, query: str, params: tuple = ()) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        names = [c.name for c in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def _one(conn: psycopg.Connection, query: str, params: tuple = ()) -> dict | None:
    rows = _all(conn, query, params)
    return rows[0] if rows else None


def counters(conn: psycopg.Connection, kind: str, order: tuple[int, ...]) -> list[dict]:
    """Счётчики из базы в порядке настроек; не указанные в настройках — в конце."""
    rows = _all(conn, "SELECT counter_id, name, site FROM metrika_counters WHERE kind = %s", (kind,))
    position = {cid: i for i, cid in enumerate(order)}
    return sorted(rows, key=lambda r: (position.get(r["counter_id"], len(order)), r["name"]))


def months(conn: psycopg.Connection, counter_id: int) -> list[date]:
    rows = _all(conn, """
        SELECT period_start FROM metrika_traffic
        WHERE counter_id = %s AND grain = 'month' AND slice = 'total'
        ORDER BY period_start DESC
    """, (counter_id,))
    return [r["period_start"] for r in rows]


def site_month(conn: psycopg.Connection, counter_id: int, month: date) -> dict | None:
    return _one(conn, f"""
        SELECT m.*, c.contacts, cp.contacts AS contacts_prev,
               cov.days_with_data, covy.days_with_data AS yoy_days_with_data
        FROM metrika_monthly m
        LEFT JOIN metrika_contacts c  ON c.counter_id = m.counter_id AND c.grain = 'month'
                                     AND c.slice = 'total' AND c.period_start = m.month
        LEFT JOIN metrika_contacts cp ON cp.counter_id = m.counter_id AND cp.grain = 'month'
                                     AND cp.slice = 'total' AND cp.period_start = (m.month - interval '1 month')::date
        LEFT JOIN metrika_month_coverage cov  ON cov.counter_id = m.counter_id AND cov.month = m.month
        LEFT JOIN metrika_month_coverage covy ON covy.counter_id = m.counter_id
                                             AND covy.month = (m.month - interval '1 year')::date
        WHERE m.counter_id = %s AND m.month = {MONTH}
    """, (counter_id, month))


def site_trend(conn: psycopg.Connection, counter_id: int) -> list[dict]:
    return _all(conn, """
        SELECT k.period_start AS month, k.visits, k.users, k.leads, coalesce(c.contacts, 0) AS contacts,
               coalesce(cov.days_with_data, 0) AS days_with_data,
               extract(day from k.period_start + interval '1 month' - interval '1 day')::integer AS days_in_month
        FROM metrika_kpi k
        LEFT JOIN metrika_contacts c USING (counter_id, grain, period_start, slice, dim_key)
        LEFT JOIN metrika_month_coverage cov ON cov.counter_id = k.counter_id AND cov.month = k.period_start
        WHERE k.counter_id = %s AND k.grain = 'month' AND k.slice = 'total'
        ORDER BY k.period_start
    """, (counter_id,))


def site_daily(conn: psycopg.Connection, counter_id: int, month: date) -> list[dict]:
    return _all(conn, f"""
        SELECT period_start AS day, visits, leads
        FROM metrika_kpi
        WHERE counter_id = %s AND grain = 'day' AND slice = 'total'
          AND period_start >= {MONTH} AND period_start < ({MONTH} + interval '1 month')
        ORDER BY period_start
    """, (counter_id, month, month))


def channels(conn: psycopg.Connection, counter_id: int, month: date) -> list[dict]:
    return _all(conn, f"""
        SELECT k.dim_label, k.visits, k.users, k.bounce_rate, k.page_depth, k.avg_duration_sec,
               k.leads, k.conversion_pct, p.visits AS visits_prev
        FROM metrika_kpi k
        LEFT JOIN metrika_traffic p ON p.counter_id = k.counter_id AND p.grain = 'month' AND p.slice = 'source'
                                   AND p.dim_key = k.dim_key AND p.period_start = {PREV}
        WHERE k.counter_id = %s AND k.grain = 'month' AND k.slice = 'source' AND k.period_start = {MONTH}
        ORDER BY k.visits DESC
    """, (month, counter_id, month))


def host_for_site(conn: psycopg.Connection, counter_id: int) -> str | None:
    row = _one(conn, "SELECT host_id FROM site_hosts WHERE counter_id = %s ORDER BY host_id LIMIT 1", (counter_id,))
    return row["host_id"] if row else None


def search_month(conn: psycopg.Connection, host_id: str, month: date) -> dict | None:
    return _one(conn, f"SELECT * FROM webmaster_search_monthly WHERE host_id = %s AND month = {MONTH}",
                (host_id, month))


def search_trend(conn: psycopg.Connection, host_id: str) -> list[dict]:
    return _all(conn, """
        SELECT month, shows, clicks, ctr_pct, days_with_data, days_in_month
        FROM webmaster_search_monthly WHERE host_id = %s ORDER BY month
    """, (host_id,))


def top_queries(conn: psycopg.Connection, host_id: str, month: date, limit: int = 15) -> list[dict]:
    return _all(conn, f"""
        SELECT q.query_text, q.shows, q.clicks,
               round(100.0 * q.clicks / nullif(q.shows, 0), 2) AS ctr_pct,
               q.avg_show_position, p.avg_show_position AS position_prev
        FROM webmaster_queries_monthly q
        LEFT JOIN webmaster_queries_monthly p ON p.host_id = q.host_id AND p.query_id = q.query_id
                                             AND p.month = {PREV}
        WHERE q.host_id = %s AND q.month = {MONTH}
        ORDER BY q.shows DESC
        LIMIT %s
    """, (month, host_id, month, limit))


def position_buckets(conn: psycopg.Connection, host_id: str, month: date) -> dict | None:
    """Показы по диапазонам позиций за дни месяца, по которым есть аналитика запросов."""
    rows = _all(conn, f"""
        SELECT position_range, sum(impressions)::integer AS impressions, min(date) AS first_day, max(date) AS last_day
        FROM webmaster_position_buckets
        WHERE host_id = %s AND date >= {MONTH} AND date < ({MONTH} + interval '1 month')
        GROUP BY position_range
    """, (host_id, month, month))
    if not rows:
        return None
    order = ["1", "2-3", "4-10", "11-50", "51+"]
    by_range = {r["position_range"]: r["impressions"] for r in rows}
    return {
        "ranges": [{"range": r, "impressions": by_range.get(r, 0)} for r in order],
        "first_day": min(r["first_day"] for r in rows),
        "last_day": max(r["last_day"] for r in rows),
    }


def search_freshness(conn: psycopg.Connection, host_id: str) -> list[dict]:
    return _all(conn, "SELECT stream, last_date, days_stale FROM webmaster_freshness WHERE host_id = %s",
                (host_id,))


def maps_month(conn: psycopg.Connection, counter_id: int, month: date) -> dict | None:
    return _one(conn, f"SELECT * FROM maps_monthly WHERE counter_id = %s AND month = {MONTH}", (counter_id, month))


def maps_trend(conn: psycopg.Connection, counter_id: int) -> list[dict]:
    return _all(conn, """
        SELECT month, card_visits, calls, routes, site FROM maps_monthly WHERE counter_id = %s ORDER BY month
    """, (counter_id,))


def sync_status(conn: psycopg.Connection) -> list[dict]:
    """Последний запуск каждого источника и последний успешный."""
    return _all(conn, """
        SELECT DISTINCT ON (r.source) r.source, r.status, r.started_at, r.finished_at, r.error,
               ok.finished_at AS last_ok
        FROM sync_runs r
        LEFT JOIN LATERAL (
            SELECT max(finished_at) AS finished_at FROM sync_runs WHERE source = r.source AND status = 'ok'
        ) ok ON true
        ORDER BY r.source, r.started_at DESC
    """)


def wordstat_latest_month(conn: psycopg.Connection, region_id: int) -> date | None:
    row = _one(conn, "SELECT max(month) AS month FROM wordstat_monthly WHERE region_id = %s", (region_id,))
    return row["month"] if row else None


def wordstat_group_series(conn: psycopg.Connection, region_id: int) -> list[dict]:
    """Спрос бренда и категорий по месяцам: kind, direction, category, month, demand."""
    return _all(conn, """
        SELECT kind, direction, category, month, demand
        FROM wordstat_groups_monthly WHERE region_id = %s AND kind IN ('brand', 'category')
        ORDER BY month
    """, (region_id,))


def wordstat_direction_series(conn: psycopg.Connection, region_id: int) -> list[dict]:
    return _all(conn, "SELECT direction, month, demand FROM wordstat_directions_monthly WHERE region_id = %s"
                      " ORDER BY month", (region_id,))


def wordstat_market(conn: psycopg.Connection, region_id: int) -> list[dict]:
    """Рыночные фразы по месяцам, в порядке файла."""
    return _all(conn, """
        SELECT k.phrase, m.month, m.count
        FROM wordstat_keywords k JOIN wordstat_monthly m USING (phrase)
        WHERE k.kind = 'market' AND m.region_id = %s
        ORDER BY k.position, m.month
    """, (region_id,))


def wordstat_categories(conn: psycopg.Connection) -> list[dict]:
    """Категории в порядке файла: направление, категория, фразы через «; »."""
    return _all(conn, """
        SELECT direction, category, string_agg(phrase, '; ' ORDER BY position) AS phrases, min(position) AS position
        FROM wordstat_keywords WHERE kind = 'category'
        GROUP BY direction, category ORDER BY min(position)
    """)


def wordstat_brand_queries(conn: psycopg.Connection, region_id: int, limit: int = 15) -> list[dict]:
    """Брендовые формулировки за 30 дней по последней загрузке."""
    return _all(conn, """
        SELECT t.query, max(t.count) AS count
        FROM wordstat_top t
        JOIN wordstat_keywords k ON k.phrase = t.phrase AND k.kind = 'brand'
        WHERE t.region_id = %s AND t.month = (SELECT max(month) FROM wordstat_top WHERE region_id = %s)
        GROUP BY t.query ORDER BY max(t.count) DESC LIMIT %s
    """, (region_id, region_id, limit))
