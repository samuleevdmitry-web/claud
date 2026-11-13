"""Канал e-mail: отпечатки, клиент UniSender, связь рассылок с Метрикой и базы с лидами."""

import os
from datetime import date, datetime

import pytest

from analytics import db, privacy
from analytics import insights as ins
from analytics.dashboard import queries as q
from analytics.sources.unisender import UnisenderClient, UnisenderError, parse_contacts
from analytics.sync import sync_metrika, sync_unisender
from tests.fakes import FakeMetrikaClient, FakeResponse, FakeSession, dim

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL не задан")

CSV = ("email,email_status,email_list_ids,phone\n"
       "Buyer@Example.ru,active,\"1,2\",+7 (917) 470-64-44\n"
       "other@example.ru,unsubscribed,1,\n"
       ",active,2,89990001122\n")


def test_fingerprints_ignore_formatting():
    assert privacy.email_hash(" Buyer@Example.RU ") == privacy.email_hash("buyer@example.ru")
    assert privacy.phone_hash("+7 (917) 470-64-44") == privacy.phone_hash("89174706444") == privacy.phone_hash("9174706444")
    assert privacy.email_hash("не почта") is None and privacy.phone_hash("123") is None
    assert privacy.email_hashes("a@b.ru, c@d.ru; a@b.ru") == [privacy.email_hash("a@b.ru"), privacy.email_hash("c@d.ru")]
    assert "buyer" not in privacy.email_hash("buyer@example.ru")


def test_contacts_keep_only_fingerprints():
    contacts = parse_contacts("﻿" + CSV)
    assert len(contacts) == 3
    first = contacts[0]
    assert first.email_hash == privacy.email_hash("buyer@example.ru") and first.list_ids == (1, 2)
    assert first.phone_hash == privacy.phone_hash("9174706444") and first.status == "active"
    assert "buyer@example.ru" not in repr(contacts).lower() and "9174706444" not in repr(contacts)


def ok(result):
    return FakeResponse(200, {"result": result})


def test_client_reads_campaigns_stats_and_base():
    session = FakeSession([
        ok([{"id": 1, "title": "Оптовики"}]),
        ok([{"id": 373194938, "start_time": "2026-09-10 10:00:00", "status": "completed", "list_id": 1,
             "message_id": 55, "subject": "Новые мембраны"}]),
        ok({"total": 1000, "sent": 1000, "delivered": 960, "read_unique": 240, "read_all": 300, "clicked_unique": 30,
            "clicked_all": 41, "unsubscribed": 6, "spam": 0}),
        ok({"task_uuid": "abc", "status": "new"}),
        ok({"task_uuid": "abc", "status": "processing"}),
        ok({"task_uuid": "abc", "status": "completed", "file_to_download": "https://api.unisender.com/file.csv"}),
        FakeResponse(200, text=CSV),
    ])
    client = UnisenderClient("key", session=session, backoff=0, pause=0)
    assert client.lists() == {1: "Оптовики"}
    [campaign] = client.campaigns(date(2026, 9, 1), date(2026, 9, 30))
    assert (campaign.campaign_id, campaign.start_time, campaign.subject) == (373194938, datetime(2026, 9, 10, 10), "Новые мембраны")
    stats = client.stats(373194938)
    assert (stats.delivered, stats.read_unique, stats.clicked_unique, stats.unsubscribed) == (960, 240, 30, 6)
    assert len(client.export_contacts(wait=0)) == 3
    assert session.calls[0][1]["api_key"] == "key" and session.calls[1][1]["from"] == "2026-09-01 00:00:00"


def test_api_error_is_explained():
    session = FakeSession([FakeResponse(200, {"error": "invalid api_key", "code": "invalid_api_key"})])
    with pytest.raises(UnisenderError, match="проверьте ключ"):
        UnisenderClient("bad", session=session, backoff=0, pause=0).lists()
    with pytest.raises(UnisenderError):
        UnisenderClient("")


class FakeUnisender:
    def lists(self):
        return {1: "Оптовики"}

    def campaigns(self, since, until):
        from analytics.sources.unisender import parse_campaign
        return [parse_campaign({"id": 101, "start_time": "2026-09-10 10:00:00", "list_id": 1, "subject": "Мембраны"}),
                parse_campaign({"id": 102, "start_time": "2026-09-20 10:00:00", "list_id": 1, "subject": "Флис"})]

    def stats(self, campaign_id):
        from analytics.sources.unisender import parse_stats
        return parse_stats(campaign_id, {"sent": 1000, "delivered": 900, "read_unique": 200, "clicked_unique": 40,
                                         "unsubscribed": 9})

    def export_contacts(self):
        return parse_contacts(CSV)


@needs_db
def test_email_channel_and_base():
    with db.connect(TEST_DATABASE_URL) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        db.apply_schema(conn)
        sep = date(2026, 9, 1)
        goals = [{"id": 1, "name": "Отправил контактные данные", "type": "contact_data_sent"}]
        email_utm = [dim("Unisender"), dim("email"), dim("101")]
        data = {
            ("month", "total"): [(sep, [], [1000, 800, 2500, 20.0, 2.5, 150.0], {1: (10, 10)})],
            ("month", "source"): [(sep, [dim("Переходы с почтовых рассылок", "email")], [120, 100, 300, 15.0, 2.5, 150.0],
                                   {1: (3, 3)})],
            ("day", "utm"): [(date(2026, 9, 10), email_utm, [80, 70, 200, 15.0, 2.5, 150.0], {1: (2, 2)}),
                             (date(2026, 9, 11), email_utm, [20, 20, 50, 15.0, 2.5, 150.0], {})],
        }
        sync_metrika(conn, FakeMetrikaClient(goals, data), 42, frozenset({1}), "lastsign", since=sep,
                     until=date(2026, 9, 30), today=date(2026, 10, 2), log=lambda _: None)
        sync_unisender(conn, FakeUnisender(), sep, date(2026, 9, 30), log=lambda _: None)

        campaigns = q.email_campaigns(conn, 42, "all", sep)
        assert [(c["campaign_id"], c["delivered"], c["visits"], c["leads"]) for c in campaigns] == [
            (101, 900, 100, 2), (102, 900, 0, 0)]
        s = ins.email_stats(campaigns, {"visits": 120, "leads": 3})
        assert (s["campaigns"], s["delivered"], round(s["open_rate"], 1), round(s["unsub_rate"], 2)) == (2, 1800, 22.2, 1.0)
        assert "Снизить отписки" in [r.title for r in ins.email_recommend(s, campaigns, None)]

        # лиды месяца: один — подписчик (почта в другом регистре), второй — нет
        conn.execute("INSERT INTO crm_form_leads (lead_id, month, created_at, stage, in_1c, orders, amount, email_hashes)"
                     " VALUES (1, %s, %s, 'Перевести в сделку', true, 1, 50000, %s),"
                     " (2, %s, %s, 'Перевести в сделку', false, 0, 0, %s)",
                     (sep, datetime(2026, 9, 12), [privacy.email_hash("BUYER@example.ru")],
                      sep, datetime(2026, 9, 13), [privacy.email_hash("new@client.ru")]))
        conn.execute("INSERT INTO crm_call_leads (lead_id, month, call_type, stage, in_1c, phone_hash)"
                     " VALUES (10, %s, 'Входящий', 'Перевести в сделку', false, %s)", (sep, privacy.phone_hash("89990001122")))
        leads = q.base_leads(conn, sep)
        base = ins.base_stats(leads["forms"], leads["calls"], [])
        assert (base["forms_base"]["leads"], base["forms_base"]["sold"], base["forms_other"]["leads"]) == (1, 1, 1)
        assert base["calls_base"] == 1
        assert q.email_base(conn)["contacts"] == 3
