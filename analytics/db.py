"""PostgreSQL: схема и загрузка данных."""

from __future__ import annotations

from dataclasses import astuple
from datetime import date
from importlib import resources

import psycopg
from psycopg import sql

from analytics.keywords import Keyword
from analytics.sources.crm_files import item_norm
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
        # схему применяют и сборщик, и дашборд при запуске — по очереди, а не одновременно
        conn.execute("SELECT pg_advisory_xact_lock(4207731)")
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


CONTACT_KINDS = ["orders", "staff", "staff_hidden"]  # файлы, в которых есть имена и контакты для списков


def imported_files(conn: psycopg.Connection, version: int = 1, need_contacts: bool = False) -> set[str]:
    """Файлы, уже разобранные этой версией разбора (и нераспознанные ею): новая версия разбирает их заново.
    need_contacts (списки включили): заказы и сотрудники, разобранные без имён и контактов, разбираются заново."""
    return {sha for (sha,) in conn.execute(
        "SELECT sha256 FROM crm_imports WHERE parser_version >= %s AND NOT (%s AND kind = ANY (%s) AND NOT contacts)",
        (version, need_contacts, CONTACT_KINDS))}


def save_crm_file(conn: psycopg.Connection, sha: str, file_name: str, parsed, version: int = 1,
                  keep_contacts: bool = False) -> int:
    """Заменяет данные месяца этого вида выгрузки содержимым файла; повторная выгрузка за месяц — новая версия.
    keep_contacts (EMAIL_LISTS=on) — сохранить у заказов название клиента, адреса и номера (для списков клиентов)."""
    month, kind = parsed.month, parsed.kind
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM crm_summary WHERE month = %s AND kind = %s", (month, kind))
        cur.executemany("INSERT INTO crm_summary (month, kind, metric, value, note) VALUES (%s, %s, %s, %s, %s)"
                        " ON CONFLICT DO NOTHING", [(month, kind, m, v, n) for m, v, n in parsed.summary])
        if kind == "orders" and parsed.orders:
            # Выгрузки заказов дополняют друг друга по номеру заказа: список заказов с контактами и построчный отчёт
            # с номенклатурой. Новое значение заменяет прежнее, только если оно есть (пустое не затирает). Сумму заказа
            # построчный отчёт не меняет: в нём могут быть не все позиции (например, только товары из рассылок).
            totals = parsed.totals
            cur.executemany(
                "INSERT INTO orders_1c (order_no, order_date, amount, status, client_hash, email_hashes, phone_hashes,"
                " items, groups, client_name, emails, phones) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (order_no) DO UPDATE SET order_date = EXCLUDED.order_date,"
                " amount = CASE WHEN EXCLUDED.amount > 0 AND (%s OR orders_1c.amount = 0) THEN EXCLUDED.amount"
                "  ELSE orders_1c.amount END,"
                " status = coalesce(EXCLUDED.status, orders_1c.status),"
                " client_hash = coalesce(EXCLUDED.client_hash, orders_1c.client_hash),"
                " email_hashes = CASE WHEN cardinality(EXCLUDED.email_hashes) > 0 THEN EXCLUDED.email_hashes"
                "  ELSE orders_1c.email_hashes END,"
                " phone_hashes = CASE WHEN cardinality(EXCLUDED.phone_hashes) > 0 THEN EXCLUDED.phone_hashes"
                "  ELSE orders_1c.phone_hashes END,"
                " items = CASE WHEN cardinality(EXCLUDED.items) > 0 THEN EXCLUDED.items ELSE orders_1c.items END,"
                " groups = CASE WHEN cardinality(EXCLUDED.groups) > 0 THEN EXCLUDED.groups ELSE orders_1c.groups END,"
                " client_name = coalesce(EXCLUDED.client_name, orders_1c.client_name),"
                " emails = CASE WHEN cardinality(EXCLUDED.emails) > 0 THEN EXCLUDED.emails ELSE orders_1c.emails END,"
                " phones = CASE WHEN cardinality(EXCLUDED.phones) > 0 THEN EXCLUDED.phones ELSE orders_1c.phones END",
                [(o.order_no, o.order_date, o.amount, o.status, o.client_hash, list(o.email_hashes),
                  list(o.phone_hashes), list(o.items), list(o.groups),
                  o.client_name if keep_contacts else None, list(o.emails) if keep_contacts else [],
                  list(o.phones) if keep_contacts else [], totals) for o in parsed.orders],
            )
        if kind in ("orders", "mailing_items"):
            # позиции заказов: выгрузка заменяет позиции своих заказов целиком
            lines = parsed.order_lines
            if lines:
                cur.execute("DELETE FROM order_lines WHERE order_no = ANY (%s)", (sorted({x.order_no for x in lines}),))
                numbers: dict[str, int] = {}
                rows = []
                for x in lines:
                    numbers[x.order_no] = numbers.get(x.order_no, 0) + 1
                    rows.append((x.order_no, numbers[x.order_no], x.item, item_norm(x.item), x.color, x.amount))
                cur.executemany("INSERT INTO order_lines (order_no, line_no, item, item_norm, color, amount)"
                                " VALUES (%s, %s, %s, %s, %s, %s)", rows)
            # позиции рассылок: файл заменяет позиции своих рассылок; дата берётся и из других строк той же рассылки
            items = parsed.mailing_items
            if items:
                dates = {x.mailing: x.mailing_date for x in items if x.mailing_date}
                cur.execute("DELETE FROM mailing_items WHERE mailing = ANY (%s)", (sorted({x.mailing for x in items}),))
                cur.executemany("INSERT INTO mailing_items (mailing, mailing_date, item, item_norm) VALUES (%s, %s, %s, %s)",
                                sorted({(x.mailing, x.mailing_date or dates.get(x.mailing), x.item, item_norm(x.item))
                                        for x in items}, key=lambda r: (r[0], str(r[1]), r[3])))
        elif kind == "forms":
            cur.execute("DELETE FROM crm_form_leads WHERE month = %s", (month,))
            cur.executemany(
                "INSERT INTO crm_form_leads (lead_id, month, created_at, stage, in_1c, orders, amount, closed, first_order,"
                " match, manager_1c, order_nos, category, email_hashes, phone_hash)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (lead_id) DO UPDATE SET month = EXCLUDED.month, created_at = EXCLUDED.created_at,"
                " stage = EXCLUDED.stage, in_1c = EXCLUDED.in_1c, orders = EXCLUDED.orders, amount = EXCLUDED.amount,"
                " closed = EXCLUDED.closed, first_order = EXCLUDED.first_order, match = EXCLUDED.match,"
                " manager_1c = EXCLUDED.manager_1c, order_nos = EXCLUDED.order_nos, category = EXCLUDED.category,"
                " email_hashes = EXCLUDED.email_hashes, phone_hash = EXCLUDED.phone_hash",
                [(x.lead_id, month, x.created_at, x.stage, x.in_1c, x.orders, x.amount, x.closed, x.first_order, x.match,
                  x.manager_1c, list(x.order_nos), x.category, list(x.email_hashes), x.phone_hash)
                 for x in parsed.form_leads],
            )
        elif kind == "calls":
            cur.execute("DELETE FROM crm_call_leads WHERE month = %s", (month,))
            cur.execute("DELETE FROM crm_call_orders WHERE month = %s", (month,))
            cur.executemany(
                "INSERT INTO crm_call_leads (lead_id, month, called_at, call_type, stage, responsible, line, in_1c,"
                " phone_hash) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (lead_id) DO UPDATE SET month = EXCLUDED.month, called_at = EXCLUDED.called_at,"
                " call_type = EXCLUDED.call_type, stage = EXCLUDED.stage, responsible = EXCLUDED.responsible,"
                " line = EXCLUDED.line, in_1c = EXCLUDED.in_1c, phone_hash = EXCLUDED.phone_hash",
                [(x.lead_id, month, x.called_at, x.call_type, x.stage, x.responsible, x.line, x.in_1c, x.phone_hash)
                 for x in parsed.call_leads],
            )
            cur.executemany(
                "INSERT INTO crm_call_orders (month, order_no, category, order_date, amount, status_1c, lead_id, manager_1c)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                [(month, x.order_no, x.category, x.order_date, x.amount, x.status_1c, x.lead_id, x.manager_1c)
                 for x in parsed.call_orders],
            )
        cur.execute(
            "INSERT INTO crm_imports (sha256, file_name, kind, month, rows_loaded, parser_version, contacts)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (sha256) DO UPDATE SET imported_at = now(),"
            " rows_loaded = EXCLUDED.rows_loaded, parser_version = EXCLUDED.parser_version, contacts = EXCLUDED.contacts",
            (sha, file_name, kind, month, parsed.rows, version, keep_contacts),
        )
    return parsed.rows


def skip_crm_file(conn: psycopg.Connection, sha: str, file_name: str, version: int = 1) -> None:
    """Файл не распознан: запоминаем, чтобы не разбирать его снова при каждой проверке (до новой версии разбора)."""
    conn.execute("INSERT INTO crm_imports (sha256, file_name, kind, month, rows_loaded, parser_version)"
                 " VALUES (%s, %s, 'unknown', '1970-01-01', 0, %s)"
                 " ON CONFLICT (sha256) DO UPDATE SET parser_version = EXCLUDED.parser_version, imported_at = now()",
                 (sha, file_name, version))


def set_import_message(conn: psycopg.Connection, sha: str, message: str) -> None:
    conn.execute("UPDATE crm_imports SET message = %s WHERE sha256 = %s", (message, sha))


def save_semantics(conn: psycopg.Connection, sha: str, file_name: str, sem, version: int = 1) -> int:
    """Семантика целиком: новая выгрузка таблицы заменяет прежнюю."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM semantic_keywords")
        cur.execute("DELETE FROM semantic_positions")
        cur.executemany("INSERT INTO semantic_keywords (norm, phrase, frequency) VALUES (%s, %s, %s)",
                        [(norm, phrase, freq) for norm, (phrase, freq) in sem.keywords.items()])
        cur.executemany("INSERT INTO semantic_positions (norm, engine, date, position) VALUES (%s, %s, %s, %s)",
                        sem.positions)
        cur.execute(
            "INSERT INTO crm_imports (sha256, file_name, kind, month, rows_loaded, parser_version)"
            " VALUES (%s, %s, 'semantics', date_trunc('month', now())::date, %s, %s)"
            " ON CONFLICT (sha256) DO UPDATE SET imported_at = now(), parser_version = EXCLUDED.parser_version",
            (sha, file_name, sem.rows, version),
        )
    return sem.rows


def save_unisender(conn: psycopg.Connection, lists: dict[int, str], campaigns: list, stats: list) -> int:
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany("INSERT INTO unisender_lists (list_id, title) VALUES (%s, %s)"
                        " ON CONFLICT (list_id) DO UPDATE SET title = EXCLUDED.title", list(lists.items()))
        cur.executemany(
            "INSERT INTO unisender_campaigns (campaign_id, start_time, status, list_id, message_id, subject)"
            " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (campaign_id) DO UPDATE SET start_time = EXCLUDED.start_time,"
            " status = EXCLUDED.status, list_id = EXCLUDED.list_id, message_id = EXCLUDED.message_id,"
            " subject = EXCLUDED.subject",
            [(c.campaign_id, c.start_time, c.status, c.list_id, c.message_id, c.subject) for c in campaigns])
        cur.executemany(
            "INSERT INTO unisender_stats (campaign_id, total, sent, delivered, read_unique, read_all, clicked_unique,"
            " clicked_all, unsubscribed, spam) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (campaign_id) DO UPDATE SET total = EXCLUDED.total, sent = EXCLUDED.sent,"
            " delivered = EXCLUDED.delivered, read_unique = EXCLUDED.read_unique, read_all = EXCLUDED.read_all,"
            " clicked_unique = EXCLUDED.clicked_unique, clicked_all = EXCLUDED.clicked_all,"
            " unsubscribed = EXCLUDED.unsubscribed, spam = EXCLUDED.spam, updated_at = now()",
            [(x.campaign_id, x.total, x.sent, x.delivered, x.read_unique, x.read_all, x.clicked_unique, x.clicked_all,
              x.unsubscribed, x.spam) for x in stats])
    return len(campaigns) + len(stats)


def save_recipients(conn: psycopg.Connection, campaign_id: int, deliveries: list, links: list | None) -> int:
    """Отчёт о доставке рассылки (заменяет прежний) и ссылки письма (если удалось их получить)."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM unisender_delivery WHERE campaign_id = %s", (campaign_id,))
        cur.executemany("INSERT INTO unisender_delivery (campaign_id, email_hash, status, level) VALUES (%s, %s, %s, %s)"
                        " ON CONFLICT DO NOTHING", [(campaign_id, d.email_hash, d.status[:40], d.level) for d in deliveries])
        if links is not None:
            cur.execute("DELETE FROM unisender_links WHERE campaign_id = %s", (campaign_id,))
            cur.executemany("INSERT INTO unisender_links (campaign_id, url, text) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                            [(campaign_id, x.url[:1000], x.text) for x in links])
        cur.execute("INSERT INTO unisender_recipients_runs (campaign_id, recipients) VALUES (%s, %s)"
                    " ON CONFLICT (campaign_id) DO UPDATE SET recipients = EXCLUDED.recipients, fetched_at = now()",
                    (campaign_id, len(deliveries)))
    return len(deliveries)


def recipients_due(conn: psycopg.Connection, campaigns: list, today: date, fresh_days: int = 30,
                   max_age_days: int = 175) -> list:
    """Рассылки, чей отчёт о доставке пора забрать: ещё не забирали, или рассылка свежая (открытия копятся),
    и при этом не старше max_age_days (UniSender отдаёт отчёт только за последние 180 дней)."""
    fetched = dict(conn.execute("SELECT campaign_id, fetched_at FROM unisender_recipients_runs").fetchall())
    out = []
    for c in campaigns:
        if not c.start_time or c.start_time.date() > today:
            continue
        age = (today - c.start_time.date()).days
        last = fetched.get(c.campaign_id)
        settled = last is not None and (last.date() - c.start_time.date()).days >= fresh_days
        if age <= max_age_days and not settled:
            out.append(c)
    return out


def save_email_contacts(conn: psycopg.Connection, pairs: list[tuple[str, str]]) -> int:
    """Адреса получателей по отпечаткам — для списков «кто открыл» (только при EMAIL_LISTS=on)."""
    rows = sorted({h: e for h, e in pairs if h and e}.items())
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany("INSERT INTO email_contacts (email_hash, email) VALUES (%s, %s) ON CONFLICT (email_hash)"
                        " DO UPDATE SET email = EXCLUDED.email, updated_at = now()", rows)
    return len(rows)


def purge_email_contacts(conn: psycopg.Connection) -> None:
    """EMAIL_LISTS выключили: стереть адреса, имена и номера, оставить отпечатки."""
    with conn.transaction():
        conn.execute("DELETE FROM email_contacts")
        conn.execute("UPDATE staff SET name = NULL, email = NULL WHERE name IS NOT NULL OR email IS NOT NULL")
        conn.execute("UPDATE orders_1c SET client_name = NULL, emails = '{}', phones = '{}'"
                     " WHERE client_name IS NOT NULL OR cardinality(emails) > 0 OR cardinality(phones) > 0")
        conn.execute("UPDATE crm_imports SET contacts = false WHERE contacts")


def replace_staff(conn: psycopg.Connection, members: list, keep: bool) -> int:
    """Сотрудники из файла: отпечатки всегда, имя и адрес — только при keep (EMAIL_LISTS=on)."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM staff")
        cur.executemany("INSERT INTO staff (email_hash, phone_hash, name, email) VALUES (%s, %s, %s, %s)",
                        [(m.email_hash, m.phone_hash, m.name if keep else None, m.email if keep else None)
                         for m in members])
    return len(members)


def replace_email_base(conn: psycopg.Connection, contacts: list) -> int:
    """База рассылок целиком: каждая выгрузка заменяет прежнюю."""
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM email_base")
        cur.executemany("INSERT INTO email_base (email_hash, phone_hash, status, list_ids) VALUES (%s, %s, %s, %s)",
                        [(c.email_hash, c.phone_hash, c.status, list(c.list_ids)) for c in contacts])
    return len(contacts)
