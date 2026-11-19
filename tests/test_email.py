"""Канал e-mail: отпечатки, клиент UniSender, связь рассылок с Метрикой и базы с лидами."""

import os
from datetime import date, datetime

import pytest

from analytics import db, privacy, topics
from analytics import insights as ins
from analytics.dashboard import queries as q
from analytics.sources.unisender import (
    Link,
    UnisenderClient,
    UnisenderError,
    delivery_level,
    extract_links,
    parse_contacts,
    parse_delivery,
    table_rows,
)
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
    client = UnisenderClient("key", session=session, backoff=0, pause=0, limit_wait=0)
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
        UnisenderClient("bad", session=session, backoff=0, pause=0, limit_wait=0).lists()
    with pytest.raises(UnisenderError):
        UnisenderClient("")


LIMIT = {"error": "Error ID:7CE5. OB20142205-02 [Api call limit exceeded for ip: limit 20 calls 329]"}


def test_rate_limit_waits_and_retries():
    waits = []
    session = FakeSession([FakeResponse(200, LIMIT), FakeResponse(200, LIMIT), ok([{"id": 1, "title": "Оптовики"}])])
    client = UnisenderClient("key", session=session, backoff=0, pause=0, limit_wait=0, log=waits.append)
    assert client.lists() == {1: "Оптовики"}
    assert len(session.calls) == 3 and len(waits) == 2 and "лимит" in waits[0]


def test_rate_limit_gives_up_with_explanation():
    session = FakeSession([FakeResponse(200, LIMIT)] * 3)
    client = UnisenderClient("key", session=session, backoff=0, pause=0, limit_wait=0, limit_retries=2,
                             log=lambda _: None)
    with pytest.raises(UnisenderError, match="повторится сама"):
        client.lists()
    assert len(session.calls) == 3


class FakeUnisender:
    def lists(self):
        return {1: "Оптовики"}

    def campaigns(self, since, until):
        from analytics.sources.unisender import parse_campaign
        return [parse_campaign({"id": 101, "start_time": "2026-09-10 10:00:00", "list_id": 1, "subject": "Мембраны",
                                "message_id": 7}),
                parse_campaign({"id": 102, "start_time": "2026-09-20 10:00:00", "list_id": 1, "subject": "Флис"})]

    def stats(self, campaign_id):
        from analytics.sources.unisender import parse_stats
        return parse_stats(campaign_id, {"sent": 1000, "delivered": 900, "read_unique": 200, "clicked_unique": 40,
                                         "unsubscribed": 9})

    def export_contacts(self, keep_emails=False):
        return parse_contacts(CSV, keep_emails)

    def delivery(self, campaign_id, keep_emails=False):
        rows = {101: [{"email": "buyer@example.ru", "send_result": "ok_link_visited"},
                      {"email": "other@example.ru", "send_result": "ok_delivered"},
                      {"email": "third@example.ru", "send_result": "ok_read"}],
                102: [{"email": "buyer@example.ru", "send_result": "ok_read"},
                      {"email": "other@example.ru", "send_result": "err_user_unknown"}]}[campaign_id]
        return parse_delivery(rows, keep_emails)

    def message_links(self, message_id):
        return [Link("https://oldos.ru/catalog/membrannye_tkani/", "Мембраны")]


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

        # отчёт о доставке по адресам и ссылки письма
        assert conn.execute("SELECT count(*) FROM unisender_delivery").fetchone()[0] == 5
        assert conn.execute("SELECT url FROM unisender_links WHERE campaign_id = 101").fetchone()[0].endswith("tkani/")
        assert q.email_base(conn)["contacts"] == 3

        # заказы 1С: покупатель (почта в другом регистре) купил мембрану через 5 дней после письма, по которому кликнул;
        # второй клиент из базы (найден по телефону) письмо не открывал; третий — не из базы
        orders = [
            ("З-1", date(2026, 9, 15), 50000, "Закрыт", "c1", [privacy.email_hash("BUYER@example.ru")], [],
             ["Мембрана 3-слойная"], ["Мембранные ткани"]),
            ("З-2", date(2026, 9, 18), 7000, "Закрыт", "c2", [], [privacy.phone_hash("89990001122")], ["Флис 280"], []),
            ("З-3", date(2026, 9, 25), 9000, "Отменен", "c3", [], [], [], []),
            ("З-4", date(2026, 9, 26), 3000, "Закрыт", "c4", [privacy.email_hash("new@client.ru")], [], ["Флис"], []),
        ]
        conn.cursor().executemany("INSERT INTO orders_1c (order_no, order_date, amount, status, client_hash, email_hashes,"
                         " phone_hashes, items, groups) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", orders)
        rows = q.mail_orders(conn, sep)
        assert [(r["order_no"], r["in_base"], sorted((t["campaign_id"], t["level"]) for t in r["touches"])) for r in rows] == [
            ("З-1", True, [(101, 3)]), ("З-2", True, []), ("З-4", False, [])]
        lift = q.mail_lift(conn, sep)
        assert [(r["campaign_id"], r["delivered"], r["opened"], r["clicked"], r["opened_ordered"], r["unopened_ordered"])
                for r in lift] == [(101, 3, 2, 1, 1, 0), (102, 1, 1, 0, 0, 0)]
        ids = sorted({t["campaign_id"] for r in rows for t in r["touches"]} | {r["campaign_id"] for r in lift})
        cov = q.orders_coverage(conn)
        m = ins.mail_orders_stats(rows, q.mail_campaigns(conn, ids), lift, cov["first"], cov["last"])
        classes = {c["key"]: (c["orders"], c["amount"]) for c in m["classes"]}
        assert classes["topic_click"] == (1, 50000.0) and classes["no_mail"] == (1, 7000.0)
        assert classes["outside"] == (1, 3000.0) and m["orders"] == 3 and m["base_orders"] == 2
        assert m["examples"][0]["order_no"] == "З-1" and m["examples"][0]["days"] == 5
        assert m["campaigns"][0]["orders"] == 1 and m["campaigns"][0]["topic"] == 1
        # у рассылки 101 окно закрыто (заказы есть по 26.09), у 102 — нет
        assert m["lift"]["campaigns"] == 1 and m["lift"]["pending"] == 1
        texts = " ".join(x.text for x in ins.email_findings(s, None, campaigns, m)).replace("\xa0", " ")
        assert "товар из письма — 1 заказ" in texts
        assert m["uncovered"] == []  # мембраны и флис — темы писем этого месяца


def test_delivery_report_and_links():
    rows = table_rows({"fields": ["email", "send_result", "last_update"],
                       "data": [["A@b.ru", "ok_read", "2026-09-10"], ["a@b.ru", "ok_link_visited", "x"],
                                ["c@d.ru", "err_user_unknown", "x"], ["e@f.ru", "ok_unsubscribed", "x"]]})
    by_level = sorted((d.status, d.level) for d in parse_delivery(rows))
    assert by_level == [("err_user_unknown", 0), ("ok_link_visited", 3), ("ok_unsubscribed", 2)]
    assert delivery_level("ok_delivered") == 1 and delivery_level("not_sent") == 0
    html = ('<p>Новинки</p><a href="https://oldos.ru/catalog/flis/?utm_source=Unisender&amp;utm_medium=email&amp;x=1">'
            '<b>Флис</b> 280</a> <a href="https://unisender.com/unsubscribe?x">Отписаться</a>'
            '<a href="mailto:a@b.ru">почта</a><a href="https://vk.com/oldos">VK</a>')
    assert extract_links(html) == [Link("https://oldos.ru/catalog/flis/?x=1", "Флис 280")]


def test_delivery_via_async_task_file():
    csv_text = "email,send_result,last_update\nbuyer@example.ru,ok_read,2026-09-10\n"
    session = FakeSession([ok({"task_uuid": "t1", "status": "new"}),
                           ok({"task_uuid": "t1", "status": "completed", "file_to_download": "https://x/file.csv"}),
                           FakeResponse(200, text=csv_text)])
    client = UnisenderClient("key", session=session, backoff=0, pause=0, limit_wait=0)
    [d] = client.delivery(101, wait=0)
    assert d.email_hash == privacy.email_hash("buyer@example.ru") and d.level == 2
    assert session.calls[0][0].endswith("async/getCampaignDeliveryStats") and session.calls[0][1]["campaign_id"] == 101


def test_topics_match_letter_and_order():
    letter = topics.campaign_terms("Новые мембраны и флис со скидкой",
                                   [("https://oldos.ru/catalog/membrannye_tkani/", "Мембранные ткани"),
                                    ("https://oldos.ru/catalog/oksford_600d/", "Оксфорд")])
    order = topics.order_terms(["Ткань Оксфорд 600D PU1000 черный"], ["Ткани для одежды"])
    assert letter & order == {"оксфо"}
    assert not topics.stems("Ткань черная, купить оптом в каталоге")  # общие слова не в счёт


@needs_db
def test_shared_client_does_not_borrow_contacts():
    """«Клиент» с множеством разных адресов (общая карточка) — его заказы не сводятся друг с другом."""
    with db.connect(TEST_DATABASE_URL) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        db.apply_schema(conn)
        sep = date(2026, 9, 1)
        sync_unisender(conn, FakeUnisender(), sep, date(2026, 9, 30), log=lambda _: None)
        emails = ["buyer@example.ru"] + [f"x{i}@example.ru" for i in range(5)]
        rows = [(f"Р-{i}", date(2026, 9, 12), 100, "Закрыт", "shared", [privacy.email_hash(e)], [], [], [])
                for i, e in enumerate(emails)]
        rows.append(("Р-без-почты", date(2026, 9, 12), 100, "Закрыт", "shared", [], [], [], []))
        conn.cursor().executemany("INSERT INTO orders_1c (order_no, order_date, amount, status, client_hash,"
                                  " email_hashes, phone_hashes, items, groups) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                                  rows)
        touched = {r["order_no"]: [t["campaign_id"] for t in r["touches"]] for r in q.mail_orders(conn, sep)}
        assert touched["Р-0"] == [101] and touched["Р-без-почты"] == []


@needs_db
def test_addresses_are_kept_only_with_email_lists():
    with db.connect(TEST_DATABASE_URL) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        db.apply_schema(conn)
        sep = date(2026, 9, 1)
        sync_unisender(conn, FakeUnisender(), sep, date(2026, 9, 30), log=lambda _: None, keep_emails=True)
        emails = {e for (e,) in conn.execute("SELECT email FROM email_contacts")}
        assert {"buyer@example.ru", "third@example.ru"} <= emails
        conn.execute("DELETE FROM unisender_recipients_runs")
        sync_unisender(conn, FakeUnisender(), sep, date(2026, 9, 30), log=lambda _: None)
        assert conn.execute("SELECT count(*) FROM email_contacts").fetchone()[0] == 0


@needs_db
def test_trace_email_follows_one_address(tmp_path, monkeypatch):
    """Клиент из базы, открывший письмо, не попадает в «нет в базе рассылок»; trace-email показывает каждый шаг,
    а смену соли DATA_SALT между загрузками — находит."""
    import openpyxl

    from analytics.sources.unisender import Contact, Delivery, parse_campaign
    from analytics.sync import import_crm_folder
    from analytics.trace import trace_email

    oct_ = date(2026, 10, 1)
    with db.connect(TEST_DATABASE_URL) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        db.apply_schema(conn)
        addr = "ryba-aist@inbox.ru"
        db.save_unisender(conn, {1: "Оптовики"}, [parse_campaign({"id": 401, "start_time": "2026-10-14 10:00:00",
                                                                  "list_id": 1, "subject": "Новинки"})], [])
        db.replace_email_base(conn, [Contact(privacy.email_hash(addr), None, "active", (1,))])
        db.save_recipients(conn, 401, [Delivery(privacy.email_hash(addr), "ok_read", 2)], [])
        db.save_email_contacts(conn, [(privacy.email_hash(addr), addr)])
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Дата", "Клиент", "Контрагент", "Номер", "Сумма", "Текущее состояние", "Электронная почта", "Телефон"])
        ws.append(["20.10.2026", "Карагод ИП", "Карагод ИП", "АТЦБ-026900", 15000, "Закрыт", "E-mail=Ryba-Aist@inbox.ru",
                   "Представление=8 900 111 22 33"])
        ws.append(["03.10.2026", "Карагод ИП", "Карагод ИП", "АТЦБ-026100", 5000, "Закрыт", "", ""])
        wb.save(tmp_path / "orders.xls".replace(".xls", ".xlsx"))
        import_crm_folder(conn, tmp_path, log=lambda _: None, keep_contacts=True)

        rows = q.mail_orders(conn, oct_)
        ids = sorted({t["campaign_id"] for o in rows for t in o["touches"]})
        stats = ins.mail_orders_stats(rows, q.mail_campaigns(conn, ids), [], None, None)
        where = {no: c["key"] for c in stats["classes"] for no in c.get("order_nos", [])}
        # заказ через 6 дней после открытого письма — «открыли письмо»; ранний заказ того же клиента без почты —
        # «в базе, писем не было», а не «нет в базе»
        assert where == {"АТЦБ-026900": "open_other", "АТЦБ-026100": "no_mail"}

        out = []
        result = trace_email(conn, "  RYBA-AIST@inbox.ru ", oct_, log=out.append)
        text = "\n".join(out)
        assert result["in_base"] and "статус active, списки: Оптовики" in text and "14.10 «Новинки» (Оптовики) — открыл" in text
        assert "АТЦБ-026900 20.10.2026 15 000 ₽ Закрыт Карагод ИП — в таблице: «Открыли письмо за 14 дней до заказа" in text
        assert "расхождений" in text and "всё сходится" in text

        # соль поменяли после загрузки UniSender: отпечатки базы больше не совпадают — trace это находит
        monkeypatch.setenv("DATA_SALT", "другая-соль")
        out.clear()
        result = trace_email(conn, addr, oct_, log=out.append)
        text = "\n".join(out)
        assert not result["in_base"] and "под другим отпечатком" in text and "другой солью" in text
        assert "соль менялась" in text
