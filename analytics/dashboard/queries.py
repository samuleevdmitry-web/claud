"""Выборки для дашборда. Показатели считают представления в базе (docs/metrics.md), здесь — только чтение.

audience — 'all' (весь трафик) или 'humans' (только люди) для всего, что берётся из Метрики.
"""

from __future__ import annotations

from datetime import date, timedelta

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


def months(conn: psycopg.Connection, counter_id: int, audience: str) -> list[date]:
    rows = _all(conn, """
        SELECT period_start FROM metrika_traffic
        WHERE counter_id = %s AND audience = %s AND grain = 'month' AND slice = 'total'
        ORDER BY period_start DESC
    """, (counter_id, audience))
    return [r["period_start"] for r in rows]


def site_month(conn: psycopg.Connection, counter_id: int, audience: str, month: date) -> dict | None:
    return _one(conn, f"""
        SELECT m.*, c.contacts, cp.contacts AS contacts_prev,
               cov.days_with_data, covy.days_with_data AS yoy_days_with_data
        FROM metrika_monthly m
        LEFT JOIN metrika_contacts c  ON c.counter_id = m.counter_id AND c.audience = m.audience
                                     AND c.grain = 'month' AND c.slice = 'total' AND c.period_start = m.month
        LEFT JOIN metrika_contacts cp ON cp.counter_id = m.counter_id AND cp.audience = m.audience
                                     AND cp.grain = 'month' AND cp.slice = 'total'
                                     AND cp.period_start = (m.month - interval '1 month')::date
        LEFT JOIN metrika_month_coverage cov  ON cov.counter_id = m.counter_id AND cov.audience = m.audience
                                             AND cov.month = m.month
        LEFT JOIN metrika_month_coverage covy ON covy.counter_id = m.counter_id AND covy.audience = m.audience
                                             AND covy.month = (m.month - interval '1 year')::date
        WHERE m.counter_id = %s AND m.audience = %s AND m.month = {MONTH}
    """, (counter_id, audience, month))


def robots_by_month(conn: psycopg.Connection, counter_id: int) -> dict[date, dict]:
    """Весь трафик против «только люди» по месяцам: сколько визитов отсеяно как роботы."""
    rows = _all(conn, """
        SELECT a.period_start AS month, a.visits AS all_visits, h.visits AS human_visits,
               round(100.0 * (a.visits - h.visits) / nullif(a.visits, 0), 1) AS robots_pct
        FROM metrika_traffic a
        JOIN metrika_traffic h ON h.counter_id = a.counter_id AND h.audience = 'humans' AND h.grain = 'month'
                              AND h.slice = 'total' AND h.period_start = a.period_start
        WHERE a.counter_id = %s AND a.audience = 'all' AND a.grain = 'month' AND a.slice = 'total'
        ORDER BY a.period_start
    """, (counter_id,))
    return {r["month"]: r for r in rows}


def site_trend(conn: psycopg.Connection, counter_id: int, audience: str) -> list[dict]:
    return _all(conn, """
        SELECT k.period_start AS month, k.visits, k.users, k.leads, coalesce(c.contacts, 0) AS contacts,
               coalesce(cov.days_with_data, 0) AS days_with_data,
               extract(day from k.period_start + interval '1 month' - interval '1 day')::integer AS days_in_month
        FROM metrika_kpi k
        LEFT JOIN metrika_contacts c USING (counter_id, audience, grain, period_start, slice, dim_key)
        LEFT JOIN metrika_month_coverage cov ON cov.counter_id = k.counter_id AND cov.audience = k.audience
                                            AND cov.month = k.period_start
        WHERE k.counter_id = %s AND k.audience = %s AND k.grain = 'month' AND k.slice = 'total'
        ORDER BY k.period_start
    """, (counter_id, audience))


def site_daily(conn: psycopg.Connection, counter_id: int, audience: str, month: date) -> list[dict]:
    return _all(conn, f"""
        SELECT period_start AS day, visits, leads
        FROM metrika_kpi
        WHERE counter_id = %s AND audience = %s AND grain = 'day' AND slice = 'total'
          AND period_start >= {MONTH} AND period_start < ({MONTH} + interval '1 month')
        ORDER BY period_start
    """, (counter_id, audience, month, month))


def slice_rows(conn: psycopg.Connection, counter_id: int, audience: str, slice_: str, month: date) -> list[dict]:
    """Строки любого отчёта Метрики за месяц с прошлым месяцем — для таблиц, сводок и перекрёстных выводов."""
    return _all(conn, f"""
        SELECT k.dim_key, k.dim_label, k.visits, k.users, k.bounce_rate, k.page_depth, k.avg_duration_sec,
               k.leads, k.conversion_pct, coalesce(c.contacts, 0) AS contacts,
               p.visits AS visits_prev, p.leads AS leads_prev
        FROM metrika_kpi k
        LEFT JOIN metrika_contacts c USING (counter_id, audience, grain, period_start, slice, dim_key)
        LEFT JOIN metrika_kpi p ON p.counter_id = k.counter_id AND p.audience = k.audience AND p.grain = 'month'
                               AND p.slice = k.slice AND p.dim_key = k.dim_key AND p.period_start = {PREV}
        WHERE k.counter_id = %s AND k.audience = %s AND k.grain = 'month' AND k.slice = %s
          AND k.period_start = {MONTH}
        ORDER BY k.visits DESC
    """, (month, counter_id, audience, slice_, month))


def goals(conn: psycopg.Connection, counter_id: int, audience: str, month: date) -> list[dict]:
    """Все цели счётчика: достижения за месяц и прошлый месяц."""
    return _all(conn, f"""
        SELECT g.goal_id, g.name, g.type, g.is_lead,
               coalesce(cur.reaches, 0) AS reaches, coalesce(cur.visits, 0) AS visits, prev.reaches AS reaches_prev
        FROM metrika_goals g
        LEFT JOIN metrika_goal_stats cur ON cur.counter_id = g.counter_id AND cur.goal_id = g.goal_id
             AND cur.audience = %s AND cur.grain = 'month' AND cur.slice = 'total' AND cur.period_start = {MONTH}
        LEFT JOIN metrika_goal_stats prev ON prev.counter_id = g.counter_id AND prev.goal_id = g.goal_id
             AND prev.audience = %s AND prev.grain = 'month' AND prev.slice = 'total' AND prev.period_start = {PREV}
        WHERE g.counter_id = %s
        ORDER BY g.is_lead DESC, coalesce(cur.reaches, 0) DESC
    """, (audience, month, audience, month, counter_id))


def contact_kinds(conn: psycopg.Connection, counter_id: int) -> set[str]:
    """Типы целей-контактов (телефон, почта, мессенджер), которые не считаются заявками."""
    rows = _all(conn, "SELECT DISTINCT type FROM metrika_goals WHERE counter_id = %s "
                      "AND type IN ('phone', 'email', 'messenger') AND NOT is_lead", (counter_id,))
    return {r["type"] for r in rows}


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


def top_queries(conn: psycopg.Connection, host_id: str, month: date, limit: int = 20) -> list[dict]:
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


def position_movers(conn: psycopg.Connection, host_id: str, month: date, min_shows: int = 20,
                    min_change: float = 1.0) -> dict[str, list[dict]]:
    """Запросы, у которых позиция выросла или упала, и те, что пропали из выдачи или появились.

    Сравниваются популярные запросы месяца и прошлого месяца по Вебмастеру; учитываются запросы
    хотя бы с min_shows показами, чтобы не шуметь на единичных показах. Позиция: меньше — лучше.
    """
    rows = _all(conn, f"""
        SELECT coalesce(c.query_text, p.query_text) AS query,
               c.shows, p.shows AS shows_prev, c.clicks,
               c.avg_show_position AS position, p.avg_show_position AS position_prev
        FROM (SELECT * FROM webmaster_queries_monthly WHERE host_id = %s AND month = {MONTH}) c
        FULL JOIN (SELECT * FROM webmaster_queries_monthly WHERE host_id = %s AND month = {PREV}) p
          ON p.query_id = c.query_id
    """, (host_id, month, host_id, month))
    up, down, lost, new = [], [], [], []
    for r in rows:
        if r["position"] is not None and r["position_prev"] is not None:
            if max(r["shows"] or 0, r["shows_prev"] or 0) < min_shows:
                continue
            r["change"] = float(r["position_prev"]) - float(r["position"])
            if r["change"] >= min_change:
                up.append(r)
            elif r["change"] <= -min_change:
                down.append(r)
        elif r["position"] is None and (r["shows_prev"] or 0) >= min_shows:
            lost.append(r)
        elif r["position_prev"] is None and (r["shows"] or 0) >= min_shows:
            new.append(r)
    weight = lambda r: abs(r.get("change", 0)) * max(r["shows"] or 0, r["shows_prev"] or 0)  # noqa: E731
    return {
        "up": sorted(up, key=weight, reverse=True),
        "down": sorted(down, key=weight, reverse=True),
        "lost": sorted(lost, key=lambda r: -(r["shows_prev"] or 0)),
        "new": sorted(new, key=lambda r: -(r["shows"] or 0)),
    }


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


def search_problems(conn: psycopg.Connection, host_id: str) -> list[dict]:
    return _all(conn, """
        SELECT problem, severity, last_state_update FROM webmaster_diagnostics
        WHERE host_id = %s AND state = 'PRESENT'
        ORDER BY array_position(ARRAY['FATAL', 'CRITICAL', 'POSSIBLE_PROBLEM', 'RECOMMENDATION'], severity)
    """, (host_id,))


def search_summary(conn: psycopg.Connection, host_id: str) -> dict | None:
    return _one(conn, "SELECT * FROM webmaster_summary WHERE host_id = %s ORDER BY snapshot_date DESC LIMIT 1",
                (host_id,))


def maps_month(conn: psycopg.Connection, counter_id: int, audience: str, month: date) -> dict | None:
    return _one(conn, f"SELECT * FROM maps_monthly WHERE counter_id = %s AND audience = %s AND month = {MONTH}",
                (counter_id, audience, month))


def maps_trend(conn: psycopg.Connection, counter_id: int, audience: str) -> list[dict]:
    return _all(conn, """
        SELECT month, card_visits, calls, routes, site FROM maps_monthly
        WHERE counter_id = %s AND audience = %s ORDER BY month
    """, (counter_id, audience))


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


def rabbit_daily(conn: psycopg.Connection, project: str) -> list[dict]:
    return _all(conn, "SELECT date, top3, top10, found, queries FROM rabbit_daily WHERE project = %s ORDER BY date",
                (project,))


def rabbit_compare(conn: psycopg.Connection, project: str, now: date, then: date) -> dict[str, list[dict]]:
    """Позиции RabbitSeo на два съёма: выросли, просели, выпали, появились и не найдены ни разу за всё время."""
    rows = _all(conn, """
        SELECT q.query, q.frequency, n.position AS position, t.position AS position_prev,
               bool_or(a.position IS NOT NULL) AS ever_found
        FROM (SELECT query, max(frequency) AS frequency FROM rabbit_positions
              WHERE project = %s GROUP BY query) q
        LEFT JOIN rabbit_positions n ON n.project = %s AND n.query = q.query AND n.date = %s
        LEFT JOIN rabbit_positions t ON t.project = %s AND t.query = q.query AND t.date = %s
        LEFT JOIN rabbit_positions a ON a.project = %s AND a.query = q.query
        GROUP BY q.query, q.frequency, n.position, t.position
    """, (project, project, now, project, then, project))
    out: dict[str, list[dict]] = {"up": [], "down": [], "lost": [], "new": [], "never": []}
    for r in rows:
        now_pos, then_pos = r["position"], r["position_prev"]
        if now_pos is not None and then_pos is not None:
            if now_pos < then_pos:
                out["up"].append(r)
            elif now_pos > then_pos:
                out["down"].append(r)
        elif now_pos is None and then_pos is not None:
            out["lost"].append(r)
        elif now_pos is not None and then_pos is None:
            out["new"].append(r)
        elif not r["ever_found"]:
            out["never"].append(r)
    for key in ("up", "down"):
        out[key].sort(key=lambda r: (-abs(r["position_prev"] - r["position"]), -r["frequency"]))
    for key in ("lost", "new", "never"):
        out[key].sort(key=lambda r: -r["frequency"])
    return out


# ----- ПФ и риски -----

def engine_weekly(conn: psycopg.Connection, counter_id: int, audience: str) -> list[dict]:
    """Визиты и отказы из Яндекса и Google по полным неделям (с понедельника) — из отчёта по дням.

    Полная неделя — все 7 дней есть в сводке счётчика по дням; поисковых визитов в какой-то день может и не быть.
    """
    return _all(conn, """
        WITH weeks AS (
            SELECT date_trunc('week', period_start)::date AS week FROM metrika_traffic
            WHERE counter_id = %(c)s AND audience = %(a)s AND grain = 'day' AND slice = 'total'
            GROUP BY 1 HAVING count(DISTINCT period_start) = 7
        ), engines AS (
            SELECT date_trunc('week', period_start)::date AS week,
                   coalesce(sum(visits) FILTER (WHERE dim_key = 'yandex'), 0) AS yandex,
                   coalesce(sum(visits) FILTER (WHERE dim_key = 'google'), 0) AS google
            FROM metrika_traffic
            WHERE counter_id = %(c)s AND audience = %(a)s AND grain = 'day' AND slice = 'search_engine'
            GROUP BY 1
        )
        SELECT w.week, e.yandex, e.google
        FROM weeks w JOIN engines e USING (week)
        ORDER BY w.week
    """, {"c": counter_id, "a": audience})


_PF_WINDOW = """
    WITH last AS (SELECT max(date) AS d FROM webmaster_query_daily WHERE host_id = %(host)s),
    project AS (SELECT DISTINCT lower(query) AS q FROM rabbit_positions WHERE project = %(project)s),
    q AS (
        SELECT w.query, lower(w.query) IN (SELECT q FROM project) AS boosted,
               sum(w.impressions) AS impressions, sum(w.clicks) AS clicks,
               round(sum(w.position * w.impressions) / nullif(sum(w.impressions) FILTER (WHERE w.position IS NOT NULL), 0), 1)
                   AS position,
               coalesce(sum(w.impressions) FILTER (WHERE w.position > 10), 0) AS impressions_far,
               coalesce(sum(w.clicks) FILTER (WHERE w.position > 10), 0) AS clicks_far,
               min(w.date) FILTER (WHERE w.impressions > 0) AS first_seen
        FROM webmaster_query_daily w, last
        WHERE w.host_id = %(host)s AND w.date > last.d - %(days)s
        GROUP BY w.query HAVING sum(w.impressions) > 0
    )
"""


def pf_window(conn: psycopg.Connection, host_id: str, project: str, days: int = 14) -> dict | None:
    """Аналитика запросов Вебмастера за последние дни: запросы проекта RabbitSeo против остальных."""
    period = _one(conn, "SELECT max(date) AS last_day FROM webmaster_query_daily WHERE host_id = %s", (host_id,))
    if not period or not period["last_day"]:
        return None
    params = {"host": host_id, "project": project, "days": days}
    groups = {r["boosted"]: r for r in _all(conn, _PF_WINDOW + """
        SELECT boosted, count(*) AS queries, sum(impressions) AS impressions, sum(clicks) AS clicks,
               sum(impressions_far) AS impressions_far, sum(clicks_far) AS clicks_far
        FROM q GROUP BY boosted
    """, params)}
    # кликов почти столько же, сколько показов, и не на первых местах — так ведут себя боты
    full_ctr = _all(conn, _PF_WINDOW + """
        SELECT query, boosted, impressions, clicks, position FROM q
        WHERE impressions >= 3 AND clicks >= 0.8 * impressions AND position >= 4
        ORDER BY boosted DESC, clicks DESC, query
    """, params)
    return {"first_day": period["last_day"] - timedelta(days=days - 1), "last_day": period["last_day"],
            "boosted": groups.get(True), "others": groups.get(False), "full_ctr": full_ctr}


def pf_domain_queries(conn: psycopg.Connection, host_id: str, project: str, domain: str, days: int = 14) -> list[dict]:
    """Запросы вида «товарный запрос + домен сайта»: живые люди так почти не ищут, а боты — когда не нашли сайт."""
    pattern = r"\s" + domain.replace(".", r"\.") + "$"
    return _all(conn, _PF_WINDOW + """
        SELECT query, boosted, impressions, clicks, position, first_seen,
               regexp_replace(lower(query), %(pattern)s, '') IN (SELECT q FROM project) AS from_project
        FROM q WHERE query ~* %(pattern)s
        ORDER BY clicks DESC, impressions DESC, query
    """, {"host": host_id, "project": project, "days": days, "pattern": pattern})


def pf_daily(conn: psycopg.Connection, host_id: str, project: str, domain: str) -> list[dict]:
    """По дням: клики по запросам проекта RabbitSeo, по «запрос + домен» и все остальные."""
    pattern = r"\s" + domain.replace(".", r"\.") + "$"
    return _all(conn, """
        WITH project AS (SELECT DISTINCT lower(query) AS q FROM rabbit_positions WHERE project = %(project)s)
        SELECT date,
               coalesce(sum(clicks) FILTER (WHERE lower(query) IN (SELECT q FROM project)), 0) AS boosted,
               coalesce(sum(clicks) FILTER (WHERE query ~* %(pattern)s), 0) AS domain,
               sum(clicks) AS total
        FROM webmaster_query_daily WHERE host_id = %(host)s
        GROUP BY date ORDER BY date
    """, {"host": host_id, "project": project, "pattern": pattern})


def rabbit_pairs(conn: psycopg.Connection, project: str, now: date, then: date) -> list[dict]:
    """Все запросы проекта RabbitSeo: позиция на два съёма, частые — первыми."""
    return _all(conn, """
        SELECT n.query, n.frequency, n.position, t.position AS position_prev
        FROM rabbit_positions n
        LEFT JOIN rabbit_positions t ON t.project = n.project AND t.query = n.query AND t.date = %s
        WHERE n.project = %s AND n.date = %s
        ORDER BY n.frequency DESC, n.query
    """, (then, project, now))


# ----- Страницы и товары -----

def pages(conn: psycopg.Connection, counter_id: int, month: date, kind: str, limit: int = 30) -> list[dict]:
    """Отчёт по просмотрам за месяц с прошлым месяцем: key, pageviews, users, pageviews_prev."""
    return _all(conn, f"""
        SELECT p.key, p.pageviews, p.users, prev.pageviews AS pageviews_prev
        FROM metrika_pages p
        LEFT JOIN metrika_pages prev ON prev.counter_id = p.counter_id AND prev.kind = p.kind AND prev.key = p.key
                                    AND prev.month = {PREV}
        WHERE p.counter_id = %s AND p.month = {MONTH} AND p.kind = %s
        ORDER BY p.pageviews DESC, p.key
        LIMIT %s
    """, (month, counter_id, month, kind, limit))


def not_found_visits(conn: psycopg.Connection, counter_id: int, month: date) -> dict:
    """Визиты, где был хоть один просмотр страницы 404: за месяц и за прошлый."""
    rows = dict(conn.execute(f"""
        SELECT month, pageviews FROM metrika_pages
        WHERE counter_id = %s AND kind = 'not_found_visits' AND month IN ({MONTH}, {PREV})
    """, (counter_id, month, month)).fetchall())
    return {"visits": rows.get(month), "visits_prev": rows.get((month.replace(day=1) - timedelta(days=1)).replace(day=1))}


def has_pages(conn: psycopg.Connection, counter_id: int, month: date) -> bool:
    return conn.execute(f"SELECT 1 FROM metrika_pages WHERE counter_id = %s AND month = {MONTH} LIMIT 1",
                        (counter_id, month)).fetchone() is not None


def products(conn: psycopg.Connection, counter_id: int, month: date, limit: int = 30) -> list[dict]:
    return _all(conn, f"""
        SELECT product, basket_sum, basket_qty, purchased_sum, purchased_qty
        FROM metrika_products WHERE counter_id = %s AND month = {MONTH}
        ORDER BY basket_sum DESC, product LIMIT %s
    """, (counter_id, month, limit))


def products_total(conn: psycopg.Connection, counter_id: int, month: date) -> dict | None:
    return _one(conn, f"""
        SELECT sum(basket_sum) AS basket_sum, sum(purchased_sum) AS purchased_sum, count(*) AS products
        FROM metrika_products WHERE counter_id = %s AND month = {MONTH}
    """, (counter_id, month))


# ----- Продажи: выгрузки Битрикс24 и 1С -----

def crm_form_leads(conn: psycopg.Connection, month: date) -> list[dict]:
    return _all(conn, f"SELECT * FROM crm_form_leads WHERE month = {MONTH}", (month,))


def crm_call_leads(conn: psycopg.Connection, month: date) -> list[dict]:
    return _all(conn, f"SELECT * FROM crm_call_leads WHERE month = {MONTH}", (month,))


def crm_call_orders(conn: psycopg.Connection, month: date) -> list[dict]:
    return _all(conn, f"SELECT * FROM crm_call_orders WHERE month = {MONTH} ORDER BY order_date, order_no", (month,))


def crm_files(conn: psycopg.Connection, month: date) -> list[dict]:
    return _all(conn, f"""
        SELECT kind, file_name, rows_loaded, imported_at FROM crm_imports WHERE month = {MONTH} ORDER BY imported_at DESC
    """, (month,))


def goal_visits_by_type(conn: psycopg.Connection, counter_id: int, audience: str, month: date) -> dict[str, int]:
    """Визиты с целью по типам целей Метрики (phone, email, contact_data_sent…) за месяц."""
    rows = _all(conn, f"""
        SELECT g.type, sum(s.visits) AS visits FROM metrika_goal_stats s
        JOIN metrika_goals g USING (counter_id, goal_id)
        WHERE s.counter_id = %s AND s.audience = %s AND s.grain = 'month' AND s.slice = 'total' AND s.period_start = {MONTH}
        GROUP BY g.type
    """, (counter_id, audience, month))
    return {r["type"]: int(r["visits"] or 0) for r in rows}


# ----- Ключи: семантика и Вебмастер -----

NORM = r"regexp_replace(replace(lower(w.query), 'ё', 'е'), '\s+', ' ', 'g')"
KEYWORD_SORTS = {
    "phrase": "phrase", "frequency": "frequency", "shows": "shows", "clicks": "clicks", "ctr": "ctr",
    "position": "position", "demand": "demand", "pos_yandex": "pos_yandex", "pos_google": "pos_google",
    "rabbit": "rabbit_position",
}


def query_days(conn: psycopg.Connection, host_id: str) -> dict | None:
    return _one(conn, "SELECT min(date) AS first, max(date) AS last FROM webmaster_query_daily WHERE host_id = %s",
                (host_id,))


def semantic_dates(conn: psycopg.Connection) -> dict[str, list[date]]:
    rows = _all(conn, "SELECT engine, array_agg(DISTINCT date ORDER BY date) AS dates FROM semantic_positions GROUP BY 1")
    return {r["engine"]: r["dates"] for r in rows}


def keywords(conn: psycopg.Connection, host_id: str, project: str, date_from: date, date_to: date, *,
             search: str = "", scope: str = "semantics", min_freq: int | None = None, max_freq: int | None = None,
             min_shows: int | None = None, ctr_min: float | None = None, ctr_max: float | None = None,
             rabbit: str = "", sort: str = "frequency", desc: bool = True, limit: int | None = 100,
             offset: int = 0) -> tuple[list[dict], int]:
    """Ключи с показами и кликами Вебмастера за период. scope: semantics — только фразы семантики,
    all — ещё и запросы Вебмастера, которых в семантике нет. Возвращает строки страницы и сколько их всего."""
    params: dict = {"host": host_id, "d1": date_from, "d2": date_to, "project": project}
    where = []
    if search:
        where.append("k.phrase ILIKE %(search)s")
        params["search"] = f"%{search.strip()}%"
    for cond, key, value in (("k.frequency >= %(min_freq)s", "min_freq", min_freq),
                             ("k.frequency <= %(max_freq)s", "max_freq", max_freq),
                             ("coalesce(k.shows, 0) >= %(min_shows)s", "min_shows", min_shows),
                             ("k.ctr >= %(ctr_min)s", "ctr_min", ctr_min), ("k.ctr <= %(ctr_max)s", "ctr_max", ctr_max)):
        if value is not None:
            where.append(cond)
            params[key] = value
    if rabbit == "yes":
        where.append("k.in_rabbit")
    elif rabbit == "no":
        where.append("NOT k.in_rabbit")
    join = "LEFT JOIN" if scope == "semantics" else "FULL JOIN"
    order = KEYWORD_SORTS.get(sort, "frequency")
    sql = f"""
        -- MATERIALIZED: каждый подзапрос считается один раз; иначе планировщик выбирает вложенные циклы
        -- и пересчитывает агрегат Вебмастера для каждой фразы (секунды вместо долей секунды)
        WITH w AS MATERIALIZED (
            SELECT {NORM} AS norm, min(w.query) AS query,
                   sum(w.impressions) AS shows, sum(w.clicks) AS clicks,
                   round(sum(w.position * w.impressions) / nullif(sum(w.impressions) FILTER (WHERE w.position IS NOT NULL), 0), 1)
                       AS position,
                   round(avg(w.demand)) AS demand, count(*) FILTER (WHERE w.impressions > 0) AS days
            FROM webmaster_query_daily w
            WHERE w.host_id = %(host)s AND w.date BETWEEN %(d1)s AND %(d2)s
            GROUP BY 1
        ), pos AS MATERIALIZED (
            SELECT DISTINCT ON (norm, engine) norm, engine, position, date FROM semantic_positions
            ORDER BY norm, engine, date DESC
        ), rb AS MATERIALIZED (
            SELECT DISTINCT ON (lower(query)) lower(query) AS q, position FROM rabbit_positions
            WHERE project = %(project)s ORDER BY lower(query), date DESC
        ), base AS MATERIALIZED (
            -- ключи: семантика и запросы проекта RabbitSeo, которых в семантике нет
            SELECT norm, phrase, frequency, true AS in_semantics FROM semantic_keywords
            UNION ALL
            SELECT r.q, r.query, r.frequency, false FROM (
                SELECT lower(query) AS q, min(query) AS query, max(frequency) AS frequency FROM rabbit_positions
                WHERE project = %(project)s GROUP BY lower(query)
            ) r LEFT JOIN semantic_keywords s ON s.norm = r.q
            WHERE s.norm IS NULL
        ), k0 AS MATERIALIZED (
            SELECT coalesce(s.norm, w.norm) AS norm, coalesce(s.phrase, w.query) AS phrase, s.frequency,
                   coalesce(s.in_semantics, false) AS in_semantics, s.norm IS NOT NULL AS is_key,
                   w.shows, w.clicks, w.position, w.demand, w.days,
                   round(100.0 * w.clicks / nullif(w.shows, 0), 1) AS ctr
            FROM base s
            {join} w ON w.norm = s.norm
        ), k AS (
            SELECT k0.*, py.position AS pos_yandex, pg.position AS pos_google,
                   rb.q IS NOT NULL AS in_rabbit, rb.position AS rabbit_position
            FROM k0
            LEFT JOIN pos py ON py.norm = k0.norm AND py.engine = 'yandex'
            LEFT JOIN pos pg ON pg.norm = k0.norm AND pg.engine = 'google'
            LEFT JOIN rb ON rb.q = k0.norm
        )
        SELECT *, count(*) OVER () AS total FROM k
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY {order} {"DESC" if desc else "ASC"} NULLS LAST, phrase
        {"LIMIT %(limit)s OFFSET %(offset)s" if limit else ""}
    """
    if limit:
        params.update(limit=limit, offset=offset)
    rows = _all(conn, sql, params)
    return rows, (rows[0]["total"] if rows else 0)


def keyword_days(conn: psycopg.Connection, host_id: str, norm: str, date_from: date, date_to: date) -> list[dict]:
    return _all(conn, f"""
        SELECT w.date, sum(w.impressions) AS shows, sum(w.clicks) AS clicks,
               round(sum(w.position * w.impressions) / nullif(sum(w.impressions), 0), 1) AS position,
               max(w.demand) AS demand, string_agg(DISTINCT w.query, ', ') AS queries
        FROM webmaster_query_daily w
        WHERE w.host_id = %s AND {NORM} = %s AND w.date BETWEEN %s AND %s
        GROUP BY w.date ORDER BY w.date
    """, (host_id, norm, date_from, date_to))


def keyword_positions(conn: psycopg.Connection, norm: str, project: str) -> dict:
    sheet = _all(conn, "SELECT engine, date, position FROM semantic_positions WHERE norm = %s ORDER BY date", (norm,))
    rabbit = _all(conn, "SELECT date, position, frequency FROM rabbit_positions WHERE project = %s AND lower(query) = %s"
                        " ORDER BY date", (project, norm))
    return {"sheet": sheet, "rabbit": rabbit}


def keyword_coverage(conn: psycopg.Connection, host_id: str, project: str, date_from: date, date_to: date) -> dict:
    """Сколько ключей вообще получали показы за период и сколько запросов Вебмастера — вне списка ключей."""
    return _one(conn, f"""
        WITH w AS (
            SELECT DISTINCT {NORM} AS norm FROM webmaster_query_daily w
            WHERE w.host_id = %(host)s AND w.date BETWEEN %(d1)s AND %(d2)s AND w.impressions > 0
        ), keys AS (
            SELECT norm FROM semantic_keywords
            UNION SELECT lower(query) FROM rabbit_positions WHERE project = %(project)s
        )
        SELECT (SELECT count(*) FROM semantic_keywords) AS semantics,
               (SELECT count(*) FROM keys) AS keys_total,
               (SELECT count(*) FROM keys JOIN w USING (norm)) AS keys_with_shows,
               (SELECT count(*) FROM w) AS queries,
               (SELECT count(*) FROM w WHERE norm NOT IN (SELECT norm FROM keys)) AS queries_outside
    """, {"host": host_id, "project": project, "d1": date_from, "d2": date_to})
