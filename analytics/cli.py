"""Командная строка: python -m analytics <команда>."""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

import psycopg

from analytics import db
from analytics.config import Settings, load_settings
from analytics.sources.metrika import SLICES, MetrikaClient, MetrikaError, dimensions_for
from analytics.sources.webmaster import WebmasterClient, WebmasterError, parse_search_history
from analytics.sync import sync_metrika, sync_webmaster

# Период ежедневной перезагрузки. Вебмастер отдаёт данные с задержкой в несколько дней и уточняет их.
DEFAULT_DAYS = {"metrika": 7, "maps": 7, "webmaster": 30}
SOURCE_ERRORS = (MetrikaError, WebmasterError)


def check_metrika(settings: Settings) -> None:
    client = MetrikaClient(settings.yandex_token)
    counters = client.counters()
    if not counters:
        raise MetrikaError("Токен рабочий, но счётчиков не видно: выдайте этому аккаунту доступ к счётчику")
    print("Доступные счётчики:")
    for c in counters:
        site = (c.get("site2") or {}).get("site") or c.get("site", "")
        print(f"  {c['id']}  {c.get('name', '')}  {site}")

    counter_id = settings.metrika_counter_id
    if counter_id is None:
        if len(counters) > 1:
            raise MetrikaError("Укажите нужный счётчик в METRIKA_COUNTER_ID")
        counter_id = counters[0]["id"]
        print(f"METRIKA_COUNTER_ID не задан, проверяю единственный счётчик {counter_id}.")

    goals = client.goals(counter_id)
    print(f"\nЦели счётчика {counter_id} (* — считается заявкой, см. METRIKA_LEAD_GOALS):")
    for g in goals:
        mark = "*" if g["id"] in settings.metrika_lead_goals else " "
        print(f"  {mark} {g['id']}  {g['name']}  [{g.get('type', '')}]")
    unknown = settings.metrika_lead_goals - {g["id"] for g in goals}
    if unknown:
        print(f"  ! В METRIKA_LEAD_GOALS есть цели, которых нет в счётчике: {sorted(unknown)}")

    # Пробный запрос по каждому срезу: проверяет, что API принимает группировки и атрибуцию.
    yesterday = date.today() - timedelta(days=1)
    print(f"\nПробные отчёты за {yesterday} (атрибуция {settings.metrika_attribution}):")
    for slice_ in SLICES:
        dims = dimensions_for("day", slice_, settings.metrika_attribution)
        report = client.report(counter_id, ["ym:s:visits"], dims, yesterday, yesterday)
        visits = sum(int(r["metrics"][0] or 0) for r in report.rows)
        print(f"  {slice_:<6} ok: {len(report.rows)} строк, {visits} визитов")


def check_webmaster(settings: Settings) -> None:
    client = WebmasterClient(settings.yandex_token)
    print("Сайты в Вебмастере:")
    for h in client.hosts():
        print(f"  {h['host_id']}  {'подтверждён' if h.get('verified') else 'НЕ подтверждён'}")
    host_id = settings.webmaster_host_id
    if host_id is None:
        raise WebmasterError("Укажите сайт в WEBMASTER_HOST_ID (значение из первой колонки)")

    summary = client.summary(host_id)
    print(f"\n{host_id}: ИКС {summary.get('sqi')}, страниц в поиске {summary.get('searchable_pages_count')}, "
          f"исключено {summary.get('excluded_pages_count')}")
    today = date.today()
    days = parse_search_history(client.search_history(host_id, today - timedelta(days=60), today))
    if days:
        print(f"Статистика запросов есть по {days[-1].date} ({(today - days[-1].date).days} дн. назад)")
    else:
        print("Статистики запросов за последние 60 дней нет")


def cmd_check(settings: Settings) -> int:
    ok = True
    for title, check in (("Метрика", check_metrika), ("Вебмастер", check_webmaster)):
        print(f"=== {title}")
        try:
            check(settings)
        except SOURCE_ERRORS as exc:
            print(f"Ошибка: {exc}")
            ok = False
        print()
    return 0 if ok else 1


def _sync_metrika(conn: psycopg.Connection, settings: Settings, since: date, until: date) -> int:
    if settings.metrika_counter_id is None:
        raise MetrikaError("Не задан METRIKA_COUNTER_ID (узнать номер: python -m analytics check)")
    if not settings.metrika_lead_goals:
        print("Внимание: METRIKA_LEAD_GOALS пуст — заявки будут нулевыми.")
    client = MetrikaClient(settings.yandex_token)
    return sync_metrika(
        conn, client, settings.metrika_counter_id, settings.metrika_lead_goals, settings.metrika_attribution,
        since, until,
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
    if settings.webmaster_host_id is None:
        raise WebmasterError("Не задан WEBMASTER_HOST_ID (список сайтов: python -m analytics check)")
    return sync_webmaster(conn, WebmasterClient(settings.yandex_token), settings.webmaster_host_id, since, until)


SYNCS = {"metrika": _sync_metrika, "maps": _sync_maps, "webmaster": _sync_webmaster}

# Что должно быть настроено, чтобы `sync` загружал источник; явный `sync-<источник>` объяснит, чего не хватает.
CONFIGURED = {
    "metrika": lambda s: s.metrika_counter_id is not None,
    "maps": lambda s: bool(s.metrika_maps_counters),
    "webmaster": lambda s: s.webmaster_host_id is not None,
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m analytics", description="Сбор данных для дашборда")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="проверить доступ к Метрике и Вебмастеру")
    sub.add_parser("init-db", help="создать или обновить таблицы и представления")
    for name, help_ in (
        ("sync", "загрузить все источники (для ежедневного запуска)"),
        ("sync-metrika", "загрузить данные Метрики"),
        ("sync-maps", "загрузить счётчики карточек на Картах"),
        ("sync-webmaster", "загрузить данные Вебмастера"),
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
