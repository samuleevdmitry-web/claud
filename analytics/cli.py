"""Командная строка: python -m analytics <команда>."""

from __future__ import annotations

import argparse
import sys
import time
import tomllib
from datetime import date, datetime, timedelta

import psycopg

from analytics import db
from analytics.config import Settings, load_settings
from analytics.schedule import TZ, due_sources
from analytics.sources.metrika import SLICES, MetrikaClient, MetrikaError, dimensions_for
from analytics.keywords import KeywordConfig, load_keywords
from analytics.sources.rabbit import RabbitClient, RabbitError
from analytics.sources.webmaster import WebmasterClient, WebmasterError, parse_search_history
from analytics.sources.wordstat import WordstatClient, WordstatError
from analytics.sync import sync_metrika, sync_rabbit, sync_webmaster, sync_wordstat

# Период ежедневной перезагрузки. Вебмастер отдаёт данные с задержкой в несколько дней и уточняет их.
DEFAULT_DAYS = {"metrika": 7, "maps": 7, "webmaster": 30, "wordstat": 7, "rabbit": 7}
SOURCE_ERRORS = (MetrikaError, WebmasterError, WordstatError, RabbitError)
WORDSTAT_SINCE = date(2023, 1, 1)  # три полных года — чтобы видеть сезонность
# Вордстат обновляет данные раз в месяц: загружаем 5-го числа.
MONTHLY_ON = {"wordstat": 5}


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


def cmd_check(settings: Settings) -> int:
    ok = True
    for title, check in (("Метрика", check_metrika), ("Вебмастер", check_webmaster), ("Вордстат", check_wordstat)):
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
        sync_metrika(conn, client, counter_id, settings.metrika_lead_goals, settings.metrika_attribution, since, until)
        for counter_id in settings.metrika_counters
    )


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


SYNCS = {"metrika": _sync_metrika, "maps": _sync_maps, "webmaster": _sync_webmaster, "wordstat": _sync_wordstat,
         "rabbit": _sync_rabbit}

# Что должно быть настроено, чтобы `sync` загружал источник; явный `sync-<источник>` объяснит, чего не хватает.
CONFIGURED = {
    "metrika": lambda s: bool(s.metrika_counters),
    "maps": lambda s: bool(s.metrika_maps_counters),
    "webmaster": lambda s: bool(s.webmaster_hosts),
    "wordstat": lambda s: bool(s.wordstat_folder_id),
    "rabbit": lambda s: bool(s.rabbit_guest_url),
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

    Так бывает после обновления, добавившего отчёты: обычная ежедневная загрузка берёт только последние дни,
    поэтому таким источникам расписание один раз загружает историю с начала, как пустой базе.
    Признак — нет ни одной месячной строки «только люди» в отчёте, который появился последним из нужных.
    """
    checks = {"metrika": (settings.metrika_counters, "hour"), "maps": (settings.metrika_maps_counters, "total")}
    missing = set()
    for source, (counters, slice_) in checks.items():
        for counter_id in counters:
            found = conn.execute(
                "SELECT 1 FROM metrika_traffic WHERE counter_id = %s AND audience = 'humans' AND grain = 'month'"
                " AND slice = %s LIMIT 1", (counter_id, slice_),
            ).fetchone()
            if found is None:
                missing.add(source)
                break
    return missing


def cmd_schedule(settings: Settings, check_every: int = 15 * 60) -> None:
    """Бесконечный цикл для Docker: раз в 15 минут проверяет, не пора ли загружать данные.

    Проверка по журналу sync_runs, а не по таймеру: ноутбук может спать, и после пробуждения
    пропущенный сбор запустится при первой же проверке.
    """
    print(f"Ежедневный сбор: проверка каждые {check_every // 60} мин")
    while True:
        try:
            with db.connect(settings.database_url) as conn:
                db.apply_schema(conn)
                rows = conn.execute("""
                    SELECT source, max(finished_at) FILTER (WHERE status = 'ok'), max(started_at)
                    FROM sync_runs GROUP BY source
                """).fetchall()
                incomplete = missing_history(conn, settings)
            runs = {source: {"last_ok": ok, "last_attempt": attempt} for source, ok, attempt in rows}
            for source in incomplete & set(runs):  # история неполная — загрузить её с начала
                runs[source]["last_ok"] = None
            now = datetime.now(TZ)
            sources = [name for name in SYNCS if CONFIGURED[name](settings)]
            for source, since in due_sources(now, runs, sources, DEFAULT_DAYS, monthly_on=MONTHLY_ON):
                try:
                    run_sync(settings, source, since, now.date())
                except Exception as exc:  # ошибка записана в sync_runs; цикл продолжается
                    print(f"{datetime.now(TZ):%d.%m %H:%M} ошибка ({source}): {exc}", file=sys.stderr)
        except psycopg.OperationalError as exc:
            print(f"{datetime.now(TZ):%d.%m %H:%M} база недоступна, повторю позже: {exc}", file=sys.stderr)
        time.sleep(check_every)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m analytics", description="Сбор данных для дашборда")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="проверить доступ к Метрике и Вебмастеру")
    sub.add_parser("init-db", help="создать или обновить таблицы и представления")
    sub.add_parser("schedule", help="ежедневный сбор в бесконечном цикле (для Docker)")
    for name, help_ in (
        ("sync", "загрузить все источники (для ежедневного запуска)"),
        ("sync-metrika", "загрузить данные Метрики"),
        ("sync-maps", "загрузить счётчики карточек на Картах"),
        ("sync-webmaster", "загрузить данные Вебмастера"),
        ("sync-wordstat", "загрузить спрос из Вордстата по ключам wordstat.toml"),
        ("sync-rabbit", "загрузить позиции из RabbitSeo по гостевой ссылке"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--since", type=date.fromisoformat,
                       help="с даты ГГГГ-ММ-ДД (по умолчанию: Метрика и Карты — 7 дней назад, Вебмастер — 30)")
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
