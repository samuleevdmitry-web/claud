import os
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from analytics import db
from analytics.dashboard.pages import seasonality
from analytics.keywords import load_keywords
from analytics.schedule import TZ, due_sources
from analytics.sources.wordstat import MonthCount, WordstatClient, WordstatError, parse_dynamics
from analytics.sync import sync_wordstat
from tests.fakes import FakeResponse, FakeSession

ROOT = Path(__file__).resolve().parent.parent
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
needs_db = pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL не задан")


def test_project_keywords_file():
    config = load_keywords(ROOT / "wordstat.toml")
    assert config.regions["oldos.ru"].id == 225 and config.regions["oldos.kz"].id == 159
    brand = [k.phrase for k in config.keywords if k.kind == "brand"]
    assert "олдос ткани" in brand
    # детская одежда и писатель Хаксли не должны попасть в брендовый B2B-спрос
    assert not any(word in p for p in brand for word in ("одежд", "куртк", "хаксли"))
    categories = {(k.direction, k.category) for k in config.keywords if k.kind == "category"}
    assert {d for d, _ in categories} == {"Ткани", "Ткани по назначению", "ПВХ", "ПВХ по назначению", "Фурнитура"}
    # внутри одной категории фраза не повторяется
    pairs = [(k.direction, k.category, k.phrase) for k in config.keywords if k.kind == "category"]
    assert len(pairs) == len(set(pairs))


def test_missing_count_means_zero():
    assert parse_dynamics([
        {"date": "2026-08-01T00:00:00Z", "count": "742", "share": 0.0001},
        {"date": "2026-09-01T00:00:00Z"},
    ]) == [MonthCount(date(2026, 8, 1), 742, 0.0001), MonthCount(date(2026, 9, 1), 0, 0.0)]


def test_client_sends_folder_and_region():
    session = FakeSession([FakeResponse(200, {"results": [{"date": "2026-09-01T00:00:00Z", "count": "5"}]})])
    client = WordstatClient("", "folder-1", session=session, pause=0)
    assert client.monthly("олдос ткани", 225, date(2023, 1, 15)) == [MonthCount(date(2026, 9, 1), 5, 0.0)]
    _, body = session.calls[0]
    assert body == {"phrase": "олдос ткани", "period": "PERIOD_MONTHLY", "fromDate": "2023-01-01T00:00:00Z",
                    "regions": ["225"], "folderId": "folder-1"}
    assert "Authorization" not in session.headers  # ключ подставляет прокси


def test_client_needs_folder():
    with pytest.raises(WordstatError, match="WORDSTAT_FOLDER_ID"):
        WordstatClient("key", "")


def test_seasonality_uses_full_years_only():
    series = {date(y, m, 1): (100 if m == 11 else 10 if m == 6 else 50) for y in (2024, 2025) for m in range(1, 13)}
    series[date(2026, 1, 1)] = 1000  # неполный год не влияет
    assert seasonality(series) == "ноя · июн"
    assert seasonality({date(2026, 1, 1): 5}) == "—"


def test_wordstat_monthly_on_the_fifth():
    monthly = {"wordstat": 5}

    def due(now, last_ok):
        runs = {"wordstat": {"last_ok": last_ok, "last_attempt": last_ok}}
        return [s for s, _ in due_sources(now, runs, ["wordstat"], {"wordstat": 7}, monthly_on=monthly)]

    loaded_nov5 = datetime(2026, 11, 5, 6, 20, tzinfo=TZ)
    assert due(datetime(2026, 11, 12, 9, 0, tzinfo=TZ), loaded_nov5) == []      # в ноябре уже загружено
    assert due(datetime(2026, 12, 4, 9, 0, tzinfo=TZ), loaded_nov5) == []       # до 5-го не трогаем
    assert due(datetime(2026, 12, 5, 5, 0, tzinfo=TZ), loaded_nov5) == []       # 5-е, но до 6:00
    assert due(datetime(2026, 12, 5, 6, 15, tzinfo=TZ), loaded_nov5) == ["wordstat"]
    # ноутбук был выключен 5-го — загрузка при первом включении после
    assert due(datetime(2026, 12, 9, 10, 0, tzinfo=TZ), loaded_nov5) == ["wordstat"]
    # январь: последний срок — 5 декабря
    assert due(datetime(2027, 1, 3, 10, 0, tzinfo=TZ), datetime(2026, 12, 5, 7, 0, tzinfo=TZ)) == []


class FakeWordstatClient:
    def __init__(self, counts: dict[str, dict[date, int]], fail: set[str] = frozenset()):
        self.counts = counts
        self.fail = fail

    def monthly(self, phrase, region_id, since):
        if phrase in self.fail:
            raise WordstatError(f"/dynamics «{phrase}»: HTTP 400: Invalid query")
        return [MonthCount(m, c, 0.0) for m, c in sorted(self.counts.get(phrase, {}).items()) if m >= since]

    def top_requests(self, phrase, region_id, limit=50):
        return [(phrase, sum(self.counts.get(phrase, {}).values()))]


KEYWORDS = """
[regions]
"example.ru" = { id = 225, name = "Россия" }

[brand]
phrases = ["бренд ткани"]

[market]
phrases = ["ткани оптом"]

[[directions]]
name = "Ткани"
categories = [
    { name = "Оксфорд", phrases = ["ткань оксфорд", "кордура ткань"] },
    { name = "Тенты", phrases = ["ткань для тента"] },
]

[[directions]]
name = "ПВХ"
categories = [
    { name = "Тенты ПВХ", phrases = ["ткань для тента"] },
]
"""


@needs_db
def test_sync_and_demand_section(tmp_path, monkeypatch):
    path = tmp_path / "wordstat.toml"
    path.write_text(KEYWORDS, encoding="utf-8")
    aug, sep = date(2026, 8, 1), date(2026, 9, 1)
    counts = {
        "бренд ткани": {aug: 80, sep: 100},
        "ткани оптом": {aug: 1000, sep: 1200},
        "ткань оксфорд": {aug: 300, sep: 330},
        "кордура ткань": {aug: 20, sep: 30},
        "ткань для тента": {aug: 50, sep: 40},
    }
    with db.connect(TEST_DATABASE_URL) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        db.apply_schema(conn)
        with pytest.raises(WordstatError, match="Не загрузились 1 фраз"):
            sync_wordstat(conn, FakeWordstatClient(counts, fail={"кордура ткань"}), load_keywords(path),
                          since=date(2023, 1, 1), today=date(2026, 11, 12), log=lambda _: None)
        sync_wordstat(conn, FakeWordstatClient(counts), load_keywords(path),
                      since=date(2023, 1, 1), today=date(2026, 11, 12), log=lambda _: None)
        oxford = conn.execute(
            "SELECT demand FROM wordstat_groups_monthly WHERE category = 'Оксфорд' AND month = %s", (sep,)
        ).fetchone()
        assert oxford == (360,)  # две фразы категории суммируются
        fabrics = conn.execute(
            "SELECT demand FROM wordstat_directions_monthly WHERE direction = 'Ткани' AND month = %s", (sep,)
        ).fetchone()
        assert fabrics == (400,)
        db.upsert_counter(conn, {"id": 42, "name": "example.ru", "site2": {"site": "example.ru"}}, "site")
        conn.execute(
            "INSERT INTO metrika_traffic (counter_id, grain, period_start, slice, dim_key, dim_label, visits, users,"
            " pageviews) VALUES (42, 'month', '2026-10-01', 'total', '', 'Всего', 10, 8, 20)"
        )

    from fastapi.testclient import TestClient

    from analytics.dashboard.app import app

    for name, value in {"DATABASE_URL": TEST_DATABASE_URL, "METRIKA_COUNTERS": "42", "METRIKA_MAPS_COUNTERS": "",
                        "WEBMASTER_HOSTS": "", "WORDSTAT_KEYWORDS": str(path)}.items():
        monkeypatch.setenv(name, value)
    page = TestClient(app).get("/demand", params={"month": "2026-10", "aud": "all"})
    assert page.status_code == 200
    text = page.text
    assert "Вордстат, Россия, сентябрь 2026" in text  # октября в Вордстате ещё нет
    assert "Брендовый спрос (B2B)" in text and "▲ 25,0%" in text  # 100 против 80
    assert "Тенты ПВХ" in text and "Оксфорд" in text
