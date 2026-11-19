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
from analytics.sources import crm_files, excel, semantics, unisender
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


def import_crm_folder(conn: psycopg.Connection, folder: Path, log: Callable[[str], None] = print,
                      keep_contacts: bool = False) -> list:
    """Загружает из папки новые выгрузки (*.xlsx): Битрикс24 и 1С, таблицу семантики; уже загруженные (по содержимому)
    пропускает.

    Возвращает разобранные файлы — по ним видно, за какие месяцы появились данные.
    """
    if not folder.is_dir():
        return []
    # сборщик и ручной import-crm не должны разбирать одни и те же файлы одновременно: второй ждёт первого
    conn.execute("SELECT pg_advisory_lock(%s)", (IMPORT_LOCK,))
    try:
        return _import_folder(conn, folder, log, keep_contacts)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (IMPORT_LOCK,))


IMPORT_LOCK = 4207732


def _import_folder(conn: psycopg.Connection, folder: Path, log: Callable[[str], None], keep_contacts: bool = False) -> list:
    # список сотрудников, разобранный без имён, перечитывается, когда списки получателей включили
    done = db.imported_files(conn, crm_files.PARSER_VERSION, ("staff_hidden",) if keep_contacts else ())
    out = []
    for path in sorted(x for x in folder.iterdir() if x.suffix.lower() in excel.EXTENSIONS):
        if path.name.startswith("~$"):
            continue  # временный файл открытого Excel
        sha = crm_files.sha256(path)
        if sha in done:
            continue
        try:
            sheets = excel.read_sheets(path)
            if semantics.is_semantics(sheets):
                sem = semantics.parse_sheets(sheets)
                db.save_semantics(conn, sha, path.name, sem, crm_files.PARSER_VERSION)
                _report(conn, sha, path.name, f"семантика — {sem.rows} фраз, {len(sem.positions)} позиций", log)
                out.append(sem)
                continue
            parsed = crm_files.parse_file(path, sheets)
        except Exception as exc:  # noqa: BLE001 — битый или чужой файл не должен останавливать загрузку остальных
            db.skip_crm_file(conn, sha, path.name, crm_files.PARSER_VERSION)
            _report(conn, sha, path.name, f"не загружен — {exc}", log)
            continue
        if parsed.kind == "staff":
            db.replace_staff(conn, parsed.staff, keep_contacts)
            parsed.kind = "staff" if keep_contacts else "staff_hidden"
            db.save_crm_file(conn, sha, path.name, parsed, crm_files.PARSER_VERSION)
            note = ("имена и адреса сохранены для списков" if keep_contacts
                    else "сохранены только отпечатки; имена и адреса — при EMAIL_LISTS=on в .env")
            _report(conn, sha, path.name, "\n".join([f"сотрудники — {len(parsed.staff)}; {note}"] + parsed.sheets_info), log)
            out.append(parsed)
            continue
        known = 0
        if parsed.orders:  # сколько заказов уже есть из других выгрузок — до того, как этот файл их дополнит
            known = conn.execute("SELECT count(*) FROM orders_1c WHERE order_no = ANY (%s)",
                                 ([o.order_no for o in parsed.orders],)).fetchone()[0]
        rows = db.save_crm_file(conn, sha, path.name, parsed, crm_files.PARSER_VERSION)
        text = (orders_summary(parsed, known) if parsed.kind in ("orders", "mailing_items")
                else f"{crm_files.KIND_NAMES[parsed.kind]} за {parsed.month:%m.%Y}, {rows} строк")
        _report(conn, sha, path.name, text, log)
        out.append(parsed)
    return out


def _report(conn: psycopg.Connection, sha: str, name: str, text: str, log: Callable[[str], None]) -> None:
    """Итог разбора файла: в журнал и в базу — его видно на странице «Данные» дашборда."""
    log(f"  {name}: " + text.replace("\n", "\n    "))
    db.set_import_message(conn, sha, text)


def orders_summary(parsed, known: int) -> str:
    """Что дала выгрузка заказов и позиций рассылок — без данных: сколько заказов, строк и позиций, что нашлось
    на каждом листе (какие колонки узнаны), у скольких заказов есть номенклатура и контакты и сколько из них уже
    было в базе из других выгрузок."""
    o = parsed.orders
    lines = []
    if o:
        dates = [x.order_date for x in o]
        total = f"{sum(x.amount for x in o):,.0f}".replace(",", " ")
        lines.append(f"{crm_files.KIND_NAMES['orders']} — {len(o)} заказов ({parsed.lines} строк), "
                     f"{min(dates):%d.%m.%Y}–{max(dates):%d.%m.%Y}, сумма {total} ₽")
        lines.append(f"с номенклатурой или группой: {sum(1 for x in o if x.items or x.groups)}, "
                     f"с почтой или телефоном: {sum(1 for x in o if x.email_hashes or x.phone_hashes)}, "
                     f"уже были в базе из других выгрузок: {known}")
    if parsed.mailing_items:
        mailings = {x.mailing for x in parsed.mailing_items}
        undated = {x.mailing for x in parsed.mailing_items if not x.mailing_date}
        lines.append(f"позиции рассылок: {len(parsed.mailing_items)} в {len(mailings)} рассылках"
                     + (f" (без даты рассылки: {len(undated)})" if undated else ""))
    lines += parsed.sheets_info
    notes = parsed.notes
    if known and o and not any(x.email_hashes or x.phone_hashes for x in o):  # контакты придут из общего списка
        notes = [n for n in notes if not n.startswith(("нет ни почты", "нет почты"))] + [
            f"контактов в файле нет — они берутся из других выгрузок по номеру заказа (совпало {known} из {len(o)})"]
    return "\n".join(lines + notes)


def sync_unisender(conn: psycopg.Connection, client, since: date, until: date,
                   log: Callable[[str], None] = print, refresh_days: int = 60, base: bool = True,
                   recipients: bool = True, keep_emails: bool = False) -> int:
    """Рассылки UniSender за период и их статистика, база контактов (отпечатки), затем по каждой рассылке —
    кому ушло письмо и что с ним сделали, и ссылки из письма.

    Открытия и клики по рассылке копятся неделями, поэтому статистика рассылок последних refresh_days дней
    перечитывается при каждой загрузке, а отчёт о доставке — пока рассылке нет месяца.
    """
    since = min(since, until - timedelta(days=refresh_days))
    lists = client.lists()
    campaigns = client.campaigns(since, until)
    stats = []
    for c in campaigns:
        try:
            stats.append(client.stats(c.campaign_id))
        except unisender.UnisenderError as exc:  # запланированная или черновая рассылка — статистики ещё нет
            log(f"  рассылка {c.campaign_id}: {exc}")
    loaded = db.save_unisender(conn, lists, campaigns, stats)
    log(f"  unisender: списков {len(lists)}, рассылок {len(campaigns)} ({since}…{until}), статистика {len(stats)}")
    if base:
        log("  unisender: выгружаю базу контактов (UniSender готовит файл, бывает несколько минут)…")
        contacts = client.export_contacts(keep_emails=True) if keep_emails else client.export_contacts()
        loaded += db.replace_email_base(conn, contacts)
        if keep_emails:
            db.save_email_contacts(conn, [(c.email_hash, c.email) for c in contacts])
            log(f"  unisender: база — {len(contacts)} контактов (адреса сохранены для списков: EMAIL_LISTS=on)")
        else:
            db.purge_email_contacts(conn)
            log(f"  unisender: база — {len(contacts)} контактов (сохранены только отпечатки почты и телефона)")
    if recipients:
        loaded += sync_recipients(conn, client, campaigns, until, log, keep_emails)
    return loaded


def sync_recipients(conn: psycopg.Connection, client, campaigns: list, today: date,
                    log: Callable[[str], None] = print, keep_emails: bool = False) -> int:
    """Отчёт о доставке (статус письма у каждого адреса) и ссылки письма — для рассылок, где их пора обновить."""
    due = db.recipients_due(conn, campaigns, today)
    if not due:
        return 0
    log(f"  unisender: отчёты о доставке по адресам — {len(due)} рассылок (каждый UniSender готовит отдельно)…")
    loaded, failed = 0, []
    for i, c in enumerate(due, 1):
        try:
            deliveries = client.delivery(c.campaign_id, keep_emails=True) if keep_emails else client.delivery(c.campaign_id)
        except unisender.UnisenderError as exc:
            failed.append(f"{c.campaign_id}: {exc}")
            continue
        links = None
        if c.message_id:
            try:
                links = client.message_links(c.message_id)
            except unisender.UnisenderError as exc:
                log(f"  рассылка {c.campaign_id}: ссылки письма не получены — {exc}")
        loaded += db.save_recipients(conn, c.campaign_id, deliveries, links)
        if keep_emails:
            db.save_email_contacts(conn, [(d.email_hash, d.email) for d in deliveries])
        if i % 10 == 0 or i == len(due):
            log(f"  unisender: готово {i} из {len(due)}")
    if failed:
        log(f"  unisender: отчёт о доставке не получен по {len(failed)} рассылкам, например {failed[0]}")
        if len(failed) == len(due):
            raise unisender.UnisenderError(f"Отчёты о доставке не получены: {failed[0]}")
    return loaded
