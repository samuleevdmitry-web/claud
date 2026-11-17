"""Выгрузки Битрикс24 и 1С: разбор Excel и загрузка из папки — на синтетических файлах."""

import os
from datetime import date, datetime

import openpyxl
import pytest

from analytics import db
from analytics import insights as ins
from analytics.sources import crm_files
from analytics.sources.crm_files import CrmFileError, parse_file
from analytics.sync import import_crm_folder

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL не задан")


def forms_file(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Совпадения"
    for row in [
        ["Итог: лиды с сайта в Б24 (01–31.10.2026) vs заказы в 1С"],
        ["Лидов в Б24", 4], ["Нашлись в 1С с заказом после лида", 1], ["Конверсия от всех лидов", 0.25], [],
        ["Лиды, по которым есть заказы"],
        ["ID лида", "Дата лида", "Стадия лида", "Имя / компания (Б24)", "Клиент 1С", "Надёжность", "Совпадение по",
         "Заказов после лида", "Сумма заказов, ₽", "Закрыт / к закрытию, ₽", "Первый заказ", "Номера заказов 1С",
         "Статусы заказов", "Менеджер 1С", "Название лида"],
        [101, datetime(2026, 10, 5), "Перевести в сделку", "Иван", "ИП Иванов", "точно (email/телефон)", "email",
         2, 50000, 30000, datetime(2026, 10, 6), "А-1, А-2", "Закрыт: 1", "Петров", "заказ"],
    ]:
        ws.append(row)
    leads = wb.create_sheet("Все лиды")
    leads.append(["ID лида", "Дата лида", "Стадия лида", "Название лида", "Имя / компания", "Email", "Телефон",
                  "Найден в 1С", "Клиент 1С", "Сумма заказов в 1С"])
    for lead_id, day, stage, found in [(101, 5, "Перевести в сделку", "да"), (102, 7, "Нецелевое обращение", "нет"),
                                       (103, 9, "Заявки с сайта", "нет"), (104, 30, "Перевести в сделку", "нет")]:
        leads.append([lead_id, datetime(2026, 10, day, 12), stage, "т", "имя", "a@b.c", "900", found, "", 0])
    wb.save(path)


def calls_file(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Итог"
    ws.append(["Продажи с входящих звонков"])
    ws.append([])
    ws.append(["Показатель", "Значение", "Комментарий"])
    ws.append(["Лидов в выгрузке звонков", 5, "все лиды файла"])
    orders = wb.create_sheet("Заказы со звонков")
    orders.append(["Категория", "Клиент 1С", "Номер заказа", "Дата заказа", "Сумма, ₽", "Статус 1С", "ID лида",
                   "Менеджер 1С"])
    orders.append(["Новая продажа после звонка", "ООО", "Б-1", datetime(2026, 10, 12), 1000, "Закрыт", 201, "Петров"])
    orders.append(["Действующий клиент (заказы были и до звонка)", "ООО", "Б-2", datetime(2026, 10, 14), 500,
                   "Ожидается оплата", 202, "Петров"])
    orders.append(["", "Итого", "", "", 1500])
    calls = wb.create_sheet("Все входящие лиды")
    calls.append(["ID лида", "Дата звонка", "Тип звонка", "Стадия лида", "Ответственный Б24", "Линия (куда звонили)",
                  "Телефон", "Найден в 1С", "Клиент 1С"])
    for lead_id, kind, stage in [(201, "Входящий", "Перевести в сделку"), (202, "Входящий", "Перевести в сделку"),
                                 (203, "Пропущенный входящий", "Не обработан"), (204, "Пропущенный входящий", "Не обработан"),
                                 (205, "Входящий", "Нецелевое обращение")]:
        calls.append([lead_id, datetime(2026, 10, 10, 9) if lead_id != 205 else None, kind, stage, "Анна", "+7900",
                      "9000000000", "да" if lead_id < 203 else "нет", ""])
    wb.save(path)


def test_parse_forms_and_calls(tmp_path):
    forms_file(tmp_path / "forms.xlsx")
    calls_file(tmp_path / "calls.xlsx")
    forms = parse_file(tmp_path / "forms.xlsx")
    assert forms.kind == "forms" and forms.month == date(2026, 10, 1)
    assert ("Лидов в Б24", 4.0, None) in forms.summary
    lead = next(x for x in forms.form_leads if x.lead_id == 101)
    assert (lead.in_1c, lead.orders, lead.amount, lead.closed, lead.match) == (True, 2, 50000.0, 30000.0, "точно")
    calls = parse_file(tmp_path / "calls.xlsx")
    assert calls.kind == "calls" and len(calls.call_leads) == 5  # звонок без даты не теряется
    assert [(o.order_no, o.category) for o in calls.call_orders] == [("Б-1", "new"), ("Б-2", "existing")]
    # персональные данные не читаются вовсе
    assert not any("a@b.c" in str(x) or "9000000000" in str(x) for x in forms.form_leads + calls.call_leads)


def test_unknown_file_is_explained(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.title = "Лист1"
    wb.save(tmp_path / "x.xlsx")
    with pytest.raises(CrmFileError, match="Лист1"):
        parse_file(tmp_path / "x.xlsx")


def test_crm_stats_findings_and_recommendations(tmp_path):
    forms_file(tmp_path / "forms.xlsx")
    calls_file(tmp_path / "calls.xlsx")
    as_rows = lambda items: [vars(x) | {"category": getattr(x, "category", None)} for x in items]  # noqa: E731
    f, c = parse_file(tmp_path / "forms.xlsx"), parse_file(tmp_path / "calls.xlsx")
    s = ins.crm_stats([x.__dict__ for x in f.form_leads], [x.__dict__ for x in c.call_leads],
                      as_rows(c.call_orders))
    assert (s["forms"], s["forms_target"], s["forms_in_1c"], s["forms_unprocessed"]) == (4, 3, 1, 1)
    assert (s["calls"], s["calls_missed"], s["calls_unprocessed"], s["new_sales"], s["new_amount"]) == (5, 2, 2, 1, 1000.0)
    # действующие клиенты в продажи не входят: заказ Б-2 на 500 ₽ не учтён нигде
    assert (s["clients_total"], s["orders_total"], s["amount_total"]) == (2, 3, 51000.0)
    titles = [r.title for r in ins.crm_recommend(s)]
    assert titles == ["Перезванивать пропущенные звонки", "Разбирать лиды до конца"]
    kinds = [x.kind for x in ins.crm_findings(s, None, 10)]
    assert "minus" in kinds and "plus" in kinds


@needs_db
def test_import_folder_once(tmp_path):
    forms_file(tmp_path / "forms.xlsx")
    calls_file(tmp_path / "calls.xlsx")
    (tmp_path / "~$forms.xlsx").write_bytes(b"lock")  # временный файл открытого Excel — пропускается
    with db.connect(TEST_DATABASE_URL) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        db.apply_schema(conn)
        loaded = import_crm_folder(conn, tmp_path, log=lambda _: None)
        assert sorted(p.kind for p in loaded) == ["calls", "forms"]
        assert import_crm_folder(conn, tmp_path, log=lambda _: None) == []  # уже загружены
        assert conn.execute("SELECT count(*), sum(amount) FROM crm_form_leads").fetchone() == (4, 50000)
        assert conn.execute("SELECT count(*) FROM crm_call_leads WHERE called_at IS NULL").fetchone() == (1,)


def lead(lead_id, stage="Перевести в сделку", in_1c=False, orders=0, amount=0.0, closed=0.0, order_nos=(), category=None):
    return {"lead_id": lead_id, "stage": stage, "in_1c": in_1c, "orders": orders, "amount": amount, "closed": closed,
            "order_nos": list(order_nos), "category": category}


def call(lead_id, kind="Входящий", stage="Перевести в сделку"):
    return {"lead_id": lead_id, "call_type": kind, "stage": stage}


def order(no, lead_id, amount, category="new", status="Закрыт"):
    return {"order_no": no, "lead_id": lead_id, "amount": amount, "category": category, "status_1c": status}


def test_new_clients_only_and_no_double_count():
    forms = [lead(1, in_1c=True, orders=2, amount=100000, closed=50000, order_nos=("А-1", "А-2")),
             lead(2, in_1c=True, orders=1, amount=70000, order_nos=("А-3",), category="existing"),  # действующий
             lead(3, "Нецелевое обращение")]
    calls = [call(10), call(11), call(12, "Пропущенный входящий", "Не обработан")]
    orders = [order("А-1", 10, 60000),                     # тот же заказ пришёл и с сайта — считается за сайтом
              order("Б-1", 11, 20000, status="Ожидается оплата"),
              order("Б-2", 12, 999999, category="existing")]  # действующий клиент — не продажа
    s = ins.crm_stats(forms, calls, orders)
    assert (s["forms_in_1c"], s["forms_amount"], s["both_orders"]) == (1, 100000.0, 1)
    assert (s["clients_total"], s["orders_total"], s["amount_total"], s["closed_total"]) == (2, 3, 120000.0, 50000.0)
    funnel = ins.crm_funnel(s, 10000, 120)
    assert [x["total"] for x in funnel] == [10000, 120, 6, 5, 2, 3]
    assert [x["from_prev"] is None for x in funnel] == [True, False, True, False, False, True]  # лиды и заказы — без доли
    assert funnel[-1]["note"] == "1,5 заказа на клиента"
    assert all(0 < x["width"] <= 100 for x in funnel)
    rows = {r["label"]: r for r in ins.crm_summary_rows(s)}
    target = rows["Конверсия целевых"]
    assert target["key"] and (target["total"], target["forms"], round(target["calls"], 1)) == (40.0, 50.0, 33.3)
    assert (rows["Нашлись среди целевых"]["total"], rows["Сумма этих заказов, ₽"]["calls"]) == (2, 20000.0)


def sheet_file(path, rows, title="Заказы"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    for row in rows:
        ws.append(row)
    wb.save(path)
    return path


def test_orders_export_by_lines(tmp_path):
    """Построчная выгрузка: сумма заказа из «Суммы документа», почта и телефоны — отпечатками, клиент — отпечатком."""
    from analytics import privacy
    path = sheet_file(tmp_path / "orders.xlsx", [
        ["Номер", "Дата", "Контрагент", "Email", "Телефон", "Статус", "Номенклатура", "Группа номенклатуры",
         "Сумма строки", "Сумма документа"],
        ["АТЦБ-1", datetime(2026, 10, 5, 12), "ИП Иванов", "Ivanov@Mail.ru", "+7 (917) 470-64-44", "Закрыт",
         "Мембрана 3-слойная", "Мембранные ткани", 30000, 50000],
        ["АТЦБ-1", datetime(2026, 10, 5, 12), "ИП Иванов", "Ivanov@Mail.ru", "+7 (917) 470-64-44", "Закрыт",
         "Флис 280", "Флис", 20000, 50000],
        ["АТЦБ-2", "06.10.2026", "ООО «Ромашка»", "", "", "В работе", "Оксфорд 600D", "Оксфорд", 9000, 9000],
        ["Итого", None, None, None, None, None, None, None, 59000, 59000],
    ])
    f = parse_file(path)
    assert (f.kind, f.month) == ("orders", date(2026, 10, 1)) and not f.notes
    a, b = sorted(f.orders, key=lambda o: o.order_no)
    assert (a.order_no, a.order_date, a.amount, a.status) == ("АТЦБ-1", date(2026, 10, 5), 50000.0, "Закрыт")
    assert a.items == ("Мембрана 3-слойная", "Флис 280") and a.groups == ("Мембранные ткани", "Флис")
    assert a.email_hashes == (privacy.email_hash("ivanov@mail.ru"),) and a.phone_hashes == (privacy.phone_hash("9174706444"),)
    assert a.client_hash == privacy.fingerprint("client", "ип иванов") and b.amount == 9000.0
    assert "Иванов" not in repr(f.orders) and "ivanov" not in repr(f.orders).lower()


def test_orders_export_documents_with_title_rows(tmp_path):
    """Список документов 1С: заголовки ниже шапки, номер и дата внутри «Заказ клиента … от …», несколько адресов."""
    from analytics import privacy
    path = sheet_file(tmp_path / "docs.xlsx", [
        ["Заказы клиентов"], ["Период: октябрь 2026"], [],
        ["Заказ клиента", "Клиент", "Электронная почта", "Телефоны", "Сумма"],
        ["Заказ клиента ЦБ-000123 от 05.10.2026 12:00:00", "Ромашка", "a@b.ru\nc@d.ru", "+7 917 470-64-44, 8 999 000 11 22",
         "12 500,50"],
    ], title="TDSheet")
    f = parse_file(path)
    [o] = f.orders
    assert (o.order_no, o.order_date, o.amount) == ("ЦБ-000123", date(2026, 10, 5), 12500.5)
    assert len(o.email_hashes) == 2 and len(o.phone_hashes) == 2 and privacy.email_hash("c@d.ru") in o.email_hashes
    assert f.notes == ["нет номенклатуры — тему письма с купленным не сравнить"]


def test_orders_single_sum_column_by_lines(tmp_path):
    """Одна колонка «Сумма» в построчной выгрузке: разные суммы строк складываются, одинаковые — сумма заказа."""
    path = sheet_file(tmp_path / "o.xlsx", [
        ["Номер заказа", "Дата заказа", "Клиент", "Номенклатура", "Сумма"],
        ["1", datetime(2026, 10, 1), "А", "Флис", 100], ["1", datetime(2026, 10, 1), "А", "Оксфорд", 250],
        ["2", datetime(2026, 10, 2), "Б", "Флис", 400], ["2", datetime(2026, 10, 2), "Б", "Тент", 400],
    ])
    amounts = {o.order_no: o.amount for o in parse_file(path).orders}
    assert amounts == {"1": 350.0, "2": 400.0}


def test_generic_clients_are_not_grouped(tmp_path):
    path = sheet_file(tmp_path / "o.xlsx", [
        ["Номер", "Дата", "Клиент", "Сумма"],
        ["1", datetime(2026, 10, 1), "Физическое лицо", 100], ["2", datetime(2026, 10, 1), "Розничный покупатель", 100],
        ["3", datetime(2026, 10, 1), "ООО «Ромашка»", 100],
    ])
    hashes = {o.order_no: o.client_hash for o in parse_file(path).orders}
    assert hashes["1"] is None and hashes["2"] is None and hashes["3"]


def test_unknown_file_lists_headers_without_data(tmp_path):
    path = sheet_file(tmp_path / "x.xlsx", [["Клиент", "Почта", "Комментарий"], ["Иванов", "ivanov@mail.ru", "перезвонить"]],
                      title="Лист1")
    with pytest.raises(CrmFileError) as err:
        parse_file(path)
    assert "Клиент, Почта, Комментарий" in str(err.value) and "ivanov" not in str(err.value)


@needs_db
def test_orders_import_and_retry_of_unknown_files(tmp_path):
    with db.connect(TEST_DATABASE_URL) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        db.apply_schema(conn)
        sheet_file(tmp_path / "orders.xlsx", [["Номер", "Дата", "Сумма", "Email"], ["А-1", datetime(2026, 10, 5), 100, "a@b.ru"]])
        sheet_file(tmp_path / "other.xlsx", [["Что-то", "Ещё", "Другое"]])
        # файл, который прежняя версия разбора не узнала, разбирается заново
        db.skip_crm_file(conn, crm_files.sha256(tmp_path / "orders.xlsx"), "orders.xlsx", version=3)
        logs = []
        import_crm_folder(conn, tmp_path, log=logs.append)
        assert conn.execute("SELECT order_no, amount FROM orders_1c").fetchall() == [("А-1", 100)]
        assert any("заказы 1С — 1 заказов" in x for x in logs) and any("other.xlsx: не загружен" in x for x in logs)
        logs.clear()
        import_crm_folder(conn, tmp_path, log=logs.append)
        assert logs == []  # оба файла уже разобраны этой версией


def test_xls_cells_are_converted():
    import xlrd

    from analytics.sources.excel import xls_value

    class Cell:
        def __init__(self, ctype, value):
            self.ctype, self.value = ctype, value

    assert xls_value(Cell(xlrd.XL_CELL_EMPTY, ""), 0) is None
    assert xls_value(Cell(xlrd.XL_CELL_NUMBER, 12.0), 0) == 12 and xls_value(Cell(xlrd.XL_CELL_NUMBER, 12.5), 0) == 12.5
    assert xls_value(Cell(xlrd.XL_CELL_DATE, 46300.0), 0) == datetime(2026, 10, 5)
    assert xls_value(Cell(xlrd.XL_CELL_TEXT, "05.10.2026"), 0) == "05.10.2026"


def test_orders_list_with_labelled_contacts(tmp_path):
    """Список заказов 1С: «Текущее состояние», подписи в ячейках почты и несколько телефонов в одной."""
    from analytics import privacy
    path = sheet_file(tmp_path / "list.xlsx", [
        ["Дата", "Клиент", "Контрагент", "Номер", "Сумма", "Текущее состояние", "Комментарий", "Менеджер",
         "Электронная почта", "Телефон"],
        ["05.10.2026", "Ромашка", "ООО «Ромашка»", "АТЦБ-026330", 49896.5, "Закрыт", "срочно", "Петров",
         "E-mail=Ivan@Mail.ru,  buh@romashka.ru", "т.=(3452) 12-34-56, 8 917 470 64 44 8 999 000 11 22"],
    ])
    [o] = parse_file(path).orders
    assert (o.order_no, o.order_date, o.amount, o.status) == ("АТЦБ-026330", date(2026, 10, 5), 49896.5, "Закрыт")
    assert o.email_hashes == (privacy.email_hash("ivan@mail.ru"), privacy.email_hash("buh@romashka.ru"))
    assert set(o.phone_hashes) == {privacy.phone_hash("3452123456"), privacy.phone_hash("9174706444"),
                                   privacy.phone_hash("9990001122")}


@needs_db
def test_order_exports_complement_each_other(tmp_path):
    """Список заказов с контактами + построчный отчёт с номенклатурой: второй не затирает контакты первого."""
    with db.connect(TEST_DATABASE_URL) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        db.apply_schema(conn)
        sheet_file(tmp_path / "a_list.xlsx", [["Номер", "Дата", "Сумма", "Электронная почта", "Текущее состояние"],
                                              ["АТЦБ-1", "05.10.2026", 500, "a@b.ru", "Закрыт"]])
        # в построчном отчёте только часть позиций (на 300 ₽ из 500) — сумма заказа остаётся из списка
        sheet_file(tmp_path / "b_lines.xlsx", [["Заказ клиента", "Номенклатура", "Сумма"],
                                               ["Заказ клиента АТЦБ-1 от 05.10.2026 12:00:00", "Флис 280", 100],
                                               ["Заказ клиента АТЦБ-1 от 05.10.2026 12:00:00", "Оксфорд 600D", 200]])
        logs = []
        import_crm_folder(conn, tmp_path, log=logs.append)
        row = conn.execute("SELECT amount, status, cardinality(email_hashes), items FROM orders_1c").fetchone()
        assert row == (500, "Закрыт", 1, ["Флис 280", "Оксфорд 600D"])
        text = "\n".join(logs)
        assert "b_lines.xlsx: заказы 1С — 1 заказов (2 строк)" in text and "номенклатура ← «Номенклатура»" in text
        assert "с номенклатурой или группой: 1" in text and "уже были в базе из других выгрузок: 1" in text
