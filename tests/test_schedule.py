from datetime import date, datetime, timedelta

from analytics.schedule import HISTORY_START, TZ, due_sources, month_close_sources

DAYS = {"metrika": 7, "webmaster": 30}


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)


def test_empty_base_loads_history():
    assert due_sources(at(11, 3), {}, ["metrika"], DAYS) == [("metrika", HISTORY_START)]


def test_daily_run_after_six_once_a_day():
    runs = {"metrika": {"last_ok": at(10, 6, 5), "last_attempt": at(10, 6, 0)}}
    assert due_sources(at(11, 5, 45), runs, ["metrika"], DAYS) == []  # ещё рано
    assert due_sources(at(11, 6, 15), runs, ["metrika"], DAYS) == [("metrika", date(2026, 11, 4))]
    done = {"metrika": {"last_ok": at(11, 6, 20), "last_attempt": at(11, 6, 15)}}
    assert due_sources(at(11, 9), done, ["metrika"], DAYS) == []  # сегодня уже загружено


def test_laptop_was_off_longer_than_the_window():
    runs = {"metrika": {"last_ok": at(1, 6), "last_attempt": at(1, 6)}}
    # окно Метрики — 7 дней, но с 1 ноября ничего не грузилось: берём с 31 октября
    assert due_sources(at(11, 8), runs, ["metrika"], DAYS) == [("metrika", date(2026, 10, 31))]


def test_failed_source_waits_an_hour():
    runs = {"webmaster": {"last_ok": at(10, 6), "last_attempt": at(11, 6, 0)}}
    assert due_sources(at(11, 6, 30), runs, ["webmaster"], DAYS) == []
    assert due_sources(at(11, 7, 5), runs, ["webmaster"], DAYS) == [("webmaster", date(2026, 10, 12))]


def test_each_source_decided_separately():
    runs = {"metrika": {"last_ok": at(11, 6, 10), "last_attempt": at(11, 6, 5)}}
    assert due_sources(at(11, 7), runs, ["metrika", "webmaster"], DAYS) == [("webmaster", HISTORY_START)]
    assert at(11, 7) - runs["metrika"]["last_ok"] < timedelta(hours=1)


WEEKLY = {"metrika": 0}  # по понедельникам; 9 и 16 ноября 2026 — понедельники
WEEK_DAYS = {"metrika": 14, "wordstat": 7}


def test_weekly_source_runs_on_monday_once():
    runs = {"metrika": {"last_ok": at(9, 6, 10), "last_attempt": at(9, 6, 5)}}
    assert due_sources(at(13, 9), runs, ["metrika"], WEEK_DAYS, weekly_on=WEEKLY) == []      # пятница
    assert due_sources(at(16, 5), runs, ["metrika"], WEEK_DAYS, weekly_on=WEEKLY) == []      # понедельник, рано
    assert due_sources(at(16, 6, 15), runs, ["metrika"], WEEK_DAYS, weekly_on=WEEKLY) == [("metrika", date(2026, 11, 2))]
    done = {"metrika": {"last_ok": at(16, 6, 40), "last_attempt": at(16, 6, 15)}}
    assert due_sources(at(18, 12), done, ["metrika"], WEEK_DAYS, weekly_on=WEEKLY) == []


def test_weekly_source_catches_up_after_missed_monday():
    runs = {"metrika": {"last_ok": at(9, 6, 10), "last_attempt": at(9, 6, 5)}}
    # в понедельник 16-го ноутбук был выключен — загрузка во вторник, с запасом до прошлой загрузки
    assert due_sources(at(17, 10), runs, ["metrika"], WEEK_DAYS, weekly_on=WEEKLY) == [("metrika", date(2026, 11, 3))]


def test_monthly_source_on_the_fifth():
    runs = {"wordstat": {"last_ok": at(5, 7), "last_attempt": at(5, 7)}}
    monthly = {"wordstat": 5}
    assert due_sources(at(30, 12), runs, ["wordstat"], WEEK_DAYS, monthly_on=monthly) == []
    december = datetime(2026, 12, 5, 6, 30, tzinfo=TZ)
    assert due_sources(december, runs, ["wordstat"], WEEK_DAYS, monthly_on=monthly) == [("wordstat", date(2026, 11, 4))]


def test_month_close_before_report():
    report_day = datetime(2026, 12, 6, 7, 0, tzinfo=TZ)
    # последний понедельник ноября — 30-е: 30 ноября в базе ещё нет, перед отчётом его надо догрузить
    runs = {"metrika": {"last_ok": at(30, 6, 20), "last_attempt": at(30, 6, 5)}}
    assert month_close_sources(report_day, runs, ["metrika"], WEEK_DAYS) == [("metrika", date(2026, 11, 22))]
    closed = {"metrika": {"last_ok": datetime(2026, 12, 1, 7, 0, tzinfo=TZ), "last_attempt": None}}
    assert month_close_sources(report_day, closed, ["metrika"], WEEK_DAYS) == []
