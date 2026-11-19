"""Проверка одного адреса по всей цепочке «база UniSender → письма → заказы 1С → строка таблицы».

Нужна, когда дашборд относит клиента не туда, куда ожидаешь: показывает, на каком шаге адрес теряется. Печатает только
то, что относится к введённому адресу, — других адресов и клиентов не выводит.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import date

import psycopg

from analytics import insights as ins
from analytics import privacy
from analytics.dashboard import queries as q
from analytics.sources import crm_files

LEVELS = {0: "не доставлено", 1: "получил, не открыл", 2: "открыл", 3: "перешёл по ссылке"}
CLASS_TITLES = dict(ins.ORDER_CLASSES)


def consistency(conn: psycopg.Connection) -> dict:
    """Совпадают ли сохранённые отпечатки с самими адресами (они есть при EMAIL_LISTS=on). Расхождение значит, что
    данные загружались с разной солью DATA_SALT, — тогда клиенты не находятся в базе рассылок."""
    contacts = conn.execute("SELECT email_hash, email FROM email_contacts").fetchall()
    bad_contacts = sum(1 for h, e in contacts if privacy.email_hash(e) != h)
    orders = conn.execute("SELECT email_hashes, emails FROM orders_1c WHERE cardinality(emails) > 0").fetchall()
    bad_orders = sum(1 for hashes, emails in orders if any(privacy.email_hash(e) not in hashes for e in emails))
    return {"contacts": len(contacts), "bad_contacts": bad_contacts, "orders": len(orders), "bad_orders": bad_orders}


def trace_email(conn: psycopg.Connection, email: str, month: date, log: Callable[[str], None] = print) -> dict:
    found = privacy.emails(email)
    if not found:
        log(f"«{email}» — не похоже на адрес почты")
        return {}
    email = found[0]
    h = privacy.email_hash(email)
    salt = "своя (DATA_SALT задана)" if os.environ.get("DATA_SALT", "").strip() else "встроенная (DATA_SALT пуста)"
    log(f"Адрес {email} → отпечаток {h[:12]}… (соль: {salt})")

    base = conn.execute("SELECT status, list_ids FROM email_base WHERE email_hash = %s", (h,)).fetchall()
    titles = dict(conn.execute("SELECT list_id, title FROM unisender_lists").fetchall())
    if base:
        lists = sorted({titles.get(i, str(i)) for _, ids in base for i in ids})
        log(f"База UniSender: есть — статус {base[0][0] or '—'}, списки: {', '.join(lists) or '—'}")
    else:
        log("База UniSender: по этому отпечатку адреса нет")
        plain = conn.execute("SELECT email_hash FROM email_contacts WHERE email = %s", (email,)).fetchone()
        if plain:
            log(f"  но сам адрес в базе есть под другим отпечатком ({plain[0][:12]}…): данные UniSender загружены "
                "с другой солью DATA_SALT")

    letters = conn.execute("""
        SELECT c.start_time, c.subject, l.title, d.level
        FROM unisender_delivery d JOIN unisender_campaigns c USING (campaign_id)
        LEFT JOIN unisender_lists l ON l.list_id = c.list_id
        WHERE d.email_hash = %s ORDER BY c.start_time
    """, (h,)).fetchall()
    in_month = [x for x in letters if x[0] and month <= x[0].date() < q._next_month(month)]
    log(f"Письма UniSender (отчёты о доставке): всего {len(letters)}, за {month:%m.%Y} — {len(in_month)}"
        + (f", открыто {sum(1 for x in in_month if x[3] >= 2)}" if in_month else ""))
    for start, subject, title, level in in_month:
        log(f"  {start:%d.%m} «{(subject or '')[:60]}» ({title or 'список ?'}) — {LEVELS.get(level, level)}")

    orders = conn.execute("""
        SELECT order_no, order_date, amount, status, client_name, client_hash, %s = ANY (email_hashes) AS by_hash
        FROM orders_1c WHERE %s = ANY (email_hashes) OR %s = ANY (emails)
        ORDER BY order_date
    """, (h, h, email)).fetchall()
    stats = None
    month_orders = [o for o in orders if month <= o[1] < q._next_month(month)]
    if month_orders:
        rows = q.mail_orders(conn, month)
        ids = sorted({t["campaign_id"] for o in rows for t in o["touches"]})
        stats = ins.mail_orders_stats(rows, q.mail_campaigns(conn, ids), [], None, None)
    where = {no: key for c in (stats or {}).get("classes", []) for key in [c["key"]] for no in c.get("order_nos", [])}
    log(f"Заказы 1С с этим адресом: {len(orders)}, за {month:%m.%Y} — {len(month_orders)}")
    for no, day, amount, status, name, _client, by_hash in orders:
        line = f"  {no} {day:%d.%m.%Y} {amount:,.0f} ₽ {status or ''} {name or ''}".replace(",", " ")
        if not by_hash:
            line += " — адрес в заказе есть, но отпечаток другой: выгрузка 1С загружена с другой солью DATA_SALT"
        elif no in where:
            line += f" — в таблице: «{CLASS_TITLES.get(where[no], where[no])}»"
        log(line)
    if not orders:
        log("  ни в одном заказе этого адреса нет — проверьте, какая почта у клиента в выгрузке 1С")

    check = consistency(conn)
    if check["contacts"] or check["orders"]:
        log(f"Сверка отпечатков: база UniSender — {check['bad_contacts']} расхождений из {check['contacts']}, "
            f"заказы 1С — {check['bad_orders']} из {check['orders']}"
            + (" — соль менялась, нужна перезагрузка" if check["bad_contacts"] or check["bad_orders"] else " — всё сходится"))
    return {"hash": h, "in_base": bool(base), "letters": letters, "orders": orders, "classes": where, "check": check}


def _order_classes(conn: psycopg.Connection, month: date) -> dict[str, str]:
    rows = q.mail_orders(conn, month)
    ids = sorted({t["campaign_id"] for o in rows for t in o["touches"]})
    stats = ins.mail_orders_stats(rows, q.mail_campaigns(conn, ids), [], None, None)
    return {no: c["key"] for c in stats["classes"] for no in c.get("order_nos", [])}


def trace_client(conn: psycopg.Connection, name: str, month: date, log: Callable[[str], None] = print) -> list:
    """Клиент по части названия из 1С: какие почта и телефоны у его заказов, есть ли они в базе UniSender и в отчётах
    о доставке, в какую строку таблицы попал каждый заказ месяца. Названия и контакты есть только при EMAIL_LISTS=on."""
    if not conn.execute("SELECT 1 FROM orders_1c WHERE client_name IS NOT NULL LIMIT 1").fetchone():
        log("Названия клиентов из 1С не сохранены: нужна строка EMAIL_LISTS=on в .env (и пересборка) — выгрузки "
            "перечитаются с контактами. Пока можно проверить по адресу: trace-email адрес@почта")
        return []
    orders = conn.execute("""
        SELECT order_no, order_date, amount, status, client_name, emails, phones
        FROM orders_1c WHERE client_name ILIKE %s ORDER BY client_name, order_date
    """, (f"%{name.strip()}%",)).fetchall()
    if not orders:
        log(f"Клиентов с «{name}» в названии в выгрузках 1С нет")
        return []
    base_emails = {h for (h,) in conn.execute("SELECT email_hash FROM email_base WHERE email_hash IS NOT NULL")}
    base_phones = {h for (h,) in conn.execute("SELECT phone_hash FROM email_base WHERE phone_hash IS NOT NULL")}
    sent = {h for (h,) in conn.execute("SELECT DISTINCT email_hash FROM unisender_delivery")}
    where = _order_classes(conn, month) if any(month <= o[1] < q._next_month(month) for o in orders) else {}
    log(f"Заказы 1С клиентов с «{name}» в названии: {len(orders)}")
    for no, day, amount, status, client, emails, phones in orders:
        line = f"  {no} {day:%d.%m.%Y} {amount:,.0f} ₽ {status or ''} {client}".replace(",", " ")
        if no in where:
            line += f" — в таблице за {month:%m.%Y}: «{CLASS_TITLES.get(where[no], where[no])}»"
        log(line)
        for e in emails or []:
            h = privacy.email_hash(e)
            state = "есть в базе UniSender" if h in base_emails else "письма получал" if h in sent else "нет в базе UniSender"
            log(f"      почта {e} — {state}")
        if not emails:
            log("      почты в выгрузке нет")
        for p in phones or []:
            state = "есть в базе UniSender" if privacy.phone_hash(p) in base_phones else (
                "в базе UniSender телефонов нет" if not base_phones else "нет в базе UniSender")
            log(f"      телефон +7{p} — {state}")
    # компании из справочника Б24 с этим названием: их почты и телефоны тоже считаются контактами клиента
    keys = {crm_files.company_key(o[4]) for o in orders} - {None}
    companies = conn.execute("SELECT name, emails, phones FROM companies WHERE company_key = ANY (%s) ORDER BY name",
                             (sorted(keys),)).fetchall() if keys else []
    if conn.execute("SELECT 1 FROM companies LIMIT 1").fetchone():
        log(f"Справочник компаний Б24: найдено по названию — {len(companies)}")
        for cname, emails, phones in companies:
            log(f"  {cname or 'название не сохранено'}")
            for e in emails or []:
                h = privacy.email_hash(e)
                state = "есть в базе UniSender" if h in base_emails else "письма получал" if h in sent else "нет в базе UniSender"
                log(f"      почта {e} — {state}")
            for p in phones or []:
                log(f"      телефон +7{p}")
    else:
        log("Справочника компаний Б24 в imports нет — по нему клиента тоже можно связать с его почтой")
    return orders
