"""Командная строка: python -m analytics <команда>."""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

import psycopg

from analytics import db
from analytics.config import Settings, load_settings
from analytics.sources.metrika import SLICES, MetrikaClient, MetrikaError, dimensions_for
from analytics.sync import sync_metrika


def cmd_check(settings: Settings) -> int:
    client = MetrikaClient(settings.yandex_token)
    counters = client.counters()
    if not counters:
        print("Токен рабочий, но счётчиков не видно: выдайте этому аккаунту доступ к счётчику.")
        return 1
    print("Доступные счётчики:")
    for c in counters:
        site = (c.get("site2") or {}).get("site") or c.get("site", "")
        print(f"  {c['id']}  {c.get('name', '')}  {site}")

    counter_id = settings.metrika_counter_id
    if counter_id is None:
        if len(counters) > 1:
            print("Укажите нужный счётчик в METRIKA_COUNTER_ID.")
            return 1
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
    return 0


def cmd_sync_metrika(settings: Settings, since: date, until: date) -> int:
    if settings.metrika_counter_id is None:
        raise MetrikaError("Не задан METRIKA_COUNTER_ID (узнать номер: python -m analytics check)")
    if not settings.metrika_lead_goals:
        print("Внимание: METRIKA_LEAD_GOALS пуст — заявки будут нулевыми.")
    client = MetrikaClient(settings.yandex_token)
    with db.connect(settings.database_url) as conn:
        db.apply_schema(conn)
        run_id = db.start_run(conn, "metrika", since, until)
        try:
            loaded = sync_metrika(
                conn,
                client,
                settings.metrika_counter_id,
                settings.metrika_lead_goals,
                settings.metrika_attribution,
                since,
                until,
            )
        except Exception as exc:
            db.finish_run(conn, run_id, error=str(exc))
            raise
        db.finish_run(conn, run_id, rows_loaded=loaded)
    print(f"Готово: загружено {loaded} строк.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m analytics", description="Сбор данных для дашборда")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="проверить доступ к Метрике: счётчики, цели, пробные отчёты")
    sub.add_parser("init-db", help="создать или обновить таблицы и представления")
    sync = sub.add_parser("sync-metrika", help="загрузить данные Метрики в базу")
    sync.add_argument("--since", type=date.fromisoformat, help="с даты ГГГГ-ММ-ДД (по умолчанию 7 дней назад)")
    sync.add_argument("--until", type=date.fromisoformat, help="по дату ГГГГ-ММ-ДД (по умолчанию сегодня)")
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
        since = args.since or today - timedelta(days=7)
        if since > until:
            parser.error("--since позже --until")
        return cmd_sync_metrika(settings, since, until)
    except MetrikaError as exc:
        print(f"Ошибка Метрики: {exc}", file=sys.stderr)
        return 1
    except psycopg.OperationalError as exc:
        print(f"Нет подключения к базе ({settings.database_url}): {exc}", file=sys.stderr)
        return 1
