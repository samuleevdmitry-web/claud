"""Выгрузки Битрикс24 и 1С: разбор Excel и загрузка из папки — на синтетических файлах."""

import os
from datetime import date, datetime

import openpyxl
import pytest

from analytics import db
from analytics import insights as ins
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
