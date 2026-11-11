from datetime import date, datetime, timedelta

from analytics.schedule import HISTORY_START, TZ, due_sources

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
