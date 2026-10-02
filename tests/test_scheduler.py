"""Расписание прогонов и внешние уведомления."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.services.scheduler as sched
from app.config import get_settings
from app.db import Base
from app.services.notify import EmailNotifier, TelegramNotifier, send_external
from app.services.scheduler import Schedule, build_trigger, first_run_at, get_schedule, save_schedule

MSK = ZoneInfo("Europe/Moscow")


def test_first_run_is_next_start_time():
    s = Schedule(48, "06:00")
    assert first_run_at(s, datetime(2026, 10, 2, 5, 0, tzinfo=MSK)) == datetime(2026, 10, 2, 6, 0, tzinfo=MSK)
    assert first_run_at(s, datetime(2026, 10, 2, 7, 0, tzinfo=MSK)) == datetime(2026, 10, 3, 6, 0, tzinfo=MSK)


def test_interval_trigger_keeps_48h_across_month_boundary():
    trigger = build_trigger(Schedule(48, "06:00"), datetime(2026, 10, 29, 7, 0, tzinfo=MSK))
    fires, prev = [], None
    now = datetime(2026, 10, 29, 7, 0, tzinfo=MSK)
    for _ in range(4):
        nxt = trigger.get_next_fire_time(prev, now)
        fires.append(nxt.astimezone(MSK).strftime("%d.%m %H:%M"))
        prev, now = nxt, nxt
    # 30.10 → 01.11 → 03.11: шаг ровно 48 часов, без сбоя на границе месяца (как у cron */2)
    assert fires == ["30.10 06:00", "01.11 06:00", "03.11 06:00", "05.11 06:00"]


def test_schedule_settings_override_env():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        assert get_schedule(s).interval_hours == get_settings().run_interval_hours
        save_schedule(s, Schedule(24, "07:30", enabled=False))
        s.commit()
        assert get_schedule(s) == Schedule(24, "07:30", enabled=False)
        with pytest.raises(ValueError):
            save_schedule(s, Schedule(24, "7 утра"))


def test_scheduler_persists_job_and_reschedules(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'jobs.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    engine = create_engine(url)
    Base.metadata.create_all(engine)

    async def scenario():
        with Session(engine) as s:
            sched.start_scheduler(get_schedule(s))
            job = sched._scheduler.get_job(sched.JOB_ID)
            assert job is not None and job.trigger.interval == timedelta(hours=48)
            first = sched.next_run_time()
            sched.shutdown_scheduler()

            # после перезапуска задание берётся из БД и не сдвигается
            sched.start_scheduler(get_schedule(s))
            assert sched.next_run_time() == first

            sched.apply_schedule(Schedule(12, "06:00"))
            assert sched._scheduler.get_job(sched.JOB_ID).trigger.interval == timedelta(hours=12)
            sched.apply_schedule(Schedule(12, "06:00", enabled=False))
            assert sched._scheduler.get_job(sched.JOB_ID) is None
            sched.shutdown_scheduler()

    try:
        asyncio.run(scenario())
    finally:
        sched.shutdown_scheduler()
        get_settings.cache_clear()


def test_scheduled_run_skips_when_run_is_active(monkeypatch):
    calls = []
    monkeypatch.setattr("app.services.jobs.launch_run", lambda trigger: calls.append(trigger) or False)
    asyncio.run(sched.scheduled_run())
    assert calls == ["schedule"]


# --------------------------------------------------------------------------- уведомления


def test_telegram_notifier_posts_message():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True})

    TelegramNotifier("TOKEN", "42", transport=httpx.MockTransport(handler)).send("Прогон 03.10", "детали")
    assert seen["url"] == "https://api.telegram.org/botTOKEN/sendMessage"
    assert seen["body"]["chat_id"] == "42" and "Прогон 03.10" in seen["body"]["text"]


def test_email_notifier(monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            sent.append("tls")

        def login(self, user, password):
            sent.append(("login", user))

        def send_message(self, msg):
            sent.append(("msg", msg["Subject"], msg["To"]))

    monkeypatch.setattr("app.services.notify.smtplib.SMTP", FakeSMTP)
    EmailNotifier("smtp.test", 587, "u", "p", "from@test", ["a@test", "b@test"]).send("Тема", "Текст")
    assert ("msg", "Тема", "a@test, b@test") in sent and "tls" in sent


def test_send_external_survives_failing_channel():
    class Broken:
        name = "broken"

        def send(self, title, body=""):
            raise RuntimeError("нет сети")

    class Ok:
        name = "ok"
        got = []

        def send(self, title, body=""):
            self.got.append((title, body))

    ok = Ok()
    send_external([("Прогон", ""), ("Площадка X не проверена", "капча")], [Broken(), ok])
    assert ok.got == [("Прогон", "Площадка X не проверена\nкапча")]


def test_run_sends_external_summary(monkeypatch, keywords_file):
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.services.keywords import activate_keyword_file
    from app.services.runner import run_all
    from app.sources.base import TenderDetails, TenderStub

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as s:
        activate_keyword_file(s, keywords_file.read_bytes(), keywords_file.name)
        s.commit()

    d = TenderDetails(
        "onlinecontract",
        "1",
        "u",
        "Закупка тапочек одноразовых",
        application_deadline=datetime.now(UTC) + timedelta(days=5),
    )

    class Adapter:
        code, title, supports_codes = "onlinecontract", "x", False

        async def search(self, *a):
            yield TenderStub("onlinecontract", "1", "u", d.title, application_deadline=d.application_deadline)

        async def fetch_details(self, stub):
            return d

    sent = []
    monkeypatch.setattr("app.services.runner.send_external", lambda messages: sent.append(messages))
    asyncio.run(run_all(factory, adapters={"onlinecontract": Adapter()}, queries=["тапочки"]))
    assert sent and "найдено 1 новых (1 релевантных" in sent[0][0][0]
