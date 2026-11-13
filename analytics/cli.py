"""Командная строка: python -m analytics <команда>."""

from __future__ import annotations

import argparse
import sys
import time
import tomllib
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg

from analytics import db
from analytics.config import Settings, load_settings
from analytics.schedule import HISTORY_START, RETRY_AFTER, TZ, due_sources, month_close_sources
from analytics.sources.metrika import SLICES, MetrikaClient, MetrikaError, dimensions_for
from analytics.keywords import KeywordConfig, load_keywords
from analytics.sources.rabbit import RabbitClient, RabbitError
from analytics.sources.semantics import SemanticsError
from analytics.sources.unisender import UnisenderClient, UnisenderError
from analytics.sources.semantics import download as semantics_download
from analytics.sources.webmaster import WebmasterClient, WebmasterError, parse_search_history
from analytics.sources.wordstat import WordstatClient, WordstatError
from analytics.sync import import_crm_folder, sync_metrika, sync_rabbit, sync_unisender, sync_webmaster, sync_wordstat

# Сколько последних дней перезагружать при плановом сборе: неделя и запас. Вебмастер отдаёт данные с задержкой
# в несколько дней и уточняет их, поэтому у него окно больше.
DEFAULT_DAYS = {"metrika": 14, "maps": 14, "webmaster": 30, "wordstat": 7, "rabbit": 14, "semantics": 7, "unisender": 14}
SOURCE_ERRORS = (MetrikaError, WebmasterError, WordstatError, RabbitError, SemanticsError, UnisenderError)
WORDSTAT_SINCE = date(2023, 1, 1)  # три полных года — чтобы видеть сезонность
# Метрика, Карты, Вебмастер и RabbitSeo — раз в неделю, в понедельник: прошедшая неделя закрыта целиком.
# Аналитика запросов Вебмастера хранит 14 дней, так что пропустить можно не больше одного понедельника.
WEEKLY_ON = {"metrika": 0, "maps": 0, "webmaster": 0, "rabbit": 0, "semantics": 0, "unisender": 0}
# Вордстат обновляет данные раз в месяц: загружаем 5-го числа.
MONTHLY_ON = {"wordstat": 5}
# Отчёт за прошлый месяц — 6-го: Метрика закрыла месяц, Вордстат загружен накануне, Вебмастер догнал задержку.
REPORT_DAY = 6


def check_metrika(settings: Settings) -> None:
    client = MetrikaClient(settings.yandex_token)
    counters = client.counters()
    if not counters:
        raise MetrikaError("Токен рабочий, но счётчиков не видно: выдайте этому аккаунту доступ к счётчику")
    configured = set(settings.metrika_counters) | set(settings.metrika_maps_counters)
    print("Доступные счётчики (* — в настройках):")
    for c in counters:
        site = (c.get("site2") or {}).get("site") or c.get("site", "")
        mark = "*" if c["id"] in configured else " "
        print(f"  {mark} {c['id']}  {c.get('name', '')}  {site}")
    if not settings.metrika_counters:
        raise MetrikaError("Укажите счётчики сайтов в METRIKA_COUNTERS")

    yesterday = date.today() - timedelta(days=1)
    for counter_id in settings.metrika_counters:
        goals = client.goals(counter_id)
        print(f"\nЦели счётчика {counter_id} (* — считается заявкой, см. METRIKA_LEAD_GOALS):")
        for g in goals:
            mark = "*" if g["id"] in settings.metrika_lead_goals else " "
            print(f"  {mark} {g['id']}  {g['name']}  [{g.get('type', '')}]")
        # Пробный запрос по каждому срезу: проверяет, что API принимает группировки и атрибуцию.
        print(f"Пробные отчёты за {yesterday} (атрибуция {settings.metrika_attribution}):")
        for slice_ in SLICES:
            dims = dimensions_for("day", slice_, settings.metrika_attribution)
            report = client.report(counter_id, ["ym:s:visits"], dims, yesterday, yesterday)
            visits = sum(int(r["metrics"][0] or 0) for r in report.rows)
            print(f"  {slice_:<6} ok: {len(report.rows)} строк, {visits} визитов")


def check_webmaster(settings: Settings) -> None:
    client = WebmasterClient(settings.yandex_token)
    print("Сайты в Вебмастере (* — в настройках):")
    for h in client.hosts():
        mark = "*" if h["host_id"] in settings.webmaster_hosts else " "
        print(f"  {mark} {h['host_id']}  {'подтверждён' if h.get('verified') else 'НЕ подтверждён'}")
    if not settings.webmaster_hosts:
        raise WebmasterError("Укажите сайты в WEBMASTER_HOSTS (значения из первой колонки)")

    today = date.today()
    for host_id in settings.webmaster_hosts:
        summary = client.summary(host_id)
        print(f"\n{host_id}: ИКС {summary.get('sqi')}, страниц в поиске {summary.get('searchable_pages_count')}, "
              f"исключено {summary.get('excluded_pages_count')}")
        days = parse_search_history(client.search_history(host_id, today - timedelta(days=60), today))
        if days:
            print(f"Статистика запросов есть по {days[-1].date} ({(today - days[-1].date).days} дн. назад)")
        else:
            print("Статистики запросов за последние 60 дней нет")


def _keywords(settings: Settings) -> KeywordConfig:
    """wordstat.toml правят руками, поэтому ошибки файла — понятным сообщением, а не трассировкой."""
    try:
        return load_keywords(settings.wordstat_keywords_path)
    except FileNotFoundError:
        raise WordstatError(f"Нет файла с ключами {settings.wordstat_keywords_path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise WordstatError(f"Ошибка в {settings.wordstat_keywords_path}: {exc}") from None


def check_wordstat(settings: Settings) -> None:
    config = _keywords(settings)
    client = WordstatClient(settings.wordstat_api_key, settings.wordstat_folder_id)
    print(f"{settings.wordstat_keywords_path}: {len(config.phrases)} фраз, регионы: "
          + ", ".join(f"{site} → {r.name} ({r.id})" for site, r in config.regions.items()))
    phrase, region = config.phrases[0], config.region_ids[0]
    top = client.top_requests(phrase, region, limit=5)
    print(f"Пробный запрос «{phrase}»: " + "; ".join(f"{q} {c}" for q, c in top))


def check_unisender(settings: Settings) -> None:
    if not settings.unisender_api_key:
        print("Не настроен (UNISENDER_API_KEY пуст) — пропускаю")
        return
    client = UnisenderClient(settings.unisender_api_key)
    lists = client.lists()
    print(f"Списков: {len(lists)} — " + ", ".join(f"{t} ({i})" for i, t in list(lists.items())[:5]))
    campaigns = client.campaigns(date.today() - timedelta(days=30), date.today())
    print(f"Рассылок за 30 дней: {len(campaigns)}")
    if campaigns:
        c = campaigns[-1]
        st = client.stats(c.campaign_id)
        when = f" ({c.start_time:%d.%m})" if c.start_time else ""
        print(f"  последняя {c.campaign_id}{when}: доставлено {st.delivered}, открыли {st.read_unique},"
              f" кликнули {st.clicked_unique}")


def cmd_check(settings: Settings) -> int:
    ok = True
    for title, check in (("Метрика", check_metrika), ("Вебмастер", check_webmaster), ("Вордстат", check_wordstat),
                         ("UniSender", check_unisender)):
        print(f"=== {title}")
        try:
            check(settings)
        except SOURCE_ERRORS as exc:
            print(f"Ошибка: {exc}")
            ok = False
        print()
    return 0 if ok else 1


def _sync_metrika(conn: psycopg.Connection, settings: Settings, since: date, until: date) -> int:
    if not settings.metrika_counters:
        raise MetrikaError("Не заданы METRIKA_COUNTERS (номера счётчиков: python -m analytics check)")
    if not settings.metrika_lead_goals:
        print("Внимание: METRIKA_LEAD_GOALS пуст — заявки будут нулевыми.")
    client = MetrikaClient(settings.yandex_token)
    return sum(
        sync_metrika(conn, client, counter_id, settings.metrika_lead_goals, settings.metrika_attribution, since, until,
                     not_found_title=settings.not_found_title, search_param=settings.site_search_param)
        for counter_id in settings.metrika_counters
    )


# Отчёты Метрики, история которых догружается отдельно, если отчёт появился после загрузки истории.
BACKFILL_REPORTS = {name for name, spec in SLICES.items() if "month" in spec.grains and name != "total"} | {"pages"}


def missing_reports(conn: psycopg.Connection, settings: Settings) -> dict[int, set[str]]:
    """Счётчик сайта → отчёты без истории: данных нет или они начинаются позже сводки больше чем на месяц.

    Отчёт догружается один раз (report_backfills), даже если данных в нём так и не оказалось —
    например, Директа на сайте нет; иначе сборщик повторял бы догрузку бесконечно.
    """
    out = {}
    for counter_id in settings.metrika_counters:
        first = dict(conn.execute(
            "SELECT slice, min(period_start) FROM metrika_traffic WHERE counter_id = %s AND audience = 'all'"
            " AND grain = 'month' GROUP BY slice", (counter_id,),
        ).fetchall())
        if "total" not in first:
            continue  # истории ещё нет совсем — её загрузит полная загрузка
        first["pages"] = conn.execute("SELECT min(month) FROM metrika_pages WHERE counter_id = %s",
                                      (counter_id,)).fetchone()[0]
        done = {r for (r,) in conn.execute("SELECT report FROM report_backfills WHERE counter_id = %s", (counter_id,))}
        late = first["total"] + timedelta(days=31)
        missing = {r for r in BACKFILL_REPORTS - done if first.get(r) is None or first[r] > late}
        if missing:
            out[counter_id] = missing
    return out


def run_backfill(settings: Settings, missing: dict[int, set[str]], today: date) -> None:
    """Догружает историю только недостающих отчётов — не всю Метрику заново."""
    client = MetrikaClient(settings.yandex_token)
    with db.connect(settings.database_url) as conn:
        run_id = db.start_run(conn, "metrika_backfill", HISTORY_START, today)
        loaded = 0
        try:
            for counter_id, reports in missing.items():
                print(f"=== догрузка истории отчётов {', '.join(sorted(reports))} (счётчик {counter_id})")
                loaded += sync_metrika(conn, client, counter_id, settings.metrika_lead_goals,
                                       settings.metrika_attribution, HISTORY_START, today, only=reports,
                                       not_found_title=settings.not_found_title,
                                       search_param=settings.site_search_param)
                db.mark_backfilled(conn, counter_id, reports)
        except Exception as exc:
            db.finish_run(conn, run_id, error=str(exc))
            raise
        db.finish_run(conn, run_id, rows_loaded=loaded)


def _sync_maps(conn: psycopg.Connection, settings: Settings, since: date, until: date) -> int:
    if not settings.metrika_maps_counters:
        raise MetrikaError("Не заданы METRIKA_MAPS_COUNTERS (счётчики карточек: python -m analytics check)")
    client = MetrikaClient(settings.yandex_token)
    return sum(
        sync_metrika(conn, client, counter_id, frozenset(), settings.metrika_attribution, since, until, kind="maps")
        for counter_id in settings.metrika_maps_counters
    )


def _sync_webmaster(conn: psycopg.Connection, settings: Settings, since: date, until: date) -> int:
    if not settings.webmaster_hosts:
        raise WebmasterError("Не заданы WEBMASTER_HOSTS (список сайтов: python -m analytics check)")
    client = WebmasterClient(settings.yandex_token)
    return sum(sync_webmaster(conn, client, host_id, since, until) for host_id in settings.webmaster_hosts)


def _sync_wordstat(conn: psycopg.Connection, settings: Settings, since: date, until: date) -> int:
    # since не используется: один запрос отдаёт всю историю фразы, поэтому она перезагружается целиком
    client = WordstatClient(settings.wordstat_api_key, settings.wordstat_folder_id)
    return sync_wordstat(conn, client, _keywords(settings), WORDSTAT_SINCE)


def _sync_rabbit(conn: psycopg.Connection, settings: Settings, since: date, until: date) -> int:
    # since не используется: страница отдаёт весь последний год одним запросом
    return sync_rabbit(conn, RabbitClient(settings.rabbit_guest_url), until)


def _sync_semantics(conn: psycopg.Connection, settings: Settings, since: date, until: date) -> int:
    """Таблица семантики по ссылке → папка imports → база (как любая выгрузка, которую кладут руками)."""
    if not settings.semantics_url:
        raise SemanticsError("Не задан SEMANTICS_URL — ссылка на Google Таблицу семантики")
    folder = Path(settings.imports_dir)
    folder.mkdir(parents=True, exist_ok=True)
    semantics_download(settings.semantics_url, folder / "semantics.xlsx")
    loaded = import_crm_folder(conn, folder)
    rows = conn.execute("SELECT count(*) FROM semantic_keywords").fetchone()[0]
    print(f"  семантика: {rows} фраз" + ("" if loaded else " (таблица не менялась)"))
    return rows


def _sync_unisender(conn: psycopg.Connection, settings: Settings, since: date, until: date) -> int:
    return sync_unisender(conn, UnisenderClient(settings.unisender_api_key), since, until)


SYNCS = {"metrika": _sync_metrika, "maps": _sync_maps, "webmaster": _sync_webmaster, "wordstat": _sync_wordstat,
         "rabbit": _sync_rabbit, "semantics": _sync_semantics, "unisender": _sync_unisender}

# Что должно быть настроено, чтобы `sync` загружал источник; явный `sync-<источник>` объяснит, чего не хватает.
CONFIGURED = {
    "metrika": lambda s: bool(s.metrika_counters),
    "maps": lambda s: bool(s.metrika_maps_counters),
    "webmaster": lambda s: bool(s.webmaster_hosts),
    "wordstat": lambda s: bool(s.wordstat_folder_id),
    "rabbit": lambda s: bool(s.rabbit_guest_url),
    "semantics": lambda s: bool(s.semantics_url),
    "unisender": lambda s: bool(s.unisender_api_key),
}


def run_sync(settings: Settings, source: str, since: date, until: date) -> None:
    """Загрузка одного источника с записью в журнал sync_runs."""
    print(f"=== {source}: {since}…{until}")
    with db.connect(settings.database_url) as conn:
        db.apply_schema(conn)
        run_id = db.start_run(conn, source, since, until)
        try:
            loaded = SYNCS[source](conn, settings, since, until)
        except Exception as exc:
            db.finish_run(conn, run_id, error=str(exc))
            raise
        db.finish_run(conn, run_id, rows_loaded=loaded)
    print(f"Готово: загружено {loaded} строк.\n")


def missing_history(conn: psycopg.Connection, settings: Settings) -> set[str]:
    """Источники Метрики, у которых в базе нет истории «только людей» или новых отчётов.

    Так бывает после обновления, добавившего отчёты: плановая загрузка берёт только последние дни,
    поэтому таким источникам расписание один раз загружает историю с начала, как пустой базе.
    Признак — нет ни одной строки «только люди» в отчёте, который появился последним из нужных
    (у сайта — поисковые системы по дням, для слежения за долей Яндекса).
    """
    base = "SELECT 1 FROM metrika_traffic t WHERE counter_id = %s AND audience = 'humans' AND "
    site_checks = [
        base + "grain = 'day' AND slice = 'search_engine' LIMIT 1",
        # заявки стали визитами с целью-заявкой: месяцы без lead_visits посчитаны по-старому
        # (у счётчика без целей-заявок колонка пустая всегда — его не перезагружаем)
        base + "grain = 'month' AND slice = 'total' AND (lead_visits IS NOT NULL OR NOT EXISTS ("
               "SELECT 1 FROM metrika_goals g WHERE g.counter_id = t.counter_id AND g.is_lead)) LIMIT 1",
    ]
    checks = {
        "metrika": (settings.metrika_counters, site_checks),
        "maps": (settings.metrika_maps_counters, [base + "grain = 'month' AND slice = 'total' LIMIT 1"]),
    }
    missing = set()
    for source, (counters, queries) in checks.items():
        if any(conn.execute(query, (c,)).fetchone() is None for c in counters for query in queries):
            missing.add(source)
    return missing


def make_reports(settings: Settings, month: date, today: date) -> list[Path]:
    """Сохраняет отчёт за месяц по каждому сайту в папку отчётов; пишет запуск в sync_runs как источник report."""
    from analytics.dashboard.app import report_file  # дашборд нужен только здесь

    folder = Path(settings.reports_dir)
    folder.mkdir(parents=True, exist_ok=True)
    month_end = (month + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    saved = []
    with db.connect(settings.database_url) as conn:
        db.apply_schema(conn)
        run_id = db.start_run(conn, "report", month, month_end)
        try:
            for counter_id in settings.metrika_counters:
                made = report_file(conn, settings, counter_id, month, today)
                if made is None:
                    print(f"  {counter_id}: нет данных за {month:%m.%Y}, отчёт не сформирован")
                    continue
                name, html = made
                (folder / name).write_text(html, encoding="utf-8")
                saved.append(folder / name)
                print(f"  сохранён {folder / name}")
        except Exception as exc:
            db.finish_run(conn, run_id, error=str(exc))
            raise
        db.finish_run(conn, run_id, rows_loaded=len(saved))
    return saved


def import_crm(settings: Settings, today: date) -> list:
    """Новые выгрузки Битрикс24 и 1С из папки imports. Если пришли за уже закрытый месяц, отчёт за него пересобирается."""
    with db.connect(settings.database_url) as conn:
        db.apply_schema(conn)
        parsed = import_crm_folder(conn, Path(settings.imports_dir))
    current = today.replace(day=1)
    for month in sorted({p.month for p in parsed if getattr(p, "month", None) and p.month < current}):
        reports = Path(settings.reports_dir)
        if any(reports.glob(f"{month:%Y-%m} *.html")):  # отчёт за месяц уже был — обновить с продажами
            print(f"=== отчёт за {month:%m.%Y} пересобирается: пришли выгрузки CRM")
            make_reports(settings, month, today)
    return parsed


def previous_month(today: date) -> date:
    return (today.replace(day=1) - timedelta(days=1)).replace(day=1)


def cmd_schedule(settings: Settings, check_every: int = 15 * 60) -> None:
    """Бесконечный цикл для Docker: раз в 15 минут проверяет, не пора ли загружать данные.

    Проверка по журналу sync_runs, а не по таймеру: ноутбук может спать, и после пробуждения
    пропущенный сбор запустится при первой же проверке.
    """
    print(f"Сбор по расписанию (по понедельникам, Вордстат — {MONTHLY_ON['wordstat']}-го, отчёт — {REPORT_DAY}-го): "
          f"проверка каждые {check_every // 60} мин")
    while True:
        try:
            with db.connect(settings.database_url) as conn:
                db.apply_schema(conn)
                rows = conn.execute("""
                    SELECT source, max(finished_at) FILTER (WHERE status = 'ok'), max(started_at)
                    FROM sync_runs GROUP BY source
                """).fetchall()
                incomplete = missing_history(conn, settings)
                backfill = missing_reports(conn, settings) if "metrika" not in incomplete else {}
            runs = {source: {"last_ok": ok, "last_attempt": attempt} for source, ok, attempt in rows}
            for source in incomplete & set(runs):  # история неполная — загрузить её с начала
                runs[source]["last_ok"] = None
            now = datetime.now(TZ)
            sources = [name for name in SYNCS if CONFIGURED[name](settings)]
            try:
                import_crm(settings, now.date())
            except Exception as exc:
                print(f"{datetime.now(TZ):%d.%m %H:%M} ошибка (выгрузки CRM): {exc}", file=sys.stderr)
            done = set()

            def sync(source: str, since: date) -> None:
                done.add(source)
                try:
                    run_sync(settings, source, since, now.date())
                except Exception as exc:  # ошибка записана в sync_runs; цикл продолжается
                    print(f"{datetime.now(TZ):%d.%m %H:%M} ошибка ({source}): {exc}", file=sys.stderr)

            for source, since in due_sources(now, runs, sources, DEFAULT_DAYS, monthly_on=MONTHLY_ON,
                                             weekly_on=WEEKLY_ON):
                sync(source, since)
            # новые отчёты Метрики, появившиеся после загрузки истории, — догрузить их историю отдельно
            last = runs.get("metrika_backfill", {})
            backfill_failed_recently = (
                last.get("last_attempt") is not None and now - last["last_attempt"] < RETRY_AFTER
                and (last.get("last_ok") is None or last["last_ok"] < last["last_attempt"])
            )
            if backfill and "metrika" in sources and not backfill_failed_recently:
                try:
                    run_backfill(settings, backfill, now.date())
                except Exception as exc:
                    print(f"{datetime.now(TZ):%d.%m %H:%M} ошибка (догрузка отчётов): {exc}", file=sys.stderr)
            # отчёт — после загрузок; если конец прошлого месяца ещё не забран (сбор раз в неделю), сначала он
            if settings.metrika_counters and due_sources(now, runs, ["report"], {"report": 0},
                                                         monthly_on={"report": REPORT_DAY}):
                weekly = [s for s in sources if s in WEEKLY_ON and s not in done]
                for source, since in month_close_sources(now, runs, weekly, DEFAULT_DAYS):
                    sync(source, since)
                month = previous_month(now.date())
                print(f"=== отчёт за {month:%m.%Y}")
                try:
                    make_reports(settings, month, now.date())
                except Exception as exc:
                    print(f"{datetime.now(TZ):%d.%m %H:%M} ошибка (отчёт): {exc}", file=sys.stderr)
        except psycopg.OperationalError as exc:
            print(f"{datetime.now(TZ):%d.%m %H:%M} база недоступна, повторю позже: {exc}", file=sys.stderr)
        time.sleep(check_every)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m analytics", description="Сбор данных для дашборда")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="проверить доступ к Метрике и Вебмастеру")
    sub.add_parser("init-db", help="создать или обновить таблицы и представления")
    sub.add_parser("schedule", help="сбор по расписанию в бесконечном цикле (для Docker)")
    sub.add_parser("import-crm", help="загрузить новые выгрузки Битрикс24 и 1С (*.xlsx) из папки imports")
    rep = sub.add_parser("report", help="сохранить отчёт за месяц в папку отчётов (по умолчанию — за прошлый месяц)")
    rep.add_argument("--month", help="месяц ГГГГ-ММ")
    for name, help_ in (
        ("sync", "загрузить все источники сейчас"),
        ("sync-metrika", "загрузить данные Метрики"),
        ("sync-maps", "загрузить счётчики карточек на Картах"),
        ("sync-webmaster", "загрузить данные Вебмастера"),
        ("sync-wordstat", "загрузить спрос из Вордстата по ключам wordstat.toml"),
        ("sync-rabbit", "загрузить позиции из RabbitSeo по гостевой ссылке"),
        ("sync-semantics", "скачать таблицу семантики по ссылке SEMANTICS_URL и загрузить её"),
        ("sync-unisender", "загрузить рассылки UniSender, их статистику и базу (отпечатки почты и телефона)"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--since", type=date.fromisoformat,
                       help="с даты ГГГГ-ММ-ДД (по умолчанию: Метрика, Карты, RabbitSeo — 14 дней назад, Вебмастер — 30)")
        p.add_argument("--until", type=date.fromisoformat, help="по дату ГГГГ-ММ-ДД (по умолчанию сегодня)")
    args = parser.parse_args(argv)
    settings = load_settings()

    try:
        if args.command == "check":
            return cmd_check(settings)
        if args.command == "init-db":
            with db.connect(settings.database_url) as conn:
                for name in db.apply_schema(conn):
                    print(f"Применён {name}")
            return 0
        if args.command == "schedule":
            cmd_schedule(settings)
        if args.command == "import-crm":
            parsed = import_crm(settings, date.today())
            print(f"Загружено файлов: {len(parsed)}" if parsed else f"Новых файлов в {settings.imports_dir} нет")
            return 0
        if args.command == "report":
            month = date.fromisoformat(args.month + "-01") if args.month else previous_month(date.today())
            return 0 if make_reports(settings, month, date.today()) else 1

        today = date.today()
        until = min(args.until or today, today)
        if args.command == "sync":
            sources = [name for name in SYNCS if CONFIGURED[name](settings)]
            skipped = sorted(set(SYNCS) - set(sources))
            if skipped:
                print(f"Не настроены, пропускаю: {', '.join(skipped)}\n")
        else:
            sources = [args.command.removeprefix("sync-")]
        failed = False
        for source in sources:
            since = args.since or today - timedelta(days=DEFAULT_DAYS[source])
            if since > until:
                parser.error("--since позже --until")
            try:
                run_sync(settings, source, since, until)
            except SOURCE_ERRORS as exc:  # один упавший источник не останавливает остальные
                print(f"Ошибка ({source}): {exc}", file=sys.stderr)
                failed = True
        return 1 if failed else 0
    except psycopg.OperationalError as exc:
        print(f"Нет подключения к базе ({settings.database_url}): {exc}", file=sys.stderr)
        return 1
