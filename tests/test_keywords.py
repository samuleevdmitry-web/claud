"""Семантика и таблица ключей: разбор таблицы, загрузка из папки, выборка с фильтрами."""

import os
from datetime import date, datetime

import openpyxl
import pytest

from analytics import db
from analytics.dashboard import queries as q
from analytics.sources.semantics import SemanticsError, export_url, normalize, parse_sheets
from analytics.sync import import_crm_folder

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL не задан")
HOST = "https:www.example.ru:443"

HEADER = ["Фраза", "Частотность год", datetime(2026, 8, 31), datetime(2026, 9, 30)]
YANDEX = [HEADER, ["ткань оксфорд", 11091, "--", 45], ["Ткань  Оксфорд", 5, 3, 3],  # дубль после нормализации
          ["мембранная ткань", 845, 8, 6], ["ткань для тюбингов", 1, "--", 12]]
GOOGLE = [HEADER, ["ткань оксфорд", 11091, "--", "--"], ["мембранная ткань", 845, 30, 25]]


def test_parse_semantics():
    sem = parse_sheets({"Яндекс": YANDEX, "Google": GOOGLE, "Заметки": [["что-то"]]})
    assert set(sem.keywords) == {"ткань оксфорд", "мембранная ткань", "ткань для тюбингов"}
    assert sem.keywords["ткань оксфорд"] == ("ткань оксфорд", 11091)
    assert ("мембранная ткань", "google", date(2026, 9, 30), 25) in sem.positions
    assert ("ткань оксфорд", "yandex", date(2026, 8, 31), None) in sem.positions  # «--» — нет в выдаче
    assert normalize("  Ткань   ЁЛКА ") == "ткань елка"
    with pytest.raises(SemanticsError):
        parse_sheets({"Лист1": [["a", "b"]]})


def test_export_url():
    url = "https://docs.google.com/spreadsheets/d/1MKsrLEGRzwyq-L2_t/edit?gid=0#gid=0"
    assert export_url(url) == "https://docs.google.com/spreadsheets/d/1MKsrLEGRzwyq-L2_t/export?format=xlsx"
    with pytest.raises(SemanticsError):
        export_url("https://example.com/table")


@needs_db
def test_keywords_table(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.title = "Яндекс"
    for r in YANDEX:
        wb["Яндекс"].append(r)
    google = wb.create_sheet("Google")
    for r in GOOGLE:
        google.append(r)
    wb.save(tmp_path / "Семантика.xlsx")
    with db.connect(TEST_DATABASE_URL) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        db.apply_schema(conn)
        loaded = import_crm_folder(conn, tmp_path, log=lambda _: None)
        assert loaded[0].rows == 3
        rows = [(HOST, date(2026, 10, d), query, imp, clk, pos) for d, query, imp, clk, pos in [
            (1, "мембранная ткань", 10, 1, 8.0), (2, "Мембранная  ткань", 20, 2, 6.0),  # тот же ключ, иначе записан
            (1, "ткань для тюбингов", 3, 3, 12.0), (1, "сетка москитная", 50, 0, 30.0)]]
        conn.cursor().executemany("INSERT INTO webmaster_query_daily (host_id, date, query, impressions, clicks, position)"
                                  " VALUES (%s, %s, %s, %s, %s, %s)", rows)
        conn.execute("INSERT INTO rabbit_positions (project, date, query, frequency, position) VALUES"
                     " ('example.ru', '2026-10-05', 'ткань для тюбингов', 1, 11), ('example.ru', '2026-10-05', 'плюш', 316, NULL)")
        d1, d2 = date(2026, 10, 1), date(2026, 10, 2)

        found, total = q.keywords(conn, HOST, "example.ru", d1, d2, sort="shows")
        assert total == 4  # три фразы семантики и запрос проекта RabbitSeo, которого в семантике нет
        mem = next(r for r in found if r["norm"] == "мембранная ткань")
        assert (mem["shows"], mem["clicks"], float(mem["ctr"]), float(mem["position"]), mem["pos_yandex"],
                mem["pos_google"]) == (30, 3, 10.0, 6.7, 6, 25)
        tub = next(r for r in found if r["norm"] == "ткань для тюбингов")
        assert (tub["in_rabbit"], tub["rabbit_position"], float(tub["ctr"])) == (True, 11, 100.0)

        assert q.keywords(conn, HOST, "example.ru", d1, d2, rabbit="yes")[1] == 2
        assert [r["norm"] for r in q.keywords(conn, HOST, "example.ru", d1, d2, ctr_min=50)[0]] == ["ткань для тюбингов"]
        assert q.keywords(conn, HOST, "example.ru", d1, d2, search="оксф")[1] == 1
        everything, total = q.keywords(conn, HOST, "example.ru", d1, d2, scope="all", min_shows=1, sort="shows")
        assert total == 3 and everything[0]["phrase"] == "сетка москитная" and not everything[0]["is_key"]
        assert q.keyword_coverage(conn, HOST, "example.ru", d1, d2)["keys_with_shows"] == 2
        days = q.keyword_days(conn, HOST, "мембранная ткань", d1, d2)
        assert [(d["date"].day, d["shows"]) for d in days] == [(1, 10), (2, 20)]
